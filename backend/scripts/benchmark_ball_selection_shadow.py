#!/usr/bin/env python3
"""Benchmark a versioned ball selector from persisted candidates only.

This evaluation utility writes isolated shadow artifacts. It never invokes
YOLO and never writes to a canonical match directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.ball_tracking import (
    BALL_SELECTION_POLICY_V1,
    BALL_SELECTION_POLICY_V2,
    build_ball_tracks_document,
    refine_ball_tracks_against_players,
)
from app.services.shot_candidates import build_logical_shot_candidates_document, build_shot_candidates_document
from evaluation.shot_candidate_benchmark import benchmark_shot_candidates
from evaluation.shot_candidate_failure_analysis import analyze_shot_candidate_failures


DEFAULT_GROUP_ID = "match-group-c3fbd48a-356d-44a0-a740-c630de69b527"
DEFAULT_GOLDSET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "shot_goldset_v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--policy", choices=(BALL_SELECTION_POLICY_V1, BALL_SELECTION_POLICY_V2), required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--goldset", default=DEFAULT_GOLDSET, type=Path)
    parser.add_argument("--source-tracks-root", type=Path, default=None, help="Optional root containing <source_match_id>/ball_tracks.json from an isolated post-YOLO reprocess.")
    args = parser.parse_args()

    manifest = _read_json(config.PUBLISHED_DIR / "match-groups" / args.group_id / "manifest.json")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, Any]] = []
    logical_sources: list[dict[str, Any]] = []
    source_metrics: list[dict[str, Any]] = []
    for member in manifest.get("members") or []:
        if not isinstance(member, dict):
            continue
        source_match_id = str(member.get("source_match_id") or "")
        if not source_match_id:
            continue
        match_dir = config.MATCHES_DIR / source_match_id
        match = _read_json(match_dir / "match.json")
        candidates = _read_json(match_dir / "ball_candidates.json")
        isolated_tracks_path = (args.source_tracks_root / source_match_id / "ball_tracks.json") if args.source_tracks_root else None
        if isolated_tracks_path and isolated_tracks_path.exists():
            refined_tracks = _read_json(isolated_tracks_path)
            actual_policy = str(_mapping(refined_tracks.get("parameters")).get("ball_selection_policy") or BALL_SELECTION_POLICY_V1)
            if actual_policy != args.policy:
                raise ValueError(
                    f"Selection policy mismatch for {source_match_id}: expected {args.policy}, got {actual_policy}."
                )
        else:
            parameters = {**_mapping(candidates.get("parameters")), "ball_selection_policy": args.policy}
            tracks = build_ball_tracks_document(
                list(candidates.get("frames") or []),
                processed_frames=[int(frame) for frame in candidates.get("processed_frames") or []],
                fps=_number(_mapping(match.get("video")).get("fps"), 0.0),
                parameters=parameters,
            )
            stable_players = _optional_json(match_dir / "stable_players.json")
            refined_tracks = refine_ball_tracks_against_players(
                tracks,
                stable_players,
                fps=_number(_mapping(match.get("video")).get("fps"), 0.0),
            ) or tracks
        source_output = output_dir / "sources" / source_match_id
        source_output.mkdir(parents=True, exist_ok=True)
        _write_json(source_output / "ball_tracks.json", refined_tracks)

        pitch = _read_json(match_dir / "pitch_config.json")
        phase = _read_json(match_dir / "match_phase_config.json")
        events = _read_json(match_dir / "event_candidates.json")
        offset = _number(member.get("logical_start_sec"), 0.0)
        source_candidates = build_shot_candidates_document(
            events,
            refined_tracks,
            phase,
            source_match_id=source_match_id,
            pitch_width_m=_number(pitch.get("width_m"), config.DEFAULT_PITCH_WIDTH_M),
            pitch_length_m=_number(pitch.get("length_m"), config.DEFAULT_PITCH_LENGTH_M),
            logical_offset_sec=offset,
        )
        logical_sources.append({"source_match_id": source_match_id, "logical_offset_sec": offset, "shot_candidates": source_candidates})
        sources.append(
            {
                "source_match_id": source_match_id,
                "logical_start_sec": member.get("logical_start_sec"),
                "logical_end_sec": member.get("logical_end_sec"),
                "event_candidates": events,
                "ball_candidates": candidates,
                "ball_tracks": refined_tracks,
                "match_phase_config": phase,
                "pitch_width_m": _number(pitch.get("width_m"), config.DEFAULT_PITCH_WIDTH_M),
                "pitch_length_m": _number(pitch.get("length_m"), config.DEFAULT_PITCH_LENGTH_M),
            }
        )
        source_metrics.append(
            {
                "source_match_id": source_match_id,
                "summary": refined_tracks.get("summary") or {},
                "selection_diagnostics": refined_tracks.get("selection_diagnostics") or {},
            }
        )

    logical = build_logical_shot_candidates_document(
        logical_sources,
        timeline_span_sec=_number(_mapping(manifest.get("timing")).get("timeline_span_sec"), 0.0),
    )
    logical["logical_match_group_id"] = args.group_id
    goldset = _read_json(args.goldset)
    benchmark = benchmark_shot_candidates(logical, goldset)
    failures = analyze_shot_candidate_failures(logical, goldset, sources)
    report = {
        "schema_version": "ball-selection-shadow-benchmark:v1",
        "evaluation_only": True,
        "inference_invoked": False,
        "logical_match_group_id": args.group_id,
        "ball_selection_policy": args.policy,
        "source_metrics": source_metrics,
        "ball_metrics": _aggregate_ball_metrics(source_metrics),
        "shot_benchmark_summary": benchmark.get("summary") or {},
        "review_budget": benchmark.get("review_budget") or {},
        "outcome_recall": benchmark.get("outcome_recall") or {},
        "team_recall": benchmark.get("team_recall") or {},
        "failure_summary": failures.get("summary") or {},
    }
    _write_json(output_dir / "ball_tracks_shadow_report.json", report)
    _write_json(output_dir / "shot_candidates.json", logical)
    _write_json(output_dir / "shot_benchmark.json", benchmark)
    _write_json(output_dir / "shot_failure_analysis.json", failures)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _aggregate_ball_metrics(source_metrics: list[dict[str, Any]]) -> dict[str, int]:
    summary_keys = ("processed_frames", "detected_frames", "interpolated_frames", "unknown_frames", "interpolation_gaps", "ball_segment_count", "player_overlap_suppressed_detections")
    selection_keys = ("selected_low_confidence_rows", "multi_candidate_frame_decisions", "active_ball_switches", "high_confidence_restarts")
    aggregate = {key: 0 for key in (*summary_keys, *selection_keys)}
    for source in source_metrics:
        summary = _mapping(source.get("summary"))
        selection = _mapping(source.get("selection_diagnostics"))
        for key in summary_keys:
            aggregate[key] += int(_number(summary.get(key), 0.0))
        for key in selection_keys:
            aggregate[key] += int(_number(selection.get(key), 0.0))
    processed = max(aggregate["processed_frames"], 1)
    aggregate["detected_coverage"] = round(aggregate["detected_frames"] / processed, 4)
    aggregate["interpolated_coverage"] = round(aggregate["interpolated_frames"] / processed, 4)
    aggregate["unknown_coverage"] = round(aggregate["unknown_frames"] / processed, 4)
    aggregate["known_coverage"] = round((aggregate["detected_frames"] + aggregate["interpolated_frames"]) / processed, 4)
    return aggregate


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _optional_json(path: Path) -> dict[str, Any] | None:
    return _read_json(path) if path.exists() else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


if __name__ == "__main__":
    main()
