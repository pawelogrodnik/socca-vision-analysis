from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_reviewed_video import (
    RENDERER_VERSION,
    render_reviewed_video,
    reviewed_source_video_digest,
)

# Reviewed rendering is a local-first, single-host job. Ownership is persisted in
# an O_EXCL filesystem lock and validated against the owner PID (plus an in-process
# active token for same-PID recovery). It is not a distributed-worker lease.


JOB_FILENAME = "reviewed_video_job.json"
LOCK_FILENAME = "reviewed_video_job.lock"
_active_job_keys: set[str] = set()
_submission_lock = threading.Lock()
logger = logging.getLogger(__name__)


class ReviewedOutputBusyError(RuntimeError):
    pass


def generate_reviewed_output(
    match_path: Path,
    snapshot: dict[str, Any],
    match_doc: dict[str, Any],
    options: dict[str, Any],
    *,
    stats_already_current: bool = False,
) -> dict[str, Any]:
    with _submission_lock:
        return _generate_reviewed_output(
            match_path,
            snapshot,
            match_doc,
            options,
            stats_already_current=stats_already_current,
        )


def _generate_reviewed_output(
    match_path: Path,
    snapshot: dict[str, Any],
    match_doc: dict[str, Any],
    options: dict[str, Any],
    *,
    stats_already_current: bool,
) -> dict[str, Any]:
    source_video_digest = reviewed_source_video_digest(match_path, match_doc)
    key = canonical_digest(
        {
            "snapshot": snapshot["semantic_digest"],
            "source_video": source_video_digest,
            "options": options,
            "renderer_version": RENDERER_VERSION,
        }
    )
    existing = _load(match_path / JOB_FILENAME)
    if _reusable_job(existing, key, match_path):
        return existing
    lock_path = match_path / LOCK_FILENAME
    owner = {
        "pid": os.getpid(),
        "job_key": key,
        "created_at": _now(),
        "ownership_mode": "single_host_filesystem_pid_lock",
    }
    if not _acquire_lock(lock_path, owner):
        existing = _load(match_path / JOB_FILENAME)
        if _reusable_job(existing, key, match_path):
            return existing
        raise ReviewedOutputBusyError("Inny reviewed render dla tego meczu jest już uruchomiony.")
    try:
        existing = _load(match_path / JOB_FILENAME)
        if _reusable_job(existing, key, match_path):
            _release_lock(lock_path, key)
            return existing
        job = {
            "schema_version": "1.1.0",
            "job_key": key,
            "match_id": snapshot.get("match_id") or match_path.name,
            "status": "queued",
            "created_at": _now(),
            "owner_pid": os.getpid(),
            "ownership_mode": "single_host_filesystem_pid_lock",
            "options": options,
            "renderer_version": RENDERER_VERSION,
            "source_snapshot_digest": snapshot["semantic_digest"],
            "source_video_digest": source_video_digest,
            "error": None,
        }
        write_identity_json_atomic(match_path / JOB_FILENAME, job)
        _log_render(job, match_path, {"stage": "queued"})
        _active_job_keys.add(_active_token(match_path, key))
        threading.Thread(
            target=_run,
            args=(match_path, snapshot, match_doc, options, job, stats_already_current),
            daemon=True,
        ).start()
        return job
    except Exception:
        _release_lock(lock_path, key)
        raise


