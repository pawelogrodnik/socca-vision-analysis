#!/usr/bin/env python3
"""Write an evaluation-only failure trace for a logical shot candidate artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from evaluation.shot_candidate_failure_analysis import analyze_shot_candidate_failures


DEFAULT_GROUP_ID = "match-group-c3fbd48a-356d-44a0-a740-c630de69b527"
DEFAULT_GOLDSET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "shot_goldset_v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--goldset", default=DEFAULT_GOLDSET, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = _read_json(config.PUBLISHED_DIR / "match-groups" / args.group_id / "manifest.json")
    sources = []
    for member in manifest.get("members") or []:
        if not isinstance(member, dict):
            continue
        source_match_id = str(member.get("source_match_id") or "")
        if not source_match_id:
            continue
        match_path = config.MATCHES_DIR / source_match_id
        pitch = _read_json(match_path / "pitch_config.json")
        sources.append({
            "source_match_id": source_match_id,
            "logical_start_sec": member.get("logical_start_sec"),
            "logical_end_sec": member.get("logical_end_sec"),
            "event_candidates": _read_json(match_path / "event_candidates.json"),
            "ball_candidates": _read_json(match_path / "ball_candidates.json"),
            "ball_tracks": _read_json(match_path / "ball_tracks.json"),
            "match_phase_config": _read_json(match_path / "match_phase_config.json"),
            "pitch_width_m": _number(pitch.get("width_m"), config.DEFAULT_PITCH_WIDTH_M),
            "pitch_length_m": _number(pitch.get("length_m"), config.DEFAULT_PITCH_LENGTH_M),
        })
    report = analyze_shot_candidate_failures(_read_json(args.candidates), _read_json(args.goldset), sources)
    report["logical_match_group_id"] = args.group_id
    output = args.output or config.STORAGE_DIR / "benchmarks" / "shot-candidates-shadow-v1" / args.group_id / "shot_candidate_failure_analysis.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": report["summary"]}, ensure_ascii=False))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


if __name__ == "__main__":
    main()
