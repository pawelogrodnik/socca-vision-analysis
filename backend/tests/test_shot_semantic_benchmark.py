from __future__ import annotations

import unittest

from evaluation.shot_semantic_benchmark import compare_semantic_policy_increment, evaluate_semantic_shot_benchmark


def candidate(candidate_id: str, timestamp_sec: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_key": candidate_id,
        "logical_timestamp_sec": timestamp_sec,
        "source_timestamp_sec": timestamp_sec,
        "source_match_id": "source-a",
        "suggested_team_name": "Corgi",
        "suggested_player_id": None,
        "confidence": 0.5,
        "reasons": [],
    }


def goldset() -> dict:
    return {
        "schema_version": "shot-goldset:v3",
        "shots": [
            {"id": "new-shot", "timestamp_sec": 445.0, "timestamp_semantics": "pre_event_playback_anchor", "timestamp_precision": "approximate", "team": "Verisk", "outcome": "blocked", "origin": "manual"},
            {"id": "actual-finish", "timestamp_sec": 1833.5, "timestamp_semantics": "pre_event_playback_anchor", "timestamp_precision": "approximate", "team": "Corgi", "outcome": "on_target", "origin": "manual"},
            {"id": "unaudited-shot", "timestamp_sec": 2000.0, "timestamp_semantics": "candidate_event_anchor", "timestamp_precision": "detector_derived", "team": "Verisk", "outcome": "goal", "origin": "accepted_suggestion"},
        ],
        "hard_negatives": [],
    }


def audit() -> dict:
    return {
        "schema_version": "shot-v5-weak-boundary-operator-audit:v1",
        "audits": [
            {"candidate_id": "new-candidate", "candidate_timestamp_sec": 445.2, "operator_class": "TRUE_SHOT_MISSING_CANONICAL", "canonical_shot_id": "new-shot"},
            {"candidate_id": "pre-shot-pass", "candidate_timestamp_sec": 1832.8, "operator_class": "PASS_PRE_SHOT_ACTION", "canonical_shot_id": "actual-finish"},
        ],
    }


def editorial(*reviews: dict) -> dict:
    return {"schema_version": "shot-review-editorial:v1", "suggested_candidate_reviews": list(reviews)}


