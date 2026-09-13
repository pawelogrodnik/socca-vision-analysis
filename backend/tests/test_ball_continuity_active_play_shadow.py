from __future__ import annotations

import json
import importlib.util
from pathlib import Path

from evaluation.ball_continuity_active_play_shadow import (
    _active_play_score,
    build_active_play_shadow_tracks_document,
    build_player_activity_context,
    select_active_play_shadow,
)


SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_ball_continuity_active_play_shadow.py"
SPEC = importlib.util.spec_from_file_location("active_play_shadow_benchmark", SCRIPT)
assert SPEC and SPEC.loader
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


def row(identifier: str, frame: int, x: float, confidence: float) -> dict:
    return {"candidate_id": identifier, "frame": frame, "time_sec": frame / 30, "position_m": [x, 0.0], "position_px": [x, 0.0], "bbox_xyxy": [x, 0, x + 1, 1], "confidence": confidence, "source": "detected"}


def test_temporally_supported_low_confidence_is_explicitly_selected() -> None:
    frames = [
        {"frame": 0, "candidates": [row("a0", 0, 0, .8)]},
        {"frame": 1, "candidates": [row("a1", 1, .2, .18)]},
        {"frame": 2, "candidates": [row("a2", 2, .4, .8)]},
        {"frame": 3, "candidates": [row("a3", 3, .6, .8)]},
    ]
    selected, diagnostics = select_active_play_shadow(frames, fps=30)
    assert selected[1]["supported_low_confidence"] is True
    assert diagnostics["supported_low_confidence_rows"] == 1
    document = build_active_play_shadow_tracks_document(frames, processed_frames=[0, 1, 2, 3], fps=30, parameters={})
    assert document["positions"][1]["supported_low_confidence"] is True


def test_isolated_low_confidence_false_positive_is_not_selected() -> None:
    selected, _ = select_active_play_shadow([
        {"frame": 0, "candidates": [row("a0", 0, 0, .8)]},
        {"frame": 1, "candidates": [row("noise", 1, .2, .18)]},
        {"frame": 2, "candidates": []},
    ], fps=30)
    assert 1 not in selected


def test_supported_low_confidence_row_does_not_replace_trusted_continuity_anchor() -> None:
    selected, _ = select_active_play_shadow([
        {"frame": 0, "candidates": [row("trusted", 0, 0, .8)]},
        {"frame": 1, "candidates": [row("supported-off-path", 1, 1.3, .2)]},
        {
            "frame": 2,
            "candidates": [
                row("strong-correct", 2, -.5, .82),
                row("weaker-near-low", 2, 1.0, .4),
            ],
        },
        {"frame": 3, "candidates": [row("future-support", 3, -.7, .8)]},
    ], fps=30)

    assert selected[1]["supported_low_confidence"] is True
    assert selected[2]["candidate_id"] == "strong-correct"


def test_stronger_plausible_ball_beats_geometrically_tempting_low_confidence() -> None:
    selected, _ = select_active_play_shadow([
        {"frame": 0, "candidates": [row("start", 0, 0, .8)]},
        {"frame": 1, "candidates": [row("low-perfect", 1, .2, .2), row("strong", 1, .8, .8)]},
        {"frame": 2, "candidates": [row("future", 2, 1.2, .8)]},
    ], fps=30)
    assert selected[1]["candidate_id"] == "strong"


def test_deflection_reacquires_strong_supported_candidate() -> None:
    selected, _ = select_active_play_shadow([
        {"frame": 0, "candidates": [row("start", 0, 0, .8)]},
        {"frame": 1, "candidates": [row("deflection", 1, 1.5, .8)]},
        {"frame": 2, "candidates": [row("deflection-2", 2, 1.7, .8)]},
        {"frame": 3, "candidates": [row("deflection-3", 3, 1.9, .8)]},
    ], fps=30)
    assert selected[1]["candidate_id"] == "deflection"


def test_impossible_jump_is_rejected() -> None:
    selected, _ = select_active_play_shadow([
        {"frame": 0, "candidates": [row("start", 0, 0, .8)]},
        {"frame": 1, "candidates": [row("nearby", 1, .2, .8)]},
        {"frame": 2, "candidates": [row("nearby-2", 2, .4, .8)]},
        {"frame": 3, "candidates": [row("nearby-3", 3, .6, .8)]},
        {"frame": 4, "candidates": [row("teleport", 4, 100, .9)]},
    ], fps=30)
    assert 4 not in selected


def test_short_gap_is_interpolated_but_not_extended_beyond_bound() -> None:
    document = build_active_play_shadow_tracks_document(
        [{"frame": 0, "candidates": [row("a", 0, 0, .8)]}, {"frame": 6, "candidates": []}, {"frame": 12, "candidates": [row("b", 12, 1, .8)]}],
        processed_frames=[0, 6, 12], fps=30, parameters={},
    )
    assert [item["source"] for item in document["positions"]] == ["detected", "interpolated", "detected"]


