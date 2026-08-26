from __future__ import annotations

import unittest

from app.services.identity_reviewed_mixed_topology import (
    MixedTemporalTopologyError,
    analyze_temporal_split_topology,
    require_simple_temporal_split,
)


def observations(tracklet_id: str, frames: list[int]) -> list[dict[str, object]]:
    return [{"tracklet_id": tracklet_id, "frame": frame} for frame in frames]


class MixedTemporalTopologyTests(unittest.TestCase):
    def test_clean_serial_lifetimes_allow_simple_split(self) -> None:
        topology = analyze_temporal_split_topology(
            observations("A", list(range(100, 151)))
            + observations("B", list(range(151, 201)))
        )

        self.assertEqual(topology["kind"], "serial")
        self.assertTrue(topology["simple_split_allowed"])
        self.assertEqual(topology["max_concurrent_tracklets"], 1)
        self.assertEqual(topology["overlap_ranges"], [])

    def test_overlapping_lifetimes_fail_closed(self) -> None:
        topology = analyze_temporal_split_topology(
            observations("A", [100, 160]) + observations("B", [140, 200])
        )

        self.assertEqual(topology["kind"], "concurrent")
        self.assertFalse(topology["simple_split_allowed"])
        self.assertEqual(
            topology["overlap_ranges"],
            [{"frame_start": 140, "frame_end": 160, "tracklet_ids": ["A", "B"]}],
        )

    def test_same_frame_is_concurrent(self) -> None:
        topology = analyze_temporal_split_topology(
            observations("A", [100, 150]) + observations("B", [150, 200])
        )
        self.assertEqual(topology["kind"], "concurrent")
        self.assertEqual(topology["overlap_ranges"][0]["frame_start"], 150)
        self.assertEqual(topology["overlap_ranges"][0]["frame_end"], 150)

    def test_three_lanes_report_peak_concurrency(self) -> None:
        topology = analyze_temporal_split_topology(
            observations("A", [100, 200])
            + observations("B", [120, 180])
            + observations("C", [140, 160])
        )
        self.assertEqual(topology["kind"], "concurrent")
        self.assertEqual(topology["max_concurrent_tracklets"], 3)

    def test_sparse_observations_use_owned_tracklet_lifetimes(self) -> None:
        topology = analyze_temporal_split_topology(
            observations("A", [100, 120, 160])
            + observations("B", [140, 180, 200])
        )
        self.assertEqual(topology["kind"], "concurrent")
        self.assertEqual(topology["overlap_ranges"][0]["frame_start"], 140)
        self.assertEqual(topology["overlap_ranges"][0]["frame_end"], 160)

    def test_real_a14_topology_is_not_temporally_separable(self) -> None:
        source = (
            observations("100128:3", [3479, 3596])
            + observations("100189:2", [3517, 3596])
            + observations("200012:1", [3597, 3710])
        )
        topology = analyze_temporal_split_topology(source)

        self.assertEqual(topology["kind"], "concurrent")
        self.assertEqual(
            topology["overlap_ranges"],
            [{
                "frame_start": 3517,
                "frame_end": 3596,
                "tracklet_ids": ["100128:3", "100189:2"],
            }],
        )
        with self.assertRaisesRegex(
            MixedTemporalTopologyError,
            "temporal_split_not_separable",
        ):
            require_simple_temporal_split(source)


if __name__ == "__main__":
    unittest.main()
