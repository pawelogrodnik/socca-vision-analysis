from __future__ import annotations

import unittest

from app.services.merged_public_match import _merge_workload


class MergedWorkloadWindowsTests(unittest.TestCase):
    def test_non_aligned_sources_recompute_one_shared_logical_window(self) -> None:
        workload = _workload(
            duration=1500.0,
            evidence=[
                (0.0, _evidence(samples=[(1100.0, 60.0)], segments=[(1100.0, 1101.0, 4.0)])),
                (1156.0, _evidence(samples=[(43.0, 60.0)], segments=[(43.0, 44.0, 6.0)])),
            ],
            detected=120.0,
            distance=10.0,
        )

        windows = workload["activity_windows"]
        self.assertEqual([(row["start_time_sec"], row["end_time_sec"]) for row in windows], [
            (0.0, 300.0), (300.0, 600.0), (600.0, 900.0), (900.0, 1200.0), (1200.0, 1500.0),
        ])
        shared = windows[3]
        self.assertEqual(shared["detected_time_sec"], 120.0)
        self.assertEqual(shared["total_distance_m"], 10.0)
        self.assertEqual(windows[4]["detected_time_sec"], 0.0)
        self.assertEqual(windows[4]["total_distance_m"], 0.0)

    def test_three_fragments_rebase_evidence_without_source_boundary_windows(self) -> None:
        workload = _workload(
            duration=731.5,
            evidence=[
                (0.0, _evidence(samples=[(299.0, 1.0)])),
                (301.0, _evidence(samples=[(298.0, 1.0)])),
                (602.0, _evidence(samples=[(120.0, 1.0)])),
            ],
            detected=3.0,
        )
        self.assertEqual([(row["start_time_sec"], row["end_time_sec"]) for row in workload["activity_windows"]], [
            (0.0, 300.0), (300.0, 600.0), (600.0, 731.5),
        ])
        self.assertEqual([row["detected_time_sec"] for row in workload["activity_windows"]], [1.0, 1.0, 1.0])
        self.assertEqual(workload["activity_windows"][-1]["display_label"], "10:00–12:11.5")

    def test_sprint_and_movement_crossing_a_logical_boundary_are_owned_once(self) -> None:
        workload = _workload(
            duration=600.0,
            evidence=[(0.0, _evidence(
                samples=[(299.0, 120.0)],
                segments=[(299.0, 301.0, 8.0)],
                sprints=[299.0],
            ))],
            detected=120.0,
            distance=8.0,
            sprints=1,
        )
        first, second = workload["activity_windows"]
        self.assertEqual(first["total_distance_m"], 8.0)
        self.assertEqual(second["total_distance_m"], 0.0)
        self.assertEqual([first["sprint_count"], second["sprint_count"]], [1, 0])

    def test_missing_evidence_is_not_silently_presented_as_source_windows(self) -> None:
        self.assertIsNone(_merge_workload(
            [], workload_evidence=[], duration_sec=300.0, detected_time_sec=0.0,
            total_distance_m=0.0, high_intensity_distance_m=0.0, sprint_count=0,
        ))


def _workload(*, duration: float, evidence: list[tuple[float, dict]], detected: float, distance: float = 0.0, sprints: int = 0) -> dict:
    return _merge_workload(
        [{"high_intensity_time_sec": 0.0, "sprint_time_sec": 0.0, "sprint_distance_m": 0.0, "max_sprint_speed_kmh": 0.0}],
        workload_evidence=evidence,
        duration_sec=duration,
        detected_time_sec=detected,
        total_distance_m=distance,
        high_intensity_distance_m=0.0,
        sprint_count=sprints,
    ) or {}


def _evidence(*, samples: list[tuple[float, float]], segments: list[tuple[float, float, float]] | None = None, sprints: list[float] | None = None) -> dict:
    return {
        "semantics": "reviewed_confirmed_detected_in_play",
        "detected_samples": [{"time_sec": time, "duration_sec": duration} for time, duration in samples],
        "movement_segments": [
            {"start_time_sec": start, "end_time_sec": end, "distance_m": distance, "speed_mps": distance / max(1.0, end - start), "kind": "observed"}
            for start, end, distance in (segments or [])
        ],
        "sprint_events": [{"start_time_sec": start} for start in (sprints or [])],
    }


if __name__ == "__main__":
    unittest.main()
