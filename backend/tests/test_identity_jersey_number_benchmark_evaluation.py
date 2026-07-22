from __future__ import annotations

import unittest

from app.services.identity_jersey_number_benchmark_evaluation import (
    evaluate_targeted_jersey_number_propagation,
)


class JerseyNumberBenchmarkEvaluationTests(unittest.TestCase):
    def test_measures_only_hidden_targets_with_strong_consensus(self) -> None:
        result = evaluate_targeted_jersey_number_propagation(
            _selection(),
            {
                "subjects": [
                    {"candidate_subject_id": "s1", "strong_consensus": True, "state": "number_confirmed"},
                    {"candidate_subject_id": "s2", "strong_consensus": False, "state": "number_unreadable"},
                ]
            },
            {
                "summary": {"cross_subject_propagations": 0, "automatic_assignments": 0},
                "subjects": [
                    {"candidate_subject_id": "s1", "propagated_tracklet_ids": ["t2"]},
                ],
            },
            generated_at="fixed",
        )

        self.assertEqual(result["summary"]["all_hidden_target_tracklets"], 2)
        self.assertEqual(result["summary"]["eligible_hidden_target_tracklets"], 1)
        self.assertEqual(result["summary"]["eligible_matched_hidden_target_tracklets"], 1)
        self.assertEqual(result["summary"]["eligible_target_recall"], 1.0)
        self.assertTrue(result["summary"]["safety_passed"])
        self.assertTrue(result["summary"]["coverage_benefit_demonstrated"])

    def test_unexpected_propagation_fails_safety_gate(self) -> None:
        result = evaluate_targeted_jersey_number_propagation(
            _selection(),
            {"subjects": [{"candidate_subject_id": "s1", "strong_consensus": True}]},
            {
                "summary": {"cross_subject_propagations": 0, "automatic_assignments": 0},
                "subjects": [
                    {"candidate_subject_id": "s1", "propagated_tracklet_ids": ["unexpected"]},
                ],
            },
            generated_at="fixed",
        )

        self.assertEqual(result["summary"]["unexpected_propagated_tracklets"], 1)
        self.assertFalse(result["summary"]["safety_passed"])


def _selection() -> dict:
    return {
        "cards": [
            {
                "candidate_subject_id": "s1",
                "benchmark_selection": {
                    "seed_tracklet_id": "t1",
                    "target_tracklet_ids": ["t2"],
                },
            },
            {
                "candidate_subject_id": "s2",
                "benchmark_selection": {
                    "seed_tracklet_id": "u1",
                    "target_tracklet_ids": ["u2"],
                },
            },
        ]
    }


if __name__ == "__main__":
    unittest.main()
