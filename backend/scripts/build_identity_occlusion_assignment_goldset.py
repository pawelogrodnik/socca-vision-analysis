from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_occlusion_assignment_goldset import (
    build_joint_assignment_goldset,
    evaluate_joint_assignment_goldset,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and evaluate a versioned joint occlusion assignment goldset.")
    parser.add_argument("--reviewed-manifest", type=Path, action="append", required=True)
    parser.add_argument("--goldset-id", default="player-identity-joint-occlusion")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prediction", action="append", default=[], metavar="BENCHMARK_ID=PATH")
    parser.add_argument("--evaluation-output", type=Path, default=None)
    parser.add_argument("--min-labeled-cases", type=int, default=8)
    parser.add_argument("--min-accuracy", type=float, default=0.90)
    parser.add_argument("--max-wrong-assignments", type=int, default=0)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite versioned goldset: {output}")
    goldset = build_joint_assignment_goldset(
        [_load_json(path.resolve()) for path in args.reviewed_manifest],
        goldset_id=args.goldset_id,
        version=args.version,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(goldset, indent=2, ensure_ascii=False), encoding="utf-8")
    result: dict[str, Any] = {"goldset": str(output), "status": goldset["status"], "summary": goldset["summary"]}
    if args.prediction:
        predictions = _parse_predictions(args.prediction)
        evaluation = evaluate_joint_assignment_goldset(
            goldset,
            predictions,
            min_labeled_cases=args.min_labeled_cases,
            min_accuracy=args.min_accuracy,
            max_wrong_assignments=args.max_wrong_assignments,
        )
        evaluation_output = (
            args.evaluation_output.resolve()
            if args.evaluation_output
            else output.with_name(f"{output.stem}-evaluation.json")
        )
        if evaluation_output.exists():
            raise FileExistsError(f"Refusing to overwrite evaluation report: {evaluation_output}")
        evaluation_output.write_text(json.dumps(evaluation, indent=2, ensure_ascii=False), encoding="utf-8")
        result.update({"evaluation": str(evaluation_output), "evaluation_status": evaluation["status"], "evaluation_summary": evaluation["summary"]})
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _parse_predictions(values: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Prediction must use BENCHMARK_ID=PATH: {value}")
        benchmark_id, raw_path = value.split("=", 1)
        benchmark_id = benchmark_id.strip()
        if not benchmark_id or benchmark_id in result:
            raise ValueError(f"Invalid or duplicate benchmark ID: {benchmark_id}")
        result[benchmark_id] = _load_json(Path(raw_path).expanduser().resolve())
    return result


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
