from __future__ import annotations

"""Derived, post-publication logical-match video generation.

The service reads only immutable video copies owned by physical publications.
One small pointer selects a coherent immutable video+manifest generation; no
request ever combines a mutable match directory with a logical match group.
"""

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_groups import (
    MATCH_GROUPS_DIR,
    PUBLISHED_MATCHES_DIR,
    MatchGroupError,
    delete_match_group,
    get_match_group,
    validate_match_group,
)
from app.services.published_video import PUBLISHED_VIDEO_ARTIFACT, load_published_video, sha256_file


COMBINED_VIDEO_FILENAME = "combined_match_video.mp4"
VIDEO_MANIFEST_FILENAME = "video_manifest.json"
VIDEO_GENERATIONS_DIRECTORY = "video-generations"
CURRENT_GENERATION_FILENAME = "current_video_generation.json"
VIDEO_JOB_FILENAME = "video_job.json"
VIDEO_LOCK_FILENAME = "video_job.lock"
VIDEO_SCHEMA_VERSION = "1.1.0"
VIDEO_POLICY_VERSION = "logical-video:v1-h264-yuv420p-no-audio;normalize-25fps-max-resolution"
VIDEO_DURATION_TOLERANCE_SEC = 0.25
_submission_lock = threading.Lock()
_active_job_keys: set[str] = set()


class MatchGroupVideoError(MatchGroupError):
    pass


def get_match_group_video_status(group_id: str) -> dict[str, Any]:
    group = get_match_group(group_id)
    group_dir = _group_dir(group)
    job = _recover_interrupted_job(group_dir)
    current = _current_generation(group_dir)
    validation = validate_match_group(group_id)
    if validation.get("status") != "compatible":
        return _state("stale", group, reason="match_group_stale", current=current, job=job)
    try:
        inputs = _validated_video_inputs(group, verify_content=False)
    except MatchGroupVideoError as error:
        return _state("stale" if current else "unavailable_source_video", group, reason=error.code, current=current, job=job)
    expected = _input_digest(inputs, group)
    if current:
        if (
            current["manifest"].get("input_semantic_digest") != expected
            or not _source_fingerprints_match(current["manifest"], inputs)
        ):
            return _state("stale", group, reason="source_video_generation_changed", current=current, job=job)
        # A failed (or still running) regeneration must not hide a valid video.
        return _state("ready", group, current=current, job=job)
    if _load(group_dir / CURRENT_GENERATION_FILENAME):
        return _state("stale", group, reason="current_video_generation_changed", job=job)
    if job.get("status") == "generating":
        return _state("generating", group, job=job)
    if job.get("status") == "failed":
        return _state("failed", group, reason=str(job.get("reason") or "video_generation_failed"), job=job)
    return _state("not_generated", group)


def submit_match_group_video_generation(group_id: str) -> dict[str, Any]:
    """Claim one durable single-host job, then return immediately."""

    with _submission_lock:
        group, job = _begin_generation(group_id)
        if job.get("already_running"):
            return _state("generating", group, job=job)
        try:
            threading.Thread(target=_background_generate, args=(group_id, job), daemon=True).start()
        except Exception as error:
            _fail_generation_start(_group_path(str(group["group_id"])), job, error)
            raise MatchGroupVideoError("video_generation_start_failed", "Could not start the combined-video generation worker.") from error
        return _state("generating", group, job=job)


