#!/usr/bin/env python3
"""Single-compile Rosetta/x86 OpenVINO ReID worker."""

import argparse
import hashlib
import json
import platform
import sys
import time
import traceback

import numpy as np
import openvino as ov


SCHEMA_VERSION = "1.0.0"


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _write_response(request, response):
    with open(request["response_json"], "w", encoding="utf-8") as output:
        json.dump(response, output, ensure_ascii=True, separators=(",", ":"))
    print(json.dumps(response, ensure_ascii=True, separators=(",", ":")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    arguments = parser.parse_args()
    request = json.loads(open(arguments.request, encoding="utf-8").read())
    stage = "request_validation"
    started = time.perf_counter()
    try:
        if request.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("worker_schema_mismatch")
        architecture = platform.machine()
        if architecture != "x86_64":
            raise RuntimeError("ROSETTA_PROCESS_ARCHITECTURE_MISMATCH")
        tensors_archive = np.load(request["inputs_npz"], allow_pickle=False)
        tensor_keys = sorted(tensors_archive.files)
        tensors = [np.asarray(tensors_archive[key]) for key in tensor_keys]
        if len(tensors) != int(request["expected_count"]):
            raise ValueError("input_count_mismatch")
        expected_shape = tuple(request["expected_input_shape"])
        if any(tensor.shape != expected_shape for tensor in tensors):
            raise ValueError("input_shape_mismatch")
        if any(tensor.dtype != np.float32 for tensor in tensors):
            raise ValueError("input_dtype_mismatch")

        stage = "core_creation"
        core_started = time.perf_counter()
        core = ov.Core()
        available_devices = list(core.available_devices)
        core_ms = (time.perf_counter() - core_started) * 1000.0
        if "CPU" not in available_devices:
            raise RuntimeError("cpu_device_missing")

        stage = "model_read"
        read_started = time.perf_counter()
        model = core.read_model(request["model_xml"], request["model_bin"])
        read_ms = (time.perf_counter() - read_started) * 1000.0

        stage = "model_compile"
        compile_started = time.perf_counter()
        compiled = core.compile_model(model, "CPU")
        compile_ms = (time.perf_counter() - compile_started) * 1000.0
        input_layer = compiled.input(0)
        output_layer = compiled.output(0)
        input_shape = list(input_layer.shape)
        output_shape = list(output_layer.shape)

        stage = "synthetic_inference"
        synthetic = np.zeros(expected_shape, dtype=np.float32)
        synthetic_started = time.perf_counter()
        compiled({input_layer: synthetic})[output_layer]
        synthetic_ms = (time.perf_counter() - synthetic_started) * 1000.0

        stage = "real_crop_inference"
        vectors = []
        inference_times = []
        rejected = []
        for index, tensor in enumerate(tensors):
            inference_started = time.perf_counter()
            try:
                output = compiled({input_layer: tensor})[output_layer]
                vector = np.asarray(output, dtype=np.float32).reshape(-1)
                norm = float(np.linalg.norm(vector))
                if (
                    vector.size != int(request["expected_dimension"])
                    or not np.isfinite(vector).all()
                    or not np.isfinite(norm)
                    or norm <= 1e-8
                ):
                    raise ValueError("invalid_embedding")
                vectors.append(vector / norm)
                rejected.append(None)
            except Exception as error:
                vectors.append(
                    np.full(
                        int(request["expected_dimension"]),
                        np.nan,
                        dtype=np.float32,
                    )
                )
                rejected.append(
                    {
                        "index": index,
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    }
                )
            inference_times.append(
                (time.perf_counter() - inference_started) * 1000.0
            )
        outputs = np.stack(vectors).astype(np.float32)
        np.save(request["outputs_npy"], outputs, allow_pickle=False)
        finite = bool(np.isfinite(outputs).all())
        norms = np.linalg.norm(outputs, axis=1)
        response = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if finite and not any(rejected) else "error",
            "runtime": {
                "architecture": architecture,
                "python_version": sys.version,
                "python_executable": sys.executable,
                "openvino_version": ov.__version__,
                "numpy_version": np.__version__,
                "available_devices": available_devices,
            },
            "model": {
                "name": request["model_name"],
                "xml_sha256": _sha256(request["model_xml"]),
                "bin_sha256": _sha256(request["model_bin"]),
                "input_shape": input_shape,
                "output_shape": output_shape,
            },
            "contracts": {
                "preprocessing": request["preprocessing_contract_version"],
                "embedding": request["embedding_contract_version"],
            },
            "result": {
                "count": len(vectors),
                "embedding_dimension": int(outputs.shape[1]),
                "finite": finite,
                "norms": [float(value) for value in norms],
                "rejected": rejected,
            },
            "timings_ms": {
                "core_creation": core_ms,
                "model_read": read_ms,
                "model_compile": compile_ms,
                "synthetic_inference": synthetic_ms,
                "per_crop_inference": inference_times,
                "batch_inference": sum(inference_times),
                "total": (time.perf_counter() - started) * 1000.0,
            },
        }
        _write_response(request, response)
        return 0 if response["status"] == "ok" else 4
    except Exception as error:
        response = {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "error_stage": stage,
            "error_code": str(error),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        }
        _write_response(request, response)
        if "ARCHITECTURE_MISMATCH" in str(error):
            return 6
        if stage == "model_compile":
            return 3
        if stage in {"synthetic_inference", "real_crop_inference"}:
            return 4
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
