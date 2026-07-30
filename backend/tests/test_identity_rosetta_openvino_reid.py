from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app.services.identity_rosetta_openvino_reid import (
    RosettaOpenVinoPersonReIdEmbedder,
    load_rosetta_openvino_embedder,
)


class RosettaOpenVinoPersonReIdEmbedderTests(unittest.TestCase):
    def test_embed_invokes_x86_worker_and_normalizes_its_vector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            model_root = models / "person-reidentification-retail-0288" / "FP16"
            model_root.mkdir(parents=True)
            (model_root / "person-reidentification-retail-0288.xml").touch()
            (model_root / "person-reidentification-retail-0288.bin").touch()
            embedder = RosettaOpenVinoPersonReIdEmbedder(
                python_executable=root / "python",
                models_dir=models,
            )

            with patch(
                "app.services.identity_rosetta_openvino_reid.subprocess.run",
                return_value=type("Completed", (), {"returncode": 0})(),
            ) as run, patch(
                "app.services.identity_rosetta_openvino_reid.np.load",
                return_value=np.full(256, 2.0, dtype=np.float32),
            ):
                vector = embedder.embed(np.ones((20, 10, 3), dtype=np.uint8))

            self.assertEqual(run.call_args.args[0][:2], ["/usr/bin/arch", "-x86_64"])
            self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=6)
            self.assertEqual(vector.shape, (256,))

    def test_loader_requires_complete_runtime_and_model_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            self.assertIsNone(load_rosetta_openvino_embedder(models))

            python = root / ".reid-runtime-lab" / "ov-2026.1-rosetta-x86" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.touch()
            model_root = models / "person-reidentification-retail-0288" / "FP16"
            model_root.mkdir(parents=True)
            (model_root / "person-reidentification-retail-0288.xml").touch()
            (model_root / "person-reidentification-retail-0288.bin").touch()

            self.assertIsInstance(
                load_rosetta_openvino_embedder(models),
                RosettaOpenVinoPersonReIdEmbedder,
            )
