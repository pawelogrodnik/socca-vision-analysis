from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_stitching_goldset import (
    build_identity_stitching_goldset,
    evaluate_identity_stitching_goldset,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a versioned player-identity stitching goldset and optional precision report.",
    )
    parser.add_argument("--reviewed-manifest", type=Path, action="append", required=True)
    parser.add_argument("--goldset-id", default="player-identity-stitching")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--prediction",
        action="append",
        default=[],
        metavar="BENCHMARK_ID=PATH",
        help="Optional identity_stitching_candidates.json used for precision evaluation.",
    )
    parser.add_argument("--evaluation-output", type=Path, default=None)
    parser.add_argument("--min-precision", type=float, default=0.95)
    parser.add_argument("--min-recall", type=float, default=0.0)
    parser.add_argument("--min-labeled", type=int, default=10)
    parser.add_argument("--max-false-positives", type=int, default=0)
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite versioned goldset: {output}")
    manifests = [_load_json(path.resolve()) for path in args.reviewed_manifest]
    goldset = build_identity_stitching_goldset(
        manifests,
        goldset_id=args.goldset_id,
        version=args.version,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(goldset, indent=2, ensure_ascii=False), encoding="utf-8")

    result: dict[str, Any] = {
        "goldset": str(output),
        "status": goldset["status"],
        "summary": goldset["summary"],
    }
    if args.prediction:
        predictions = _parse_predictions(args.prediction)
        evaluation = evaluate_identity_stitching_goldset(
            goldset,
            predictions,
            min_precision=args.min_precision,
            min_recall=args.min_recall,
            min_labeled=args.min_labeled,
            max_false_positives=args.max_false_positives,
        )
        evaluation_output = (
            args.evaluation_output.resolve()
            if args.evaluation_output
            else output.with_name(f"{output.stem}-evaluation.json")
        )
        if evaluation_output.exists():
            raise FileExistsError(f"Refusing to overwrite evaluation report: {evaluation_output}")
        evaluation_output.write_text(
            json.dumps(evaluation, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        result["evaluation"] = str(evaluation_output)
        result["evaluation_status"] = evaluation["status"]
        result["evaluation_summary"] = evaluation["summary"]
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
