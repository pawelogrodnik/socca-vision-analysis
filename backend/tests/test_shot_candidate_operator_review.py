from __future__ import annotations

import unittest

from evaluation.shot_candidate_operator_review import evaluate_operator_review_ab


def candidate(candidate_id: str, *, timestamp: float, confidence: float, receiver: bool = False, endpoint: float = 20.0) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_key": candidate_id,
        "source_match_id": "source-one",
        "source_timestamp_sec": timestamp,
        "suggested_team_label": "A",
        "confidence": confidence,
        "receiver_evidence": {"same_team_receiver_before_goal": receiver},
        "trajectory_evidence": {"endpoint_goal_distance_m": endpoint, "goal_corridor_distance_m": 1.0, "cross_like": False},
    }


class ShotCandidateOperatorReviewTests(unittest.TestCase):
    def test_evaluation_compares_historical_decisions_without_production_input(self) -> None:
        current = {"policy_version": "shot-candidate-shadow:v2", "timeline_span_sec": 30, "candidates": [
            candidate("accepted", timestamp=10, confidence=.8, endpoint=2),
            candidate("rejected-pass", timestamp=11, confidence=.6, receiver=True),
            candidate("rejected-other", timestamp=20, confidence=.5),
        ]}
        v3 = {"policy_version": "shot-candidate-shadow:v3", "timeline_span_sec": 30, "candidates": [
            candidate("accepted", timestamp=10, confidence=.8, endpoint=2),
            candidate("rejected-other", timestamp=20, confidence=.5),
        ]}
        editorial = {"suggested_candidate_reviews": [
            {"candidate_id": "accepted", "review_status": "accepted"},
            {"candidate_id": "rejected-pass", "review_status": "rejected"},
            {"candidate_id": "rejected-other", "review_status": "rejected"},
        ]}

        report = evaluate_operator_review_ab(current, v3, editorial)

        self.assertTrue(report["evaluation_only"])
        self.assertEqual(report["comparison"], {
            "accepted_suggestions_kept": 1,
            "accepted_suggestions_lost": 0,
            "rejected_candidates_surviving": 1,
            "rejected_candidates_removed": 1,
            "review_action_reduction": 0,
            "review_action_reduction_ratio": 0.0,
        })
        self.assertEqual(report["root_cause_diagnostics"]["primary_categories"]["pass_continuation_to_same_team_receiver"], 1)


if __name__ == "__main__":
    unittest.main()
