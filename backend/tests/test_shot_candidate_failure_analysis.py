from __future__ import annotations

import unittest

from evaluation.shot_candidate_failure_analysis import _classify_failure


class ShotCandidateFailureAnalysisTests(unittest.TestCase):
    def test_raw_detector_miss_requires_no_raw_or_filtered_ball_evidence(self) -> None:
        result = _classify_failure(
            matched=False,
            contact={"team_name": "Corgi"},
            contact_delta=0.0,
            raw_evidence={"raw_prediction_count": 0, "accepted_candidate_count": 0, "rejected_candidate_count": 0},
            selected_position=None,
            launch=None,
            trajectory=None,
            rejection_reason="missing_launch_ball_position",
            gold_team="Corgi",
        )

        self.assertEqual(result["primary_category"], "RAW_DETECTOR_MISS")
        self.assertEqual(result["first_failing_stage"], "launch_selection")

    def test_filtered_ball_evidence_is_not_reported_as_detector_miss(self) -> None:
        result = _classify_failure(
            matched=False,
            contact={"team_name": "Corgi"},
            contact_delta=0.0,
            raw_evidence={"raw_prediction_count": 1, "accepted_candidate_count": 0, "rejected_candidate_count": 1},
            selected_position=None,
            launch=None,
            trajectory=None,
            rejection_reason="missing_launch_ball_position",
            gold_team="Corgi",
        )

        self.assertEqual(result["primary_category"], "BALL_CANDIDATE_FILTERED")

    def test_accepted_ball_without_launch_is_track_selection_failure(self) -> None:
        result = _classify_failure(
            matched=False,
            contact={"team_name": "Corgi"},
            contact_delta=0.0,
            raw_evidence={"raw_prediction_count": 1, "accepted_candidate_count": 1, "rejected_candidate_count": 0},
            selected_position=None,
            launch=None,
            trajectory=None,
            rejection_reason="missing_launch_ball_position",
            gold_team="Corgi",
        )

        self.assertEqual(result["primary_category"], "BALL_TRACK_SELECTION_FAILURE")

    def test_wrong_nearest_contact_team_is_attribution_failure_before_trajectory(self) -> None:
        result = _classify_failure(
            matched=False,
            contact={"team_name": "Corgi"},
            contact_delta=0.1,
            raw_evidence={},
            selected_position={},
            launch={},
            trajectory=None,
            rejection_reason="missing_continuous_ball_trajectory",
            gold_team="Verisk",
        )

        self.assertEqual(result["primary_category"], "CONTACT_OR_ATTRIBUTION_FAILURE")
        self.assertEqual(result["first_failing_stage"], "contact_attribution")

    def test_different_selected_ball_is_an_active_ball_selection_failure(self) -> None:
        result = _classify_failure(
            matched=False,
            contact=None,
            contact_delta=None,
            raw_evidence={"closest_candidate": {"candidate_id": "raw-ball"}},
            selected_position={"candidate_id": "selected-other-ball"},
            launch=None,
            trajectory=None,
            rejection_reason=None,
            gold_team="Verisk",
        )

        self.assertEqual(result["primary_category"], "BALL_TRACK_SELECTION_FAILURE")
        self.assertEqual(result["first_failing_stage"], "active_ball_selection")

    def test_trajectory_rejection_is_continuity_failure_when_contact_is_consistent(self) -> None:
        result = _classify_failure(
            matched=False,
            contact={"team_name": "Corgi"},
            contact_delta=0.1,
            raw_evidence={},
            selected_position={},
            launch={},
            trajectory=None,
            rejection_reason="missing_continuous_ball_trajectory",
            gold_team="Corgi",
        )

        self.assertEqual(result["primary_category"], "BALL_CONTINUITY_FAILURE")
