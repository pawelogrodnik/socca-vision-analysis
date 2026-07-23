from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_sequence_evaluation import (
    evaluate_jersey_number_sequence_shadow,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate diagnostic-only shadow CRNN jersey-number sequences.")
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--sequence-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    dataset_manifest, wrapper_provenance = _dataset_manifest(_load(args.dataset_manifest))
    result = evaluate_jersey_number_sequence_shadow(
        dataset_manifest, args.sequence_checkpoint.resolve(), device=args.device
    )
    if wrapper_provenance is not None:
        result["source"] = {"diagnostic_assistant_dataset_provenance": wrapper_provenance}
    _write(output_dir / "sequence_offline_evaluation.json", result)
    print(json.dumps({"mode": "single_match_diagnostic_shadow_only", **result}, indent=2, sort_keys=True))


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
