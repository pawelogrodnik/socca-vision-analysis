from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_panel_readiness import (
    build_identity_jersey_number_panel_readiness,
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit operator-reviewed jersey number panels.")
    parser.add_argument("--subject-review", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--montage-reviewed", action="store_true")
    args = parser.parse_args()
    result = build_identity_jersey_number_panel_readiness(
        _load(args.subject_review),
        artifact_root=args.artifact_root.resolve(),
        output_root=args.output_root.resolve(),
        montage_reviewed=True if args.montage_reviewed else None,
    )
    report = result["identity_jersey_number_panel_readiness"]
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    if report["status"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
