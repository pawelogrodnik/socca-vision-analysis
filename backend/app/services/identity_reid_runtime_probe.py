from __future__ import annotations

"""Small, repeatable local probe for the preferred person-ReID runtime."""

import hashlib
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_same_match_reid import (
    PersonReIdEmbedder,
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
    embedder, load_status = load_default_embedder(models_dir)
    selected_runtime = load_status.get("selected_runtime")
    inference = _probe_inference(embedder, crop_path)
    ready = embedder is not None and inference["status"] == "passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "apple_silicon_reid_runtime_probe_read_only",
        "status": (
            "PREFERRED_REID_RUNTIME_AVAILABLE"
            if ready
            else "PREFERRED_REID_RUNTIME_BLOCKED"
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
            "fallback_used": False,
        },
        "inference": inference,
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


def _probe_inference(
    embedder: PersonReIdEmbedder | None,
    crop_path: Path | None,
) -> dict[str, Any]:
    if embedder is None:
        return {
            "status": "not_run_preferred_runtime_unavailable",
            "crop_path": str(crop_path) if crop_path else None,
        }
    if crop_path is None or not crop_path.exists():
        return {
            "status": "not_run_real_crop_missing",
            "crop_path": str(crop_path) if crop_path else None,
        }
    image = cv2.imread(str(crop_path))
    if image is None or image.size == 0:
        return {
            "status": "not_run_real_crop_invalid",
            "crop_path": str(crop_path),
        }
    try:
        first = np.asarray(embedder.embed(image), dtype=np.float32).reshape(-1)
        second = np.asarray(embedder.embed(image), dtype=np.float32).reshape(-1)
    except Exception as exc:
        return {
            "status": "failed",
            "crop_path": str(crop_path),
            "error": str(exc),
        }
    norm = float(np.linalg.norm(first))
    finite = bool(np.all(np.isfinite(first)))
    deterministic = bool(np.array_equal(first, second))
    normalized = math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4)
    non_zero = norm > 1e-12
    passed = finite and non_zero and normalized and deterministic
    return {
        "status": "passed" if passed else "failed_validation",
        "crop_path": str(crop_path),
        "crop_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        "embedding_dimension": int(first.size),
        "finite_values": finite,
        "non_zero_norm": non_zero,
        "l2_normalized": normalized,
        "embedding_norm": round(norm, 8),
        "deterministic_repeated_inference": deterministic,
    }
