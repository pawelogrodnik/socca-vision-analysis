#!/usr/bin/env python3
"""Run ball-selection:v3-active-play-shadow from persisted artifacts only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.ball_tracking import refine_ball_tracks_against_players
from app.services.shot_candidates import build_logical_shot_candidates_document, build_shot_candidates_document
from evaluation.ball_continuity_active_play_shadow import (
    POLICY_VERSION,
    build_active_play_shadow_tracks_document,
    build_player_activity_context,
)
from evaluation.shot_candidate_benchmark import benchmark_shot_candidates
from evaluation.shot_candidate_failure_analysis import analyze_shot_candidate_failures


DEFAULT_GROUP_ID = "match-group-c3fbd48a-356d-44a0-a740-c630de69b527"
DEFAULT_GOLDSET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "shot_goldset_v1.json"
DEFAULT_OPERATOR_VALIDATION = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ball_continuity_operator_validation_v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--goldset", default=DEFAULT_GOLDSET, type=Path)
    parser.add_argument("--operator-validation", default=DEFAULT_OPERATOR_VALIDATION, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if (config.STORAGE_DIR / "benchmarks").resolve() not in output.parents:
        raise ValueError("shadow_output_must_be_under_storage_benchmarks")
    output.mkdir(parents=True, exist_ok=True)
    manifest = _read(config.PUBLISHED_DIR / "match-groups" / args.group_id / "manifest.json")
    logical_sources: list[dict[str, Any]] = []
    source_inputs: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []
    tracks_by_source: dict[str, dict[str, Any]] = {}
    for member in manifest.get("members") or []:
        source_id = str(member.get("source_match_id") or "")
        match_root = config.MATCHES_DIR / source_id
        match = _read(match_root / "match.json")
        candidates = _read(match_root / "ball_candidates.json")
        raw_tracks = _read_list(match_root / "tracks.json")
        fps = float((match.get("video") or {}).get("fps") or 0.0)
        shadow = build_active_play_shadow_tracks_document(
            candidates.get("frames") or [],
            processed_frames=[int(frame) for frame in candidates.get("processed_frames") or []],
            fps=fps,
            parameters=candidates.get("parameters") or {},
            player_context_by_frame=build_player_activity_context(raw_tracks),
        )
        refined = refine_ball_tracks_against_players(shadow, _optional(match_root / "stable_players.json"), fps=fps) or shadow
        tracks_by_source[source_id] = refined
        source_output = output / "sources" / source_id
        source_output.mkdir(parents=True, exist_ok=True)
        _write(source_output / "ball_tracks.json", refined)
        pitch, phase, events = _read(match_root / "pitch_config.json"), _read(match_root / "match_phase_config.json"), _read(match_root / "event_candidates.json")
        shot_candidates = build_shot_candidates_document(events, refined, phase, source_match_id=source_id, pitch_width_m=float(pitch.get("width_m") or config.DEFAULT_PITCH_WIDTH_M), pitch_length_m=float(pitch.get("length_m") or config.DEFAULT_PITCH_LENGTH_M), logical_offset_sec=float(member.get("logical_start_sec") or 0.0))
        logical_sources.append({"source_match_id": source_id, "logical_offset_sec": float(member.get("logical_start_sec") or 0.0), "shot_candidates": shot_candidates})
        source_inputs.append({"source_match_id": source_id, "logical_start_sec": member.get("logical_start_sec"), "logical_end_sec": member.get("logical_end_sec"), "event_candidates": events, "ball_candidates": candidates, "ball_tracks": refined, "match_phase_config": phase, "pitch_width_m": float(pitch.get("width_m") or config.DEFAULT_PITCH_WIDTH_M), "pitch_length_m": float(pitch.get("length_m") or config.DEFAULT_PITCH_LENGTH_M)})
        source_reports.append({"source_match_id": source_id, "summary": refined.get("summary") or {}, "selection_diagnostics": refined.get("selection_diagnostics") or {}})
    logical = build_logical_shot_candidates_document(logical_sources, timeline_span_sec=float((manifest.get("timing") or {}).get("timeline_span_sec") or 0.0))
    goldset = _read(args.goldset)
    benchmark = benchmark_shot_candidates(logical, goldset)
    failures = analyze_shot_candidate_failures(logical, goldset, source_inputs)
    validation = _read(args.operator_validation)
    report = {"schema_version": "ball-continuity-active-play-shadow-report:v1", "evaluation_only": True, "inference_invoked": False, "canonical_artifacts_mutated": False, "ball_selection_policy": POLICY_VERSION, "shot_benchmark_summary": benchmark["summary"], "review_budget": benchmark["review_budget"], "failure_summary": failures["summary"], "ball_metrics": _metrics(source_reports), "targeted_cases": _targeted(validation, tracks_by_source), "sources": source_reports}
    _write(output / "report.json", report)
    _write(output / "logical_shot_candidates.json", logical)
    _write(output / "shot_benchmark.json", benchmark)
    _write(output / "shot_failure_analysis.json", failures)
    print(json.dumps(report, indent=2))


def _targeted(validation: dict[str, Any], tracks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in validation.get("contacts") or []:
        source = tracks.get(str(row.get("source_match_id") or ""), {})
        frame = int(row.get("frame") or 0)
        position = next((item for item in source.get("positions") or [] if int(item.get("frame") or -1) == frame), {})
        result.append({"gold_shot_id": row.get("gold_shot_id"), "kind": row.get("kind"), "source_match_id": row.get("source_match_id"), "frame": frame, "shadow_candidate_id": position.get("candidate_id"), "shadow_source": position.get("source"), "shadow_reason": position.get("selection_reason")})
    return result


def _metrics(sources: list[dict[str, Any]]) -> dict[str, int | float]:
    keys = ("processed_frames", "detected_frames", "interpolated_frames", "unknown_frames", "interpolation_gaps", "ball_segment_count", "supported_low_confidence_frames")
    total = {key: sum(int((source.get("summary") or {}).get(key) or 0) for source in sources) for key in keys}
    total["known_ball_coverage"] = round((total["detected_frames"] + total["interpolated_frames"]) / max(total["processed_frames"], 1), 4)
    return total


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _read_list(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _optional(path: Path) -> dict[str, Any] | None:
    return _read(path) if path.exists() else None


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
