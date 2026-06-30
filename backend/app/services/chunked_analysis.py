from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_analysis_chunk_manifest(
    *,
    video_metadata: dict[str, Any],
    payload: dict[str, Any],
    job_id: str | None = None,
) -> dict[str, Any]:
    fps = float(video_metadata.get("fps") or 0.0)
    duration_sec = float(video_metadata.get("duration_sec") or 0.0)
    frame_count = int(video_metadata.get("frame_count") or 0)
    max_seconds = float(payload.get("max_seconds") or 0.0)
    analysis_duration = duration_sec if max_seconds <= 0 else min(duration_sec, max_seconds)
    if analysis_duration <= 0 and fps > 0 and frame_count > 0:
        analysis_duration = frame_count / fps
    chunk_duration_sec = max(10.0, float(payload.get("chunk_duration_sec") or 120.0))
    overlap_sec = max(0.0, min(float(payload.get("chunk_overlap_sec") or 2.0), chunk_duration_sec / 2.0))
    chunks = []
    start = 0.0
    index = 1
    while start < analysis_duration - 1e-6:
        end = min(analysis_duration, start + chunk_duration_sec)
        chunks.append(
            {
                "chunk_id": f"chunk-{index:04d}",
                "index": index,
                "start_time_sec": round(start, 3),
                "end_time_sec": round(end, 3),
                "duration_sec": round(max(0.0, end - start), 3),
                "start_frame": int(round(start * fps)) if fps > 0 else None,
                "end_frame": int(round(end * fps)) if fps > 0 else None,
                "status": "planned",
                "artifacts": {},
            }
        )
        if end >= analysis_duration:
            break
        start = max(0.0, end - overlap_sec)
        index += 1

    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "job_id": job_id,
        "status": "planned",
        "execution_mode": "chunked_foundation_single_pass",
        "note": "Chunk boundaries are planned and persisted. Current runner still executes the existing single-pass analyzer; per-chunk CV and merge will be implemented next.",
        "video": {
            "fps": fps,
            "frame_count": frame_count,
            "duration_sec": round(duration_sec, 3),
        },
        "parameters": {
            "max_seconds": max_seconds,
            "analysis_duration_sec": round(analysis_duration, 3),
            "chunk_duration_sec": chunk_duration_sec,
            "chunk_overlap_sec": overlap_sec,
            "frame_stride": payload.get("frame_stride"),
        },
        "summary": {
            "chunks": len(chunks),
            "analysis_duration_sec": round(analysis_duration, 3),
            "chunk_duration_sec": chunk_duration_sec,
            "chunk_overlap_sec": overlap_sec,
        },
        "chunks": chunks,
    }


def write_analysis_chunk_manifest(match_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    (match_path / "analysis_chunk_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return manifest


def mark_chunk_manifest_single_pass_completed(match_path: Path, report: dict[str, Any]) -> dict[str, Any] | None:
    path = match_path / "analysis_chunk_manifest.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        return None
    manifest["status"] = "single_pass_completed"
    manifest["updated_at"] = now_iso()
    manifest["single_pass_run"] = {
        "run_id": report.get("run_id"),
        "status": report.get("status"),
        "frames_processed": report.get("frames_processed"),
        "run_directory": report.get("run_directory"),
        "run_manifest": report.get("run_manifest"),
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
