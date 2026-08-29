from __future__ import annotations

import unittest

from app.services.identity_reviewed_workload import (
    WORKLOAD_MIN_RATE_SAMPLE_SEC,
    build_reviewed_player_workload,
)


class ReviewedPlayerWorkloadTests(unittest.TestCase):
    def test_rates_use_confirmed_detected_time_and_preserve_valid_zero_or_nonzero_values(self) -> None:
        # 1200 confirmed 10fps observations are exactly two detected minutes.
        # Each accepted 0.1s segment covers 0.6m (6m/s), so it is both HI and
        # one continuous accepted sprint; rates have an exact 300/120 multiplier.
        rows = [_row(frame, frame * 0.6, fps=10.0) for frame in range(1200)]
        workload = build_reviewed_player_workload(
            [rows],
            fps=10.0,
            video_duration_sec=300.0,
            canonical=_canonical(
                detected_time_sec=120.0,
                total_distance_m=719.4,
                high_intensity_distance_m=719.4,
                sprint_count=1,
            ),
        )

        self.assertEqual(workload["detected_time_sec"], 120.0)
        self.assertEqual(workload["distance_per_5min_m"], 1798.5)
        self.assertEqual(workload["high_intensity_distance_per_5min_m"], 1798.5)
        self.assertEqual(workload["sprints_per_5min"], 2.5)
        self.assertEqual(workload["high_intensity_distance_ratio"], 1.0)

    def test_uses_actual_video_duration_without_inventing_a_forty_minute_clock(self) -> None:
        workload = build_reviewed_player_workload(
            [], fps=25.0, video_duration_sec=2172.0, canonical=_canonical()
        )

        self.assertEqual(
            [(row["start_time_sec"], row["end_time_sec"]) for row in workload["activity_windows"]],
            [(0.0, 300.0), (300.0, 600.0), (600.0, 900.0), (900.0, 1200.0), (1200.0, 1500.0), (1500.0, 1800.0), (1800.0, 2100.0), (2100.0, 2172.0)],
        )
        self.assertEqual(workload["activity_windows"][-1]["display_label"], "35–36")

    def test_small_detected_sample_keeps_raw_values_but_hides_extrapolated_rates(self) -> None:
        rows = _rows(0, 90, fps=1.0, meters_per_frame=1.0)
        workload = build_reviewed_player_workload(
            [rows], fps=1.0, video_duration_sec=300.0, canonical=_canonical()
        )

        window = workload["activity_windows"][0]
        self.assertLess(window["detected_time_sec"], WORKLOAD_MIN_RATE_SAMPLE_SEC)
        self.assertGreater(window["total_distance_m"], 0.0)
        self.assertIsNone(window["distance_per_5min_m"])
        self.assertIsNone(window["high_intensity_distance_per_5min_m"])
        self.assertIsNone(window["sprints_per_5min"])

    def test_segment_crossing_window_boundary_is_owned_once_by_its_start_window(self) -> None:
        rows = [_row(2990, 0.0, fps=10.0), _row(3010, 2.0, fps=10.0)]
        workload = build_reviewed_player_workload(
            [rows], fps=10.0, video_duration_sec=600.0, canonical=_canonical()
        )

        first, second = workload["activity_windows"]
        self.assertEqual(first["total_distance_m"], 2.0)
        self.assertEqual(second["total_distance_m"], 0.0)
        self.assertEqual(first["total_distance_m"] + second["total_distance_m"], 2.0)

    def test_sprint_crossing_window_boundary_is_counted_once_by_its_start(self) -> None:
        # A three-second 6m/s sprint crosses 05:00 but belongs to its 04:59 start window.
        rows = [_row(2990 + index, index * 0.6, fps=10.0) for index in range(31)]
        workload = build_reviewed_player_workload(
            [rows], fps=10.0, video_duration_sec=600.0, canonical=_canonical()
        )

        self.assertEqual([window["sprint_count"] for window in workload["activity_windows"]], [1, 0])

    def test_separated_confirmed_fragments_leave_empty_available_video_windows_empty(self) -> None:
        fragments = [
            _rows(0, 10, fps=1.0, meters_per_frame=1.0),
            _rows(900, 10, fps=1.0, meters_per_frame=1.0),
            _rows(1500, 10, fps=1.0, meters_per_frame=1.0),
            _rows(2100, 10, fps=1.0, meters_per_frame=1.0),
        ]
        workload = build_reviewed_player_workload(
            fragments, fps=1.0, video_duration_sec=2172.0, canonical=_canonical()
        )

        self.assertEqual(
            [window["detected_time_sec"] for window in workload["activity_windows"]],
            [10.0, 0.0, 0.0, 10.0, 0.0, 10.0, 0.0, 10.0],
        )

    def test_full_normalized_metrics_use_canonical_totals_not_window_sums(self) -> None:
        rows = [_row(0, 0.0, fps=10.0), _row(1, 0.5, fps=10.0)]
        workload = build_reviewed_player_workload(
            [rows],
            fps=10.0,
            video_duration_sec=300.0,
            canonical=_canonical(
                detected_time_sec=120.0,
                total_distance_m=800.0,
                high_intensity_distance_m=120.0,
                sprint_count=5,
            ),
        )

        self.assertEqual(workload["activity_windows"][0]["total_distance_m"], 0.5)
        self.assertEqual(workload["distance_per_5min_m"], 2000.0)
        self.assertEqual(workload["high_intensity_distance_per_5min_m"], 300.0)
        self.assertEqual(workload["sprints_per_5min"], 12.5)
        self.assertEqual(workload["high_intensity_distance_ratio"], 0.15)

    def test_sub_centimeter_valid_movement_retains_canonical_workload_rate(self) -> None:
        rows = _rows(0, 120, fps=1.0, meters_per_frame=0.009)
        workload = build_reviewed_player_workload(
            [rows],
            fps=1.0,
            video_duration_sec=300.0,
            canonical=_canonical(detected_time_sec=120.0, total_distance_m=1.07),
        )

        self.assertEqual(workload["activity_windows"][0]["total_distance_m"], 1.07)
        self.assertEqual(workload["distance_per_5min_m"], 2.67)

    def test_activity_windows_retain_observed_and_estimated_distance(self) -> None:
        rows = [_row(0, 0.0, fps=10.0), _row(3, 1.0, fps=10.0)]
        workload = build_reviewed_player_workload(
            [rows], fps=10.0, video_duration_sec=300.0, canonical=_canonical()
        )

        window = workload["activity_windows"][0]
        self.assertEqual(window["observed_distance_m"], 0.0)
        self.assertEqual(window["estimated_short_gap_distance_m"], 1.0)
        self.assertEqual(window["total_distance_m"], 1.0)

    def test_activity_windows_exclude_the_same_speed_outliers_as_reviewed_stats(self) -> None:
        rows = [
            _row(0, 0.0, fps=10.0),
            _row(1, 0.5, fps=10.0),
            _row(2, 10.0, fps=10.0),
        ]
        workload = build_reviewed_player_workload(
            [rows], fps=10.0, video_duration_sec=300.0, canonical=_canonical()
        )

        self.assertEqual(workload["activity_windows"][0]["observed_distance_m"], 0.5)
        self.assertEqual(workload["activity_windows"][0]["total_distance_m"], 0.5)

    def test_short_final_window_is_not_reportable_or_best(self) -> None:
        rows = _rows(2100, 72, fps=1.0, meters_per_frame=1.0)
        workload = build_reviewed_player_workload(
            [rows], fps=1.0, video_duration_sec=2172.0, canonical=_canonical()
        )

        final_window = workload["activity_windows"][-1]
        self.assertEqual(final_window["duration_sec"], 72.0)
        self.assertIsNone(final_window["distance_per_5min_m"])
        self.assertIsNone(workload["best_activity_window"])

    def test_best_window_uses_highest_reportable_window_rate(self) -> None:
        fragments = [
            _rows(0, 120, fps=1.0, meters_per_frame=1.0),
            _rows(300, 120, fps=1.0, meters_per_frame=2.0),
        ]
        workload = build_reviewed_player_workload(
            fragments,
            fps=1.0,
            video_duration_sec=600.0,
            canonical=_canonical(),
        )

        self.assertEqual(workload["best_activity_window"]["window_index"], 1)
        self.assertEqual(workload["best_activity_window"]["distance_per_5min_m"], 595.0)


def _rows(start_frame: int, count: int, *, fps: float, meters_per_frame: float) -> list[dict[str, object]]:
    return [_row(start_frame + index, index * meters_per_frame, fps=fps) for index in range(count)]


def _row(frame: int, x: float, *, fps: float) -> dict[str, object]:
    return {
        "frame": frame,
        "time_sec": frame / fps,
        "tracklet_id": "tracklet-1",
        "pitch_m": [x, 10.0],
    }


def _canonical(
    *,
    detected_time_sec: float = 0.0,
    total_distance_m: float = 0.0,
    high_intensity_distance_m: float = 0.0,
    sprint_count: int = 0,
) -> dict[str, float | int]:
    return {
        "detected_time_sec": detected_time_sec,
        "total_distance_m": total_distance_m,
        "high_intensity_distance_m": high_intensity_distance_m,
        "high_intensity_time_sec": 0.0,
        "sprint_count": sprint_count,
        "sprint_time_sec": 0.0,
        "sprint_distance_m": 0.0,
        "max_sprint_speed_kmh": 0.0,
    }


if __name__ == "__main__":
    unittest.main()
