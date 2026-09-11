from __future__ import annotations

import unittest

from app.services.reviewed_sprint_policy import (
    SPRINT_MIN_DURATION_SEC,
    _coalesce_adjacent_events,
    _is_meaningful_burst_start,
    _trusted_segment,
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

    def test_continuous_trusted_fragments_coalesce_without_counting_bridge_distance(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="high", detected_time_sec=120.0
        )
        rows = _rows([6.0] * 31)
        events = _coalesce_adjacent_events(
            [_event(0, 10, 0.0, 1.0, distance=6.0), _event(15, 25, 1.5, 2.5, distance=7.0)],
            [rows],
            fps=10.0,
            policy=policy,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_time_sec"], 0.0)
        self.assertEqual(events[0]["end_time_sec"], 2.5)
        self.assertEqual(events[0]["qualifying_time_sec"], 2.0)
        self.assertEqual(events[0]["qualifying_distance_m"], 13.0)
        self.assertEqual(events[0]["merged_fragment_count"], 2)

    def test_coalescing_refuses_tracklet_identity_and_large_gap_boundaries(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=24.0, speed_quality="high", detected_time_sec=120.0
        )
        rows = _rows([6.0] * 31)
        first = _event(0, 10, 0.0, 1.0)
        self.assertEqual(len(_coalesce_adjacent_events(
            [first, {**_event(15, 25, 1.5, 2.5), "tracklet_id": "other"}], [rows], fps=10.0, policy=policy,
        )), 2)
        self.assertEqual(len(_coalesce_adjacent_events(
            [first, {**_event(15, 25, 1.5, 2.5), "_canonical_player_id": "other-player"}], [rows], fps=10.0, policy=policy,
        )), 2)
        self.assertEqual(len(_coalesce_adjacent_events(
            [first, _event(20, 30, 2.0, 3.0)], [rows], fps=10.0, policy=policy,
        )), 2)

    def test_stable_moderate_running_near_threshold_is_not_a_burst(self) -> None:
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=20.0, speed_quality="high", detected_time_sec=120.0
        )
        # A trusted 17.6 km/h cruise follows the same 17.6 km/h baseline.
        rows = _rows([4.9] * 18)
        segments = [
            segment
            for left, right in zip(rows, rows[1:])
            if (segment := _trusted_segment(left, right, 10.0)) is not None
        ]
        self.assertFalse(
            _is_meaningful_burst_start(
                {"start_frame": 8, "speed_mps": 4.9},
                rows=rows,
                segments=segments,
                fps=10.0,
                policy=policy,
                previous_accepted=None,
            )
        )


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


def _event(
    start_frame: int,
    end_frame: int,
    start_time_sec: float,
    end_time_sec: float,
    *,
    distance: float = 6.0,
) -> dict[str, object]:
    return {
        "start_frame": start_frame,
        "end_frame": end_frame,
        "start_time_sec": start_time_sec,
        "end_time_sec": end_time_sec,
        "tracklet_id": "tracklet",
        "qualifying_time_sec": 1.0,
        "qualifying_distance_m": distance,
        "max_speed_mps": 6.0,
        "raw_segment_peak_mps": 6.2,
        "qualifying_segment_count": 10,
        "merged_fragment_count": 1,
        "_fragment_index": 0,
        "_canonical_player_id": "player-1",
    }


if __name__ == "__main__":
    unittest.main()
