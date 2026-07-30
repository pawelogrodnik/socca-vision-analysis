from __future__ import annotations

"""Validated Rosetta/x86 OpenVINO adapter for the preferred ReID model."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any

import cv2
import numpy as np


MODEL_NAME = "person-reidentification-retail-0288"
WORKER_SCHEMA_VERSION = "1.0.0"
PREPROCESSING_CONTRACT_VERSION = "bgr-cv2-linear-nchw-f32-0-255-128x256-v1"
EMBEDDING_CONTRACT_VERSION = "l2-f32-256-v1"
RUNTIME_NAME = "openvino_rosetta_x86_cpu"
EXPECTED_INPUT_SHAPE = (1, 3, 256, 128)
EXPECTED_EMBEDDING_DIMENSION = 256
MINIMUM_EMBEDDING_NORM = 1e-8


class RosettaReIdRuntimeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class RosettaRuntimeCandidate:
    python_executable: Path
    worker: Path
    model_xml: Path
    model_bin: Path
    runtime_directory: Path

    @property
    def present(self) -> bool:
        return all(
            path.is_file()
            for path in (
                self.python_executable,
                self.worker,
                self.model_xml,
                self.model_bin,
            )
        )

    def manifest(self) -> dict[str, Any]:
        paths = {
            "python_executable": self.python_executable,
            "worker": self.worker,
            "model_xml": self.model_xml,
            "model_bin": self.model_bin,
        }
        return {
            "runtime_candidate_present": self.present,
            "runtime_directory": str(self.runtime_directory),
            "paths": {key: str(value) for key, value in paths.items()},
            "path_presence": {
                key: value.is_file() for key, value in paths.items()
            },
        }


def preprocess_person_reid_0288(crop_bgr: np.ndarray) -> np.ndarray:
    """Apply the single explicit preprocessing contract in the native process."""

    if crop_bgr.ndim != 3 or crop_bgr.shape[2] != 3 or crop_bgr.size == 0:
        raise ValueError("INVALID_REID_CROP")
    resized = cv2.resize(
        crop_bgr,
        (128, 256),
        interpolation=cv2.INTER_LINEAR,
    )
    tensor = np.transpose(
        resized.astype(np.float32, copy=False),
        (2, 0, 1),
    )[None, ...]
    if tensor.shape != EXPECTED_INPUT_SHAPE:
        raise ValueError("INVALID_PREPROCESSED_REID_SHAPE")
    return np.ascontiguousarray(tensor, dtype=np.float32)


def discover_rosetta_openvino_runtime(
    models_dir: Path,
    *,
    runtime_directory: Path | None = None,
) -> RosettaRuntimeCandidate:
    runtime_root = runtime_directory or (
        models_dir.parent
        / ".reid-runtime-lab"
        / "ov-2026.1-rosetta-x86"
    )
    model_root = models_dir / MODEL_NAME / "FP16"
    return RosettaRuntimeCandidate(
        python_executable=runtime_root / "bin" / "python",
        worker=Path(__file__).resolve().parents[2]
        / "scripts"
        / "embed_openvino_rosetta.py",
        model_xml=model_root / f"{MODEL_NAME}.xml",
        model_bin=model_root / f"{MODEL_NAME}.bin",
        runtime_directory=runtime_root,
    )


def probe_rosetta_openvino_runtime(
    candidate: RosettaRuntimeCandidate,
    *,
    real_crop_bgr: np.ndarray,
    timeout_seconds: float = 60.0,
    diagnostics_directory: Path | None = None,
) -> dict[str, Any]:
    """Prove architecture, compile, synthetic inference and real repeatability."""

    base = {
        "schema_version": WORKER_SCHEMA_VERSION,
        "mode": "rosetta_openvino_runtime_probe",
        "candidate": candidate.manifest(),
        "status": "ROSETTA_REID_RUNTIME_BLOCKED",
        "available": False,
    }
    if not candidate.present:
        return {
            **base,
            "reason": "runtime_candidate_incomplete",
            "error_stage": "discovery",
        }
    try:
        tensor = preprocess_person_reid_0288(real_crop_bgr)
        response, outputs, process = _run_worker(
            candidate,
            tensors=[tensor, tensor.copy()],
            operation="runtime_probe",
            timeout_seconds=timeout_seconds,
            diagnostics_directory=diagnostics_directory,
        )
        _validate_worker_response(candidate, response, outputs, expected_count=2)
        if not np.allclose(outputs[0], outputs[1], rtol=1e-5, atol=1e-6):
            raise RosettaReIdRuntimeError(
                "embedding_repeatability_failed",
                "Real crop embeddings are not repeatable",
                diagnostics={"response": response},
            )
        runtime = response["runtime"]
        model = response["model"]
        return {
            **base,
            "status": "PREFERRED_REID_RUNTIME_AVAILABLE",
            "available": True,
            "runtime_candidate_present": True,
            "runtime": runtime,
            "model": model,
            "result": response.get("result") or {},
            "timings_ms": response.get("timings_ms") or {},
            "worker": {
                "exit_code": process.returncode,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "timeout": False,
            },
            "runtime_manifest_digest": _digest(
                {
                    "candidate": candidate.manifest(),
                    "runtime": runtime,
                    "model": model,
                    "preprocessing_contract_version": (
                        PREPROCESSING_CONTRACT_VERSION
                    ),
                    "embedding_contract_version": (
                        EMBEDDING_CONTRACT_VERSION
                    ),
                }
            ),
        }
    except RosettaReIdRuntimeError as error:
        diagnostics = error.diagnostics
        return {
            **base,
            "reason": error.code,
            "error_stage": diagnostics.get("error_stage", "runtime_probe"),
            "error_type": type(error).__name__,
            "error_message": str(error),
            **diagnostics,
        }


class RosettaOpenVinoPersonReIdEmbedder:
    embedding_dimension = EXPECTED_EMBEDDING_DIMENSION

    def __init__(
        self,
        *,
        candidate: RosettaRuntimeCandidate,
        runtime_probe: dict[str, Any],
        batch_timeout_seconds: float = 300.0,
        diagnostics_directory: Path | None = None,
    ) -> None:
        if not runtime_probe.get("available"):
            raise ValueError("Rosetta embedder requires a passed runtime probe")
        self.candidate = candidate
        self.runtime_probe = runtime_probe
        self.batch_timeout_seconds = float(batch_timeout_seconds)
        self.diagnostics_directory = diagnostics_directory
        runtime = runtime_probe["runtime"]
        model = runtime_probe["model"]
        self.model_name = MODEL_NAME
        self.model_xml_sha256 = str(model["xml_sha256"])
        self.model_bin_sha256 = str(model["bin_sha256"])
        self.model_version = (
            f"{MODEL_NAME}-fp16-{self.model_xml_sha256[:12]}"
        )
        self.runtime_name = RUNTIME_NAME
        self.runtime_version = str(runtime["openvino_version"])
        self.runtime_architecture = str(runtime["architecture"])
        self.preprocessing_version = PREPROCESSING_CONTRACT_VERSION
        self.embedding_contract_version = EMBEDDING_CONTRACT_VERSION
        self.cache_namespace = {
            "model_xml_sha256": self.model_xml_sha256,
            "model_bin_sha256": self.model_bin_sha256,
            "runtime_name": self.runtime_name,
            "runtime_version": self.runtime_version,
            "preprocessing_version": self.preprocessing_version,
            "embedding_contract_version": self.embedding_contract_version,
            "embedding_dimension": self.embedding_dimension,
        }

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        return self.embed_batch([crop_bgr])[0]

    def embed_batch(self, crops: list[np.ndarray]) -> np.ndarray:
        if not crops:
            return np.empty((0, self.embedding_dimension), dtype=np.float32)
        tensors = [preprocess_person_reid_0288(crop) for crop in crops]
        response, outputs, _process = _run_worker(
            self.candidate,
            tensors=tensors,
            operation="embed_batch",
            timeout_seconds=self.batch_timeout_seconds,
            diagnostics_directory=self.diagnostics_directory,
        )
        _validate_worker_response(
            self.candidate,
            response,
            outputs,
            expected_count=len(tensors),
        )
        return outputs


def activate_rosetta_openvino_runtime(
    candidate: RosettaRuntimeCandidate,
    *,
    real_crop_bgr: np.ndarray,
    diagnostics_directory: Path | None = None,
) -> tuple[RosettaOpenVinoPersonReIdEmbedder | None, dict[str, Any]]:
    probe = probe_rosetta_openvino_runtime(
        candidate,
        real_crop_bgr=real_crop_bgr,
        diagnostics_directory=diagnostics_directory,
    )
    if not probe.get("available"):
        return None, {
            **probe,
            "quality_tier": "preferred_reid_model",
            "selected_runtime": None,
        }
    try:
        embedder = RosettaOpenVinoPersonReIdEmbedder(
            candidate=candidate,
            runtime_probe=probe,
            diagnostics_directory=diagnostics_directory,
        )
        return embedder, {
            **probe,
            "available": True,
            "runtime_details": probe.get("runtime") or {},
            "model_name": embedder.model_name,
            "model_version": embedder.model_version,
            "runtime": embedder.runtime_name,
            "runtime_version": embedder.runtime_version,
            "runtime_architecture": embedder.runtime_architecture,
            "selected_runtime": embedder.runtime_name,
            "embedding_dimension": embedder.embedding_dimension,
            "cache_namespace": embedder.cache_namespace,
            "quality_tier": "preferred_reid_model",
            "fallback_used": False,
        }
    except (KeyError, TypeError, ValueError) as error:
        return None, {
            **probe,
            "available": False,
            "reason": "runtime_activation_failed",
            "error_stage": "activation",
            "error_type": type(error).__name__,
            "error_message": str(error),
            "selected_runtime": None,
            "quality_tier": "preferred_reid_model",
        }


def _run_worker(
    candidate: RosettaRuntimeCandidate,
    *,
    tensors: list[np.ndarray],
    operation: str,
    timeout_seconds: float,
    diagnostics_directory: Path | None,
) -> tuple[dict[str, Any], np.ndarray, subprocess.CompletedProcess[str]]:
    with tempfile.TemporaryDirectory(prefix="orlik-rosetta-reid-") as directory:
        root = Path(directory)
        inputs_path = root / "inputs.npz"
        outputs_path = root / "outputs.npy"
        response_path = root / "response.json"
        request_path = root / "request.json"
        np.savez_compressed(
            inputs_path,
            **{
                f"tensor_{index:06d}": tensor
                for index, tensor in enumerate(tensors)
            },
        )
        request = {
            "schema_version": WORKER_SCHEMA_VERSION,
            "operation": operation,
            "model_name": MODEL_NAME,
            "model_xml": str(candidate.model_xml),
            "model_bin": str(candidate.model_bin),
            "inputs_npz": str(inputs_path),
            "outputs_npy": str(outputs_path),
            "response_json": str(response_path),
            "expected_count": len(tensors),
            "expected_dimension": EXPECTED_EMBEDDING_DIMENSION,
            "expected_input_shape": list(EXPECTED_INPUT_SHAPE),
            "preprocessing_contract_version": PREPROCESSING_CONTRACT_VERSION,
            "embedding_contract_version": EMBEDDING_CONTRACT_VERSION,
        }
        request_path.write_text(
            json.dumps(request, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        command = [
            "/usr/bin/arch",
            "-x86_64",
            str(candidate.python_executable),
            str(candidate.worker),
            "--request",
            str(request_path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            diagnostics = {
                "error_stage": operation,
                "exit_code": None,
                "stdout": _text(error.stdout),
                "stderr": _text(error.stderr),
                "timeout": True,
            }
            _persist_diagnostics(diagnostics_directory, operation, diagnostics)
            raise RosettaReIdRuntimeError(
                "worker_timeout",
                f"Rosetta worker timed out after {timeout_seconds:.1f}s",
                diagnostics=diagnostics,
            ) from error
        diagnostics = {
            "error_stage": operation,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timeout": False,
        }
        _persist_diagnostics(diagnostics_directory, operation, diagnostics)
        response = _load_worker_response(response_path, completed.stdout)
        if completed.returncode != 0:
            raise RosettaReIdRuntimeError(
                str(response.get("error_code") or "worker_non_zero_exit"),
                str(
                    response.get("error_message")
                    or completed.stderr
                    or completed.stdout
                    or "Rosetta worker failed"
                ),
                diagnostics={**diagnostics, "response": response},
            )
        if not outputs_path.is_file():
            raise RosettaReIdRuntimeError(
                "missing_output_matrix",
                "Rosetta worker did not create the output matrix",
                diagnostics={**diagnostics, "response": response},
            )
        try:
            outputs = np.load(outputs_path, allow_pickle=False)
        except (OSError, ValueError) as error:
            raise RosettaReIdRuntimeError(
                "invalid_output_matrix",
                str(error),
                diagnostics={**diagnostics, "response": response},
            ) from error
        return response, np.asarray(outputs, dtype=np.float32), completed


def _load_worker_response(
    response_path: Path,
    stdout: str,
) -> dict[str, Any]:
    if not response_path.is_file():
        raise RosettaReIdRuntimeError(
            "missing_response_file",
            "Rosetta worker did not create response.json",
            diagnostics={"stdout": stdout},
        )
    try:
        response = json.loads(response_path.read_text(encoding="utf-8"))
        stdout_response = json.loads(stdout.strip())
    except (OSError, json.JSONDecodeError) as error:
        raise RosettaReIdRuntimeError(
            "invalid_worker_json",
            str(error),
            diagnostics={"stdout": stdout},
        ) from error
    if response != stdout_response:
        raise RosettaReIdRuntimeError(
            "worker_handshake_mismatch",
            "stdout handshake differs from response.json",
            diagnostics={"stdout": stdout, "response": response},
        )
    return response


def _validate_worker_response(
    candidate: RosettaRuntimeCandidate,
    response: dict[str, Any],
    outputs: np.ndarray,
    *,
    expected_count: int,
) -> None:
    if response.get("schema_version") != WORKER_SCHEMA_VERSION:
        raise RosettaReIdRuntimeError(
            "worker_schema_mismatch", "Unexpected Rosetta worker schema"
        )
    if response.get("status") != "ok":
        raise RosettaReIdRuntimeError(
            str(response.get("error_code") or "worker_status_failed"),
            str(response.get("error_message") or "Rosetta worker failed"),
            diagnostics={"response": response},
        )
    runtime = response.get("runtime") or {}
    if runtime.get("architecture") != "x86_64":
        raise RosettaReIdRuntimeError(
            "ROSETTA_PROCESS_ARCHITECTURE_MISMATCH",
            f"Expected x86_64, received {runtime.get('architecture')}",
            diagnostics={"response": response},
        )
    if "CPU" not in (runtime.get("available_devices") or []):
        raise RosettaReIdRuntimeError(
            "cpu_device_missing", "CPU is absent from OpenVINO devices"
        )
    model = response.get("model") or {}
    expected_digests = {
        "xml_sha256": _sha256(candidate.model_xml),
        "bin_sha256": _sha256(candidate.model_bin),
    }
    if any(model.get(key) != value for key, value in expected_digests.items()):
        raise RosettaReIdRuntimeError(
            "model_digest_mismatch", "Worker used unexpected model files"
        )
    if model.get("input_shape") != list(EXPECTED_INPUT_SHAPE):
        raise RosettaReIdRuntimeError(
            "model_input_shape_mismatch", "Unexpected model input shape"
        )
    if model.get("output_shape") != [1, EXPECTED_EMBEDDING_DIMENSION]:
        raise RosettaReIdRuntimeError(
            "model_output_shape_mismatch", "Unexpected model output shape"
        )
    if outputs.shape != (expected_count, EXPECTED_EMBEDDING_DIMENSION):
        raise RosettaReIdRuntimeError(
            "output_shape_mismatch",
            f"Unexpected output matrix shape: {outputs.shape}",
        )
    if not np.isfinite(outputs).all():
        raise RosettaReIdRuntimeError(
            "INVALID_NONFINITE_EMBEDDING",
            "Output contains NaN or infinite values",
        )
    norms = np.linalg.norm(outputs, axis=1)
    if np.any(norms <= MINIMUM_EMBEDDING_NORM):
        raise RosettaReIdRuntimeError(
            "INVALID_ZERO_NORM_EMBEDDING",
            "Output contains a zero-norm embedding",
        )
    if not np.allclose(norms, 1.0, rtol=1e-5, atol=1e-6):
        raise RosettaReIdRuntimeError(
            "embedding_not_normalized",
            "Output embeddings are not L2 normalized",
        )


def _persist_diagnostics(
    directory: Path | None,
    operation: str,
    diagnostics: dict[str, Any],
) -> None:
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"rosetta_{operation}_last_process.json"
    target.write_text(
        json.dumps(diagnostics, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
