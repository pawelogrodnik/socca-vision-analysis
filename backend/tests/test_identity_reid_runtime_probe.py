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
from app.services.identity_reid_runtime_probe import build_reid_runtime_probe


class ReidRuntimeProbeTests(unittest.TestCase):
    def test_probe_reports_preferred_runtime_when_inference_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crop_path = root / "crop.jpg"
            cv2.imwrite(
                str(crop_path),
                np.full((80, 40, 3), (10, 20, 200), dtype=np.uint8),
            )
            with patch(
                "app.services.identity_reid_runtime_probe."
                "collect_reid_runtime_capabilities",
                return_value={"model_files_present": True},
            ), patch(
                "app.services.identity_reid_runtime_probe.load_default_embedder",
                return_value=(
                    PortableAppearanceEmbedder(),
                    {
                        "model_name": "preferred",
                        "selected_runtime": "opencv_dnn_openvino",
                        "attempted_runtimes": ["opencv_dnn_openvino"],
                    },
                ),
            ):
                result = build_reid_runtime_probe(
                    models_dir=root,
                    crop_path=crop_path,
                )

        self.assertEqual(
            result["status"], "PREFERRED_REID_RUNTIME_AVAILABLE"
        )
        self.assertTrue(result["inference"]["l2_normalized"])
        self.assertTrue(
            result["inference"]["deterministic_repeated_inference"]
        )

    def test_probe_preserves_unavailable_runtime_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch(
                "app.services.identity_reid_runtime_probe."
                "collect_reid_runtime_capabilities",
                return_value={"model_files_present": True},
            ), patch(
                "app.services.identity_reid_runtime_probe.load_default_embedder",
                return_value=(
                    None,
                    {
                        "attempted_runtimes": [
                            "opencv_dnn_openvino",
                            "openvino_cpu",
                        ],
                        "load_errors": [{"runtime": "openvino_cpu"}],
                    },
                ),
            ):
                result = build_reid_runtime_probe(
                    models_dir=root,
                    crop_path=None,
                )

        self.assertEqual(
            result["status"], "PREFERRED_REID_RUNTIME_BLOCKED"
        )
        self.assertFalse(result["model"]["fallback_used"])
        self.assertEqual(
            result["inference"]["status"],
            "not_run_preferred_runtime_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
