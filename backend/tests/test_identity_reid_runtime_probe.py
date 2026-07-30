from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.services.identity_approved_appearance_reid import (
    PortableAppearanceEmbedder,
)
from app.services.identity_reid_runtime_probe import (
    build_reid_runtime_probe,
    build_reid_runtime_repair_request,
)
from app.services.identity_same_match_reid import load_default_embedder


class ReidRuntimeProbeTests(unittest.TestCase):
    def test_probe_reports_preferred_runtime_when_inference_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crop_path = root / "crop.jpg"
            cv2.imwrite(str(crop_path), _image())
            attempt = {
                "runtime": "opencv_dnn_openvino",
                "model_loaded": True,
                "inference_attempted": True,
                "inference_passed": True,
            }
            with patch(
                "app.services.identity_reid_runtime_probe."
                "collect_reid_runtime_capabilities",
                return_value={"model_files_present": True, "openvino_import_available": True},
            ), patch(
                "app.services.identity_reid_runtime_probe.load_default_embedder",
                return_value=(
                    PortableAppearanceEmbedder(),
                    {
                        "model_name": "preferred",
                        "selected_runtime": "opencv_dnn_openvino",
                        "attempted_runtimes": ["opencv_dnn_openvino"],
                        "runtime_attempts": [attempt],
                    },
                ),
            ):
                result = build_reid_runtime_probe(
                    models_dir=root,
                    crop_path=crop_path,
                )

        self.assertEqual(result["status"], "PREFERRED_REID_RUNTIME_AVAILABLE")
        self.assertEqual(result["inference"], attempt)

    def test_opencv_inference_failure_falls_through_to_openvino(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _model_root(Path(temporary))
            with patch(
                "app.services.identity_same_match_reid."
                "OpenCvPersonReIdEmbedder.from_openvino_ir",
                return_value=_FailingEmbedder(),
            ), patch(
                "app.services.identity_same_match_reid."
                "OpenVinoRuntimePersonReIdEmbedder.from_openvino_ir",
                return_value=PortableAppearanceEmbedder(),
            ):
                embedder, status = load_default_embedder(
                    root,
                    smoke_crop_bgr=_image(),
                )

        self.assertIsInstance(embedder, PortableAppearanceEmbedder)
        self.assertEqual(status["selected_runtime"], "openvino_cpu")
        self.assertEqual(status["runtime_attempts"][0]["error_type"], "inference_error")

    def test_repeatability_within_tolerance_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _model_root(Path(temporary))
            with patch(
                "app.services.identity_same_match_reid."
                "OpenCvPersonReIdEmbedder.from_openvino_ir",
                return_value=_NearRepeatEmbedder(),
            ):
                embedder, status = load_default_embedder(
                    root,
                    smoke_crop_bgr=_image(),
                )

        self.assertIsNotNone(embedder)
        self.assertEqual(status["selected_runtime"], "opencv_dnn_openvino")
        self.assertTrue(status["runtime_attempts"][0]["repeatability_passed"])

    def test_both_preferred_inference_paths_fail_without_portable_probe_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _model_root(Path(temporary))
            with patch(
                "app.services.identity_same_match_reid."
                "OpenCvPersonReIdEmbedder.from_openvino_ir",
                return_value=_FailingEmbedder(),
            ), patch(
                "app.services.identity_same_match_reid."
                "OpenVinoRuntimePersonReIdEmbedder.from_openvino_ir",
                return_value=_FailingEmbedder(),
            ):
                embedder, status = load_default_embedder(
                    root,
                    smoke_crop_bgr=_image(),
                )

        self.assertIsNone(embedder)
        self.assertIsNone(status["selected_runtime"])
        self.assertEqual(len(status["runtime_attempts"]), 2)
        self.assertTrue(
            all(
                row["error_type"] == "inference_error"
                for row in status["runtime_attempts"]
            )
        )

    def test_model_files_missing_never_attempts_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            embedder, status = load_default_embedder(
                Path(temporary),
                smoke_crop_bgr=_image(),
            )

        self.assertIsNone(embedder)
        self.assertEqual(status["reason"], "model_files_missing")
        self.assertEqual(status["runtime_attempts"], [])

    def test_repair_request_is_explicit_but_non_mutating(self) -> None:
        request = build_reid_runtime_repair_request({
            "capabilities": {
                "model_files_present": True,
                "python_version": "3.11.15",
                "platform_machine": "arm64",
                "opencv_version": "4.11.0",
                "openvino_import_available": True,
                "openvino_version": "2025.4.1",
                "openvino_available_devices": ["CPU"],
            },
            "model": {"runtime_attempts": []},
        })

        self.assertTrue(request["approval_required"])
        self.assertIn("openvino==2025.4.1", request["proposed_command"])
        self.assertFalse(request["installation_performed"])


class _FailingEmbedder:
    model_name = "failing"
    model_version = "1"
    embedding_dimension = 3

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        raise RuntimeError("inference failed")


class _NearRepeatEmbedder:
    model_name = "near"
    model_version = "1"
    embedding_dimension = 3

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        self.calls += 1
        value = np.asarray([1.0, 0.0, self.calls * 1e-7], dtype=np.float32)
        return value / np.linalg.norm(value)


def _image() -> np.ndarray:
    return np.full((80, 40, 3), (10, 20, 200), dtype=np.uint8)


def _model_root(root: Path) -> Path:
    model = root / "person-reidentification-retail-0288" / "FP16"
    model.mkdir(parents=True)
    (model / "person-reidentification-retail-0288.xml").write_text("x")
    (model / "person-reidentification-retail-0288.bin").write_bytes(b"x")
    return root


if __name__ == "__main__":
    unittest.main()
