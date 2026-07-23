from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import torch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_sequence_training import train_jersey_number_sequence


def main() -> None:
    parser = argparse.ArgumentParser(description="Train diagnostic-only shadow CRNN jersey-number sequence model.")
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--device")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    dataset_manifest, wrapper_provenance = _dataset_manifest(_load(args.dataset_manifest))
    result = train_jersey_number_sequence(
        dataset_manifest,
        epochs=args.epochs,
        device=args.device,
        seed=args.seed,
    )
    checkpoint = result["checkpoint"]
    report = result["report"]
    if wrapper_provenance is not None:
        report["source"] = {"diagnostic_assistant_dataset_provenance": wrapper_provenance}
    torch.save(checkpoint, output_dir / "identity_jersey_number_sequence_checkpoint.pt")
    _write(output_dir / "identity_jersey_number_sequence_checkpoint_metadata.json", checkpoint["metadata"])
    _write(output_dir / "identity_jersey_number_sequence_training_report.json", report)
    print(json.dumps({"training_status": "diagnostic_training_only", **report}, indent=2, sort_keys=True))


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


def _dataset_manifest(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if "dataset_manifest" not in document:
        return document, None
    manifest = document.get("dataset_manifest")
    provenance = document.get("provenance")
    if not isinstance(manifest, dict) or (provenance is not None and not isinstance(provenance, dict)):
        raise ValueError("diagnostic assistant dataset wrapper has invalid nested shapes")
    return manifest, provenance


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
