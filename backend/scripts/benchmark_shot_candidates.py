#!/usr/bin/env python3
"""Evaluate an already-generated shadow candidate artifact against the frozen goldset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.shot_candidate_benchmark import DEFAULT_TOLERANCE_SEC, benchmark_shot_candidates


DEFAULT_GOLDSET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "shot_goldset_v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--goldset", type=Path, default=DEFAULT_GOLDSET)
    parser.add_argument("--tolerance-sec", type=float, default=DEFAULT_TOLERANCE_SEC)
    parser.add_argument("--output", type=Path, help="Optional evaluation-only JSON report path.")
    args = parser.parse_args()
    report = benchmark_shot_candidates(
        _read_json(args.candidates),
        _read_json(args.goldset),
        tolerance_sec=args.tolerance_sec,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
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
