from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_fragment_endpoint_reliability import (
    evaluate_fragment_endpoint_reliability,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate advisory endpoint quality against the fragment goldset.",
    )
    parser.add_argument("--goldset", type=Path, required=True)
    parser.add_argument("--prediction", action="append", required=True, metavar="BENCHMARK_ID=PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite endpoint reliability report: {output}")
    report = evaluate_fragment_endpoint_reliability(
        _load_json(args.goldset.expanduser().resolve()),
        _parse_predictions(args.prediction),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output), "summary": report["summary"]}, indent=2))


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
