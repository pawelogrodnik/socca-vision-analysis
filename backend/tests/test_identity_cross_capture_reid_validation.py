from __future__ import annotations

import unittest

from app.services.identity_cross_capture_reid_validation import (
    build_operator_name_display_gate,
    evaluate_h1_to_h2_cross_capture,
)


class CrossCaptureReidValidationTests(unittest.TestCase):
    def test_rank_two_truth_is_top3_but_not_top1(self) -> None:
        evaluation = evaluate_h1_to_h2_cross_capture(
            [{
                "candidate_subject_id": "subject-1",
                "suggestions": [
                    {"player_id": "player-a", "distance": 0.1},
                    {"player_id": "player-b", "distance": 0.2},
                ],
            }],
            h2_operator_decisions=[{
                "action": "assign_roster_player",
                "observation_key": "obs-1",
                "assigned_player": {"player_id": "player-b"},
                "assigned_team": {"team_label": "A"},
                "provenance": {"tracklet_id": "tracklet-1"},
            }],
            h2_candidate_document={"subjects": [{
                "candidate_subject_id": "subject-1",
                "tracklet_ids": ["tracklet-1"],
            }]},
            player_team_by_id={"player-a": "A", "player-b": "A"},
        )

        self.assertEqual(evaluation["queries"], 1)
        self.assertEqual(evaluation["top1_accuracy"], 0.0)
        self.assertEqual(evaluation["top3_accuracy"], 1.0)
        self.assertEqual(evaluation["rows"][0]["truth_rank"], 2)

    def test_unmapped_decision_is_not_injected_into_ranking(self) -> None:
        evaluation = evaluate_h1_to_h2_cross_capture(
            [],
            h2_operator_decisions=[{
                "action": "assign_roster_player",
                "observation_key": "obs-1",
                "assigned_player": {"player_id": "player-a"},
                "assigned_team": {"team_label": "A"},
                "provenance": {"tracklet_id": "missing"},
            }],
            h2_candidate_document={"subjects": []},
            player_team_by_id={"player-a": "A"},
        )

        self.assertEqual(evaluation["queries"], 0)
        self.assertEqual(len(evaluation["unmapped_operator_decisions"]), 1)
        self.assertFalse(evaluation["ground_truth_used_as_ranking_input"])

    def test_gate_requires_cross_capture_ground_truth_even_for_preferred_model(self) -> None:
        gate = build_operator_name_display_gate(
            model_status={"quality_tier": "preferred_reid_model", "selected_runtime": "opencv"},
            internal_calibration={"queries": 12, "top1_accuracy": 1.0},
            cross_capture_evaluation={
                "queries": 1,
                "top1_accuracy": 1.0,
                "top3_accuracy": 1.0,
                "cross_team_violations": 0,
            },
        )

        self.assertFalse(gate["display_eligible"])
        self.assertIn(
            "insufficient_cross_capture_ground_truth",
            gate["suppression_reason_codes"],
        )

    def test_fallback_is_never_operator_visible(self) -> None:
        gate = build_operator_name_display_gate(
            model_status={"quality_tier": "baseline_fallback"},
            internal_calibration={"queries": 12, "top1_accuracy": 1.0},
            cross_capture_evaluation={
                "queries": 8,
                "top1_accuracy": 1.0,
                "top3_accuracy": 1.0,
                "cross_team_violations": 0,
            },
        )

        self.assertFalse(gate["display_eligible"])
        self.assertIn(
            "baseline_fallback_not_operator_eligible",
            gate["suppression_reason_codes"],
        )


if __name__ == "__main__":
    unittest.main()
