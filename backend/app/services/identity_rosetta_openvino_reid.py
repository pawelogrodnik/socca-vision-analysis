from __future__ import annotations

"""Dedicated subprocess adapter for the proven Rosetta OpenVINO runtime."""

import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np


class RosettaOpenVinoPersonReIdEmbedder:
    model_name = "person-reidentification-retail-0288"
    model_version = "openvino-2025.3.0-rosetta-x86"
    embedding_dimension = 256

    def __init__(self, *, python_executable: Path, models_dir: Path) -> None:
        self.python_executable = python_executable
        root = models_dir / "person-reidentification-retail-0288" / "FP16"
        self.model_xml = root / "person-reidentification-retail-0288.xml"
        self.model_bin = root / "person-reidentification-retail-0288.bin"
        self.worker = Path(__file__).resolve().parents[2] / "scripts" / "embed_openvino_rosetta.py"

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        if crop_bgr.size == 0:
            raise ValueError("Cannot embed an empty crop")
        with tempfile.TemporaryDirectory(prefix="orlik-reid-") as directory:
            root = Path(directory)
            input_path = root / "crop.npy"
            output_path = root / "embedding.npy"
            manifest = root / "manifest.json"
            np.save(input_path, crop_bgr)
            manifest.write_text(json.dumps({
                "model_xml": str(self.model_xml), "model_bin": str(self.model_bin),
                "input_npy": str(input_path), "output_npy": str(output_path),
            }), encoding="utf-8")
            completed = subprocess.run(
                ["/usr/bin/arch", "-x86_64", str(self.python_executable), str(self.worker), "--manifest", str(manifest)],
                check=False, capture_output=True, text=True, timeout=30,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr or completed.stdout)
            vector = np.load(output_path).astype(np.float32)
        if vector.size != self.embedding_dimension or not np.isfinite(vector).all():
            raise ValueError("Invalid Rosetta OpenVINO embedding")
        return vector / max(float(np.linalg.norm(vector)), 1e-12)


def load_rosetta_openvino_embedder(models_dir: Path) -> RosettaOpenVinoPersonReIdEmbedder | None:
    python = models_dir.parent / ".reid-runtime-lab" / "ov-2026.1-rosetta-x86" / "bin" / "python"
    embedder = RosettaOpenVinoPersonReIdEmbedder(python_executable=python, models_dir=models_dir)
    if not python.exists() or not embedder.model_xml.is_file() or not embedder.model_bin.is_file():
        return None
    return embedder
