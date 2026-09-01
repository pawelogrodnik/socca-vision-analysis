from __future__ import annotations

"""Export compact, Git-safe calibration artifacts from live Review audit logs."""

import argparse
from pathlib import Path

from app.services.identity_reviewed_decision_audit import (
    export_review_decision_calibration_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_ids", nargs="+", help="Match IDs to export")
    parser.add_argument(
        "--matches-root", type=Path, default=Path("storage/matches"),
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("decision-benchmarks"),
    )
    args = parser.parse_args()
    for match_id in args.match_ids:
        result = export_review_decision_calibration_artifacts(
            args.matches_root / match_id, args.output_root,
        )
        print(
            f"{result['match_id']}: {result['calibration_samples']} calibration samples "
            f"-> {result['output_path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
