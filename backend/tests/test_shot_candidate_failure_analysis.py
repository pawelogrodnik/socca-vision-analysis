from __future__ import annotations

import unittest

from evaluation.shot_candidate_failure_analysis import (
    _has_same_frame_selection_divergence,
    _launch_failure_category,
    _raw_ball_evidence,
    analyze_shot_candidate_failures,
)


def contact(event_id: str, end_time_sec: float, *, team: str) -> dict:
    return {
        "event_id": event_id,
        "event_type": "ball_contact",
        "review_status": "accepted",
        "end_time_sec": end_time_sec,
        "end_frame": round(end_time_sec * 30),
        "team_label": "A" if team == "Corgi" else "B",
        "team_id": f"team-{team}",
        "team_name": team,
        "stable_player_id": f"{team}-player",
    }


def source(events: list[dict], positions: list[dict], frames: list[dict]) -> dict:
    return {
        "source_match_id": "m1",
        "logical_start_sec": 0.0,
        "logical_end_sec": 100.0,
        "event_candidates": {"events": events},
        "ball_tracks": {"positions": positions},
        "ball_candidates": {"frames": frames},
        "match_phase_config": {
            "periods": [{
                "period_id": "all",
                "start_time_sec": 0.0,
                "end_time_sec": 100.0,
                "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
                "direction_source": "test",
            }],
        },
        "pitch_width_m": 30.0,
        "pitch_length_m": 47.4,
    }


def goldset(team: str = "Verisk") -> dict:
    return {
        "schema_version": "shot-goldset:v1",
        "shots": [{"id": "gold-1", "timestamp_sec": 10.0, "timestamp_display": "00:10~", "team": team, "outcome": "blocked"}],
        "hard_negatives": [],
    }


def candidates_document() -> dict:
    return {"policy_version": "shot-candidate-shadow:v1", "timeline_span_sec": 100.0, "candidates": []}


class ShotCandidateFailureAnalysisTests(unittest.TestCase):
    def test_correct_second_contact_is_evaluated_not_hidden_by_wrong_nearest_contact(self) -> None:
        report = analyze_shot_candidate_failures(
            candidates_document(),
            goldset(),
            [source(
                [contact("wrong-nearest", 9.9, team="Corgi"), contact("correct-second", 10.2, team="Verisk")],
                [
                    {"frame": 297, "time_sec": 9.9, "position_m": [15.0, 20.0], "source": "detected", "confidence": 0.9, "candidate_id": "wrong"},
                    {"frame": 306, "time_sec": 10.2, "position_m": [15.0, 30.0], "source": "detected", "confidence": 0.9, "candidate_id": "correct"},
                    {"frame": 307, "time_sec": 10.233, "position_m": None, "source": "unknown", "confidence": 0.0},
                ],
                [],
            )],
        )

        trace = report["gold_shot_traces"][0]
        self.assertEqual([row["contact"]["nearest_contact_event_id"] for row in trace["contact_paths"]], ["wrong-nearest", "correct-second"])
        self.assertEqual(trace["classification"]["primary_category"], "BALL_CONTINUITY_FAILURE")

    def test_unrelated_accepted_ball_outside_launch_neighborhood_is_not_detector_evidence(self) -> None:
        evidence = _raw_ball_evidence(
            {"frames": [{
                "frame": 333,
                "time_sec": 11.1,
                "raw_predictions": 1,
                "candidates": [{"candidate_id": "unrelated", "frame": 333, "time_sec": 11.1, "confidence": 0.9}],
                "rejected_candidates": [],
            }]},
            10.0,
            [],
        )

        self.assertEqual(evidence["raw_prediction_count"], 0)
        self.assertEqual(_launch_failure_category(evidence)["primary_category"], "RAW_DETECTOR_MISS")

    def test_low_confidence_accepted_ball_selected_by_canonical_track_is_not_detector_miss(self) -> None:
        evidence = _raw_ball_evidence(
            {"frames": [{
                "frame": 300,
                "time_sec": 10.0,
                "raw_predictions": 1,
                "candidates": [{"candidate_id": "c00", "frame": 300, "time_sec": 10.0, "confidence": 0.20}],
                "rejected_candidates": [],
            }]},
            10.0,
            [{"frame": 300, "time_sec": 10.0, "position_m": [25.0, 35.0], "source": "detected", "confidence": 0.20, "candidate_id": "c00"}],
        )

        classification = _launch_failure_category(evidence)
        self.assertEqual(classification["primary_category"], "BALL_CONTINUITY_FAILURE")
        self.assertEqual(classification["diagnostic_reason"], "low_confidence_launch_evidence")

    def test_low_confidence_candidate_with_other_same_frame_selection_is_selection_failure(self) -> None:
        evidence = _raw_ball_evidence(
            {"frames": [{
                "frame": 300,
                "time_sec": 10.0,
                "raw_predictions": 2,
                "candidates": [
                    {"candidate_id": "c00", "frame": 300, "time_sec": 10.0, "confidence": 0.20},
                    {"candidate_id": "c01", "frame": 300, "time_sec": 10.0, "confidence": 0.80},
                ],
                "rejected_candidates": [],
            }]},
            10.0,
            [{"frame": 300, "time_sec": 10.0, "position_m": [25.0, 35.0], "source": "detected", "confidence": 0.80, "candidate_id": "c01"}],
        )

        self.assertEqual(_launch_failure_category(evidence)["primary_category"], "BALL_TRACK_SELECTION_FAILURE")

    def test_no_raw_prediction_near_launch_is_detector_miss(self) -> None:
        evidence = _raw_ball_evidence({"frames": []}, 10.0, [])

        self.assertEqual(_launch_failure_category(evidence)["primary_category"], "RAW_DETECTOR_MISS")

    def test_same_frame_multiple_balls_preserves_candidates_and_selected_identity(self) -> None:
        evidence = _raw_ball_evidence(
            {"frames": [{
                "frame": 300,
                "time_sec": 10.0,
                "raw_predictions": 2,
                "candidates": [
                    {"candidate_id": "c00", "frame": 300, "time_sec": 10.0, "confidence": 0.9},
                    {"candidate_id": "c01", "frame": 300, "time_sec": 10.0, "confidence": 0.8},
                ],
                "rejected_candidates": [],
            }]},
            10.0,
            [{"frame": 300, "time_sec": 10.0, "position_m": [25.0, 35.0], "source": "detected", "confidence": 0.8, "candidate_id": "c01"}],
        )

        frame = evidence["simultaneous_accepted_candidate_frames"][0]
        self.assertEqual(frame["accepted_candidate_ids"], ["c00", "c01"])
        self.assertEqual(frame["canonical_selected"]["candidate_id"], "c01")
        self.assertTrue(_has_same_frame_selection_divergence(evidence))

    def test_rapid_distinct_contacts_remain_two_paths(self) -> None:
        report = analyze_shot_candidate_failures(
            candidates_document(),
            goldset(),
            [source(
                [contact("free-kick", 10.0, team="Verisk"), contact("rebound", 11.8, team="Verisk")],
                [],
                [],
            )],
        )

        self.assertEqual(len(report["gold_shot_traces"][0]["contact_paths"]), 2)

    def test_trace_persists_frozen_gold_team_for_downstream_path_filtering(self) -> None:
        report = analyze_shot_candidate_failures(
            candidates_document(),
            goldset(team="Corgi"),
            [source([], [], [])],
        )

        self.assertEqual(report["gold_shot_traces"][0]["gold_team"], "Corgi")


if __name__ == "__main__":
    unittest.main()
