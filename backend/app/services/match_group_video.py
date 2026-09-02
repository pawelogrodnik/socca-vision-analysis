from __future__ import annotations

"""Derived, post-publication logical-match video generation.

This service deliberately consumes only immutable video copies from published
physical generations.  It never opens MATCHES_DIR, Review state, or CV data.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_groups import MATCH_GROUPS_DIR, PUBLISHED_MATCHES_DIR, MatchGroupError, get_match_group, validate_match_group
from app.services.published_video import PUBLISHED_VIDEO_ARTIFACT, load_published_video, sha256_file


COMBINED_VIDEO_FILENAME = "combined_match_video.mp4"
VIDEO_MANIFEST_FILENAME = "video_manifest.json"
VIDEO_STATUS_FILENAME = "video_status.json"
VIDEO_SCHEMA_VERSION = "1.0.0"
VIDEO_POLICY_VERSION = "logical-video:v1-h264-yuv420p-no-audio;normalize-25fps-max-resolution"
VIDEO_DURATION_TOLERANCE_SEC = 0.25
_submission_lock = threading.Lock()
_active: set[str] = set()


class MatchGroupVideoError(MatchGroupError):
    pass


def get_match_group_video_status(group_id: str) -> dict[str, Any]:
    group = get_match_group(group_id)
    group_dir = MATCH_GROUPS_DIR / str(group["group_id"])
    validation = validate_match_group(group_id)
    manifest = _load(group_dir / VIDEO_MANIFEST_FILENAME)
    status = _load(group_dir / VIDEO_STATUS_FILENAME)
    output = group_dir / COMBINED_VIDEO_FILENAME
    if validation.get("status") != "compatible":
        return _state("stale", group, reason="match_group_stale", manifest=manifest, output=output)
    try:
        inputs = _validated_video_inputs(group)
    except MatchGroupVideoError as error:
        if manifest:
            return _state(
                "stale",
                group,
                reason=error.code,
                manifest=manifest,
                output=output,
            )
        return _state("unavailable_source_video", group, reason=error.code, manifest=manifest, output=output)
    expected = _input_digest(inputs, group)
    if manifest:
        if manifest.get("input_semantic_digest") != expected or not _valid_output(output, manifest):
            return _state("stale", group, reason="source_video_generation_changed", manifest=manifest, output=output)
        return _state("ready", group, manifest=manifest, output=output)
    if status.get("status") in {"generating", "failed"}:
        return {**status, "group_id": group["group_id"], "artifact_url": _artifact_url(group["group_id"]) if output.is_file() else None}
    return _state("not_generated", group)


def submit_match_group_video_generation(group_id: str) -> dict[str, Any]:
    group = get_match_group(group_id)
    status = get_match_group_video_status(group_id)
    if status.get("status") == "generating":
        return status
    if validate_match_group(group_id).get("status") != "compatible":
        raise MatchGroupVideoError("match_group_stale", "Logical match sources must be current before generating video.")
    _validated_video_inputs(group)
    with _submission_lock:
        if group_id in _active:
            return get_match_group_video_status(group_id)
        _active.add(group_id)
        queued = {
            "schema_version": VIDEO_SCHEMA_VERSION,
            "group_id": group_id,
            "status": "generating",
            "started_at": _now(),
            "source_count": len(group.get("members") or []),
        }
        _write(MATCH_GROUPS_DIR / group_id / VIDEO_STATUS_FILENAME, queued)
        threading.Thread(target=_background_generate, args=(group_id,), daemon=True).start()
        return queued


def generate_match_group_video(group_id: str) -> dict[str, Any]:
    """Synchronously generate one video; used by the background worker and tests."""

    group = get_match_group(group_id)
    if validate_match_group(group_id).get("status") != "compatible":
        raise MatchGroupVideoError("match_group_stale", "Logical match sources must be current before generating video.")
    inputs = _validated_video_inputs(group)
    input_digest = _input_digest(inputs, group)
    group_dir = MATCH_GROUPS_DIR / group_id
    target = group_dir / COMBINED_VIDEO_FILENAME
    prior_manifest = _load(group_dir / VIDEO_MANIFEST_FILENAME)
    started = time.monotonic()
    stage = Path(tempfile.mkdtemp(prefix="generation-", dir=_staging_parent(group_dir)))
    try:
        probes = [_probe(item["path"]) for item in inputs]
        _validate_source_media(probes, inputs)
        output = stage / COMBINED_VIDEO_FILENAME
        mode = "stream_copy" if _stream_copy_compatible(probes) else "normalized"
        if mode == "stream_copy":
            _concat([item["path"] for item in inputs], output, copy_streams=True)
        else:
            normalized = [_normalize(item["path"], stage, index, probes) for index, item in enumerate(inputs)]
            _concat(normalized, output, copy_streams=True)
        output_probe = _probe(output)
        timeline_span = float(group["timing"]["timeline_span_sec"])
        duration = float(output_probe["duration_sec"])
        if abs(duration - timeline_span) > VIDEO_DURATION_TOLERANCE_SEC:
            raise MatchGroupVideoError("output_duration_mismatch", "Combined video duration differs from the logical timeline.")
        if (
            output_probe.get("codec") != "h264"
            or output_probe.get("pix_fmt") != "yuv420p"
            or output_probe.get("audio")
        ):
            raise MatchGroupVideoError("output_video_invalid", "Combined video does not meet the H.264/yuv420p output contract.")
        if output.stat().st_size <= 0:
            raise MatchGroupVideoError("output_video_invalid", "Combined video is empty.")
        output_digest = sha256_file(output)
        video_manifest = {
            "schema_version": VIDEO_SCHEMA_VERSION,
            "group_id": group_id,
            "generation_status": "ready",
            "input_semantic_digest": input_digest,
            "policy_version": VIDEO_POLICY_VERSION,
            "logical_timeline": dict(group["timing"]),
            "members": [{key: value for key, value in item.items() if key != "path"} for item in inputs],
            "output": {
                "artifact": COMBINED_VIDEO_FILENAME,
                "semantic_digest": output_digest,
                "duration_sec": duration,
                "codec": output_probe["codec"],
                "width": output_probe["width"],
                "height": output_probe["height"],
                "fps": output_probe["fps"],
                "file_size_bytes": output.stat().st_size,
            },
            "observability": {
                "started_at": _now(),
                "completed_at": _now(),
                "elapsed_sec": round(time.monotonic() - started, 3),
                "source_count": len(inputs),
                "source_total_bytes": sum(item["file_size_bytes"] for item in inputs),
                "output_bytes": output.stat().st_size,
                "generation_mode": mode,
            },
        }
        _write(stage / VIDEO_MANIFEST_FILENAME, video_manifest)
        # Both bytes have been probe/digest validated before either replaces a
        # coherent artifact.  A failed render never touches the current pair.
        os.replace(output, target)
        os.replace(stage / VIDEO_MANIFEST_FILENAME, group_dir / VIDEO_MANIFEST_FILENAME)
        (group_dir / VIDEO_STATUS_FILENAME).unlink(missing_ok=True)
        return _state("ready", group, manifest=video_manifest, output=target)
    except Exception as error:
        _write(group_dir / VIDEO_STATUS_FILENAME, {
            "schema_version": VIDEO_SCHEMA_VERSION,
            "group_id": group_id,
            "status": "failed",
            "failed_at": _now(),
            "reason": error.code if isinstance(error, MatchGroupVideoError) else "video_generation_failed",
            "detail": str(error),
            "previous_coherent_video": bool(prior_manifest and target.is_file()),
        })
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def combined_video_path(group_id: str) -> Path:
    group = get_match_group(group_id)
    path = MATCH_GROUPS_DIR / str(group["group_id"]) / COMBINED_VIDEO_FILENAME
    if not path.is_file():
        raise FileNotFoundError(group_id)
    return path


def _background_generate(group_id: str) -> None:
    try:
        generate_match_group_video(group_id)
    except Exception:
        pass
    finally:
        with _submission_lock:
            _active.discard(group_id)


def _validated_video_inputs(group: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for member in group.get("members") or []:
        published_id = str(member.get("published_id") or "")
        source_dir = PUBLISHED_MATCHES_DIR / published_id
        video = load_published_video(source_dir, expected_public_report_digest=str(member.get("public_report_semantic_digest") or ""))
        if video is None:
            raise MatchGroupVideoError("unavailable_source_video", "A selected publication has no proven final reviewed video.", member=published_id)
        path = source_dir / PUBLISHED_VIDEO_ARTIFACT
        logical_start = float(member.get("logical_start_sec") or 0)
        logical_end = float(member.get("logical_end_sec") or 0)
        duration = float(video.get("duration_sec") or 0)
        if abs(duration - (logical_end - logical_start)) > VIDEO_DURATION_TOLERANCE_SEC:
            raise MatchGroupVideoError("source_video_duration_mismatch", "Published final video duration does not match its logical source duration.", member=published_id)
        items.append({
            "sequence_index": int(member.get("sequence_index") or 0),
            "published_id": published_id,
            "source_match_id": str(member.get("source_match_id") or ""),
            "logical_start_sec": logical_start,
            "logical_end_sec": logical_end,
            "source_video": {key: video[key] for key in ("semantic_digest", "duration_sec", "codec", "width", "height", "fps", "pix_fmt")},
            "file_size_bytes": path.stat().st_size,
            "path": path,
        })
    return items


def _input_digest(inputs: list[dict[str, Any]], group: dict[str, Any]) -> str:
    return canonical_json_sha256({
        "policy_version": VIDEO_POLICY_VERSION,
        "members": [{key: value for key, value in item.items() if key not in {"path", "file_size_bytes"}} for item in inputs],
        "logical_timeline": group.get("timing"),
    })


def _probe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,pix_fmt,width,height,r_frame_rate,codec_type", "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        )
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
    return all(
        probe["codec"] == "h264" and probe["pix_fmt"] == "yuv420p" and not probe["audio"]
        and all(probe[key] == first[key] for key in ("codec", "pix_fmt", "width", "height", "fps", "audio"))
        for probe in probes
    )


def _concat(paths: list[Path], output: Path, *, copy_streams: bool) -> None:
    listing = output.parent / "concat.txt"
    listing.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths), encoding="utf-8")
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    command.extend(["-c", "copy"] if copy_streams else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"])
    command.append(str(output))
    _run_ffmpeg(command)


def _normalize(path: Path, stage: Path, index: int, probes: list[dict[str, Any]]) -> Path:
    target_width = max(int(probe["width"]) for probe in probes)
    target_height = max(int(probe["height"]) for probe in probes)
    target_width += target_width % 2
    target_height += target_height % 2
    normalized = stage / f"normalized-{index}.mp4"
    vf = f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2"
    _run_ffmpeg(["ffmpeg", "-y", "-i", str(path), "-vf", vf, "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(normalized)])
    return normalized


def _run_ffmpeg(command: list[str]) -> None:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise MatchGroupVideoError("video_tool_missing", "ffmpeg is required to generate a combined video.") from error
    if result.returncode != 0:
        tail = (result.stderr or "").strip()[-1000:]
        raise MatchGroupVideoError("video_generation_failed", f"ffmpeg failed: {tail}")


def _valid_output(path: Path, manifest: dict[str, Any]) -> bool:
    output = manifest.get("output") if isinstance(manifest.get("output"), dict) else {}
    return path.is_file() and path.stat().st_size > 0 and sha256_file(path) == output.get("semantic_digest")


def _state(status: str, group: dict[str, Any], *, reason: str | None = None, manifest: dict[str, Any] | None = None, output: Path | None = None) -> dict[str, Any]:
    return {"group_id": group["group_id"], "status": status, "reason": reason, "manifest": manifest or None, "artifact_url": _artifact_url(group["group_id"]) if output and output.is_file() else None}


def _artifact_url(group_id: str) -> str:
    return f"/api/published/match-groups/{group_id}/video/file"


def _staging_parent(group_dir: Path) -> Path:
    parent = group_dir / ".video-staging"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def _load(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _write(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
