from __future__ import annotations

"""Small, repeatable local probe for the preferred person-ReID runtime."""

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_same_match_reid import (
    collect_reid_runtime_capabilities,
    load_default_embedder,
)


SCHEMA_VERSION = "0.1.0"


def build_reid_runtime_probe(
    *,
    models_dir: Path,
    crop_path: Path | None,
) -> dict[str, Any]:
    """Probe local model runtimes with one existing crop, without downloads."""

    capabilities = collect_reid_runtime_capabilities(models_dir)
    image = _load_real_crop(crop_path)
    embedder, load_status = load_default_embedder(
        models_dir,
        smoke_crop_bgr=image,
    )
    selected_runtime = load_status.get("selected_runtime")
    attempts = load_status.get("runtime_attempts") or []
    ready = embedder is not None and selected_runtime is not None
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "apple_silicon_reid_runtime_probe_read_only",
        "status": (
            _probe_status(capabilities, attempts, ready)
        ),
        "capabilities": capabilities,
        "model": {
            "model_name": load_status.get("model_name"),
            "model_version": load_status.get("model_version"),
            "model_files_present": capabilities["model_files_present"],
            "attempted_runtimes": load_status.get("attempted_runtimes")
            or [],
            "selected_runtime": selected_runtime,
            "load_errors": load_status.get("load_errors") or [],
            "runtime_attempts": attempts,
            "repeatability_tolerance": load_status.get(
                "repeatability_tolerance"
            ) or {},
            "fallback_used": False,
        },
        "inference": (
            next(
                (
                    row
                    for row in attempts
                    if row.get("runtime") == selected_runtime
                ),
                None,
            )
        ),
        "safety": {
            "reran_yolo": False,
            "reran_tracking": False,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "opens_operator_session": False,
            "writes_operator_telemetry": False,
            "download_performed": False,
            "installation_performed": False,
        },
    }


def _load_real_crop(crop_path: Path | None) -> np.ndarray | None:
    if crop_path is None or not crop_path.exists():
        return None
    image = cv2.imread(str(crop_path))
    if image is None or image.size == 0:
        return None
    return image


def _probe_status(
    capabilities: dict[str, Any],
    attempts: list[dict[str, Any]],
    ready: bool,
) -> str:
    if not capabilities.get("model_files_present"):
        return "MODEL_FILES_MISSING"
    if ready:
        return "PREFERRED_REID_RUNTIME_AVAILABLE"
    if not capabilities.get("openvino_import_available"):
        return "OPENVINO_PACKAGE_MISSING"
    if attempts and attempts[0].get("error_type") == "load_error":
        if len(attempts) == 1:
            return "OPENCV_DNN_LOAD_FAILED"
    if attempts and attempts[0].get("error_type") == "inference_error":
        if len(attempts) == 1:
            return "OPENCV_DNN_INFERENCE_FAILED"
    if len(attempts) > 1 and attempts[1].get("error_type") == "load_error":
        return "OPENVINO_LOAD_FAILED"
    if len(attempts) > 1 and attempts[1].get("error_type") == "inference_error":
        return "OPENVINO_INFERENCE_FAILED"
    return "PREFERRED_REID_RUNTIME_BLOCKED"
