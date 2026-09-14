#!/usr/bin/env python3
"""Evaluate the v5 weak-boundary experiment from persisted artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from evaluation.shot_candidate_operator_review import evaluate_operator_review_four_way
from evaluation.shot_weak_boundary_shadow import evaluate_weak_boundary_shadow


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
BENCHMARK_DIR = config.STORAGE_DIR / "benchmarks" / "shot-goldset-v2"
EDITORIAL = config.STORAGE_DIR / "editorial" / "shots" / "published-merged-c33c30c0-b93d-46c7-9644-83bc4a8242f4.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2", type=Path, default=BENCHMARK_DIR / "candidates-v2-rerun.json")
    parser.add_argument("--v3", type=Path, default=BENCHMARK_DIR / "candidates-v3-rerun.json")
    parser.add_argument("--v4", type=Path, default=BENCHMARK_DIR / "candidates-v4-continuity-bridge.json")
    parser.add_argument("--v5", type=Path, default=BENCHMARK_DIR / "candidates-v5-weak-boundary.json")
    parser.add_argument("--goldset", type=Path, default=FIXTURES_DIR / "shot_goldset_v2.json")
    parser.add_argument("--v4-deep-dive", type=Path, default=BENCHMARK_DIR / "miss-deep-dive-v4.json")
    parser.add_argument("--editorial", type=Path, default=EDITORIAL)
    parser.add_argument("--output", type=Path, default=BENCHMARK_DIR / "weak-boundary-shadow-v5.json")
    args = parser.parse_args()

    v2, v3, v4, v5 = (_read_json(path) for path in (args.v2, args.v3, args.v4, args.v5))
    report = evaluate_weak_boundary_shadow(v4, v5, _read_json(args.goldset), _read_json(args.v4_deep_dive))
    report["historical_operator_review"] = evaluate_operator_review_four_way(v2, v3, v4, v5, _read_json(args.editorial))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "v5_summary": report["benchmark"]["v5"],
        "incremental": report["incremental_value_over_v4"],
    }, ensure_ascii=False))


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


if __name__ == "__main__":
    main()
