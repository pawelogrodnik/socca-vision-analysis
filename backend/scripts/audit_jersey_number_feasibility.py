from __future__ import annotations

"""Non-identity jersey-number feasibility check for the frozen H1 crops."""

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bakeoff-root", required=True, type=Path)
    options = parser.parse_args()
    source = _load(options.bakeoff_root / "h1_crop_quality_audit.json")
    rows = source.get("crops") or []
    plausible = [
        row for row in rows
        if int(row.get("height") or 0) >= 70
        and float(row.get("blur_laplacian_variance") or 0) >= 25.0
        and float(row.get("contrast") or 0) >= 20.0
    ]
    artifact = {
        "schema_version": "1.0.0",
        "mode": "jersey_number_feasibility_audit",
        "status": "NOT_VIABLE_FOR_CURRENT_IDENTITY_PIPELINE",
        "reason": [
            "No ground-truth visible jersey-number labels exist for calibration.",
            "H1 ReID separability is insufficient and crop-level pixel heuristics cannot establish readable digits.",
            "No automatic jersey recognition or identity assignment is enabled.",
        ],
        "screening": {
            "crops_reviewed": len(rows),
            "pixel_quality_plausible_crops": len(plausible),
            "requires_front_or_back_orientation_labels": True,
            "requires_readable_number_ground_truth": True,
        },
        "next_step": "Collect a small operator-confirmed, readable jersey-number panel only if the product needs it; do not infer it from these crops.",
        "safety": {"automatic_identity_assignments": 0, "production_identity_mutations": 0},
    }
    _write(options.bakeoff_root / "jersey_number_feasibility_audit.json", artifact)
    return 0


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
