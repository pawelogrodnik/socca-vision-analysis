from __future__ import annotations

import unittest

from evaluation.shot_candidate_benchmark import benchmark_shot_candidates


def goldset() -> dict:
    return {
        "schema_version": "shot-goldset:v1",
        "shots": [
            {"id": "shot-1", "timestamp_sec": 10.0, "timestamp_display": "00:10~", "team": "Corgi", "player": "A01", "outcome": "goal"},
            {"id": "shot-2", "timestamp_sec": 20.0, "timestamp_display": "00:20~", "team": "Verisk", "player": None, "outcome": "blocked"},
        ],
        "hard_negatives": [
            {"id": "hard-1", "timestamp_sec": 30.0, "negative_type": "cross"},
        ],
    }


def candidate(key: str, time_sec: float, *, confidence: float = 0.6) -> dict:
    return {
        "candidate_id": key,
        "candidate_key": key,
        "logical_timestamp_sec": time_sec,
        "source_timestamp_sec": time_sec,
        "source_match_id": "m1",
        "suggested_team_name": "Corgi",
        "suggested_player_id": "A01",
        "confidence": confidence,
        "reasons": ["contact_release", "towards_opponent_goal"],
        "start_position_m": [15.0, 25.0],
        "trajectory_evidence": {"distance_m": 12.0, "mean_speed_mps": 8.0},
    }


class ShotCandidateBenchmarkTests(unittest.TestCase):
    def test_matches_approximate_timestamps_deterministically_and_reports_metrics(self) -> None:
        document = {
            "policy_version": "shot-candidate-shadow:v1",
            "timeline_span_sec": 600.0,
            "candidates": [candidate("later", 10.8, confidence=0.9), candidate("near", 10.2), candidate("second", 20.9)],
        }

        report = benchmark_shot_candidates(document, goldset(), tolerance_sec=1.5)

        self.assertEqual(report["summary"]["gold_shots"], 2)
        self.assertEqual(report["summary"]["matched_gold_shots"], 2)
        self.assertEqual(report["summary"]["total_generated_candidates"], 3)
        self.assertEqual(report["summary"]["candidates_per_10_minutes"], 3.0)
        self.assertEqual(report["matches"][0]["candidate_key"], "near")
        self.assertEqual(report["matches"][1]["candidate_key"], "second")
        self.assertEqual(report["duplicate_candidates_around_gold"][0]["gold_shot_id"], "shot-1")

    def test_outside_tolerance_is_a_miss_and_hard_negative_hit_is_not_global_precision(self) -> None:
        document = {"timeline_span_sec": 600.0, "candidates": [candidate("miss", 12.0), candidate("negative", 30.1)]}

        report = benchmark_shot_candidates(document, goldset(), tolerance_sec=1.5)

        self.assertEqual(report["summary"]["matched_gold_shots"], 0)
        self.assertEqual(report["summary"]["hard_negative_hits"], 1)
        self.assertEqual(report["missed_gold_shots"][0]["timestamp_display"], "00:10~")
        self.assertIn("not a representative sample for global precision", report["limitations"][0])


if __name__ == "__main__":
    unittest.main()
