#!/usr/bin/env python3
"""Freeze current canonical Shot Review as an evaluation-only v3 goldset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.shot_goldset_v3 import build_shot_goldset_v3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editorial", required=True, type=Path)
    parser.add_argument("--public-report", required=True, type=Path)
    parser.add_argument("--prior-goldset", required=True, type=Path, help="Used only to carry forward the frozen hard-negative list.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prior = _read_json(args.prior_goldset)
    frozen = build_shot_goldset_v3(
        _read_json(args.editorial),
        _read_json(args.public_report),
        hard_negatives=[row for row in prior.get("hard_negatives") or [] if isinstance(row, dict)],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "shots": len(frozen["shots"]), "hard_negatives": len(frozen["hard_negatives"])}, ensure_ascii=False))


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


if __name__ == "__main__":
    main()
