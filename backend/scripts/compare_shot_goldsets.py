#!/usr/bin/env python3
"""Compare the frozen v1 anchors with the canonical v2 evaluation goldset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.shot_goldset_comparison import DEFAULT_MATCH_TOLERANCE_SEC, compare_shot_goldsets


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1", type=Path, default=FIXTURES_DIR / "shot_goldset_v1.json")
    parser.add_argument("--v2", type=Path, default=FIXTURES_DIR / "shot_goldset_v2.json")
    parser.add_argument("--tolerance-sec", type=float, default=DEFAULT_MATCH_TOLERANCE_SEC)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare_shot_goldsets(_read_json(args.v1), _read_json(args.v2), tolerance_sec=args.tolerance_sec)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


if __name__ == "__main__":
    main()
