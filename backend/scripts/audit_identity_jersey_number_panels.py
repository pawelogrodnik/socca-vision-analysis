from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_panel_audit import (  # noqa: E402
    APPROVAL_FILENAME,
    FINDINGS_FILENAME,
    READINESS_FILENAME,
    SELECTION_FILENAME,
    audit_identity_jersey_number_panels,
    build_montage_approval_template,
    render_panel_readiness_findings,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit tight jersey-number panels from a dataset manifest.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--approval", type=Path)
    args = parser.parse_args()

    dataset_doc = json.loads(args.dataset.resolve().read_text(encoding="utf-8"))
    if not isinstance(dataset_doc, dict):
        raise ValueError("dataset must contain a JSON object")
    output_root = args.output_root.resolve()
    selection_path = args.selection.resolve() if args.selection else output_root / SELECTION_FILENAME
    approval_path = args.approval.resolve() if args.approval else output_root / APPROVAL_FILENAME
    selection_doc = _read_optional_object(selection_path)
    approval_doc = _read_optional_object(approval_path)
    report = audit_identity_jersey_number_panels(
        dataset_doc,
        output_root=output_root,
        selection_doc=selection_doc,
        approval_doc=approval_doc,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(
        json.dumps(report["panel_experiment_selection"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if approval_doc is None:
        approval_path.write_text(
            json.dumps(build_montage_approval_template(report), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    (output_root / READINESS_FILENAME).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / FINDINGS_FILENAME).write_text(
        render_panel_readiness_findings(report),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"status={report['status']} final_decision={report['final_decision']}")


def _read_optional_object(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


if __name__ == "__main__":
    main()
