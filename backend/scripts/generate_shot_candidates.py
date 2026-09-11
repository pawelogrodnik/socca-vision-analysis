#!/usr/bin/env python3
"""Generate shadow-only shot candidates from canonical source artifacts.

This command deliberately does not read any manual goldset. It writes one
logical, non-public shadow artifact; use ``benchmark_shot_candidates.py``
separately for evaluation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.shot_candidates import (
    POLICY_VERSION,
    PREVIOUS_POLICY_VERSION,
    build_logical_shot_candidates_document,
    build_shot_candidates_document,
)


DEFAULT_GROUP_ID = "match-group-c3fbd48a-356d-44a0-a740-c630de69b527"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--output", type=Path, help="Shadow-only logical output path.")
    parser.add_argument("--policy-version", choices=(PREVIOUS_POLICY_VERSION, POLICY_VERSION), default=POLICY_VERSION)
    args = parser.parse_args()

    manifest_path = config.PUBLISHED_DIR / "match-groups" / args.group_id / "manifest.json"
    manifest = _read_json(manifest_path)
    source_documents = []
    for member in manifest.get("members") or []:
        if not isinstance(member, dict):
            continue
        source_match_id = str(member.get("source_match_id") or "")
        if not source_match_id:
            continue
        match_dir = config.MATCHES_DIR / source_match_id
        pitch = _read_json(match_dir / "pitch_config.json")
        candidate_document = build_shot_candidates_document(
            _read_json(match_dir / "event_candidates.json"),
            _read_json(match_dir / "ball_tracks.json"),
            _read_json(match_dir / "match_phase_config.json"),
            source_match_id=source_match_id,
            pitch_width_m=_dimension(pitch, "width_m", config.DEFAULT_PITCH_WIDTH_M),
            pitch_length_m=_dimension(pitch, "length_m", config.DEFAULT_PITCH_LENGTH_M),
            logical_offset_sec=float(member.get("logical_start_sec") or 0.0),
            policy_version=args.policy_version,
        )
        source_documents.append({
            "source_match_id": source_match_id,
            "logical_offset_sec": member.get("logical_start_sec"),
            "shot_candidates": candidate_document,
        })
    timeline_span = float((manifest.get("timing") or {}).get("timeline_span_sec") or 0.0)
    logical = build_logical_shot_candidates_document(
        source_documents,
        timeline_span_sec=timeline_span,
        policy_version=args.policy_version,
    )
    logical["logical_match_group_id"] = args.group_id
    logical["source_documents"] = [
        {"source_match_id": row["source_match_id"], "logical_offset_sec": row["logical_offset_sec"], "candidate_count": len(row["shot_candidates"]["candidates"])}
        for row in source_documents
    ]
    output_path = args.output or config.STORAGE_DIR / "benchmarks" / "shot-candidates-shadow-v1" / args.group_id / "shot_candidates.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(logical, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "summary": logical["summary"], "policy_version": logical["policy_version"]}, ensure_ascii=False))


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


def _dimension(pitch: dict[str, Any], key: str, default: float) -> float:
    value = pitch.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


if __name__ == "__main__":
    main()
