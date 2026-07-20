from __future__ import annotations

import unittest

from scripts.evaluate_identity_reid_fusion_shadow import (
    evaluate_reid_fusion_goldset,
)


class EvaluateIdentityReIdFusionShadowTests(unittest.TestCase):
    def test_evaluation_reports_auc_improvement_without_merge(self) -> None:
        goldset = {
            "items": [
                {
                    "benchmark_label": "easy",
                    "candidate_key": "same",
                    "review_status": "confirmed_same",
                    "expected_same_person": True,
                },
                {
                    "benchmark_label": "easy",
                    "candidate_key": "different",
                    "review_status": "confirmed_different",
                    "expected_same_person": False,
                },
            ]
        }
        fusion = {
            "proposals": [
                _row("same", baseline=0.4, fused=0.2),
                _row("different", baseline=0.3, fused=0.6),
            ]
        }

        evaluation = evaluate_reid_fusion_goldset(goldset, {"easy": fusion})

        self.assertEqual(evaluation["status"], "passed")
        self.assertEqual(evaluation["summary"]["baseline_roc_auc"], 0.0)
        self.assertEqual(evaluation["summary"]["roc_auc"], 1.0)
        self.assertTrue(evaluation["gates"]["no_automatic_merges"])

    def test_existing_acceptance_policy_is_identical_for_p113_and_p114(self) -> None:
        goldset = {
            "items": [
                {
                    "benchmark_label": "easy",
                    "candidate_key": "same",
                    "review_status": "confirmed_same",
                    "expected_same_person": True,
                }
            ]
        }
        row = _row("same", baseline=0.2, fused=0.1)
        row["strict_gate_passed"] = True
        content = {
            "pairs": [
                {"proposal_key": "same", "quality": "person_content_supported"}
            ]
        }

        evaluation = evaluate_reid_fusion_goldset(
            goldset,
            {"easy": {"proposals": [row]}},
            content_by_case={"easy": content},
        )

        self.assertEqual(evaluation["summary"]["selected_accepted_edges"], 1)
        self.assertEqual(
            evaluation["summary"]["estimated_manual_review_items_delta"], 0
        )

    def test_hard_constraint_adjustment_fails_safety_gate(self) -> None:
        goldset = {
            "items": [
                {
                    "benchmark_label": "easy",
                    "candidate_key": "same",
                    "review_status": "confirmed_same",
                    "expected_same_person": True,
                },
                {
                    "benchmark_label": "easy",
                    "candidate_key": "different",
                    "review_status": "confirmed_different",
                    "expected_same_person": False,
                },
            ]
        }
        same = _row("same", baseline=0.2, fused=0.1)
        same["hard_constraint_reasons"] = ["known_team_mismatch"]
        evaluation = evaluate_reid_fusion_goldset(
            goldset,
            {"easy": {"proposals": [same, _row("different", baseline=0.7, fused=0.8)]}},
        )

        self.assertEqual(evaluation["status"], "failed")
        self.assertFalse(evaluation["gates"]["hard_constraints_never_adjusted"])


def _row(key: str, *, baseline: float, fused: float) -> dict:
    return {
        "proposal_key": key,
        "baseline_cost": baseline,
        "fused_cost": fused,
        "prototype_distance": fused,
        "reid_applied": True,
        "hard_constraint_reasons": [],
        "strict_gate_passed": False,
        "strict_gate_reason_codes": ["manual_review"],
        "automatic_merge": False,
        "baseline_rank": 1,
        "fused_rank": 1,
        "rank_delta": 0,
    }


if __name__ == "__main__":
    unittest.main()
