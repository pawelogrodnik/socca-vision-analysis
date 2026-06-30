from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.config import MATCHES_DIR
from app.services.analysis_quality_smoke import build_quality_smoke_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Check analysis_quality_report.json for local matches.")
    parser.add_argument("--matches-dir", type=Path, default=MATCHES_DIR)
    parser.add_argument("--match-id", action="append", default=None, help="Match id to check. Can be passed multiple times.")
    parser.add_argument("--min-score", type=float, default=70.0)
    parser.add_argument("--max-ghost-boxes", type=int, default=0)
    parser.add_argument("--max-low-visible-rate", type=float, default=0.35)
    parser.add_argument("--max-predicted-visible-boxes", type=int, default=0)
    parser.add_argument("--write", type=Path, default=None, help="Optional path where the JSON report should be written.")
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 even when checks fail.")
    args = parser.parse_args()

    report = build_quality_smoke_report(
        args.matches_dir,
        match_ids=args.match_id,
        min_score=args.min_score,
        max_ghost_boxes=args.max_ghost_boxes,
        max_low_visible_rate=args.max_low_visible_rate,
        max_predicted_visible_boxes=args.max_predicted_visible_boxes,
    )
    output = json.dumps(report, indent=2)
    print(output)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(output + "\n", encoding="utf-8")
    return 0 if args.no_fail or report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
