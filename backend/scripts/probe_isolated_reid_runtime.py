#!/usr/bin/env python3
"""Run a self-contained OpenVINO ReID probe in the dedicated probe venv."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import time
import traceback
from typing import Any, Callable
import xml.etree.ElementTree as ElementTree

import cv2
import numpy as np


def main() -> int:
    arguments = _arguments()
    output = Path(arguments.output)
    manifest_path = Path(arguments.manifest_output)
    model_path = Path(arguments.model_xml)
    weights_path = Path(arguments.model_bin)
    crop_path = Path(arguments.crop)

    manifest = _environment_manifest()
    _write_json(manifest_path, manifest)
    report = _probe(model_path, weights_path, crop_path, manifest_path)
    _write_json(output, report)
    return int(report["exit_code"])


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-xml", required=True)
    parser.add_argument("--model-bin", required=True)
    parser.add_argument("--crop", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest-output", required=True)
    return parser.parse_args()


def _probe(
    model_path: Path,
    weights_path: Path,
    crop_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    ov = _load_openvino()
    steps: list[dict[str, Any]] = []
    model: Any | None = None
    compiled: Any | None = None
    core: Any | None = None

    core = _step(steps, "create_core", ov.Core)
    if core is not None:
        devices = _step(steps, "available_devices", lambda: core.available_devices)
    else:
        devices = []
    metadata = _model_metadata(model_path, weights_path)
    model = _step(
        steps,
        "read_model_explicit_weights",
        lambda: core.read_model(str(model_path), str(weights_path)),
    ) if core is not None else None
    if model is None and core is not None:
        _step(
            steps,
            "read_model_implicit_weights",
            lambda: core.read_model(str(model_path)),
        )
    if model is not None and core is not None:
        compiled = _step(
            steps,
            "compile_model_cpu",
            lambda: core.compile_model(model, device_name="CPU"),
        )
    model_io = _model_io(model)
    if compiled is not None:
        input_tensor = _synthetic_input(model_io)
        _step(
            steps,
            "synthetic_inference",
            lambda: _infer(compiled, input_tensor, model_io),
        )
        image = _step(steps, "read_real_h1_crop", lambda: _read_crop(crop_path))
        if image is not None:
            first = _step(
                steps,
                "real_crop_inference_first",
                lambda: _infer(compiled, _preprocess(image, model_io), model_io),
            )
            second = _step(
                steps,
                "real_crop_inference_second",
                lambda: _infer(compiled, _preprocess(image, model_io), model_io),
            )
            _embedding_contract_step(steps, first, second)

    compile_passed = _passed(steps, "compile_model_cpu")
    contract_passed = _passed(steps, "embedding_contract")
    return {
        "schema_version": "0.1.0",
        "mode": "isolated_openvino_reid_runtime_probe_read_only",
        "status": (
            "PREFERRED_REID_RUNTIME_AVAILABLE"
            if compile_passed and contract_passed
            else "PREFERRED_REID_RUNTIME_BLOCKED"
        ),
        "exit_code": 0 if compile_passed and contract_passed else 3,
        "model": metadata,
        "environment_manifest_path": str(manifest_path),
        "openvino_version": str(getattr(ov, "__version__", "unknown")),
        "available_devices": devices or [],
        "model_io": model_io,
        "steps": steps,
        "safety": {
            "reran_yolo": False,
            "reran_tracking": False,
            "automatic_assignments": 0,
            "production_applies": 0,
            "download_performed": False,
            "writes_operator_telemetry": False,
        },
    }


def _step(
    steps: list[dict[str, Any]],
    name: str,
    operation: Callable[[], Any],
) -> Any | None:
    started = time.monotonic()
    try:
        value = operation()
    except Exception as error:  # Runtime diagnostics must preserve detail.
        steps.append({
            "step": name,
            "passed": False,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        })
        return None
    steps.append({
        "step": name,
        "passed": True,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "error_type": None,
        "error_message": None,
    })
    return value


def _environment_manifest() -> dict[str, Any]:
    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "python_architecture": platform.machine(),
        "wheel_platform": sysconfig.get_platform(),
        "pip_freeze": sorted(
            line for line in freeze.stdout.splitlines() if line.strip()
        ),
        "openvino_version": _installed_version("openvino"),
        "numpy_version": np.__version__,
        "opencv_version": cv2.__version__,
    }


def _load_openvino() -> Any:
    import openvino

    return openvino


def _installed_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _model_metadata(model_path: Path, weights_path: Path) -> dict[str, Any]:
    root = ElementTree.parse(model_path).getroot()
    return {
        "xml_path": str(model_path.resolve()),
        "bin_path": str(weights_path.resolve()),
        "xml_exists": model_path.is_file(),
        "bin_exists": weights_path.is_file(),
        "xml_sha256": _sha256(model_path),
        "bin_sha256": _sha256(weights_path),
        "ir_version": root.attrib.get("version"),
        "layer_versions": sorted({
            str(layer.attrib.get("version"))
            for layer in root.findall("./layers/layer")
            if layer.attrib.get("version")
        }),
    }


def _model_io(model: Any | None) -> dict[str, Any]:
    if model is None:
        return {"inputs": [], "outputs": []}
    return {
        "inputs": [
            {
                "name": item.get_any_name(),
                "shape": list(item.shape),
                "element_type": str(item.element_type),
            }
            for item in model.inputs
        ],
        "outputs": [
            {
                "name": item.get_any_name(),
                "shape": list(item.shape),
                "element_type": str(item.element_type),
            }
            for item in model.outputs
        ],
    }


def _synthetic_input(model_io: dict[str, Any]) -> np.ndarray:
    shape = tuple(int(value) for value in model_io["inputs"][0]["shape"])
    return np.zeros(shape, dtype=np.float32)


def _read_crop(crop_path: Path) -> np.ndarray:
    image = cv2.imread(str(crop_path))
    if image is None or image.size == 0:
        raise ValueError(f"Unreadable crop: {crop_path}")
    return image


def _preprocess(image: np.ndarray, model_io: dict[str, Any]) -> np.ndarray:
    _, channels, height, width = model_io["inputs"][0]["shape"]
    if channels != 3:
        raise ValueError(f"Expected 3 channels, got {channels}")
    resized = cv2.resize(image, (int(width), int(height)))
    return resized.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)


def _infer(
    compiled: Any,
    tensor: np.ndarray,
    model_io: dict[str, Any],
) -> np.ndarray:
    result = compiled({model_io["inputs"][0]["name"]: tensor})
    return np.asarray(result[compiled.output(0)]).reshape(-1).astype(np.float32)


def _embedding_contract_step(
    steps: list[dict[str, Any]],
    first: np.ndarray | None,
    second: np.ndarray | None,
) -> None:
    started = time.monotonic()
    try:
        if first is None or second is None:
            raise ValueError("Real crop inference did not return embeddings")
        norm = float(np.linalg.norm(first))
        normalized = first / max(norm, 1e-12)
        if first.size != 256:
            raise ValueError(f"Expected embedding dimension 256, got {first.size}")
        if not np.isfinite(first).all() or norm <= 0.0:
            raise ValueError("Embedding is non-finite or has zero norm")
        if not np.allclose(first, second, rtol=1e-5, atol=1e-6):
            raise ValueError("Repeat inference is outside rtol=1e-5, atol=1e-6")
        steps.append({
            "step": "embedding_contract",
            "passed": True,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "embedding_dimension": int(first.size),
            "finite": True,
            "norm": norm,
            "normalized_norm": float(np.linalg.norm(normalized)),
            "repeatability_passed": True,
            "error_type": None,
            "error_message": None,
        })
    except Exception as error:
        steps.append({
            "step": "embedding_contract",
            "passed": False,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        })


def _passed(steps: list[dict[str, Any]], name: str) -> bool:
    return next(
        (bool(step["passed"]) for step in steps if step["step"] == name),
        False,
    )


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
