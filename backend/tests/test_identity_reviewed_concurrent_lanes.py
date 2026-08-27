from __future__ import annotations

import unittest

from app.services.identity_reviewed_concurrent_lanes import (
    CONCURRENT_LANE_SET_STALE,
    ConcurrentLaneResolutionError,
    derive_concurrent_lanes,
    expanded_concurrent_lane_segments,
    validate_concurrent_lane_resolutions,
)


def _rows(tracklet_id: str, frames: range | list[int]) -> list[dict]:
    return [
        {"tracklet_id": tracklet_id, "frame": frame, "team_label": "A"}
        for frame in frames
    ]


class ReviewedIdentityConcurrentLaneTests(unittest.TestCase):
    def test_lanes_are_derived_only_from_exact_parent_observations(self) -> None:
        observations = [*_rows("A", range(100, 161)), *_rows("B", range(140, 201))]
        topology, lanes = derive_concurrent_lanes("case", "parent", observations)

        self.assertEqual(topology["kind"], "concurrent")
        self.assertEqual([lane["tracklet_id"] for lane in lanes], ["A", "B"])
        self.assertEqual([lane["observation_count"] for lane in lanes], [61, 61])
        self.assertEqual(lanes[0]["overlap_lane_ids"], [lanes[1]["lane_id"]])
        self.assertNotIn("C", {lane["tracklet_id"] for lane in lanes})

    def test_unknown_or_omitted_lane_is_rejected(self) -> None:
        _topology, lanes = derive_concurrent_lanes(
            "case",
            "parent",
            [*_rows("A", range(100, 161)), *_rows("B", range(140, 201))],
        )
        direct = lambda lane: {
            "lane_id": lane["lane_id"],
            "lane_source_digest": lane["source_ownership_digest"],
            "resolution": "direct",
            "assignment": {"action": "assign_team", "team_label": "A"},
        }
        with self.assertRaisesRegex(ConcurrentLaneResolutionError, CONCURRENT_LANE_SET_STALE):
            validate_concurrent_lane_resolutions(lanes, [direct(lanes[0])])
        forged = direct(lanes[1]) | {"lane_id": "unknown"}
        with self.assertRaisesRegex(ConcurrentLaneResolutionError, CONCURRENT_LANE_SET_STALE):
            validate_concurrent_lane_resolutions(lanes, [direct(lanes[0]), forged])

    def test_lane_local_split_never_pulls_another_lane_into_children(self) -> None:
        _topology, lanes = derive_concurrent_lanes(
            "case",
            "parent",
            [*_rows("A", range(100, 201)), *_rows("B", range(140, 181))],
        )
        submitted = [
            {
                "lane_id": lanes[0]["lane_id"],
                "lane_source_digest": lanes[0]["source_ownership_digest"],
                "resolution": "temporal_split",
                "split_after_frames": [150],
                "segment_assignments": [
                    {"action": "assign_team", "team_label": "A"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            },
            {
                "lane_id": lanes[1]["lane_id"],
                "lane_source_digest": lanes[1]["source_ownership_digest"],
                "resolution": "direct",
                "assignment": {"action": "assign_team", "team_label": "A"},
            },
        ]
        normalized = validate_concurrent_lane_resolutions(lanes, submitted)
        segments = expanded_concurrent_lane_segments(lanes, normalized)

        self.assertEqual(
            [(row["lane"]["tracklet_id"], min(value["frame"] for value in row["observations"]), max(value["frame"] for value in row["observations"])) for row in segments],
            [("A", 100, 150), ("A", 151, 200), ("B", 140, 180)],
        )

    def test_leading_refinement_boundary_keeps_the_first_refined_frame_in_next_child(self) -> None:
        _topology, lanes = derive_concurrent_lanes(
            "case",
            "parent",
            [*_rows("A", range(100, 161)), *_rows("B", range(140, 201))],
        )
        normalized = validate_concurrent_lane_resolutions(lanes, [
            {
                "lane_id": lanes[0]["lane_id"],
                "lane_source_digest": lanes[0]["source_ownership_digest"],
                "resolution": "temporal_split",
                "split_after_frames": [100],
                "segment_assignments": [
                    {"action": "assign_team", "team_label": "A"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            },
            {
                "lane_id": lanes[1]["lane_id"],
                "lane_source_digest": lanes[1]["source_ownership_digest"],
                "resolution": "direct",
                "assignment": {"action": "assign_team", "team_label": "A"},
            },
        ])
        segments = expanded_concurrent_lane_segments(lanes, normalized)

        self.assertEqual(
            [
                sorted(int(value["frame"]) for value in row["observations"])
                for row in segments[:2]
            ],
            [[100], list(range(101, 161))],
        )

    def test_single_observation_lane_is_not_offered_a_split(self) -> None:
        _topology, lanes = derive_concurrent_lanes(
            "case",
            "parent",
            [*_rows("A", [100]), *_rows("B", range(100, 103))],
        )
        self.assertFalse(next(lane for lane in lanes if lane["tracklet_id"] == "A")["split_allowed"])
        self.assertTrue(next(lane for lane in lanes if lane["tracklet_id"] == "B")["split_allowed"])


if __name__ == "__main__":
    unittest.main()
