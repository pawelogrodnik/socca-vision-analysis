from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_heldout_validation import (
    build_identity_jersey_number_heldout_validation,
)


DOCUMENT_FIELDS = {
    "recognizer": "recognizer_doc",
    "assignment": "assignment_doc",
    "propagation": "propagation_doc",
    "targeted_evaluation": "targeted_evaluation_doc",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the N5.8 multi-match shadow gate.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = _load(manifest_path)
    cases = [_load_case(row, manifest_path.parent) for row in manifest.get("cases") or []]
    parameters = manifest.get("parameters") or {}
    result = build_identity_jersey_number_heldout_validation(
        cases,
        minimum_distinct_source_matches=int(
            parameters.get("minimum_distinct_source_matches") or 2
        ),
        minimum_positive_multi_tracklet_propagations=int(
            parameters.get("minimum_positive_multi_tracklet_propagations") or 2
        ),
    )
    result["suite_id"] = str(manifest.get("suite_id") or manifest_path.stem)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "identity_jersey_number_heldout_validation.json", result)
    (output / "JERSEY_NUMBER_HELDOUT_REPORT.md").write_text(
        _markdown(result), encoding="utf-8"
    )
    print(json.dumps(result["summary"], indent=2))


def _load_case(row: dict[str, Any], root: Path) -> dict[str, Any]:
    case_contract = row.get("case_contract")
    if case_contract:
        return {
            "case_contract_doc": _load((root / str(case_contract)).resolve()),
        }
    case = {
        "benchmark_id": row.get("benchmark_id"),
        "source_match_key": row.get("source_match_key"),
        "held_out": bool(row.get("held_out")),
        "production_identity_unchanged": row.get("production_identity_unchanged"),
    }
    for manifest_name, document_name in DOCUMENT_FIELDS.items():
        value = row.get(manifest_name)
        if value:
            case[document_name] = _load((root / str(value)).resolve())
    return case


def _markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Jersey Number Held-out Validation",
        "",
        f"- Status: `{result['status']}`",
        f"- Gate passed: `{str(summary['activation_gate_passed']).lower()}`",
        f"- Held-out cases: `{summary['heldout_cases']}`",
        f"- Distinct source matches: `{summary['distinct_source_matches']}`",
        f"- Positive multi-tracklet propagations: `{summary['positive_multi_tracklet_propagations']}`",
        f"- Identity false assignments: `{summary['identity_false_assignments']}`",
        f"- False number reads: `{summary['false_number_reads']}`",
        f"- Unexpected propagated tracklets: `{summary['unexpected_propagated_tracklets']}`",
        "",
        "## Blockers",
        "",
    ]
    reasons = summary.get("reason_codes") or []
    lines.extend(f"- `{reason}`" for reason in reasons)
    if not reasons:
        lines.append("- none")
    lines.extend(["", "## Cases", ""])
    for case in result.get("cases") or []:
        lines.append(
            f"- `{case['benchmark_id']}`: source `{case.get('source_match_key')}`, "
            f"positive propagation `{case['positive_multi_tracklet_propagations']}`, "
            f"valid `{str(case['case_contract_valid']).lower()}`"
        )
        lines.extend(
            f"  - blocker: `{reason}`" for reason in case.get("reason_codes") or []
        )
    return "\n".join(lines) + "\n"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
