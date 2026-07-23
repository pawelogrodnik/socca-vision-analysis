from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_panel_audit import (  # noqa: E402
    READINESS_FILENAME,
    audit_identity_jersey_number_panels,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit tight jersey-number panels from a dataset manifest.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    dataset_doc = json.loads(args.dataset.resolve().read_text(encoding="utf-8"))
    if not isinstance(dataset_doc, dict):
        raise ValueError("dataset must contain a JSON object")
    report = audit_identity_jersey_number_panels(dataset_doc, output_root=args.output_root.resolve())
    args.output_root.resolve().mkdir(parents=True, exist_ok=True)
    (args.output_root.resolve() / READINESS_FILENAME).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
