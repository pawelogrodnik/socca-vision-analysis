from __future__ import annotations

import unittest

from evaluation.shot_weak_boundary_shadow import evaluate_weak_boundary_shadow


def candidate(candidate_id: str, *, event_id: str, time_sec: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_key": candidate_id,
        "source_match_id": "source-1",
        "source_event_id": event_id,
        "logical_timestamp_sec": time_sec,
        "source_timestamp_sec": time_sec,
        "confidence": .6,
        "reasons": ["continuity_bridge_applied"],
    }


class WeakBoundaryShadowEvaluationTests(unittest.TestCase):
    def test_evaluation_reports_only_v5_recoveries_and_preserves_v4_matches(self) -> None:
        goldset = {
            "schema_version": "shot-goldset:v2",
            "shots": [
                {"id": "already", "timestamp_sec": 10.0, "team": "A", "outcome": "goal", "origin": "manual"},
                {"id": "weak", "timestamp_sec": 20.0, "team": "A", "outcome": "goal", "origin": "manual"},
            ],
            "hard_negatives": [],
        }
        v4 = {"policy_version": "shot-candidate-shadow:v4-continuity-bridge", "timeline_span_sec": 30, "candidates": [candidate("existing", event_id="other", time_sec=10.0)]}
        v5 = {
            "policy_version": "shot-candidate-shadow:v5-weak-boundary",
            "timeline_span_sec": 30,
            "candidates": [candidate("existing", event_id="other", time_sec=10.0), candidate("weak-candidate", event_id="contact-1", time_sec=20.0)],
            "weak_boundary_diagnostics": [{"source_match_id": "source-1", "contact_event_id": "contact-1", "weak_boundary_state": "weak_boundary_reinterpreted"}],
        }
        deep_dive = {"misses": [{
            "gold_shot_id": "weak", "timestamp_display": "00:20.0", "logical_timestamp_sec": 20.0, "source_match_id": "source-1",
            "weak_detected_boundaries": [{"contact_event_id": "contact-1"}],
        }]}

        first = evaluate_weak_boundary_shadow(v4, v5, goldset, deep_dive)
        second = evaluate_weak_boundary_shadow(v4, v5, goldset, deep_dive)

        self.assertEqual(first, second)
        self.assertEqual(first["incremental_value_over_v4"]["canonical_shots_recovered"], ["weak"])
        self.assertEqual(first["incremental_value_over_v4"]["canonical_shots_lost"], [])
        self.assertEqual(first["weak_boundary_cases"][0]["reason"], "recovered_safely_within_benchmark_tolerance")
        self.assertEqual(first["v5_only_candidates"][0]["candidate_id"], "weak-candidate")


if __name__ == "__main__":
    unittest.main()
