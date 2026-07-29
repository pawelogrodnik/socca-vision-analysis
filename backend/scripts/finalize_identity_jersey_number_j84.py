from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.identity_jersey_number_j84_closeout import build_j84_closeout_report


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize the frozen J8.4 jersey-number diagnostic cycle.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--r2-report", required=True, type=Path)
    parser.add_argument("--r3-report", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = build_j84_closeout_report(
        _read_json(args.dataset),
        _read_json(args.selection),
        _read_json(args.r2_report),
        _read_json(args.r3_report),
        checkpoint_path=args.checkpoint,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "final_decision": report["final_decision"]}))


if __name__ == "__main__":
    main()
