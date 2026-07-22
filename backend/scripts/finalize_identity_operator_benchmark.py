from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_operator_benchmark import apply_identity_operator_card_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize a P1.22 benchmark with a reviewed card manifest.")
    parser.add_argument("--benchmark-json", type=Path, required=True)
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    benchmark = _load(args.benchmark_json)
    review = _load(args.review_json)
    result = apply_identity_operator_card_audit(
        benchmark,
        review,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    (output_root / "identity_operator_benchmark.json").write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_root / "operator_card_audit.json").write_text(
        json.dumps(review, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_root / "P1_22_FINAL_REPORT.md").write_text(_markdown(result), encoding="utf-8")
    print(json.dumps(result["metrics"], indent=2, sort_keys=True))


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def _markdown(result: dict[str, Any]) -> str:
    metrics = result.get("metrics") or {}
    audit = result.get("operator_card_audit") or {}
    precision = metrics.get("audited_candidate_precision")
    return "\n".join(
        [
            "# P1.22 Final Operator Safety Audit",
            "",
            f"Status: **{audit.get('status', 'unknown')}**",
            "",
            f"- reviewed cards: {metrics.get('reviewed_card_count', 0)}",
            f"- candidate correct: {metrics.get('candidate_correct_count', 0)}",
            f"- candidate wrong: {metrics.get('candidate_wrong_count', 0)}",
            f"- unclear: {metrics.get('unclear_count', 0)}",
            f"- unreviewed: {metrics.get('unreviewed_count', 0)}",
            f"- audited precision: {precision if precision is not None else 'n/a'}",
            f"- identity false assignments: {metrics.get('false_assignment_count', 'n/a')}",
            "",
            "A wrong card is counted as an identity-level false assignment for gate calibration.",
            "The report does not mutate production identity or published statistics.",
            "",
        ]
    )


if __name__ == "__main__":
    main()
