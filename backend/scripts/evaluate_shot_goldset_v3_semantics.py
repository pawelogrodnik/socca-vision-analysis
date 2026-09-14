#!/usr/bin/env python3
"""Evaluate v2/v3/v4/v5 against frozen v3 truth plus operator semantics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from evaluation.shot_candidate_operator_review import evaluate_operator_review_four_way
from evaluation.shot_goldset_comparison import compare_shot_goldsets
from evaluation.shot_semantic_benchmark import compare_semantic_policy_increment, evaluate_semantic_shot_benchmark


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
BENCHMARK_DIR = config.STORAGE_DIR / "benchmarks" / "shot-goldset-v2"
EDITORIAL = config.STORAGE_DIR / "editorial" / "shots" / "published-merged-c33c30c0-b93d-46c7-9644-83bc4a8242f4.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2", type=Path, default=BENCHMARK_DIR / "candidates-v2-rerun.json")
    parser.add_argument("--v3", type=Path, default=BENCHMARK_DIR / "candidates-v3-rerun.json")
    parser.add_argument("--v4", type=Path, default=BENCHMARK_DIR / "candidates-v4-continuity-bridge.json")
    parser.add_argument("--v5", type=Path, default=BENCHMARK_DIR / "candidates-v5-weak-boundary.json")
    parser.add_argument("--goldset-v2", type=Path, default=FIXTURES_DIR / "shot_goldset_v2.json")
    parser.add_argument("--goldset-v3", type=Path, default=FIXTURES_DIR / "shot_goldset_v3.json")
    parser.add_argument("--audit", type=Path, default=FIXTURES_DIR / "shot_v5_weak_boundary_operator_audit_v1.json")
    parser.add_argument("--editorial", type=Path, default=EDITORIAL)
    parser.add_argument("--output", type=Path, default=BENCHMARK_DIR / "goldset-v3-semantic-benchmark.json")
    args = parser.parse_args()
    documents = {key: _read_json(path) for key, path in {"v2": args.v2, "v3": args.v3, "v4": args.v4, "v5": args.v5}.items()}
    goldset_v3 = _read_json(args.goldset_v3)
    audit = _read_json(args.audit)
    semantic_reports = {key: evaluate_semantic_shot_benchmark(document, goldset_v3, audit) for key, document in documents.items()}
    historical_operator_review = evaluate_operator_review_four_way(
        documents["v2"], documents["v3"], documents["v4"], documents["v5"], _read_json(args.editorial),
    )
    for key, semantic_report in semantic_reports.items():
        semantic_report["review_clusters"] = historical_operator_review["policies"][key]["review_clusters"]
    report = {
        "schema_version": "shot-goldset-v3-semantic-policy-evaluation:v2",
        "evaluation_only": True,
        "goldset_reconciliation": compare_shot_goldsets(_read_json(args.goldset_v2), goldset_v3),
        "policies": semantic_reports,
        "v5_vs_v4": {
            **compare_semantic_policy_increment(semantic_reports["v4"], semantic_reports["v5"]),
            "additional_raw_candidates": len(documents["v5"].get("candidates") or []) - len(documents["v4"].get("candidates") or []),
        },
        "historical_operator_review": historical_operator_review,
    }
    report["v5_vs_v4"]["additional_review_clusters"] = (
        report["historical_operator_review"]["comparisons"]["v5"]["delta_review_clusters_vs_v4"]
    )
    report["v5_vs_v4"]["hard_negative_delta"] = (
        semantic_reports["v5"]["temporal_benchmark"]["summary"]["hard_negative_hits"]
        - semantic_reports["v4"]["temporal_benchmark"]["summary"]["hard_negative_hits"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "goldset_v3_shots": len(goldset_v3.get("shots") or []),
        "v5_vs_v4": report["v5_vs_v4"],
    }, ensure_ascii=False))


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


if __name__ == "__main__":
    main()