def generate_match_group_video(group_id: str, *, job: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synchronously render a generation; public direct calls own a job too."""

    owned_here = job is None
    if job is None:
        with _submission_lock:
            group, job = _begin_generation(group_id)
            if job.get("already_running"):
                return _state("generating", group, job=job)
    else:
        group = get_match_group(group_id)
    assert job is not None
    try:
        return _render_generation(group, job)
    finally:
        if owned_here:
            _finish_job(_group_path(str(group["group_id"])), job)


def combined_video_path(group_id: str) -> Path:
    group = get_match_group(group_id)
    current = _current_generation(_group_dir(group))
    if not current:
        raise FileNotFoundError(group_id)
    return current["video_path"]


def generation_video(group_id: str, generation_id: str) -> dict[str, Any]:
    """Return one proven immutable generation; never follow the current pointer."""

    group = get_match_group(group_id)
    group_dir = _group_dir(group)
    normalized_generation_id = _validated_generation_id(generation_id)
    generation_dir = _generation_dir(group_dir, normalized_generation_id)
    manifest = _load(generation_dir / VIDEO_MANIFEST_FILENAME)
    if (
        manifest.get("group_id") != group["group_id"]
        or manifest.get("generation_id") != normalized_generation_id
        or not _generation_is_valid(generation_dir, manifest)
    ):
        raise FileNotFoundError(normalized_generation_id)
    return {"manifest": manifest, "video_path": generation_dir / COMBINED_VIDEO_FILENAME}


def delete_match_group_when_video_idle(group_id: str) -> dict[str, Any]:
    """Delete only after taking the same durable lock used by render workers."""

    group = get_match_group(group_id)
    group_dir = _group_dir(group)
    with _submission_lock:
        job = _recover_interrupted_job(group_dir)
        if job.get("status") == "generating":
            raise MatchGroupVideoError("video_generation_in_progress", "Cannot delete a logical match while its combined video is generating.")
        job_key = f"delete-{uuid.uuid4().hex}"
        owner = {"pid": os.getpid(), "job_key": job_key, "created_at": _now(), "ownership_mode": "single_host_filesystem_pid_lock"}
        lock_path = group_dir / VIDEO_LOCK_FILENAME
        if not _acquire_lock(lock_path, owner, create_parent=False):
            raise MatchGroupVideoError("video_generation_in_progress", "Cannot delete a logical match while its combined video is generating.")
        _active_job_keys.add(_active_token(group_dir, job_key))
        try:
            return delete_match_group(group_id)
        finally:
            _active_job_keys.discard(_active_token(group_dir, job_key))
            _release_lock(lock_path, job_key)


def _begin_generation(group_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Claim an existing group before any expensive source-video preflight."""

    initial_group = get_match_group(group_id)
    group_dir = _group_dir(initial_group)
    current_job = _recover_interrupted_job(group_dir)
    if current_job.get("status") == "generating":
        return initial_group, {**current_job, "already_running": True}
    # The lock cannot depend on the source digest because hashing belongs
    # inside the durable ownership interval.
    job_key = f"video-generation-{uuid.uuid4().hex}"
    owner = {"pid": os.getpid(), "job_key": job_key, "created_at": _now(), "ownership_mode": "single_host_filesystem_pid_lock"}
    lock_path = group_dir / VIDEO_LOCK_FILENAME
    if not _acquire_lock(lock_path, owner, create_parent=False):
        if not _group_manifest_exists(group_dir):
            raise KeyError(group_id)
        current_job = _recover_interrupted_job(group_dir)
        if current_job.get("status") == "generating":
            return initial_group, {**current_job, "already_running": True}
        lock = _load(lock_path)
        if lock and _lock_owner_alive(group_dir, lock):
            return initial_group, {
                "status": "generating",
                "job_key": lock.get("job_key"),
                "started_at": lock.get("created_at"),
                "already_running": True,
            }
        raise MatchGroupVideoError("video_generation_busy", "Combined video generation is already owned by another local worker.")
    active_token = _active_token(group_dir, job_key)
    _active_job_keys.add(active_token)
    try:
        if not _group_manifest_exists(group_dir):
            raise KeyError(group_id)
        # Never build a job from a pre-lock snapshot: delete/update can win
        # before ownership is acquired.
        group = get_match_group(group_id)
        if validate_match_group(str(group["group_id"])).get("status") != "compatible":
            raise MatchGroupVideoError("match_group_stale", "Logical match sources must be current before generating video.")
        inputs = _validated_video_inputs(group, verify_content=True)
        input_digest = _input_digest(inputs, group)
        job = {
            "schema_version": VIDEO_SCHEMA_VERSION, "group_id": group["group_id"], "job_key": job_key,
            "input_semantic_digest": input_digest, "status": "generating", "started_at": _now(),
            "group_contract_digest": _group_contract_digest(group), "owner_pid": os.getpid(),
            "ownership_mode": "single_host_filesystem_pid_lock", "source_count": len(inputs),
        }
        _write(group_dir / VIDEO_JOB_FILENAME, job)
        return group, job
    except Exception:
        _active_job_keys.discard(active_token)
        _release_lock(lock_path, job_key)
        raise


def _render_generation(group: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    group_dir = _group_dir(group)
    started_monotonic = time.monotonic()
    started_at = str(job.get("started_at") or _now())
    generation_id = f"{str(job['input_semantic_digest'])[:16]}-{uuid.uuid4().hex[:12]}"
    generation_dir = _generation_dir(group_dir, generation_id)
    generation_dir.mkdir(parents=True, exist_ok=False)
    work_dir = generation_dir / ".work"
    work_dir.mkdir()
    try:
        if validate_match_group(str(group["group_id"])).get("status") != "compatible":
            raise MatchGroupVideoError("match_group_stale", "Logical match sources changed before video generation.")
        inputs = _validated_video_inputs(group, verify_content=True)
        input_digest = _input_digest(inputs, group)
        if input_digest != job.get("input_semantic_digest"):
            raise MatchGroupVideoError("source_video_generation_changed", "Source video inputs changed after the generation was queued.")
        probes = [_probe(item["path"]) for item in inputs]
        _validate_source_media(probes, inputs)
        output = generation_dir / COMBINED_VIDEO_FILENAME
        mode = "stream_copy" if _stream_copy_compatible(probes) else "normalized"
        if mode == "stream_copy":
            _concat([item["path"] for item in inputs], output, copy_streams=True)
        else:
            normalized = [_normalize(item["path"], work_dir, index, probes) for index, item in enumerate(inputs)]
            _concat(normalized, output, copy_streams=True)
        output_probe = _probe(output)
        timeline_span = float(group["timing"]["timeline_span_sec"])
        duration = float(output_probe["duration_sec"])
        if abs(duration - timeline_span) > VIDEO_DURATION_TOLERANCE_SEC:
            raise MatchGroupVideoError("output_duration_mismatch", "Combined video duration differs from the logical timeline.")
        if output_probe.get("codec") != "h264" or output_probe.get("pix_fmt") != "yuv420p" or output_probe.get("audio") or output.stat().st_size <= 0:
            raise MatchGroupVideoError("output_video_invalid", "Combined video does not meet the H.264/yuv420p/no-audio output contract.")
        shutil.rmtree(work_dir)
        completed_at = _now()
        manifest = {
            "schema_version": VIDEO_SCHEMA_VERSION, "group_id": group["group_id"], "generation_id": generation_id,
            "generation_status": "ready", "input_semantic_digest": input_digest, "policy_version": VIDEO_POLICY_VERSION,
            "logical_timeline": dict(group["timing"]), "members": [{key: value for key, value in item.items() if key != "path"} for item in inputs],
            "output": {"artifact": COMBINED_VIDEO_FILENAME, "semantic_digest": sha256_file(output), "duration_sec": duration, "codec": output_probe["codec"], "width": output_probe["width"], "height": output_probe["height"], "fps": output_probe["fps"], "file_size_bytes": output.stat().st_size, "fingerprint": _file_fingerprint(output)},
            "observability": {"started_at": started_at, "completed_at": completed_at, "elapsed_sec": round(time.monotonic() - started_monotonic, 3), "source_count": len(inputs), "source_total_bytes": sum(item["file_size_bytes"] for item in inputs), "output_bytes": output.stat().st_size, "generation_mode": mode},
        }
        _write(generation_dir / VIDEO_MANIFEST_FILENAME, manifest)
        if not _generation_is_valid(generation_dir, manifest, verify_content=True):
            raise MatchGroupVideoError("output_video_invalid", "Generated video could not be verified before publication.")
        _validate_pre_commit(group, job, inputs)
        previous = _current_generation(group_dir)
        previous_generation_id = str(previous["manifest"].get("generation_id") or "") if previous else None
        # One atomic pointer chooses a fully written immutable video+manifest pair.
        _write(group_dir / CURRENT_GENERATION_FILENAME, {"schema_version": VIDEO_SCHEMA_VERSION, "group_id": group["group_id"], "generation_id": generation_id, "input_semantic_digest": input_digest, "published_at": completed_at})
    except Exception as error:
        shutil.rmtree(generation_dir, ignore_errors=True)
        # Deleting the group wins over a background render: do not recreate a
        # manifest-less directory or leave a new durable job behind.
        if _group_manifest_exists(group_dir):
            _write(group_dir / VIDEO_JOB_FILENAME, {**job, "status": "failed", "failed_at": _now(), "reason": error.code if isinstance(error, MatchGroupVideoError) else "video_generation_failed", "detail": str(error)})
        raise

    # The pointer is the commit point.  Never roll back this immutable
    # generation because best-effort cleanup of the now-obsolete job failed.
    cleanup_job = _post_commit_cleanup(group_dir, generation_id, previous_generation_id, job, completed_at)
    return _state("ready", group, current={"manifest": manifest, "video_path": output}, job=cleanup_job)


def _background_generate(group_id: str, job: dict[str, Any]) -> None:
    group_dir = _group_path(group_id)
    try:
        _render_generation(get_match_group(group_id), job)
    except Exception:
        pass
    finally:
        _finish_job(group_dir, job)


def _finish_job(group_dir: Path, job: dict[str, Any]) -> None:
    job_key = str(job.get("job_key") or "")
    _active_job_keys.discard(_active_token(group_dir, job_key))
    _release_lock(group_dir / VIDEO_LOCK_FILENAME, job_key)


def _fail_generation_start(group_dir: Path, job: dict[str, Any], error: Exception) -> None:
    if _group_manifest_exists(group_dir):
        try:
            _write(
                group_dir / VIDEO_JOB_FILENAME,
                {
                    **job,
                    "status": "failed",
                    "failed_at": _now(),
                    "reason": "video_generation_start_failed",
                    "detail": str(error),
                },
            )
        except OSError:
            pass
    _finish_job(group_dir, job)


def _current_generation(group_dir: Path) -> dict[str, Any] | None:
    pointer = _load(group_dir / CURRENT_GENERATION_FILENAME)
    generation_id = str(pointer.get("generation_id") or "")
    if not generation_id or Path(generation_id).name != generation_id:
        return None
    generation_dir = _generation_dir(group_dir, generation_id)
    manifest = _load(generation_dir / VIDEO_MANIFEST_FILENAME)
    if not _generation_is_valid(generation_dir, manifest):
        return None
    return {"pointer": pointer, "manifest": manifest, "video_path": generation_dir / COMBINED_VIDEO_FILENAME}


def _post_commit_cleanup(
    group_dir: Path,
    generation_id: str,
    previous_generation_id: str | None,
    job: dict[str, Any],
    completed_at: str,
) -> dict[str, Any] | None:
    """Best-effort cleanup after the pointer commit; never touch current media."""

    warnings: list[str] = []
    try:
        (group_dir / VIDEO_JOB_FILENAME).unlink(missing_ok=True)
    except OSError as error:
        warnings.append(f"job cleanup: {error}")
    try:
        _remove_superseded_generations(group_dir, generation_id, previous_generation_id)
    except OSError as error:
        warnings.append(f"superseded generation cleanup: {error}")
    if not warnings:
        return None
    completed = {
        **job,
        "status": "completed",
        "completed_at": completed_at,
        "cleanup_warning": "; ".join(warnings),
    }
    try:
        _write(group_dir / VIDEO_JOB_FILENAME, completed)
    except OSError:
        # The pointer already commits the generation.  A later status read can
        # safely recover this stale job without touching the media.
        pass
    return completed


def _remove_superseded_generations(
    group_dir: Path,
    current_generation_id: str,
    previous_generation_id: str | None,
) -> None:
    root = group_dir / VIDEO_GENERATIONS_DIRECTORY
    if not root.is_dir():
        return
    for candidate in root.iterdir():
        if candidate.is_dir() and candidate.name not in {current_generation_id, previous_generation_id}:
            shutil.rmtree(candidate)


def _generation_is_valid(
    generation_dir: Path,
    manifest: dict[str, Any],
    *,
    verify_content: bool = False,
) -> bool:
    output = manifest.get("output") if isinstance(manifest.get("output"), dict) else {}
    video = generation_dir / COMBINED_VIDEO_FILENAME
    if (
        manifest.get("generation_status") != "ready"
        or not video.is_file()
        or video.stat().st_size <= 0
        or not _fingerprints_match(_file_fingerprint(video), output.get("fingerprint"))
    ):
        return False
    return not verify_content or sha256_file(video) == output.get("semantic_digest")


def _file_fingerprint(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file() or stat.st_size <= 0:
        return None
    return {
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _fingerprints_match(current: dict[str, Any] | None, expected: object) -> bool:
    if not isinstance(expected, dict) or current is None:
        return False
    return all(current.get(key) == expected.get(key) for key in ("size_bytes", "mtime_ns"))


def _source_fingerprints_match(manifest: dict[str, Any], inputs: list[dict[str, Any]]) -> bool:
    members = manifest.get("members")
    if not isinstance(members, list) or len(members) != len(inputs):
        return False
    for member, source in zip(members, inputs, strict=True):
        if not isinstance(member, dict):
            return False
        if member.get("published_id") != source.get("published_id"):
            return False
        if not _fingerprints_match(source.get("source_fingerprint"), member.get("source_fingerprint")):
            return False
    return True


def _group_contract_digest(group: dict[str, Any]) -> str:
    """Stable generation contract; intentionally excludes presentation metadata."""

    members = group.get("members") if isinstance(group.get("members"), list) else []
    pinned_keys = (
        "sequence_index",
        "published_id",
        "source_match_id",
        "aggregation_input_schema_version",
        "aggregation_policy_version",
        "aggregation_input_semantic_digest",
        "public_report_schema_version",
        "public_report_semantic_digest",
        "reviewed_identity_digest",
        "logical_start_sec",
        "logical_end_sec",
    )
    return canonical_json_sha256({
        "group_id": group.get("group_id"),
        "members": [{key: member.get(key) for key in pinned_keys} for member in members if isinstance(member, dict)],
        "timing": group.get("timing"),
    })


def _validate_pre_commit(
    original_group: dict[str, Any],
    job: dict[str, Any],
    captured_inputs: list[dict[str, Any]],
) -> None:
    """Cheaply reject a candidate if its logical sources drifted during ffmpeg."""

    group_id = str(original_group["group_id"])
    current_group = get_match_group(group_id)
    if validate_match_group(group_id).get("status") != "compatible":
        raise MatchGroupVideoError("match_group_changed_during_generation", "Logical match sources changed while the combined video was rendering.")
    if _group_contract_digest(current_group) != job.get("group_contract_digest"):
        raise MatchGroupVideoError("match_group_changed_during_generation", "Logical match order, timing, or pinned publication changed during rendering.")
    current_inputs = _validated_video_inputs(current_group, verify_content=False)
    if _input_digest(current_inputs, current_group) != job.get("input_semantic_digest"):
        raise MatchGroupVideoError("source_video_generation_changed", "Published source video descriptors changed during rendering.")
    if not _input_fingerprints_match(captured_inputs, current_inputs):
        raise MatchGroupVideoError("source_video_generation_changed", "Published source video files changed during rendering.")


def _input_fingerprints_match(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> bool:
    if len(first) != len(second):
        return False
    return all(
        first_item.get("published_id") == second_item.get("published_id")
        and _fingerprints_match(first_item.get("source_fingerprint"), second_item.get("source_fingerprint"))
        for first_item, second_item in zip(first, second, strict=True)
    )


def _recover_interrupted_job(group_dir: Path) -> dict[str, Any]:
    job = _load(group_dir / VIDEO_JOB_FILENAME)
    if job.get("status") != "generating":
        return job
    lock = _load(group_dir / VIDEO_LOCK_FILENAME)
    job_key = str(job.get("job_key") or "")
    if lock.get("job_key") == job_key and _lock_owner_alive(group_dir, lock):
        return job
    interrupted = {**job, "status": "failed", "failed_at": _now(), "reason": "video_generation_interrupted", "detail": "Persisted combined-video owner is no longer alive; generation may be retried."}
    if _group_manifest_exists(group_dir):
        _write(group_dir / VIDEO_JOB_FILENAME, interrupted)
    _release_lock(group_dir / VIDEO_LOCK_FILENAME, job_key)
    return interrupted


def _acquire_lock(path: Path, owner: dict[str, Any], *, create_parent: bool = True) -> bool:
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.is_dir():
        return False
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileNotFoundError:
            return False
        except FileExistsError:
            current = _load(path)
            if current and _lock_owner_alive(path.parent, current):
                return False
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(owner, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        return True
    return False


def _release_lock(path: Path, job_key: str) -> None:
    current = _load(path)
    if not current or str(current.get("job_key") or "") == job_key:
        path.unlink(missing_ok=True)


def _lock_owner_alive(group_dir: Path, owner: dict[str, Any]) -> bool:
    try:
        pid = int(owner.get("pid"))
    except (TypeError, ValueError):
        return False
    if pid == os.getpid():
        return _active_token(group_dir, str(owner.get("job_key") or "")) in _active_job_keys
    try:
        os.kill(pid, 0)
        return pid > 0
    except OSError:
        return False


def _validated_video_inputs(group: dict[str, Any], *, verify_content: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for member in group.get("members") or []:
        published_id = str(member.get("published_id") or "")
        source_dir = PUBLISHED_MATCHES_DIR / published_id
        video = load_published_video(
            source_dir,
            expected_public_report_digest=str(member.get("public_report_semantic_digest") or ""),
            verify_content=verify_content,
        )
        if video is None:
            raise MatchGroupVideoError("unavailable_source_video", "A selected publication has no proven final reviewed video.", member=published_id)
        path = source_dir / PUBLISHED_VIDEO_ARTIFACT
        logical_start, logical_end = float(member.get("logical_start_sec") or 0), float(member.get("logical_end_sec") or 0)
        if abs(float(video.get("duration_sec") or 0) - (logical_end - logical_start)) > VIDEO_DURATION_TOLERANCE_SEC:
            raise MatchGroupVideoError("source_video_duration_mismatch", "Published final video duration does not match its logical source duration.", member=published_id)
        fingerprint = _file_fingerprint(path)
        if fingerprint is None:
            raise MatchGroupVideoError("unavailable_source_video", "A selected publication has no proven final reviewed video.", member=published_id)
        items.append({"sequence_index": int(member.get("sequence_index") or 0), "published_id": published_id, "source_match_id": str(member.get("source_match_id") or ""), "logical_start_sec": logical_start, "logical_end_sec": logical_end, "source_video": {key: video[key] for key in ("semantic_digest", "duration_sec", "codec", "width", "height", "fps", "pix_fmt")}, "source_fingerprint": fingerprint, "file_size_bytes": fingerprint["size_bytes"], "path": path})
    return items


def _input_digest(inputs: list[dict[str, Any]], group: dict[str, Any]) -> str:
    return canonical_json_sha256({"policy_version": VIDEO_POLICY_VERSION, "members": [{key: value for key, value in item.items() if key not in {"path", "file_size_bytes", "source_fingerprint"}} for item in inputs], "logical_timeline": group.get("timing")})


def _probe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,pix_fmt,width,height,r_frame_rate,codec_type", "-of", "json", str(path)], check=True, capture_output=True, text=True)
        document = json.loads(result.stdout)
        streams = document.get("streams") if isinstance(document.get("streams"), list) else []
        video = next((row for row in streams if isinstance(row, dict) and row.get("codec_type") == "video"), None)
        if not isinstance(video, dict):
            raise ValueError("video stream missing")
        numerator, denominator = str(video.get("r_frame_rate") or "0/1").split("/", 1)
        return {"codec": str(video.get("codec_name") or ""), "pix_fmt": str(video.get("pix_fmt") or ""), "width": int(video.get("width") or 0), "height": int(video.get("height") or 0), "fps": float(numerator) / max(float(denominator), 1), "duration_sec": float((document.get("format") or {}).get("duration") or 0), "audio": any(isinstance(row, dict) and row.get("codec_type") == "audio" for row in streams)}
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise MatchGroupVideoError("video_codec_probe_failed", f"Could not inspect a source video: {error}") from error


def _validate_source_media(probes: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> None:
    for probe, item in zip(probes, inputs, strict=True):
        if probe["width"] <= 0 or probe["height"] <= 0 or probe["fps"] <= 0 or probe["duration_sec"] <= 0:
            raise MatchGroupVideoError("source_video_invalid", "Source video probe returned invalid media properties.", member=item["published_id"])
        if abs(probe["duration_sec"] - item["source_video"]["duration_sec"]) > VIDEO_DURATION_TOLERANCE_SEC:
            raise MatchGroupVideoError("source_video_generation_changed", "Source video no longer matches its published descriptor.", member=item["published_id"])


def _stream_copy_compatible(probes: list[dict[str, Any]]) -> bool:
    first = probes[0]
    return all(probe["codec"] == "h264" and probe["pix_fmt"] == "yuv420p" and not probe["audio"] and all(probe[key] == first[key] for key in ("codec", "pix_fmt", "width", "height", "fps", "audio")) for probe in probes)


def _concat(paths: list[Path], output: Path, *, copy_streams: bool) -> None:
    listing = output.parent / ".work" / "concat.txt"
    listing.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths), encoding="utf-8")
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    command.extend(["-c", "copy"] if copy_streams else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"])
    _run_ffmpeg([*command, str(output)])


def _normalize(path: Path, generation_dir: Path, index: int, probes: list[dict[str, Any]]) -> Path:
    width, height = max(int(probe["width"]) for probe in probes), max(int(probe["height"]) for probe in probes)
    width += width % 2
    height += height % 2
    normalized = generation_dir / f"normalized-{index}.mp4"
    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    _run_ffmpeg(["ffmpeg", "-y", "-i", str(path), "-vf", vf, "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(normalized)])
    return normalized


def _run_ffmpeg(command: list[str]) -> None:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise MatchGroupVideoError("video_tool_missing", "ffmpeg is required to generate a combined video.") from error
    if result.returncode != 0:
        raise MatchGroupVideoError("video_generation_failed", f"ffmpeg failed: {(result.stderr or '').strip()[-1000:]}")


def _state(status: str, group: dict[str, Any], *, reason: str | None = None, current: dict[str, Any] | None = None, job: dict[str, Any] | None = None) -> dict[str, Any]:
    generation_id = str(current["manifest"].get("generation_id") or "") if current else None
    return {
        "group_id": group["group_id"],
        "status": status,
        "reason": reason,
        "generation_id": generation_id,
        "manifest": current["manifest"] if current else None,
        "artifact_url": _artifact_url(group["group_id"], generation_id) if generation_id else None,
        "last_attempt": _public_job(job) if job else None,
    }


def _public_job(job: dict[str, Any]) -> dict[str, Any] | None:
    if not job:
        return None
    return {key: job.get(key) for key in (
        "status", "started_at", "completed_at", "failed_at", "reason", "detail", "cleanup_warning", "source_count",
    )}


def _artifact_url(group_id: str, generation_id: str) -> str:
    return f"/api/published/match-groups/{group_id}/video/generations/{generation_id}/file"


def _group_dir(group: dict[str, Any]) -> Path:
    path = _group_path(str(group["group_id"]))
    if not _group_manifest_exists(path):
        raise KeyError(str(group["group_id"]))
    return path


def _group_path(group_id: str) -> Path:
    return MATCH_GROUPS_DIR / group_id


def _group_manifest_exists(group_dir: Path) -> bool:
    return (group_dir / "manifest.json").is_file()


def _validated_generation_id(value: str) -> str:
    generation_id = str(value or "").strip()
    if not generation_id or Path(generation_id).name != generation_id or generation_id in {".", ".."}:
        raise FileNotFoundError(generation_id)
    return generation_id


def _generation_dir(group_dir: Path, generation_id: str) -> Path:
    return group_dir / VIDEO_GENERATIONS_DIRECTORY / generation_id


def _load(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _write(path: Path, document: dict[str, Any]) -> None:
    # Every video write belongs either to an existing group or to a generation
    # directory explicitly created by the renderer.  Creating parents here
    # could resurrect a group that won a concurrent delete race.
    if not path.parent.is_dir() or (
        path.name in {VIDEO_JOB_FILENAME, CURRENT_GENERATION_FILENAME}
        and not _group_manifest_exists(path.parent)
    ):
        raise FileNotFoundError(path.parent)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_token(group_dir: Path, job_key: str) -> str:
    return f"{group_dir.resolve()}::{job_key}"