class SemanticShotBenchmarkTests(unittest.TestCase):
    def test_true_audited_shot_maps_semantically_and_unaudited_temporal_match_stays_unaudited(self) -> None:
        document = {"policy_version": "v5", "timeline_span_sec": 2100.0, "candidates": [candidate("new-candidate", 445.2), candidate("plain", 2000.1)]}

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit(), editorial())
        by_gold = {row["gold_shot_id"]: row for row in report["semantic_matches"]}

        self.assertEqual(by_gold["new-shot"]["semantic_match_status"], "validated_true")
        self.assertTrue(by_gold["new-shot"]["semantic_validated_match"])
        self.assertEqual(by_gold["new-shot"]["semantic_truth_source"], "frozen_v5_audit")
        self.assertEqual(by_gold["unaudited-shot"]["semantic_match_status"], "unaudited")
        self.assertIsNone(by_gold["unaudited-shot"]["semantic_validated_match"])

    def test_pre_shot_pass_can_match_temporally_but_is_semantically_false(self) -> None:
        document = {"policy_version": "v5", "timeline_span_sec": 2100.0, "candidates": [candidate("pre-shot-pass", 1832.8)]}

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit(), editorial())

        match = report["semantic_matches"][0]
        self.assertEqual((match["gold_shot_id"], match["temporal_match"], match["semantic_match_status"], match["semantic_validated_match"]), ("actual-finish", True, "validated_false", False))
        self.assertEqual(report["temporal_false_positive_matches"][0]["operator_class"], "PASS_PRE_SHOT_ACTION")
        audit_row = next(row for row in report["audited_v5_candidate_table"] if row["candidate_id"] == "pre-shot-pass")
        self.assertFalse(audit_row["anchor_aware_eligible"])
        self.assertEqual(audit_row["semantic_match_status"], "validated_false")

    def test_durable_accepted_same_canonical_shot_is_validated_true_in_both_views(self) -> None:
        document = {"policy_version": "v2", "timeline_span_sec": 2100.0, "candidates": [candidate("durable-accepted", 445.2)]}
        reviews = editorial({"candidate_id": "durable-accepted", "review_status": "accepted", "canonical_shot_id": "new-shot"})

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit(), reviews)

        self.assertEqual(report["semantic_matches"][0]["semantic_match_status"], "validated_true")
        self.assertEqual(report["semantic_matches"][0]["semantic_truth_source"], "durable_shot_review")
        self.assertEqual(report["anchor_aware_semantic_matches"][0]["semantic_match_status"], "validated_true")
        self.assertEqual(report["semantic_summary"]["operator_truth_coverage"]["reviewed_matched_candidates"], 1)

    def test_durable_rejection_remains_false_in_temporal_and_anchor_aware_views(self) -> None:
        document = {"policy_version": "v2", "timeline_span_sec": 2100.0, "candidates": [candidate("durable-rejected", 445.2)]}
        reviews = editorial({"candidate_id": "durable-rejected", "review_status": "rejected"})

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit(), reviews)

        self.assertEqual(report["semantic_matches"][0]["semantic_match_status"], "validated_false")
        self.assertEqual(report["anchor_aware_semantic_matches"][0]["semantic_match_status"], "validated_false")
        self.assertEqual(report["semantic_summary"]["false_matches_by_operator_truth_source"]["durable_shot_review"], 1)

    def test_durable_accepted_different_canonical_shot_is_explicit_false_action_mismatch(self) -> None:
        document = {"policy_version": "v2", "timeline_span_sec": 2100.0, "candidates": [candidate("different-action", 445.2)]}
        reviews = editorial({"candidate_id": "different-action", "review_status": "accepted", "canonical_shot_id": "actual-finish"})

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit(), reviews)

        match = report["semantic_matches"][0]
        self.assertEqual((match["semantic_match_status"], match["durable_review_canonical_shot_id"]), ("validated_false", "actual-finish"))

    def test_no_frozen_or_durable_truth_remains_unaudited(self) -> None:
        document = {"policy_version": "v2", "timeline_span_sec": 2100.0, "candidates": [candidate("unreviewed", 445.2)]}

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit(), editorial())

        self.assertEqual((report["semantic_matches"][0]["semantic_match_status"], report["semantic_matches"][0]["semantic_truth_source"]), ("unaudited", "none"))

    def test_conflicting_frozen_and_durable_truth_fails_closed(self) -> None:
        document = {"policy_version": "v5", "timeline_span_sec": 2100.0, "candidates": [candidate("new-candidate", 445.2)]}
        reviews = editorial({"candidate_id": "new-candidate", "review_status": "rejected"})

        with self.assertRaisesRegex(ValueError, "operator_truth_conflict"):
            evaluate_semantic_shot_benchmark(document, goldset(), audit(), reviews)

    def test_agreeing_frozen_audit_has_deterministic_precedence_over_durable_truth(self) -> None:
        document = {"policy_version": "v5", "timeline_span_sec": 2100.0, "candidates": [candidate("new-candidate", 445.2)]}
        reviews = editorial({"candidate_id": "new-candidate", "review_status": "accepted", "canonical_shot_id": "new-shot"})

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit(), reviews)

        self.assertEqual(report["semantic_matches"][0]["semantic_truth_source"], "frozen_v5_audit")

    def test_durable_review_order_does_not_change_semantic_output(self) -> None:
        document = {"policy_version": "v2", "timeline_span_sec": 2100.0, "candidates": [candidate("durable-accepted", 445.2), candidate("durable-rejected", 2000.1)]}
        first_reviews = editorial(
            {"candidate_id": "durable-accepted", "review_status": "accepted", "canonical_shot_id": "new-shot"},
            {"candidate_id": "durable-rejected", "review_status": "rejected"},
        )
        second_reviews = editorial(*reversed(first_reviews["suggested_candidate_reviews"]))

        self.assertEqual(
            evaluate_semantic_shot_benchmark(document, goldset(), audit(), first_reviews),
            evaluate_semantic_shot_benchmark(document, goldset(), audit(), second_reviews),
        )

    def test_semantic_evaluation_and_v5_increment_are_deterministic(self) -> None:
        v4 = evaluate_semantic_shot_benchmark({"policy_version": "v4", "timeline_span_sec": 2100.0, "candidates": []}, goldset(), audit(), editorial())
        v5_document = {"policy_version": "v5", "timeline_span_sec": 2100.0, "candidates": [candidate("new-candidate", 445.2), candidate("pre-shot-pass", 1832.8)]}
        first = evaluate_semantic_shot_benchmark(v5_document, goldset(), audit(), editorial())
        second = evaluate_semantic_shot_benchmark(v5_document, goldset(), audit(), editorial())
        increment = compare_semantic_policy_increment(v4, first)

        self.assertEqual(first, second)
        self.assertEqual(increment["semantic_validated_incremental_recoveries"], ["new-shot"])
        self.assertEqual(increment["temporal_incremental_recoveries"], ["actual-finish", "new-shot"])
        self.assertEqual(increment["false_temporal_incremental_recoveries"][0]["canonical_shot_id"], "actual-finish")


if __name__ == "__main__":
    unittest.main()
