#!/usr/bin/env python3
"""Compare v1 and active-play v3 from identical persisted artifacts only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from app import config
from app.services.ball_tracking import (
    BALL_SELECTION_POLICY_V1,
    build_ball_tracks_document,
    refine_ball_tracks_against_players,
)
from app.services.shot_candidates import (
    MIN_BALL_CONFIDENCE,
    build_logical_shot_candidates_document,
    build_shot_candidates_document,
)
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
LOCAL_DIVERGENCE_WINDOW_FRAMES = 120


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
    artifacts = _source_artifacts(manifest)
    goldset = _read(args.goldset)
    validation = _read(args.operator_validation)
    runs = {
        "v1": _run_policy(BALL_SELECTION_POLICY_V1, artifacts, manifest, goldset, output / "v1"),
        "v3": _run_policy(POLICY_VERSION, artifacts, manifest, goldset, output / "v3"),
    }
    report = {
        "schema_version": "ball-continuity-active-play-shadow-report:v2",
        "evaluation_only": True,
        "inference_invoked": False,
        "canonical_artifacts_mutated": False,
        "input_contract": "same_persisted_candidates_tracks_events_phase_pitch_and_manifest_for_v1_and_v3",
        "runs": {name: _run_summary(run) for name, run in runs.items()},
        "targeted_cases": _targeted(validation, runs),
    }
    _write(output / "report.json", report)
    print(json.dumps(report, indent=2))


def _source_artifacts(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for member in manifest.get("members") or []:
        if not isinstance(member, Mapping):
            continue
        source_id = str(member.get("source_match_id") or "")
        if not source_id:
            continue
        match_root = config.MATCHES_DIR / source_id
        match = _read(match_root / "match.json")
        artifacts.append(
            {
                "member": dict(member),
                "source_match_id": source_id,
                "fps": float((match.get("video") or {}).get("fps") or 0.0),
                "ball_candidates": _read(match_root / "ball_candidates.json"),
                "raw_tracks": _read_list(match_root / "tracks.json"),
                "stable_players": _optional(match_root / "stable_players.json"),
                "pitch": _read(match_root / "pitch_config.json"),
                "phase": _read(match_root / "match_phase_config.json"),
                "events": _read(match_root / "event_candidates.json"),
            }
        )
    return artifacts


def _run_policy(
    policy: str,
    artifacts: list[dict[str, Any]],
    manifest: Mapping[str, Any],
    goldset: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    logical_sources: list[dict[str, Any]] = []
    source_inputs: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []
    tracks_by_source: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        source_id = str(artifact["source_match_id"])
        member = artifact["member"]
        candidates = artifact["ball_candidates"]
        fps = float(artifact["fps"])
        if policy == BALL_SELECTION_POLICY_V1:
            tracks = build_ball_tracks_document(
                candidates.get("frames") or [],
                processed_frames=[int(frame) for frame in candidates.get("processed_frames") or []],
                fps=fps,
                parameters={**dict(candidates.get("parameters") or {}), "ball_selection_policy": policy},
            )
        else:
            tracks = build_active_play_shadow_tracks_document(
                candidates.get("frames") or [],
                processed_frames=[int(frame) for frame in candidates.get("processed_frames") or []],
                fps=fps,
                parameters=candidates.get("parameters") or {},
                player_context_by_frame=build_player_activity_context(artifact["raw_tracks"]),
            )
        refined = refine_ball_tracks_against_players(tracks, artifact["stable_players"], fps=fps) or tracks
        tracks_by_source[source_id] = refined
        source_output = output / "sources" / source_id
        source_output.mkdir(parents=True, exist_ok=True)
        _write(source_output / "ball_tracks.json", refined)

        pitch = artifact["pitch"]
        shot_candidates = build_shot_candidates_document(
            artifact["events"],
            refined,
            artifact["phase"],
            source_match_id=source_id,
            pitch_width_m=float(pitch.get("width_m") or config.DEFAULT_PITCH_WIDTH_M),
            pitch_length_m=float(pitch.get("length_m") or config.DEFAULT_PITCH_LENGTH_M),
            logical_offset_sec=float(member.get("logical_start_sec") or 0.0),
        )
        logical_sources.append(
            {
                "source_match_id": source_id,
                "logical_offset_sec": float(member.get("logical_start_sec") or 0.0),
                "shot_candidates": shot_candidates,
            }
        )
        source_inputs.append(
            {
                "source_match_id": source_id,
                "logical_start_sec": member.get("logical_start_sec"),
                "logical_end_sec": member.get("logical_end_sec"),
                "event_candidates": artifact["events"],
                "ball_candidates": candidates,
                "ball_tracks": refined,
                "match_phase_config": artifact["phase"],
                "pitch_width_m": float(pitch.get("width_m") or config.DEFAULT_PITCH_WIDTH_M),
                "pitch_length_m": float(pitch.get("length_m") or config.DEFAULT_PITCH_LENGTH_M),
            }
        )
        source_reports.append(
            {
                "source_match_id": source_id,
                "summary": refined.get("summary") or {},
                "selection_diagnostics": refined.get("selection_diagnostics") or {},
            }
        )

    logical = build_logical_shot_candidates_document(
        logical_sources,
        timeline_span_sec=float((manifest.get("timing") or {}).get("timeline_span_sec") or 0.0),
    )
    benchmark = benchmark_shot_candidates(logical, goldset)
    failures = analyze_shot_candidate_failures(logical, goldset, source_inputs)
    _write(output / "logical_shot_candidates.json", logical)
    _write(output / "shot_benchmark.json", benchmark)
    _write(output / "shot_failure_analysis.json", failures)
    return {
        "ball_selection_policy": policy,
        "tracks_by_source": tracks_by_source,
        "source_reports": source_reports,
        "benchmark": benchmark,
        "failures": failures,
    }


def _run_summary(run: Mapping[str, Any]) -> dict[str, Any]:
    ball_metrics = _metrics(run.get("source_reports") or [])
    ball_metrics["supported_low_confidence_rows_used_as_trusted_shot_trajectory"] = _supported_low_confidence_used_as_trusted_shot_trajectory(
        run.get("tracks_by_source") or {}
    )
    ball_metrics["shot_trusted_min_confidence"] = MIN_BALL_CONFIDENCE
    benchmark = run["benchmark"]
    return {
        "ball_selection_policy": run["ball_selection_policy"],
        "shot_policy": "shot-candidate-shadow:v2",
        "shot_benchmark_summary": benchmark["summary"],
        "review_budget": benchmark["review_budget"],
        "failure_summary": run["failures"]["summary"],
        "ball_metrics": ball_metrics,
    }


def _supported_low_confidence_used_as_trusted_shot_trajectory(tracks_by_source: Mapping[str, Any]) -> int:
    """Count rows eligible for the unchanged shot-trust contract.

    Supported rows preserve their detector confidence, so a row labelled as
    supported-low-confidence cannot satisfy the existing minimum of 0.35.
    Keeping this computed field makes that separation visible in the A/B
    output rather than treating supported evidence as silently trusted.
    """

    return sum(
        1
        for tracks in tracks_by_source.values()
        if isinstance(tracks, Mapping)
        for row in tracks.get("positions") or []
        if isinstance(row, Mapping)
        and bool(row.get("supported_low_confidence"))
        and str(row.get("source") or "") in {"detected", "interpolated"}
        and float(row.get("confidence") or 0.0) >= MIN_BALL_CONFIDENCE
    )


def _targeted(validation: Mapping[str, Any], runs: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for reference in validation.get("references") or []:
        if not isinstance(reference, Mapping):
            continue
        source_id = str(reference.get("source_match_id") or "")
        frame = int(reference.get("frame") or 0)
        shot_id = str(reference.get("gold_shot_id") or "")
        v1_position = _position(runs["v1"].get("tracks_by_source") or {}, source_id, frame)
        v3_position = _position(runs["v3"].get("tracks_by_source") or {}, source_id, frame)
        row: dict[str, Any] = {
            "gold_shot_id": shot_id,
            "kind": reference.get("kind"),
            "source_match_id": source_id,
            "operator_reference": {"frame": frame, "time_sec": reference.get("reference_time_sec")},
            "v1": {**v1_position, "shot_matched": _shot_matched(runs["v1"], shot_id)},
            "v3": {**v3_position, "shot_matched": _shot_matched(runs["v3"], shot_id)},
            "first_v1_v3_divergence": _first_divergence(
                runs["v1"].get("tracks_by_source") or {},
                runs["v3"].get("tracks_by_source") or {},
                source_id,
                frame,
            ),
        }
        expected = reference.get("expected_candidate_id")
        if isinstance(expected, str) and expected:
            row["expected_candidate_id"] = expected
            row["v1_matches_operator_truth"] = v1_position["candidate_id"] == expected
            row["v3_matches_operator_truth"] = v3_position["candidate_id"] == expected
        result.append(row)
    return result


def _position(tracks_by_source: Mapping[str, Any], source_id: str, frame: int) -> dict[str, Any]:
    tracks = tracks_by_source.get(source_id)
    if not isinstance(tracks, Mapping):
        return {"candidate_id": None, "source": "unknown", "confidence": None, "selection_reason": None}
    row = next((item for item in tracks.get("positions") or [] if isinstance(item, Mapping) and int(item.get("frame") or -1) == frame), None)
    if not isinstance(row, Mapping):
        return {"candidate_id": None, "source": "unknown", "confidence": None, "selection_reason": None}
    return {
        "candidate_id": row.get("candidate_id"),
        "source": row.get("source"),
        "confidence": row.get("confidence"),
        "selection_reason": row.get("selection_reason"),
    }


def _shot_matched(run: Mapping[str, Any], gold_shot_id: str) -> bool:
    return any(str(row.get("gold_shot_id") or "") == gold_shot_id for row in run["benchmark"].get("matches") or [])


def _first_divergence(
    v1_tracks: Mapping[str, Any],
    v3_tracks: Mapping[str, Any],
    source_id: str,
    reference_frame: int,
) -> dict[str, Any] | None:
    v1 = v1_tracks.get(source_id)
    v3 = v3_tracks.get(source_id)
    if not isinstance(v1, Mapping) or not isinstance(v3, Mapping):
        return None
    v1_rows = {int(row.get("frame") or -1): row for row in v1.get("positions") or [] if isinstance(row, Mapping)}
    v3_rows = {int(row.get("frame") or -1): row for row in v3.get("positions") or [] if isinstance(row, Mapping)}
    lower, upper = reference_frame - LOCAL_DIVERGENCE_WINDOW_FRAMES, reference_frame + LOCAL_DIVERGENCE_WINDOW_FRAMES
    for frame in sorted(frame for frame in set(v1_rows) | set(v3_rows) if lower <= frame <= upper):
        left, right = v1_rows.get(frame, {}), v3_rows.get(frame, {})
        if (left.get("candidate_id"), left.get("source")) != (right.get("candidate_id"), right.get("source")):
            return {
                "search_window_frames": [lower, upper],
                "frame": frame,
                "time_sec": right.get("time_sec") or left.get("time_sec"),
                "v1_candidate_id": left.get("candidate_id"),
                "v3_candidate_id": right.get("candidate_id"),
                "v1_source": left.get("source"),
                "v3_source": right.get("source"),
            }
    return None


def _metrics(sources: list[Mapping[str, Any]]) -> dict[str, int | float]:
    keys = (
        "processed_frames",
        "detected_frames",
        "interpolated_frames",
        "unknown_frames",
        "interpolation_gaps",
        "ball_segment_count",
        "supported_low_confidence_frames",
    )
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


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
