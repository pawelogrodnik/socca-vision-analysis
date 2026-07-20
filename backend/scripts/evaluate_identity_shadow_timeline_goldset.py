from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_shadow_timeline_goldset import (  # noqa: E402
    build_shadow_timeline_goldset,
    evaluate_shadow_timeline_goldset,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate P1.4 shadow timeline against reviewed audit manifests.")
    parser.add_argument("--review", action="append", required=True, help="Path to a reviewed audit JSON.")
    parser.add_argument(
        "--prediction",
        action="append",
        required=True,
        help="BENCHMARK_ID=path/to/identity_offline_shadow_timeline.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--goldset-id", default="player-identity-shadow-timeline")
    parser.add_argument("--version", default="1.0.0-codex-provisional")
    args = parser.parse_args()

    reviews = [_load_json(Path(value)) for value in args.review]
    predictions = {
        benchmark_id: _load_json(path)
        for benchmark_id, path in (_split_mapping(value) for value in args.prediction)
    }
    goldset = build_shadow_timeline_goldset(
        reviews,
        goldset_id=args.goldset_id,
        version=args.version,
    )
    evaluation = evaluate_shadow_timeline_goldset(goldset, predictions)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    goldset_path = args.output_dir / f"{args.goldset_id}-{args.version}.json"
    evaluation_path = args.output_dir / f"{args.goldset_id}-{args.version}-evaluation.json"
    goldset_path.write_text(json.dumps(goldset, indent=2), encoding="utf-8")
    evaluation_path.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": evaluation["status"],
                "goldset": str(goldset_path.resolve()),
                "evaluation": str(evaluation_path.resolve()),
                "summary": evaluation["summary"],
            },
            indent=2,
        )
    )
    if evaluation["status"] != "passed":
        raise SystemExit(1)


def _split_mapping(value: str) -> tuple[str, Path]:
    benchmark_id, separator, path = value.partition("=")
    if not separator or not benchmark_id or not path:
        raise ValueError(f"Expected BENCHMARK_ID=path, got: {value}")
    return benchmark_id, Path(path)


def _load_json(path: Path) -> dict:
    return json.loads(path.resolve().read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
