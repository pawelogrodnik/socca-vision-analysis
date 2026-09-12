from __future__ import annotations

import unittest
from pathlib import Path

from evaluation.ball_continuity_root_cause_analysis import (
    _candidate_tracklets,
    _diagnose,
    _validation_packet,
    analyze_ball_continuity_root_causes,
)


def raw(*, predictions: int, accepted: int, rejected: int) -> dict:
    return {
        "raw_prediction_count": predictions,
        "accepted_candidate_count": accepted,
        "rejected_candidate_count": rejected,
    }


class BallContinuityRootCauseAnalysisTests(unittest.TestCase):
    def test_raw_detector_miss_and_filtered_candidate_are_distinct(self) -> None:
        missing = _diagnose(
            raw=raw(predictions=0, accepted=0, rejected=0),
            pre_position=None,
            post_position=None,
            suppression=None,
            trajectory={},
            shot_policy={},
            hypotheses=[],
        )
        filtered = _diagnose(
            raw=raw(predictions=1, accepted=0, rejected=1),
            pre_position=None,
            post_position=None,
            suppression=None,
            trajectory={},
            shot_policy={},
            hypotheses=[],
        )

        self.assertEqual(missing["primary_category"], "RAW_DETECTOR_MISS")
        self.assertEqual(filtered["primary_category"], "BALL_CANDIDATE_FILTERED")

    def test_accepted_pre_refinement_candidate_missing_after_refinement_is_not_detector_miss(self) -> None:
        diagnosis = _diagnose(
            raw=raw(predictions=1, accepted=1, rejected=0),
            pre_position={"source": "detected", "candidate_id": "c00", "confidence": 0.8},
            post_position={"source": "unknown", "candidate_id": None, "confidence": 0.0},
            suppression=None,
            trajectory={},
            shot_policy={},
            hypotheses=[],
        )

        self.assertEqual(diagnosis["primary_category"], "ACCEPTED_CANDIDATE_NOT_SELECTED")

    def test_player_overlap_suppression_wins_over_later_unknown_boundary(self) -> None:
        diagnosis = _diagnose(
            raw=raw(predictions=1, accepted=1, rejected=0),
            pre_position={"source": "detected", "candidate_id": "c00"},
            post_position={"source": "unknown"},
            suppression={"reason": "overlapping_player_bbox", "candidate_id": "c00"},
            trajectory={"boundary_reason": "unknown"},
            shot_policy={},
            hypotheses=[],
        )

        self.assertEqual(diagnosis["primary_category"], "PLAYER_OVERLAP_SUPPRESSED")

    def test_interpolation_unknown_and_shot_layer_are_distinct(self) -> None:
        interpolation = _diagnose(
            raw=raw(predictions=1, accepted=1, rejected=0),
            pre_position={"source": "detected", "candidate_id": "c00"},
            post_position={"source": "interpolated", "candidate_id": None},
            suppression=None,
            trajectory={"boundary_reason": "first_interpolated"},
            shot_policy={},
            hypotheses=[],
        )
        unknown = _diagnose(
            raw=raw(predictions=1, accepted=1, rejected=0),
            pre_position={"source": "unknown"},
            post_position={"source": "unknown"},
            suppression=None,
            trajectory={"boundary_reason": "first_unknown"},
            shot_policy={},
            hypotheses=[],
        )
        shot_layer = _diagnose(
            raw=raw(predictions=1, accepted=1, rejected=0),
            pre_position={"source": "detected", "candidate_id": "c00", "confidence": 0.9},
            post_position={"source": "detected", "candidate_id": "c00", "confidence": 0.9},
            suppression=None,
            trajectory={},
            shot_policy={"generated": False, "rejection_reason": "receiver_before_goal"},
            hypotheses=[],
        )

        self.assertEqual(interpolation["primary_category"], "INTERPOLATION_BOUNDARY")
        self.assertEqual(unknown["primary_category"], "UNKNOWN_GAP")
        self.assertEqual(shot_layer["primary_category"], "SHOT_LAYER_AFTER_VALID_BALL_TRACK")

    def test_candidate_tracklets_are_deterministic(self) -> None:
        frames = [
            {
                "frame": 301,
                "time_sec": 10.033,
                "candidates": [
                    {"candidate_id": "b1", "frame": 301, "time_sec": 10.033, "confidence": 0.7, "position_m": [20.2, 10.0]},
                    {"candidate_id": "a1", "frame": 301, "time_sec": 10.033, "confidence": 0.8, "position_m": [10.2, 10.0]},
                ],
            },
            {
                "frame": 300,
                "time_sec": 10.0,
                "candidates": [
                    {"candidate_id": "a0", "frame": 300, "time_sec": 10.0, "confidence": 0.8, "position_m": [10.0, 10.0]},
                    {"candidate_id": "b0", "frame": 300, "time_sec": 10.0, "confidence": 0.7, "position_m": [20.0, 10.0]},
                ],
            },
        ]

        first = _candidate_tracklets({"frames": frames}, 10.0)
        second = _candidate_tracklets({"frames": list(reversed(frames))}, 10.0)

        self.assertEqual(first, second)
        self.assertEqual([row["candidate_ids"] for row in first], [["a0", "a1"], ["b0", "b1"]])

    def test_human_validated_pre_shot_divergence_is_wrong_active_track(self) -> None:
        failure_analysis = {
            "gold_shot_traces": [{
                "gold_shot_id": "gold-1",
                "logical_timestamp_sec": 20.0,
                "source_match_id": "m1",
                "source_timestamp_sec": 20.0,
                "benchmark_matched": False,
                "classification": {"primary_category": "BALL_CONTINUITY_FAILURE", "contributing_categories": []},
                "contact_paths": [],
                "raw_ball_evidence": raw(predictions=1, accepted=1, rejected=0),
                "ball_launch": {"time_sec": 20.0},
                "trajectory": {"boundary_reason": "unknown"},
                "shot_policy": {},
            }],
        }
        pre = {"positions": [{"frame": 500, "time_sec": 16.667, "source": "detected", "candidate_id": "match-ball"}, {"frame": 600, "time_sec": 20.0, "source": "detected", "candidate_id": "match-ball"}]}
        post = {"positions": [{"frame": 500, "time_sec": 16.667, "source": "detected", "candidate_id": "foreign-ball"}, {"frame": 600, "time_sec": 20.0, "source": "detected", "candidate_id": "foreign-ball"}]}
        report = analyze_ball_continuity_root_causes(
            failure_analysis,
            [{
                "source_match_id": "m1",
                "ball_candidates": {"frames": []},
                "pre_player_refinement_tracks": pre,
                "ball_tracks": post,
                "human_validation": {"canonical_candidate_is_active": False},
            }],
        )

        self.assertEqual(report["cases"][0]["diagnosis"]["primary_category"], "WRONG_ACTIVE_TRACK_ALREADY_ESTABLISHED")
        self.assertEqual(report["cases"][0]["path_diagnoses"][0]["divergence"]["divergence_frame"], 500)

    def test_case_uses_team_consistent_path_not_nearest_wrong_team_path(self) -> None:
        report = self._multi_path_report([
            self._path("wrong-nearest", "Corgi", low_confidence=False),
            self._path("correct-team", "Verisk", low_confidence=True),
        ])

        case = report["cases"][0]
        self.assertEqual(case["diagnosis"]["primary_category"], "LOW_CONFIDENCE_CONTINUITY_BREAK")
        self.assertEqual([path["contact_path"]["event_id"] for path in case["path_diagnoses"]], ["correct-team"])

    def test_different_team_consistent_path_diagnoses_are_mixed_and_preserved(self) -> None:
        report = self._multi_path_report([
            self._path("low", "Verisk", low_confidence=True),
            self._path("shot-layer", "Verisk", low_confidence=False),
        ])

        case = report["cases"][0]
        self.assertEqual(case["diagnosis"]["primary_category"], "MIXED")
        self.assertTrue(case["diagnosis"]["requires_human_validation"])
        self.assertEqual(
            {path["diagnosis"]["primary_category"] for path in case["path_diagnoses"]},
            {"LOW_CONFIDENCE_CONTINUITY_BREAK", "SHOT_LAYER_AFTER_VALID_BALL_TRACK"},
        )

    def test_same_team_consistent_path_subtype_remains_case_subtype(self) -> None:
        report = self._multi_path_report([
            self._path("first", "Verisk", low_confidence=True),
            self._path("second", "Verisk", low_confidence=True),
        ])

        case = report["cases"][0]
        self.assertEqual(case["diagnosis"]["primary_category"], "LOW_CONFIDENCE_CONTINUITY_BREAK")
        self.assertEqual(len(case["path_diagnoses"]), 2)

    def test_validation_packet_uses_distinct_logical_and_source_clocks(self) -> None:
        packet = _validation_packet(
            {"gold_shot_id": "gold-1", "logical_timestamp_sec": 1900.0, "source_timestamp_sec": 138.0},
            {"frames": [
                {"frame": 4134, "time_sec": 137.8, "accepted_candidates": [], "canonical_selected": {}},
                {"frame": 4140, "time_sec": 138.0, "accepted_candidates": [{"candidate_id": "c00"}], "canonical_selected": {"candidate_id": "c00"}},
                {"frame": 4146, "time_sec": 138.2, "accepted_candidates": [], "canonical_selected": {}},
            ]},
            [],
            {},
            {"requires_human_validation": True},
            source_center=138.0,
        )

        self.assertEqual(packet["frame"], 4140)
        self.assertEqual(packet["logical_window_sec"], [1899.5, 1900.5])
        self.assertEqual(packet["source_window_sec"], [137.5, 138.5])

    def _multi_path_report(self, contact_paths: list[dict]) -> dict:
        return analyze_ball_continuity_root_causes(
            {"gold_shot_traces": [{
                "gold_shot_id": "gold-1",
                "gold_team": "Verisk",
                "logical_timestamp_sec": 20.0,
                "source_match_id": "m1",
                "source_timestamp_sec": 20.0,
                "benchmark_matched": False,
                "classification": {"primary_category": "BALL_CONTINUITY_FAILURE", "contributing_categories": []},
                "contact_paths": contact_paths,
            }]},
            [{
                "source_match_id": "m1",
                "ball_candidates": {"frames": []},
                "pre_player_refinement_tracks": {"positions": [{"frame": 600, "time_sec": 20.0, "source": "detected", "candidate_id": "c00", "confidence": 0.9}]},
                "ball_tracks": {"positions": [{"frame": 600, "time_sec": 20.0, "source": "detected", "candidate_id": "c00", "confidence": 0.9}]},
            }],
        )

    def _path(self, event_id: str, team: str, *, low_confidence: bool) -> dict:
        return {
            "contact": {"nearest_contact_event_id": event_id, "team": team, "timing_delta_sec": 0.1},
            "ball_launch": {"time_sec": 20.0},
            "raw_ball_evidence": raw(predictions=1, accepted=1, rejected=0),
            "trajectory": {"boundary_reason": "untrusted_detected"} if low_confidence else {},
            "shot_policy": {"generated": False, "rejection_reason": "missing_continuous_ball_trajectory" if low_confidence else "not_goalward"},
        }

    def test_production_modules_do_not_depend_on_shot_goldset(self) -> None:
        app_root = Path(__file__).resolve().parents[1] / "app"
        production_sources = list(app_root.rglob("*.py"))

        self.assertTrue(production_sources)
        self.assertTrue(all("shot_goldset" not in path.read_text(encoding="utf-8") for path in production_sources))


if __name__ == "__main__":
    unittest.main()
