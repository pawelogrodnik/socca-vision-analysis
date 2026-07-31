from __future__ import annotations

"""Isolated batch worker for the public OSNet-AIN ReID checkpoint.

It deliberately talks only through npz/npy files.  The experiment runner keeps
torchreid and its checkpoint in ``backend/.reid-runtime-lab`` so the product
runtime and its dependency lock are untouched.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from torchreid.reid.models import build_model


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--response", required=True)
    return parser.parse_args()


def _normalise(vector: torch.Tensor) -> torch.Tensor:
    return vector / torch.clamp(torch.linalg.vector_norm(vector, dim=1, keepdim=True), min=1e-12)


def main() -> int:
    args = _parse_args()
    inputs = np.load(args.inputs, allow_pickle=False)["images"]
    if inputs.ndim != 4 or inputs.shape[-1] != 3:
        raise ValueError("Expected BGR image batch shaped [N,H,W,3]")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = build_model(
        name="osnet_ain_x1_0",
        num_classes=4101,
        loss="softmax",
        pretrained=False,
    )
    source = torch.load(args.weights, map_location="cpu", weights_only=True)
    model.load_state_dict(
        {key.removeprefix("module."): value for key, value in source.items()},
        strict=True,
    )
    model.to(device).eval()
    rgb = inputs[..., ::-1].copy()
    tensor = torch.from_numpy(rgb).permute(0, 3, 1, 2).float().to(device) / 255.0
    tensor = torch.nn.functional.interpolate(
        tensor, size=(256, 128), mode="bilinear", align_corners=False
    )
    mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
    with torch.no_grad():
        embeddings = _normalise(model((tensor - mean) / std)).cpu().numpy().astype(np.float32)
    np.save(args.outputs, embeddings, allow_pickle=False)
    Path(args.response).write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "status": "ok",
                "model_name": "osnet_ain_x1_0_msmt17",
                "weights_sha256": hashlib.sha256(Path(args.weights).read_bytes()).hexdigest(),
                "torch_version": torch.__version__,
                "device": device,
                "embedding_dimension": int(embeddings.shape[1]),
                "input_count": int(embeddings.shape[0]),
                "finite": bool(np.isfinite(embeddings).all()),
                "norm_min": float(np.linalg.norm(embeddings, axis=1).min()),
                "norm_max": float(np.linalg.norm(embeddings, axis=1).max()),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
