from __future__ import annotations

import json
from pathlib import Path

import torch
from torchreid.reid.models import build_model


def main() -> int:
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = build_model(
        "osnet_ain_x1_0", num_classes=7, loss="softmax", pretrained=False
    ).to(device).eval()
    value = model(torch.zeros((2, 3, 256, 128), device=device))
    print(json.dumps({
        "status": "ok", "torch_version": torch.__version__, "device": device,
        "embedding_dimension": int(value.shape[1]), "finite": bool(torch.isfinite(value).all()),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
