from __future__ import annotations

import platform
import sys
from typing import Any


AUTO_DEVICE_VALUES = {"", "auto", "default", "none", "null"}


def normalize_yolo_device(device: str | None) -> str | None:
    """Normalize user-facing device aliases to Ultralytics-compatible values."""
    raw = str(device or "").strip()
    lowered = raw.lower()
    if lowered in AUTO_DEVICE_VALUES:
        return None
    if lowered in {"cuda", "gpu", "nvidia"}:
        return "0"
    if lowered.startswith("cuda:") and lowered[5:].isdigit():
        return lowered[5:]
    return raw


def requested_device_label(device: str | None) -> str:
    raw = str(device or "").strip()
    return "auto" if raw.lower() in AUTO_DEVICE_VALUES else raw


def collect_runtime_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "schema_version": "0.1.0",
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "platform": platform.platform(),
        },
        "torch": {
            "available": False,
            "version": None,
            "cuda_available": False,
            "cuda_device_count": 0,
            "cuda_device_names": [],
            "mps_available": False,
            "mps_built": False,
        },
        "recommended_yolo_devices": ["cpu"],
    }
    try:
        import torch
    except Exception as exc:  # pragma: no cover - environment dependent
        info["torch"]["import_error"] = str(exc)
        return info

    cuda_available = bool(torch.cuda.is_available())
    cuda_device_count = int(torch.cuda.device_count()) if cuda_available else 0
    cuda_device_names: list[str] = []
    if cuda_available:
        for index in range(cuda_device_count):
            try:
                cuda_device_names.append(str(torch.cuda.get_device_name(index)))
            except Exception:
                cuda_device_names.append(f"cuda:{index}")

    mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
    mps_available = bool(mps_backend and mps_backend.is_available())
    mps_built = bool(mps_backend and mps_backend.is_built())

    recommended: list[str] = []
    if cuda_available:
        recommended.append("0")
    if mps_available:
        recommended.append("mps")
    recommended.append("cpu")

    info["torch"] = {
        "available": True,
        "version": str(torch.__version__),
        "cuda_available": cuda_available,
        "cuda_device_count": cuda_device_count,
        "cuda_device_names": cuda_device_names,
        "mps_available": mps_available,
        "mps_built": mps_built,
    }
    info["recommended_yolo_devices"] = recommended
    return info


def build_performance_report(
    *,
    label: str,
    requested_device: str | None,
    normalized_device: str | None,
    elapsed_wall_sec: float,
    analysis_report: dict[str, Any],
    runtime_info: dict[str, Any],
) -> dict[str, Any]:
    video = analysis_report.get("video") if isinstance(analysis_report.get("video"), dict) else {}
    parameters = analysis_report.get("parameters") if isinstance(analysis_report.get("parameters"), dict) else {}
    frames_processed = int(analysis_report.get("frames_processed") or 0)
    fps = float(video.get("fps") or 0.0)
    duration_sec = float(video.get("duration_sec") or 0.0)
    max_seconds = float(parameters.get("max_seconds") or 0.0)
    frame_stride = max(1, int(parameters.get("frame_stride") or 1))
    analyzed_video_sec = duration_sec if max_seconds <= 0 else min(duration_sec, max_seconds)
    if frames_processed > 0 and fps > 0:
        analyzed_video_sec = min(analyzed_video_sec, frames_processed * frame_stride / fps)
    processed_fps = frames_processed / elapsed_wall_sec if elapsed_wall_sec > 0 else 0.0
    video_seconds_per_wall_second = analyzed_video_sec / elapsed_wall_sec if elapsed_wall_sec > 0 else 0.0
    estimated_40_min_wall_min = (
        (40.0 * 60.0) / video_seconds_per_wall_second / 60.0
        if video_seconds_per_wall_second > 0
        else None
    )
    return {
        "schema_version": "0.1.0",
        "label": label,
        "runtime": runtime_info,
        "requested_device": requested_device_label(requested_device),
        "normalized_yolo_device": normalized_device or "auto",
        "elapsed_wall_sec": round(elapsed_wall_sec, 3),
        "throughput": {
            "processed_frames": frames_processed,
            "processed_frames_per_wall_sec": round(processed_fps, 3),
            "analyzed_video_sec": round(analyzed_video_sec, 3),
            "video_seconds_per_wall_second": round(video_seconds_per_wall_second, 3),
            "estimated_40_min_wall_min": round(estimated_40_min_wall_min, 2)
            if estimated_40_min_wall_min is not None
            else None,
        },
        "analysis_summary": {
            "status": analysis_report.get("status"),
            "analysis_type": analysis_report.get("analysis_type"),
            "run_id": analysis_report.get("run_id"),
            "frames_processed": frames_processed,
            "tracks_count": analysis_report.get("tracks_count"),
            "stable_players_count": analysis_report.get("stable_players_count"),
            "warnings": analysis_report.get("warnings") or [],
        },
        "parameters": parameters,
        "artifacts": {
            "analysis_report": "analysis_report.json",
            "performance_report": "performance_report.json",
            "run_directory": analysis_report.get("run_directory"),
            "run_manifest": analysis_report.get("run_manifest"),
        },
    }
