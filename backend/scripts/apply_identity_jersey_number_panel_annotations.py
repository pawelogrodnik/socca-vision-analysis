from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_panel_annotation_audit import (  # noqa: E402
    apply_panel_annotation_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply reviewed jersey number panel boxes to a dataset manifest."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dataset = _read_object(args.dataset.resolve())
    reviewed = _read_object(args.reviewed.resolve())
    output = args.output.resolve()
    updated = apply_panel_annotation_audit(dataset, reviewed)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(updated["panel_annotation_import"], indent=2, ensure_ascii=False))
    print(f"dataset={output}")


def _read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


if __name__ == "__main__":
    main()
