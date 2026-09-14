#!/usr/bin/env python3
"""Evaluate candidate-policy A/B output against durable operator review labels.

This is evaluation-only tooling. Do not import it from production services.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.shot_candidate_operator_review import evaluate_operator_review_ab


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--v3", required=True, type=Path)
    parser.add_argument("--editorial", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_operator_review_ab(_read_json(args.current), _read_json(args.v3), _read_json(args.editorial))
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
