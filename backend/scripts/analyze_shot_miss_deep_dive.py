#!/usr/bin/env python3
"""Create a deterministic, evaluation-only v4 shot-miss deep-dive report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from app import config
from evaluation.shot_miss_deep_dive import analyze_shot_miss_deep_dive


DEFAULT_GROUP_ID = "match-group-c3fbd48a-356d-44a0-a740-c630de69b527"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
DEFAULT_BENCHMARK_DIR = config.STORAGE_DIR / "benchmarks" / "shot-goldset-v2"
DEFAULT_EDITORIAL = config.STORAGE_DIR / "editorial" / "shots" / "published-merged-c33c30c0-b93d-46c7-9644-83bc4a8242f4.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--v4-candidates", type=Path, default=DEFAULT_BENCHMARK_DIR / "candidates-v4-continuity-bridge.json")
    parser.add_argument("--v2-candidates", type=Path, default=DEFAULT_BENCHMARK_DIR / "candidates-v2-rerun.json")
    parser.add_argument("--v3-candidates", type=Path, default=DEFAULT_BENCHMARK_DIR / "candidates-v3-rerun.json")
    parser.add_argument("--goldset", type=Path, default=FIXTURES_DIR / "shot_goldset_v2.json")
    parser.add_argument("--editorial", type=Path, default=DEFAULT_EDITORIAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_BENCHMARK_DIR / "miss-deep-dive-v4.json")
    args = parser.parse_args()

    manifest = _read_json(config.PUBLISHED_DIR / "match-groups" / args.group_id / "manifest.json")
    sources = [_source(member) for member in manifest.get("members") or [] if isinstance(member, dict) and member.get("source_match_id")]
    report = analyze_shot_miss_deep_dive(
        _read_json(args.v4_candidates),
        _read_json(args.goldset),
        sources,
        v2_candidates_doc=_read_json(args.v2_candidates),
        v3_candidates_doc=_read_json(args.v3_candidates),
        editorial_doc=_read_json(args.editorial),
    )
    report["logical_match_group_id"] = args.group_id
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "miss_count": report["miss_count"], "funnel": report["funnel"]}, ensure_ascii=False))


def _source(member: Mapping[str, Any]) -> dict[str, Any]:
    source_match_id = str(member["source_match_id"])
    match_path = config.MATCHES_DIR / source_match_id
    pitch = _read_json(match_path / "pitch_config.json")
    return {
        "source_match_id": source_match_id,
        "logical_start_sec": member.get("logical_start_sec"),
        "logical_end_sec": member.get("logical_end_sec"),
        "event_candidates": _read_json(match_path / "event_candidates.json"),
        "ball_candidates": _read_json(match_path / "ball_candidates.json"),
        "ball_tracks": _read_json(match_path / "ball_tracks.json"),
        "match_phase_config": _read_json(match_path / "match_phase_config.json"),
        "pitch_width_m": _number(pitch.get("width_m"), config.DEFAULT_PITCH_WIDTH_M),
        "pitch_length_m": _number(pitch.get("length_m"), config.DEFAULT_PITCH_LENGTH_M),
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


if __name__ == "__main__":
    main()