def reviewed_output_status(
    match_path: Path,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job = _load(match_path / JOB_FILENAME)
    if not job:
        return {"status": "missing"}
    if snapshot and snapshot.get("status") == "stale":
        return {**job, "status": "stale", "stale_reason": "reviewed_identity_changed"}
    if snapshot and job.get("source_snapshot_digest") != snapshot.get("semantic_digest") and job.get("status") == "completed":
        return {**job, "status": "stale", "stale_reason": "reviewed_identity_changed"}
    if job.get("status") in {"queued", "running"}:
        lock = _load(match_path / LOCK_FILENAME)
        job_key = str(job.get("job_key") or "")
        if lock.get("job_key") == job_key and _lock_owner_alive(match_path, lock):
            return job
        interrupted = {
            **job,
            "status": "failed",
            "failed_at": _now(),
            "error": {
                "message": "Render został przerwany; uruchom go ponownie.",
                "developer_detail": "Persisted renderer owner is no longer alive.",
            },
        }
        write_identity_json_atomic(match_path / JOB_FILENAME, interrupted)
        _release_lock(match_path / LOCK_FILENAME, job_key)
        return interrupted
    if job.get("status") == "completed" and not _completed_output_matches(job, match_path):
        return {
            **job,
            "status": "stale",
            "stale_reason": "reviewed_video_digest_mismatch",
        }
    return job


def reviewed_output_status_read_only(
    match_path: Path,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return render state for polling without recovering or writing a job file."""
    job = _load(match_path / JOB_FILENAME)
    if not job:
        return {"status": "missing"}
    if snapshot and snapshot.get("status") == "stale":
        return {**job, "status": "stale", "stale_reason": "reviewed_identity_changed"}
    if snapshot and job.get("source_snapshot_digest") != snapshot.get("semantic_digest") and job.get("status") == "completed":
        return {**job, "status": "stale", "stale_reason": "reviewed_identity_changed"}
    if job.get("status") in {"queued", "running"}:
        lock = _load(match_path / LOCK_FILENAME)
        job_key = str(job.get("job_key") or "")
        if lock.get("job_key") != job_key or not _lock_owner_alive(match_path, lock):
            return {
                **job,
                "status": "failed",
                "error": {"message": "Render został przerwany; uruchom go ponownie."},
            }
    if job.get("status") == "completed" and not _completed_output_matches(job, match_path):
        return {**job, "status": "stale", "stale_reason": "reviewed_video_digest_mismatch"}
    return job


def _run(
    path: Path,
    snapshot: dict[str, Any],
    match_doc: dict[str, Any],
    options: dict[str, Any],
    job: dict[str, Any],
    stats_already_current: bool,
) -> None:
    job_key = str(job["job_key"])
    last_progress: dict[str, Any] = {"stage": "queued"}

    def progress(event: dict[str, Any]) -> None:
        nonlocal last_progress
        last_progress = dict(event)
        _log_render(job, path, event)

    try:
        running = {**job, "status": "running", "started_at": _now()}
        write_identity_json_atomic(path / JOB_FILENAME, running)
        if stats_already_current:
            stats = {"reviewed_player_stats.json": _load(path / "reviewed_player_stats.json")}
        else:
            progress({"stage": "build_reviewed_stats"})
            stats = build_reviewed_stats(path, snapshot, match_doc, _load(path / "pitch_config.json"))
        manifest = render_reviewed_video(
            path,
            snapshot,
            match_doc,
            include_minimap=bool(options.get("include_minimap", True)),
            include_ball=bool(options.get("include_ball", True)),
            show_roster_number=bool(options.get("show_roster_number", False)),
            progress_callback=progress,
        )
        output = {
            **running,
            "status": "completed",
            "completed_at": _now(),
            "video_manifest": "reviewed_video_manifest.json",
            "video_digest": manifest["digest"],
            "stats_readiness": "reviewed_stats_readiness.json",
        }
        write_identity_json_atomic(path / JOB_FILENAME, output)
        write_identity_json_atomic(
            path / "reviewed_output_manifest.json",
            {
                "schema_version": "1.1.0",
                "match_id": snapshot.get("match_id"),
                "job_key": job_key,
                "reviewed_identity": {"status": "fresh", "digest": snapshot["semantic_digest"]},
                "video": {
                    "status": "completed",
                    "path": "reviewed_video.mp4",
                    "digest": manifest["digest"],
                    "source_snapshot_digest": snapshot["semantic_digest"],
                },
                "minimap": manifest["minimap"],
                "semantic_checks": manifest["semantic_checks"],
                "stats": {
                    "status": "completed",
                    "source_snapshot_digest": snapshot["semantic_digest"],
                    "players": len(stats["reviewed_player_stats.json"].get("players") or []),
                },
                "stale": False,
                "safety": manifest["safety"],
            },
        )
        progress(
            {
                "stage": "completed",
                "processed_frames": manifest.get("frames"),
                "total_frames": manifest.get("frames"),
                "elapsed_sec": manifest.get("render_duration_sec"),
                "output": "reviewed_video.mp4",
                "codec": "h264",
                "pix_fmt": "yuv420p",
            }
        )
    except Exception as exc:
        _log_render(
            job,
            path,
            {
                **last_progress,
                "stage": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        write_identity_json_atomic(
            path / JOB_FILENAME,
            {
                **job,
                "status": "failed",
                "failed_at": _now(),
                "error": {
                    "message": "Nie udało się wygenerować reviewed video.",
                    "developer_detail": f"{type(exc).__name__}: {exc}",
                },
            },
        )
    finally:
        _active_job_keys.discard(_active_token(path, job_key))
        _release_lock(path / LOCK_FILENAME, job_key)


def _log_render(job: dict[str, Any], match_path: Path, event: dict[str, Any]) -> None:
    """Write compact terminal-only render diagnostics; never mutate the job API."""
    if event.get("stage") in {"performance_profile_stage", "performance_profile_summary"}:
        _log_render_profile(job, match_path, event)
        return
    job_key = str(job.get("job_key") or "")[:8]
    fields = [
        "[reviewed-render]",
        f"match={job.get('match_id') or match_path.name}",
        f"job={job_key}",
        f"stage={event.get('stage') or 'unknown'}",
    ]
    processed = event.get("processed_frames")
    total = event.get("total_frames")
    if processed is not None and total is not None:
        fields.append(f"frames={processed}/{total}")
    if event.get("progress") is not None:
        fields.append(f"progress={float(event['progress']) * 100:.1f}%")
    if event.get("elapsed_sec") is not None:
        fields.append(f"elapsed={float(event['elapsed_sec']):.1f}s")
    if event.get("frames_per_sec") is not None:
        fields.append(f"speed={float(event['frames_per_sec']):.2f}fps")
    if event.get("eta_sec") is not None:
        fields.append(f"eta={float(event['eta_sec']):.1f}s")
    timing_profile = event.get("timing_profile")
    if isinstance(timing_profile, dict):
        for key in (
            "decode_avg_ms",
            "overlay_avg_ms",
            "diagnostics_avg_ms",
            "labels_avg_ms",
            "minimap_avg_ms",
            "hud_avg_ms",
            "writer_avg_ms",
        ):
            if timing_profile.get(key) is not None:
                fields.append(f"{key}={float(timing_profile[key]):.1f}")
    if event.get("output"):
        fields.append(f"output={event['output']}")
    if event.get("codec"):
        fields.append(f"codec={event['codec']}")
    if event.get("pix_fmt"):
        fields.append(f"pix_fmt={event['pix_fmt']}")
    if event.get("error"):
        fields.append(f"error={event['error']}")
    logger.info(" ".join(fields))


def _log_render_profile(
    job: dict[str, Any],
    match_path: Path,
    event: dict[str, Any],
) -> None:
    prefix = (
        f"[reviewed-render-profile] match={job.get('match_id') or match_path.name} "
        f"job={str(job.get('job_key') or '')[:8]}"
    )
    if event.get("stage") == "performance_profile_stage":
        logger.info(
            "%s stage=%s elapsed=%.3fs",
            prefix,
            event.get("profile_stage") or "unknown",
            float(event.get("elapsed_sec") or 0),
        )
        return
    profile = event.get("performance_profile")
    if not isinstance(profile, dict):
        return
    resolution = event.get("resolution") or [0, 0]
    logger.info(
        "%s frames=%s resolution=%sx%s fps=%.2f",
        prefix,
        event.get("frames") or 0,
        resolution[0],
        resolution[1],
        float(event.get("fps") or 0),
    )
    logger.info(
        "%s decode=%.3fs diagnostics=%.3fs labels=%.3fs minimap=%.3fs "
        "hud=%.3fs writer=%.3fs render_frames_wall=%.3fs render_other=%.3fs",
        prefix,
        float(profile.get("decode_sec") or 0),
        float(profile.get("identity_diagnostics_sec") or 0),
        float(profile.get("draw_labels_sec") or 0),
        float(profile.get("draw_minimap_sec") or 0),
        float(profile.get("draw_hud_sec") or 0),
        float(profile.get("writer_write_sec") or 0),
        float(profile.get("render_frames_wall_sec") or 0),
        float(profile.get("render_other_sec") or 0),
    )
    throughput = profile.get("raw_avi_write_mb_per_sec")
    logger.info(
        "%s raw_avi=%.1fMB raw_avi_bytes=%s raw_write_throughput=%s",
        prefix,
        float(profile.get("raw_avi_mb") or 0),
        profile.get("raw_avi_bytes") or 0,
        f"{float(throughput):.2f}MB/s" if throughput is not None else "n/a",
    )
    encode_sec = float(profile.get("encode_mp4_sec") or 0)
    frames = int(event.get("frames") or 0)
    encode_fps = frames / encode_sec if frames and encode_sec > 0 else None
    logger.info(
        "%s encode_mp4=%.3fs encode_fps=%s hash_source=%.3fs hash_output=%.3fs "
        "manifest_build=%.3fs manifest_write=%.3fs total_wall=%.3fs",
        prefix,
        encode_sec,
        f"{encode_fps:.2f}" if encode_fps is not None else "n/a",
        float(profile.get("hash_source_sec") or 0),
        float(profile.get("hash_output_sec") or 0),
        float(profile.get("manifest_build_sec") or 0),
        float(profile.get("manifest_write_sec") or 0),
        float(profile.get("total_wall_sec") or 0),
    )


def _acquire_lock(path: Path, owner: dict[str, Any]) -> bool:
    for _attempt in range(2):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            current = _load(path)
            if current and _lock_owner_alive(path.parent, current):
                return False
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(owner, handle, sort_keys=True)
        return True
    return False


def _release_lock(path: Path, job_key: str) -> None:
    current = _load(path)
    if not current or str(current.get("job_key") or "") == job_key:
        path.unlink(missing_ok=True)


def _pid_alive(value: Any) -> bool:
    try:
        pid = int(value)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _lock_owner_alive(match_path: Path, owner: dict[str, Any]) -> bool:
    try:
        pid = int(owner.get("pid"))
    except (TypeError, ValueError):
        return False
    if pid == os.getpid():
        return _active_token(match_path, str(owner.get("job_key") or "")) in _active_job_keys
    return _pid_alive(pid)


def _reusable_job(job: dict[str, Any], key: str, match_path: Path) -> bool:
    if job.get("job_key") != key:
        return False
    if job.get("status") in {"queued", "running"}:
        lock = _load(match_path / LOCK_FILENAME)
        return lock.get("job_key") == key and _lock_owner_alive(match_path, lock)
    return (
        job.get("status") == "completed"
        and _completed_output_matches(job, match_path)
    )


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_token(path: Path, job_key: str) -> str:
    return f"{path.resolve()}::{job_key}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_output_matches(job: dict[str, Any], match_path: Path) -> bool:
    video = match_path / "reviewed_video.mp4"
    return (
        bool(job.get("video_digest"))
        and video.exists()
        and _sha256(video) == job.get("video_digest")
    )
