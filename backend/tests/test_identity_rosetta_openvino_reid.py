from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.services.identity_rosetta_openvino_reid import (
    EMBEDDING_CONTRACT_VERSION,
    EXPECTED_INPUT_SHAPE,
    PREPROCESSING_CONTRACT_VERSION,
    RosettaOpenVinoPersonReIdEmbedder,
    RosettaReIdRuntimeError,
    RosettaRuntimeCandidate,
    _run_worker,
    _validate_worker_response,
    activate_rosetta_openvino_runtime,
    discover_rosetta_openvino_runtime,
    preprocess_person_reid_0288,
    probe_rosetta_openvino_runtime,
)


class RosettaOpenVinoPersonReIdEmbedderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = _candidate(self.root)
        self.response = _response(self.candidate)
        self.outputs = np.tile(
            _unit_vector(),
            (2, 1),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_files_only_discover_a_candidate_not_an_available_runtime(self) -> None:
        manifest = self.candidate.manifest()
        self.assertTrue(manifest["runtime_candidate_present"])
        self.assertNotIn("available", manifest)

    def test_candidate_files_exist_but_probe_failure_blocks_runtime(self) -> None:
        with patch(
            "app.services.identity_rosetta_openvino_reid._run_worker",
            side_effect=RosettaReIdRuntimeError(
                "worker_non_zero_exit",
                "failed",
                diagnostics={"exit_code": 3, "stderr": "compile failed"},
            ),
        ):
            probe = probe_rosetta_openvino_runtime(
                self.candidate,
                real_crop_bgr=_crop(),
            )

        self.assertFalse(probe["available"])
        self.assertEqual(probe["reason"], "worker_non_zero_exit")

    def test_passed_probe_activates_versioned_runtime_from_handshake(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "{}", "")
        with patch(
            "app.services.identity_rosetta_openvino_reid._run_worker",
            return_value=(self.response, self.outputs, completed),
        ):
            embedder, status = activate_rosetta_openvino_runtime(
                self.candidate,
                real_crop_bgr=_crop(),
            )

        self.assertIsInstance(embedder, RosettaOpenVinoPersonReIdEmbedder)
        self.assertTrue(status["available"])
        self.assertEqual(status["runtime_version"], "2025.3.0-real")
        self.assertIn(_sha256(self.candidate.model_xml)[:12], status["model_version"])
        self.assertEqual(
            status["cache_namespace"]["preprocessing_version"],
            PREPROCESSING_CONTRACT_VERSION,
        )

    def test_repeatability_failure_blocks_activation(self) -> None:
        different = self.outputs.copy()
        different[1] = np.roll(different[1], 1)
        completed = subprocess.CompletedProcess([], 0, "{}", "")
        with patch(
            "app.services.identity_rosetta_openvino_reid._run_worker",
            return_value=(self.response, different, completed),
        ):
            embedder, status = activate_rosetta_openvino_runtime(
                self.candidate,
                real_crop_bgr=_crop(),
            )

        self.assertIsNone(embedder)
        self.assertFalse(status["available"])
        self.assertEqual(status["reason"], "embedding_repeatability_failed")

    def test_batch_calls_worker_once_and_preserves_output_order(self) -> None:
        probe = {
            "available": True,
            "runtime": self.response["runtime"],
            "model": self.response["model"],
        }
        embedder = RosettaOpenVinoPersonReIdEmbedder(
            candidate=self.candidate,
            runtime_probe=probe,
        )
        three = np.stack(
            [_unit_vector(), np.roll(_unit_vector(), 1), np.roll(_unit_vector(), 2)]
        )
        response = {
            **self.response,
            "result": {**self.response["result"], "count": 3},
        }
        completed = subprocess.CompletedProcess([], 0, "{}", "")
        with patch(
            "app.services.identity_rosetta_openvino_reid._run_worker",
            return_value=(response, three, completed),
        ) as run:
            result = embedder.embed_batch([_crop(1), _crop(2), _crop(3)])

        self.assertEqual(run.call_count, 1)
        np.testing.assert_array_equal(result, three)

    def test_preprocessing_preserves_bgr_and_contract(self) -> None:
        crop = np.zeros((256, 128, 3), dtype=np.uint8)
        crop[:, :, 0] = 11
        crop[:, :, 1] = 22
        crop[:, :, 2] = 33

        tensor = preprocess_person_reid_0288(crop)

        self.assertEqual(tensor.shape, EXPECTED_INPUT_SHAPE)
        self.assertEqual(tensor.dtype, np.float32)
        self.assertEqual(float(tensor[0, 0, 0, 0]), 11.0)
        self.assertEqual(float(tensor[0, 1, 0, 0]), 22.0)
        self.assertEqual(float(tensor[0, 2, 0, 0]), 33.0)

    def test_architecture_mismatch_is_rejected(self) -> None:
        response = {
            **self.response,
            "runtime": {**self.response["runtime"], "architecture": "arm64"},
        }
        with self.assertRaisesRegex(
            RosettaReIdRuntimeError,
            "Expected x86_64",
        ):
            _validate_worker_response(
                self.candidate,
                response,
                self.outputs,
                expected_count=2,
            )

    def test_zero_nan_wrong_row_and_wrong_dimension_outputs_are_rejected(self) -> None:
        malformed = (
            np.zeros((2, 256), dtype=np.float32),
            np.full((2, 256), np.nan, dtype=np.float32),
            np.tile(_unit_vector(), (1, 1)),
            np.ones((2, 255), dtype=np.float32),
        )
        for outputs in malformed:
            with self.subTest(shape=outputs.shape, finite=np.isfinite(outputs).all()):
                with self.assertRaises(RosettaReIdRuntimeError):
                    _validate_worker_response(
                        self.candidate,
                        self.response,
                        outputs,
                        expected_count=2,
                    )

    def test_timeout_is_classified_and_partial_output_is_ignored(self) -> None:
        with patch(
            "app.services.identity_rosetta_openvino_reid.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["worker"], 1),
        ):
            with self.assertRaisesRegex(
                RosettaReIdRuntimeError,
                "timed out",
            ) as raised:
                _run_worker(
                    self.candidate,
                    tensors=[
                        preprocess_person_reid_0288(_crop()),
                    ],
                    operation="embed_batch",
                    timeout_seconds=1,
                    diagnostics_directory=None,
                )
        self.assertEqual(raised.exception.code, "worker_timeout")


@unittest.skipUnless(
    os.environ.get("RUN_ROSETTA_REID_INTEGRATION") == "1",
    "Set RUN_ROSETTA_REID_INTEGRATION=1 for the real x86 OpenVINO test",
)
class RosettaOpenVinoRealIntegrationTests(unittest.TestCase):
    def test_real_probe_and_three_crop_batch_are_repeatable(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        candidate = discover_rosetta_openvino_runtime(
            repository_root / "backend" / "models"
        )
        crop_paths = sorted(
            (
                repository_root
                / "backend"
                / "storage"
                / "benchmarks"
                / "player_identity"
                / "product-flow-20260730-v4"
                / "cross_capture_reid_diagnostic"
                / "h1"
            ).rglob("*.jpg")
        )
        self.assertGreaterEqual(len(crop_paths), 3)
        crops = [cv2.imread(str(path)) for path in crop_paths[:3]]
        self.assertTrue(all(crop is not None and crop.size for crop in crops))

        embedder, status = activate_rosetta_openvino_runtime(
            candidate,
            real_crop_bgr=crops[0],
        )
        self.assertIsNotNone(embedder)
        self.assertTrue(status["available"])
        self.assertEqual(status["runtime_architecture"], "x86_64")
        self.assertEqual(status["embedding_dimension"], 256)
        first = embedder.embed_batch(crops)
        second = embedder.embed_batch(crops)

        self.assertEqual(first.shape, (3, 256))
        self.assertTrue(np.isfinite(first).all())
        np.testing.assert_allclose(np.linalg.norm(first, axis=1), 1.0, atol=1e-6)
        np.testing.assert_allclose(first, second, rtol=1e-5, atol=1e-6)
        self.assertFalse(np.allclose(first[0], first[1]))
        self.assertEqual(
            status["model"]["xml_sha256"],
            _sha256(candidate.model_xml),
        )
        self.assertEqual(
            status["model"]["bin_sha256"],
            _sha256(candidate.model_bin),
        )


def _candidate(root: Path) -> RosettaRuntimeCandidate:
    python = root / "runtime" / "bin" / "python"
    worker = root / "worker.py"
    xml = root / "model.xml"
    binary = root / "model.bin"
    for path, content in (
        (python, b"python"),
        (worker, b"worker"),
        (xml, b"xml"),
        (binary, b"bin"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return RosettaRuntimeCandidate(
        python_executable=python,
        worker=worker,
        model_xml=xml,
        model_bin=binary,
        runtime_directory=python.parents[1],
    )


def _response(candidate: RosettaRuntimeCandidate) -> dict:
    return {
        "schema_version": "1.0.0",
        "status": "ok",
        "runtime": {
            "architecture": "x86_64",
            "python_version": "3.9.6",
            "python_executable": str(candidate.python_executable),
            "openvino_version": "2025.3.0-real",
            "numpy_version": "2.0.2",
            "available_devices": ["CPU"],
        },
        "model": {
            "name": "person-reidentification-retail-0288",
            "xml_sha256": _sha256(candidate.model_xml),
            "bin_sha256": _sha256(candidate.model_bin),
            "input_shape": [1, 3, 256, 128],
            "output_shape": [1, 256],
        },
        "result": {
            "count": 2,
            "embedding_dimension": 256,
            "finite": True,
            "norms": [1.0, 1.0],
            "rejected": [None, None],
        },
    }


def _unit_vector() -> np.ndarray:
    vector = np.arange(1, 257, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def _crop(seed: int = 1) -> np.ndarray:
    random = np.random.default_rng(seed)
    return random.integers(0, 256, (40, 20, 3), dtype=np.uint8)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
