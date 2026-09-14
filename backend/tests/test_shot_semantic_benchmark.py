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
            {"id": "new-shot", "timestamp_sec": 445.0, "team": "Verisk", "outcome": "blocked", "origin": "manual"},
            {"id": "actual-finish", "timestamp_sec": 1833.5, "team": "Corgi", "outcome": "on_target", "origin": "manual"},
            {"id": "unaudited-shot", "timestamp_sec": 2000.0, "team": "Verisk", "outcome": "goal", "origin": "accepted_suggestion"},
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


class SemanticShotBenchmarkTests(unittest.TestCase):
    def test_true_audited_shot_maps_semantically_and_unaudited_temporal_match_stays_unaudited(self) -> None:
        document = {"policy_version": "v5", "timeline_span_sec": 2100.0, "candidates": [candidate("new-candidate", 445.2), candidate("plain", 2000.1)]}

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit())
        by_gold = {row["gold_shot_id"]: row for row in report["semantic_matches"]}

        self.assertEqual(by_gold["new-shot"]["semantic_match_status"], "validated_true")
        self.assertTrue(by_gold["new-shot"]["semantic_validated_match"])
        self.assertEqual(by_gold["unaudited-shot"]["semantic_match_status"], "unaudited")
        self.assertIsNone(by_gold["unaudited-shot"]["semantic_validated_match"])

    def test_pre_shot_pass_can_match_temporally_but_is_semantically_false(self) -> None:
        document = {"policy_version": "v5", "timeline_span_sec": 2100.0, "candidates": [candidate("pre-shot-pass", 1832.8)]}

        report = evaluate_semantic_shot_benchmark(document, goldset(), audit())

        match = report["semantic_matches"][0]
        self.assertEqual((match["gold_shot_id"], match["temporal_match"], match["semantic_match_status"], match["semantic_validated_match"]), ("actual-finish", True, "validated_false", False))
        self.assertEqual(report["temporal_false_positive_matches"][0]["operator_class"], "PASS_PRE_SHOT_ACTION")

    def test_semantic_evaluation_and_v5_increment_are_deterministic(self) -> None:
        v4 = evaluate_semantic_shot_benchmark({"policy_version": "v4", "timeline_span_sec": 2100.0, "candidates": []}, goldset(), audit())
        v5_document = {"policy_version": "v5", "timeline_span_sec": 2100.0, "candidates": [candidate("new-candidate", 445.2), candidate("pre-shot-pass", 1832.8)]}
        first = evaluate_semantic_shot_benchmark(v5_document, goldset(), audit())
        second = evaluate_semantic_shot_benchmark(v5_document, goldset(), audit())
        increment = compare_semantic_policy_increment(v4, first)

        self.assertEqual(first, second)
        self.assertEqual(increment["semantic_validated_incremental_recoveries"], ["new-shot"])
        self.assertEqual(increment["temporal_incremental_recoveries"], ["actual-finish", "new-shot"])
        self.assertEqual(increment["false_temporal_incremental_recoveries"][0]["canonical_shot_id"], "actual-finish")


if __name__ == "__main__":
    unittest.main()
