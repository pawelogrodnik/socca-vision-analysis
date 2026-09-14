from __future__ import annotations

import unittest

from evaluation.shot_composed_suppression_shadow import evaluate_composed_suppression_shadow


def candidate(candidate_id: str, timestamp_sec: float, *, terminal: bool = False) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_key": candidate_id,
        "source_match_id": "source-1",
        "source_event_id": candidate_id,
        "source_timestamp_sec": timestamp_sec,
        "candidate_timestamp_sec": timestamp_sec,
        "logical_timestamp_sec": timestamp_sec,
        "suggested_team_name": "Corgi",
        "suggested_team_label": "Corgi",
        "confidence": 0.7,
        "reasons": ["terminal_goal_approach"] if terminal else [],
    }


class ComposedSuppressionShadowTests(unittest.TestCase):
    def test_v6_report_is_deterministic_and_keeps_true_while_exposing_suppressed_audit_row(self) -> None:
        accepted = candidate("accepted", 10.0)
        bridge = candidate("bridge", 20.2, terminal=True)
        pass_like = candidate("pass-like", 30.2)
        documents = {
            "v2": {"policy_version": "shot-candidate-shadow:v2", "timeline_span_sec": 60, "candidates": [accepted]},
            "v3": {"policy_version": "shot-candidate-shadow:v3", "timeline_span_sec": 60, "candidates": [accepted]},
            "v4": {"policy_version": "shot-candidate-shadow:v4-continuity-bridge", "timeline_span_sec": 60, "candidates": [accepted]},
            "v5": {"policy_version": "shot-candidate-shadow:v5-weak-boundary", "timeline_span_sec": 60, "candidates": [accepted, bridge, pass_like]},
            "v6": {
                "policy_version": "shot-candidate-shadow:v6-v5-structural-suppression",
                "timeline_span_sec": 60,
                "candidates": [accepted, bridge],
                "suppressed_candidate_diagnostics": [{"candidate_id": "pass-like", "candidate_key": "pass-like", "suppression_reason": "pass_like_same_team_receiver"}],
            },
        }
        goldset = {
            "schema_version": "shot-goldset:v3",
            "shots": [
                {"id": "base", "timestamp_sec": 10.0, "timestamp_semantics": "candidate_event_anchor", "timestamp_precision": "detector_derived", "team": "Corgi", "outcome": "goal", "origin": "accepted_suggestion"},
                {"id": "bridge-shot", "timestamp_sec": 20.0, "timestamp_semantics": "pre_event_playback_anchor", "timestamp_precision": "approximate", "team": "Corgi", "outcome": "on_target", "origin": "manual"},
                {"id": "pass-shot", "timestamp_sec": 30.0, "timestamp_semantics": "pre_event_playback_anchor", "timestamp_precision": "approximate", "team": "Corgi", "outcome": "off_target", "origin": "manual"},
            ],
            "hard_negatives": [],
        }
        audit = {
            "schema_version": "shot-v5-weak-boundary-operator-audit:v1",
            "audits": [
                {"candidate_id": "bridge", "candidate_timestamp_sec": 20.2, "operator_class": "TRUE_SHOT_EXISTING_CANONICAL", "canonical_shot_id": "bridge-shot"},
                {"candidate_id": "pass-like", "candidate_timestamp_sec": 30.2, "operator_class": "PASS", "canonical_shot_id": "pass-shot"},
            ],
        }
        editorial = {"schema_version": "shot-review-editorial:v1", "suggested_candidate_reviews": [{"candidate_id": "accepted", "review_status": "accepted", "canonical_shot_id": "base"}]}

        first = evaluate_composed_suppression_shadow(documents, goldset, audit, editorial)
        second = evaluate_composed_suppression_shadow(documents, goldset, audit, editorial)

        self.assertEqual(first, second)
        self.assertEqual(first["v5_to_v6"]["delta"]["raw_candidates"], -1)
        self.assertEqual(first["v5_to_v6"]["validated_true_lost_from_v5"], [])
        self.assertEqual(first["v5_to_v6"]["suppressed_reason_distribution"], {"pass_like_same_team_receiver": 1})
        self.assertEqual(first["v5_to_v6"]["terminal_goal_approach_protected_candidates"], ["bridge"])
        family = {row["candidate_id"]: row for row in first["v5_only_audited_family"]}
        self.assertTrue(family["bridge"]["present_in_v6"])
        self.assertFalse(family["pass-like"]["present_in_v6"])
        self.assertEqual(family["pass-like"]["suppression_reason"], "pass_like_same_team_receiver")
        self.assertEqual(family["pass-like"]["semantic_verdict"], "validated_false")


if __name__ == "__main__":
    unittest.main()
