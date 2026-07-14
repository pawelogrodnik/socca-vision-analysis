from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import MATCHES_DIR
from app.services.pass_quality import evaluate_pass_candidates_against_gold, load_pass_goldset


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare pass_candidates.json against a manual pass goldset.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--match-id", default=None, help="Existing match id from backend/storage/matches.")
    source.add_argument("--match-dir", type=Path, default=None, help="Directory containing pass_candidates.json.")
    parser.add_argument("--goldset", type=Path, required=True, help="Manual pass goldset JSON.")
    parser.add_argument("--tolerance-frames", type=int, default=45, help="Frame tolerance for matching candidates.")
    parser.add_argument("--output", type=Path, default=None, help="Optional output JSON path.")
    args = parser.parse_args()

    match_dir = MATCHES_DIR / args.match_id if args.match_id else args.match_dir
    if match_dir is None:
        raise ValueError("Missing match directory.")
    pass_path = match_dir / "pass_candidates.json"
    pass_doc = json.loads(pass_path.read_text(encoding="utf-8"))
    goldset = load_pass_goldset(args.goldset)
    report = evaluate_pass_candidates_against_gold(
        pass_doc,
        goldset,
        tolerance_frames=max(0, int(args.tolerance_frames)),
    )
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
