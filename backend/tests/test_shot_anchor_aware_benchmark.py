from __future__ import annotations

import unittest

from evaluation.shot_anchor_aware_benchmark import anchor_aware_sensitivity, benchmark_anchor_aware_shot_candidates
from evaluation.shot_candidate_benchmark import benchmark_shot_candidates


def candidate(candidate_id: str, timestamp_sec: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_key": candidate_id,
        "logical_timestamp_sec": timestamp_sec,
        "source_timestamp_sec": timestamp_sec,
        "source_match_id": "source-a",
        "suggested_team_name": "Verisk",
        "suggested_player_id": None,
        "confidence": 0.5,
        "reasons": [],
    }


def manual(shot_id: str, timestamp_sec: float) -> dict:
    return {
        "id": shot_id,
        "timestamp_sec": timestamp_sec,
        "timestamp_semantics": "pre_event_playback_anchor",
        "timestamp_precision": "approximate",
        "team": "Verisk",
        "outcome": "blocked",
        "origin": "manual",
    }


def accepted(shot_id: str, timestamp_sec: float) -> dict:
    return {
        "id": shot_id,
        "timestamp_sec": timestamp_sec,
        "timestamp_semantics": "candidate_event_anchor",
        "timestamp_precision": "detector_derived",
        "team": "Verisk",
        "outcome": "blocked",
        "origin": "accepted_suggestion",
    }


def goldset(*shots: dict) -> dict:
    return {"schema_version": "shot-goldset:v3", "shots": list(shots), "hard_negatives": []}


class AnchorAwareShotBenchmarkTests(unittest.TestCase):
    def test_manual_anchor_finds_true_candidate_forward_without_calling_its_offset_timing_error(self) -> None:
        document = {"policy_version": "v5", "candidates": [candidate("actual-shot", 445.479)]}

        report = benchmark_anchor_aware_shot_candidates(document, goldset(manual("shot-07245", 444.5)))

        match = report["matches"][0]
        self.assertEqual(match["candidate_after_anchor_sec"], 0.979)
        self.assertIsNone(match["candidate_event_anchor_offset_sec"])
        self.assertEqual(report["summary"]["manual_playback_anchor_offsets"]["matched_shots"], 1)
        self.assertEqual(report["summary"]["detector_derived_timestamp_offsets"]["matched_shots"], 0)
        self.assertNotIn("timing_error", report["summary"])

    def test_forward_horizon_is_directional_and_can_exceed_legacy_tolerance(self) -> None:
        document = {"policy_version": "v5", "candidates": [candidate("late-but-plausible", 11.8)]}
        gold = goldset(manual("manual-shot", 10.0))

        legacy = benchmark_shot_candidates(document, gold, tolerance_sec=1.5)
        anchor = benchmark_anchor_aware_shot_candidates(document, gold, manual_forward_horizon_sec=2.0, manual_backward_slack_sec=0.25)

        self.assertEqual(legacy["summary"]["matched_gold_shots"], 0)
        self.assertEqual(anchor["matches"][0]["candidate_id"], "late-but-plausible")
        self.assertEqual(anchor["matches"][0]["candidate_after_anchor_sec"], 1.8)

    def test_candidate_substantially_before_manual_anchor_is_not_symmetric_match(self) -> None:
        document = {"policy_version": "v5", "candidates": [candidate("pre-anchor", 9.2)]}

        report = benchmark_anchor_aware_shot_candidates(document, goldset(manual("manual-shot", 10.0)))

        self.assertEqual(report["summary"]["anchor_aware_eligible_gold_shots"], 0)

    def test_detector_derived_offsets_include_only_accepted_suggestion_timestamps(self) -> None:
        document = {"policy_version": "v5", "candidates": [candidate("manual-event", 11.0), candidate("accepted-event", 20.2)]}

        report = benchmark_anchor_aware_shot_candidates(document, goldset(manual("manual-shot", 10.0), accepted("accepted-shot", 20.0)))

        self.assertEqual(report["summary"]["manual_playback_anchor_offsets"]["matched_shots"], 1)
        precise = report["summary"]["detector_derived_timestamp_offsets"]
        self.assertEqual(precise["timestamp_semantics_included"], ["candidate_event_anchor"])
        self.assertEqual(precise["matched_shots"], 1)
        self.assertEqual(precise["median_candidate_event_anchor_offset_sec"], 0.2)

    def test_sensitivity_uses_explicit_non_ground_truth_forward_horizons(self) -> None:
        document = {"policy_version": "v5", "candidates": [candidate("actual-shot", 12.2)]}

        sensitivity = anchor_aware_sensitivity(document, goldset(manual("manual-shot", 10.0)))

        self.assertEqual(sensitivity["forward_horizon_1.5s"]["summary"]["anchor_aware_eligible_gold_shots"], 0)
        self.assertEqual(sensitivity["forward_horizon_2.0s"]["summary"]["anchor_aware_eligible_gold_shots"], 0)
        self.assertEqual(sensitivity["forward_horizon_2.5s"]["summary"]["anchor_aware_eligible_gold_shots"], 1)


if __name__ == "__main__":
    unittest.main()
