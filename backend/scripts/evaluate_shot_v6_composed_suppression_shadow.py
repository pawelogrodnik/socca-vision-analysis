#!/usr/bin/env python3
"""Evaluate the v6 v5-plus-v3-suppression composition from persisted artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from evaluation.shot_composed_suppression_shadow import evaluate_composed_suppression_shadow


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
BENCHMARK_DIR = config.STORAGE_DIR / "benchmarks" / "shot-goldset-v2"
EDITORIAL = config.STORAGE_DIR / "editorial" / "shots" / "published-merged-c33c30c0-b93d-46c7-9644-83bc4a8242f4.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2", type=Path, default=BENCHMARK_DIR / "candidates-v2-rerun.json")
    parser.add_argument("--v3", type=Path, default=BENCHMARK_DIR / "candidates-v3-rerun.json")
    parser.add_argument("--v4", type=Path, default=BENCHMARK_DIR / "candidates-v4-continuity-bridge.json")
    parser.add_argument("--v5", type=Path, default=BENCHMARK_DIR / "candidates-v5-weak-boundary.json")
    parser.add_argument("--v6", type=Path, default=BENCHMARK_DIR / "candidates-v6-v5-structural-suppression.json")
    parser.add_argument("--goldset", type=Path, default=FIXTURES_DIR / "shot_goldset_v3.json")
    parser.add_argument("--audit", type=Path, default=FIXTURES_DIR / "shot_v5_weak_boundary_operator_audit_v1.json")
    parser.add_argument("--editorial", type=Path, default=EDITORIAL)
    parser.add_argument("--output", type=Path, default=BENCHMARK_DIR / "v6-composed-suppression-shadow.json")
    args = parser.parse_args()
    documents = {
        key: _read_json(path)
        for key, path in {
            "v2": args.v2,
            "v3": args.v3,
            "v4": args.v4,
            "v5": args.v5,
            "v6": args.v6,
        }.items()
    }
    report = evaluate_composed_suppression_shadow(
        documents,
        _read_json(args.goldset),
        _read_json(args.audit),
        _read_json(args.editorial),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "v5_to_v6": report["v5_to_v6"],
    }, ensure_ascii=False))


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


if __name__ == "__main__":
    main()
