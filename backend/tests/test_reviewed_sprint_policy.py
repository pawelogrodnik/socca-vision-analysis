from __future__ import annotations

import unittest

from app.services.reviewed_sprint_policy import (
    SPRINT_MIN_DURATION_SEC,
    classify_reviewed_sprints,
    reviewed_sprint_policy,
)


class ReviewedSprintPolicyTests(unittest.TestCase):
    def test_player_relative_threshold_uses_peak_when_reference_is_reliable(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="medium", detected_time_sec=120.0
        )
        self.assertEqual(policy["reference_source"], "current_match_peak_sustained")
        self.assertEqual(policy["start_threshold_kmh"], 19.68)
        self.assertEqual(policy["continue_threshold_kmh"], 18.0)

    def test_floor_and_fallback_policy_are_explicit(self) -> None:
        floor = reviewed_sprint_policy(
            peak_sustained_speed_kmh=19.0, speed_quality="high", detected_time_sec=120.0
        )
        fallback = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="low", detected_time_sec=120.0
        )
        self.assertEqual(floor["start_threshold_kmh"], 16.5)
        self.assertEqual(floor["continue_threshold_kmh"], 15.0)
        self.assertEqual(fallback["reference_source"], "fallback_absolute")
        self.assertEqual(fallback["start_threshold_kmh"], 18.0)

    def test_hysteresis_counts_only_qualifying_evidence_and_closes_after_long_dip(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="high", detected_time_sec=120.0
        )
        # Sustained high movement, then one permitted dip, then a long dip.
        rows = _rows([6.0, 6.0, 6.0, 6.0, 6.0, 6.0, 4.0, 6.0, 4.0, 4.0, 4.0])
        result = classify_reviewed_sprints([rows], fps=10.0, policy=policy)
        self.assertEqual(result["sprint_count"], 1)
        self.assertGreaterEqual(result["sprint_time_sec"], 0.5)
        self.assertGreater(result["sprint_distance_m"], 3.0)

    def test_detection_gap_and_tracklet_boundary_never_bridge_a_sprint(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="high", detected_time_sec=120.0
        )
        split_by_gap = _rows([6.0, 6.0, 6.0, 6.0], frames=[0, 1, 2, 4, 5])
        self.assertEqual(classify_reviewed_sprints([split_by_gap], fps=10.0, policy=policy)["sprint_count"], 0)
        first = _rows([6.0, 6.0, 6.0], tracklet="one")
        second = _rows([6.0, 6.0, 6.0], start_frame=3, tracklet="two")
        self.assertEqual(classify_reviewed_sprints([first, second], fps=10.0, policy=policy)["sprint_count"], 0)

    def test_sprint_requires_a_shared_half_second_sustained_window(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="high", detected_time_sec=120.0
        )
        short = _rows([6.0, 6.0, 6.0, 6.0])
        exact = _rows([6.0, 6.0, 6.0, 6.0, 6.0])
        self.assertEqual(classify_reviewed_sprints([short], fps=10.0, policy=policy)["sprint_count"], 0)
        self.assertEqual(SPRINT_MIN_DURATION_SEC, 0.4)
        self.assertEqual(classify_reviewed_sprints([exact], fps=10.0, policy=policy)["sprint_count"], 1)

    def test_classifier_uses_the_supplied_policy_and_exposes_dynamic_diagnostics(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="high", detected_time_sec=120.0
        )
        policy["minimum_duration_sec"] = 0.7
        result = classify_reviewed_sprints([_rows([6.0] * 6)], fps=10.0, policy=policy)
        self.assertEqual(result["sprint_count"], 0)
        self.assertEqual(result["rejected_sprint_candidate_count"], 1)
        self.assertEqual(result["best_sprint_candidate_reason"], "too_short")
        self.assertEqual(result["best_rejected_sprint_candidate"]["reason"], "too_short")
        self.assertEqual(result["best_sprint_candidate_speed_kmh"], 21.6)
        self.assertEqual(result["best_sprint_candidate_duration_sec"], 0.6)
        self.assertEqual(result["best_sprint_candidate_distance_m"], 3.6)

    def test_single_spike_below_raw_outlier_ceiling_does_not_create_a_sprint(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="high", detected_time_sec=120.0
        )
        # 30.24 km/h is below the 30.6 km/h raw safety ceiling, but it is one
        # positional jump between ordinary 10–13 km/h movement samples.
        result = classify_reviewed_sprints(
            [_rows([3.1, 3.2, 3.3, 8.4, 3.2, 3.1, 3.2, 3.1])],
            fps=10.0,
            policy=policy,
        )
        self.assertEqual(result["sprint_count"], 0)
        self.assertEqual(result["max_sprint_speed_kmh"], 0.0)

    def test_alternating_raw_spikes_do_not_cross_the_sustained_threshold(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="high", detected_time_sec=120.0
        )
        result = classify_reviewed_sprints(
            [_rows([3.0, 8.0, 3.1, 7.8, 3.0, 8.1, 3.2, 7.9, 3.0])],
            fps=10.0,
            policy=policy,
        )
        self.assertEqual(result["sprint_count"], 0)

    def test_sustained_acceleration_creates_one_sprint_with_a_validated_peak(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=20.0, speed_quality="high", detected_time_sec=120.0
        )
        # 13 -> 15 -> 17 -> 19 -> 20 -> 19 -> 17 km/h, followed by enough
        # steady fast evidence to pass the shared 0.5s speed window.
        result = classify_reviewed_sprints(
            [_rows([3.61, 4.17, 4.72, 5.28, 5.56, 5.28, 4.72, 4.9, 4.8])],
            fps=10.0,
            policy=policy,
        )
        self.assertEqual(result["sprint_count"], 1)
        self.assertGreater(result["max_sprint_speed_kmh"], 16.5)
        self.assertLessEqual(result["max_sprint_speed_kmh"], 20.0)


def _rows(
    speeds_mps: list[float],
    *,
    start_frame: int = 0,
    frames: list[int] | None = None,
    tracklet: str = "tracklet",
) -> list[dict[str, object]]:
    frame_values = frames or list(range(start_frame, start_frame + len(speeds_mps) + 1))
    x = 0.0
    rows = [{"frame": frame_values[0], "time_sec": frame_values[0] / 10, "tracklet_id": tracklet, "pitch_m": [x, 0.0]}]
    for index, speed in enumerate(speeds_mps):
        frame_gap = frame_values[index + 1] - frame_values[index]
        x += speed * (frame_gap / 10)
        rows.append({"frame": frame_values[index + 1], "time_sec": frame_values[index + 1] / 10, "tracklet_id": tracklet, "pitch_m": [x, 0.0]})
    return rows


if __name__ == "__main__":
    unittest.main()
