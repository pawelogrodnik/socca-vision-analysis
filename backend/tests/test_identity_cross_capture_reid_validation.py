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
                "frame_number": 10,
                "assigned_player": {"player_id": "player-b"},
                "assigned_team": {"team_label": "A"},
                "provenance": {"tracklet_id": "tracklet-1"},
            }],
            h2_candidate_document={"subjects": [{
                "candidate_subject_id": "subject-1",
                "team_label": "A",
                "tracklet_ids": ["tracklet-1"],
            }]},
            h2_reanchor_document=_reanchor_document(),
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
                "frame_number": 10,
                "assigned_player": {"player_id": "player-a"},
                "assigned_team": {"team_label": "A"},
                "provenance": {"tracklet_id": "missing"},
            }],
            h2_candidate_document={"subjects": []},
            h2_reanchor_document=_reanchor_document(),
            player_team_by_id={"player-a": "A"},
        )

        self.assertEqual(evaluation["queries"], 0)
        self.assertEqual(len(evaluation["unmapped_operator_decisions"]), 1)
        self.assertFalse(evaluation["ground_truth_used_as_ranking_input"])

    def test_same_tracklet_on_wrong_frame_is_not_exactly_mapped(self) -> None:
        evaluation = evaluate_h1_to_h2_cross_capture(
            [],
            h2_operator_decisions=[{
                "action": "assign_roster_player",
                "observation_key": "obs-1",
                "frame_number": 11,
                "assigned_player": {"player_id": "player-a"},
                "assigned_team": {"team_label": "A"},
                "provenance": {"tracklet_id": "tracklet-1"},
            }],
            h2_candidate_document={"subjects": [{
                "candidate_subject_id": "subject-1",
                "team_label": "A",
                "tracklet_ids": ["tracklet-1"],
            }]},
            h2_reanchor_document=_reanchor_document(),
            player_team_by_id={"player-a": "A"},
        )

        self.assertEqual(evaluation["queries"], 0)
        self.assertEqual(
            evaluation["unmapped_operator_decisions"][0]["reason"],
            "unmapped_exact_observation",
        )

    def test_duplicate_matching_decisions_count_one_subject_once(self) -> None:
        decision = {
            "action": "assign_roster_player",
            "observation_key": "obs-1",
            "frame_number": 10,
            "assigned_player": {"player_id": "player-a"},
            "assigned_team": {"team_label": "A"},
            "provenance": {"tracklet_id": "tracklet-1"},
        }
        evaluation = evaluate_h1_to_h2_cross_capture(
            [{"candidate_subject_id": "subject-1", "suggestions": []}],
            h2_operator_decisions=[decision, dict(decision)],
            h2_candidate_document={"subjects": [{
                "candidate_subject_id": "subject-1",
                "team_label": "A",
                "tracklet_ids": ["tracklet-1"],
            }]},
            h2_reanchor_document=_reanchor_document(),
            player_team_by_id={"player-a": "A"},
        )

        self.assertEqual(evaluation["queries"], 1)
        self.assertEqual(len(evaluation["rows"][0]["observation_provenance"]["source_observation_keys"]), 2)

    def test_ambiguous_candidate_mapping_is_excluded(self) -> None:
        evaluation = evaluate_h1_to_h2_cross_capture(
            [],
            h2_operator_decisions=[_decision("player-a")],
            h2_candidate_document={"subjects": [
                _candidate("subject-1"), _candidate("subject-2"),
            ]},
            h2_reanchor_document=_reanchor_document(),
            player_team_by_id={"player-a": "A"},
        )

        self.assertEqual(evaluation["queries"], 0)
        self.assertEqual(
            evaluation["unmapped_operator_decisions"][0]["reason"],
            "ambiguous_exact_observation",
        )

    def test_conflicting_and_invalid_ground_truth_are_excluded(self) -> None:
        conflicting = evaluate_h1_to_h2_cross_capture(
            [],
            h2_operator_decisions=[_decision("player-a"), _decision("player-b")],
            h2_candidate_document={"subjects": [_candidate("subject-1")]},
            h2_reanchor_document=_reanchor_document(),
            player_team_by_id={"player-a": "A", "player-b": "A"},
        )
        invalid = evaluate_h1_to_h2_cross_capture(
            [],
            h2_operator_decisions=[_decision("missing-player")],
            h2_candidate_document={"subjects": [_candidate("subject-1")]},
            h2_reanchor_document=_reanchor_document(),
            player_team_by_id={"player-a": "A"},
        )

        self.assertEqual(conflicting["queries"], 0)
        self.assertEqual(len(conflicting["conflicting_ground_truth"]), 1)
        self.assertEqual(invalid["queries"], 0)
        self.assertEqual(len(invalid["invalid_ground_truth"]), 1)

    def test_ground_truth_mutation_does_not_change_ranking_order(self) -> None:
        rankings = [{
            "candidate_subject_id": "subject-1",
            "suggestions": [
                {"player_id": "player-a", "distance": 0.1},
                {"player_id": "player-b", "distance": 0.2},
            ],
        }]
        kwargs = {
            "h2_candidate_document": {"subjects": [_candidate("subject-1")]},
            "h2_reanchor_document": _reanchor_document(),
            "player_team_by_id": {"player-a": "A", "player-b": "A"},
        }
        first = evaluate_h1_to_h2_cross_capture(
            rankings, h2_operator_decisions=[_decision("player-a")], **kwargs
        )
        second = evaluate_h1_to_h2_cross_capture(
            rankings, h2_operator_decisions=[_decision("player-b")], **kwargs
        )

        self.assertEqual(
            first["rows"][0]["ranked_candidate_player_ids"],
            second["rows"][0]["ranked_candidate_player_ids"],
        )
        self.assertEqual(rankings[0]["suggestions"][0]["player_id"], "player-a")

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

    def test_invalid_ranked_player_blocks_preferred_gate(self) -> None:
        gate = build_operator_name_display_gate(
            model_status={
                "quality_tier": "preferred_reid_model",
                "selected_runtime": "openvino_cpu",
            },
            internal_calibration={"queries": 12, "top1_accuracy": 1.0},
            cross_capture_evaluation={
                "queries": 8,
                "top1_accuracy": 1.0,
                "top3_accuracy": 1.0,
                "cross_team_violations": 0,
                "invalid_ranked_players": 1,
            },
        )

        self.assertFalse(gate["display_eligible"])
        self.assertIn(
            "invalid_ranked_player_detected",
            gate["suppression_reason_codes"],
        )


def _reanchor_document() -> dict[str, object]:
    return {
        "frames": [{
            "frame_number": 10,
            "observations": [{
                "observation_key": "obs-1",
                "bbox_xyxy": [1, 2, 3, 4],
                "provenance": {
                    "tracklet_id": "tracklet-1",
                    "stable_subject_id": "slot-A01",
                },
            }],
        }]
    }


def _candidate(candidate_subject_id: str) -> dict[str, object]:
    return {
        "candidate_subject_id": candidate_subject_id,
        "team_label": "A",
        "tracklet_ids": ["tracklet-1"],
    }


def _decision(player_id: str) -> dict[str, object]:
    return {
        "action": "assign_roster_player",
        "observation_key": "obs-1",
        "frame_number": 10,
        "assigned_player": {"player_id": player_id},
        "assigned_team": {"team_label": "A"},
        "provenance": {"tracklet_id": "tracklet-1"},
    }


if __name__ == "__main__":
    unittest.main()