def test_stationary_restart_with_nearby_players_remains_eligible() -> None:
    selected, _ = select_active_play_shadow(
        [{"frame": 0, "candidates": [row("restart", 0, 1, .8)]}, {"frame": 1, "candidates": [row("restart", 1, 1, .8)]}],
        fps=30, player_context_by_frame={1: [{"position_m": [1.2, 0], "previous_position_m": [1.4, 0]}]},
    )
    assert selected[1]["candidate_id"] == "restart"


def test_more_players_converging_increases_active_play_score() -> None:
    candidate_row = row("ball", 3, 3, .7)
    one = _active_play_score(candidate_row, [{"position_m": [3.5, 0], "previous_position_m": [4.0, 0]}])
    many = _active_play_score(candidate_row, [{"position_m": [3.5, 0], "previous_position_m": [4.0, 0]}, {"position_m": [3.7, 0], "previous_position_m": [4.4, 0]}])
    assert many > one


def test_tie_breaking_is_deterministic() -> None:
    frames = [{"frame": 0, "candidates": [row("start", 0, 0, .8)]}, {"frame": 1, "candidates": [row("z", 1, .2, .8), row("a", 1, .2, .8)]}]
    first, _ = select_active_play_shadow(frames, fps=30)
    second, _ = select_active_play_shadow(list(reversed(frames)), fps=30)
    assert first[1]["candidate_id"] == second[1]["candidate_id"] == "a"


def test_player_context_uses_persisted_track_positions_only() -> None:
    context = build_player_activity_context([{"positions": [{"frame": 4, "pitch_m": [1, 2]}, {"frame": 5, "pitch_m": [2, 2]}]}])
    assert context[5][0]["previous_position_m"] == [1.0, 2.0]


def test_operator_validation_fixture_is_evaluation_only_and_not_runtime_imported() -> None:
    fixture = Path(__file__).with_name("fixtures") / "ball_continuity_operator_validation_v1.json"
    validation = json.loads(fixture.read_text(encoding="utf-8"))
    assert validation["operator_labels_are_algorithm_inputs"] is False
    active_ball = next(row for row in validation["references"] if row["gold_shot_id"] == "shot-028")
    assert active_ball["expected_candidate_id"] == "ball-f005038-c00"
    runtime = (Path(__file__).parents[1] / "app").rglob("*.py")
    assert all("ball_continuity_operator_validation" not in path.read_text(encoding="utf-8") for path in runtime)
    assert all("ball_continuity_active_play_shadow" not in path.read_text(encoding="utf-8") for path in runtime)


def test_active_play_context_beats_stationary_foreign_ball_and_keeps_supported_restart() -> None:
    context = {10: [{"position_m": [5.1, 0], "previous_position_m": [5.8, 0]}, {"position_m": [5.3, 0], "previous_position_m": [6.0, 0]}]}
    selected, _ = select_active_play_shadow([
        {"frame": 0, "candidates": [row("start", 0, 0, .8)]},
        {"frame": 10, "candidates": [row("stationary-foreign", 10, .1, .85), row("active-play", 10, 5, .72)]},
        {"frame": 20, "candidates": [row("active-play-2", 20, 5.2, .72)]},
        {"frame": 30, "candidates": [row("active-play-3", 30, 5.4, .72)]},
    ], fps=30, player_context_by_frame=context)
    assert selected[10]["candidate_id"] == "active-play"


def test_targeted_benchmark_compares_both_policies_against_operator_truth() -> None:
    validation = {
        "references": [
            {
                "gold_shot_id": "shot-028",
                "source_match_id": "source-a",
                "frame": 10,
                "reference_time_sec": 1 / 3,
                "kind": "active_play_identity",
                "expected_candidate_id": "active-ball",
            }
        ]
    }
    runs = {
        "v1": {
            "tracks_by_source": {"source-a": {"positions": [{"frame": 10, "time_sec": 1 / 3, "candidate_id": "foreign-ball", "source": "detected", "confidence": .8}]}},
            "benchmark": {"matches": []},
        },
        "v3": {
            "tracks_by_source": {"source-a": {"positions": [{"frame": 10, "time_sec": 1 / 3, "candidate_id": "active-ball", "source": "detected", "confidence": .7, "selection_reason": "active_play_continuation"}]}},
            "benchmark": {"matches": [{"gold_shot_id": "shot-028"}]},
        },
    }

    targeted = BENCHMARK._targeted(validation, runs)

    assert targeted[0]["expected_candidate_id"] == "active-ball"
    assert targeted[0]["v1_matches_operator_truth"] is False
    assert targeted[0]["v3_matches_operator_truth"] is True
    assert targeted[0]["v1"]["shot_matched"] is False
    assert targeted[0]["v3"]["shot_matched"] is True


def test_supported_low_confidence_is_not_counted_as_trusted_shot_evidence() -> None:
    tracks = {
        "source-a": {
            "positions": [
                {
                    "supported_low_confidence": True,
                    "source": "detected",
                    "confidence": .2,
                }
            ]
        }
    }

    assert BENCHMARK._supported_low_confidence_used_as_trusted_shot_trajectory(tracks) == 0
