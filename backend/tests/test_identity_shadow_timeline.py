from __future__ import annotations

import unittest

from app.services.identity_shadow_timeline import build_shadow_resolved_timeline


FPS = 10.0


def tracklet(
    tracklet_id: str,
    start: int,
    end: int,
    *,
    team_label: str = "A",
    bbox_xyxy: list[int] | None = None,
) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team_label,
        "positions_m": [
            {
                "frame": frame,
                "time_sec": frame / FPS,
                "pitch_m": [float(frame), 5.0],
                "smoothed_pitch_m": [float(frame), 5.0],
                "bbox_xyxy": bbox_xyxy or [10, 10, 30, 70],
                "confidence": 0.9,
                "play_area_status": "inside_play",
            }
            for frame in range(start, end + 1)
        ],
    }


def quality(*tracklet_ids: str, unreliable: dict[str, list[tuple[int, int]]] | None = None) -> dict:
    unreliable = unreliable or {}
    return {
        "tracklets": [
            {
                "tracklet_id": tracklet_id,
                "quality_class": "trusted",
                "unreliable_footpoint_ranges": [
                    {"start_frame": start, "end_frame": end}
                    for start, end in unreliable.get(tracklet_id, [])
                ],
                "unreliable_appearance_ranges": [],
            }
            for tracklet_id in tracklet_ids
        ]
    }


def offline(
    source: str,
    target: str,
    *,
    occlusion: bool = False,
) -> dict:
    return {
        "algorithm": {"name": "test", "version": "1"},
        "accepted_edges": [
            {
                "edge_key": "edge-1",
                "source_tracklet_id": source,
                "target_tracklet_id": target,
                "confidence": 0.9,
                "recommendation_source": "stitching",
                "occlusion_event_ids": ["occ-1"] if occlusion else [],
                "current_source_subject_ids": ["slot-A01"],
                "current_target_subject_ids": ["slot-A02"],
                "current_identity_relation": "different_subjects",
            }
        ],
        "subjects": [
            {
                "shadow_subject_id": "shadow-a-1",
                "team_label": "A",
                "tracklet_ids": [source, target],
                "production_subject_ids": ["slot-A01", "slot-A02"],
                "quality_flags": ["merges_multiple_production_subjects"],
            }
        ],
    }


class ShadowResolvedTimelineTests(unittest.TestCase):
    def test_short_occlusion_gap_has_explicit_occluded_state(self) -> None:
        rows = [tracklet("s1", 0, 4), tracklet("t1", 8, 12)]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1", occlusion=True),
            rows,
            quality("s1", "t1"),
            fps=FPS,
            generated_at="fixed",
        )

        subject = result["subjects"][0]
        gap = next(row for row in subject["state_runs"] if row["status"] == "occluded")
        self.assertEqual((gap["start_frame"], gap["end_frame"]), (5, 7))
        self.assertEqual(gap["position_source"], "linear_endpoint_prediction")
        self.assertFalse(gap["eligible_for_distance"])
        self.assertEqual(result["transition_events"][0]["status"], "occluded")
        self.assertTrue(result["transition_events"][0]["requires_review"])

    def test_short_non_occlusion_gap_is_predicted_but_not_observed(self) -> None:
        rows = [tracklet("s1", 0, 4), tracklet("t1", 7, 10)]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1"),
            rows,
            quality("s1", "t1"),
            fps=FPS,
            generated_at="fixed",
        )

        gap = next(row for row in result["subjects"][0]["state_runs"] if row["status"] == "predicted")
        self.assertEqual((gap["start_frame"], gap["end_frame"]), (5, 6))
        self.assertFalse(gap["eligible_for_heatmap"])

    def test_cross_team_spatial_blocker_marks_short_gap_as_occluded(self) -> None:
        rows = [
            tracklet("s1", 0, 4),
            tracklet("t1", 7, 10),
            tracklet("blocker", 4, 7, team_label="B"),
        ]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1"),
            rows,
            quality("s1", "t1", "blocker"),
            fps=FPS,
            generated_at="fixed",
        )

        gap = next(row for row in result["subjects"][0]["state_runs"] if row["status"] == "occluded")
        self.assertEqual(gap["reason"], "gap_with_local_contact_occlusion_evidence")
        self.assertEqual(
            gap["local_occlusion_evidence"]["blocker_tracklet_ids"],
            ["blocker"],
        )
        self.assertEqual(
            gap["local_occlusion_evidence"]["cross_team_overlap_frame_count"],
            2,
        )
        self.assertEqual(
            gap["local_occlusion_evidence"]["endpoint_blocker_tracklet_ids"],
            ["blocker"],
        )

    def test_cross_team_overlap_without_endpoint_contact_remains_predicted(self) -> None:
        rows = [
            tracklet("s1", 0, 4),
            tracklet("t1", 7, 10),
            tracklet("blocker", 5, 6, team_label="B"),
        ]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1"),
            rows,
            quality("s1", "t1", "blocker"),
            fps=FPS,
            generated_at="fixed",
        )

        gap = next(row for row in result["subjects"][0]["state_runs"] if row["status"] == "predicted")
        self.assertFalse(gap["local_occlusion_evidence"]["supported"])

    def test_same_team_overlap_without_event_remains_predicted(self) -> None:
        rows = [
            tracklet("s1", 0, 4),
            tracklet("t1", 7, 10),
            tracklet("duplicate", 5, 6, team_label="A"),
        ]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1"),
            rows,
            quality("s1", "t1", "duplicate"),
            fps=FPS,
            generated_at="fixed",
        )

        gap = next(row for row in result["subjects"][0]["state_runs"] if row["status"] == "predicted")
        self.assertFalse(gap["local_occlusion_evidence"]["supported"])

    def test_long_gap_abstains_as_missing(self) -> None:
        rows = [tracklet("s1", 0, 4), tracklet("t1", 30, 35)]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1", occlusion=True),
            rows,
            quality("s1", "t1"),
            fps=FPS,
            generated_at="fixed",
        )

        gap = next(row for row in result["subjects"][0]["state_runs"] if row["status"] == "missing")
        self.assertEqual(gap["reason"], "insufficient_evidence_for_shadow_prediction")
        self.assertIsNone(gap["position_source"])

    def test_unreliable_detected_frame_is_not_stats_eligible(self) -> None:
        rows = [tracklet("s1", 0, 2), tracklet("t1", 3, 5)]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1"),
            rows,
            quality("s1", "t1", unreliable={"s1": [(1, 1)]}),
            fps=FPS,
            generated_at="fixed",
        )

        observation = next(row for row in result["subjects"][0]["observations"] if row["frame"] == 1)
        self.assertEqual(observation["status"], "detected")
        self.assertFalse(observation["footpoint_reliable"])
        self.assertFalse(observation["eligible_for_distance"])
        self.assertEqual(result["summary"]["trusted_detected_frames"], 5)

    def test_direct_tracklet_transition_is_still_auditable(self) -> None:
        rows = [tracklet("s1", 0, 2), tracklet("t1", 3, 5)]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1"),
            rows,
            quality("s1", "t1"),
            fps=FPS,
            generated_at="fixed",
        )

        event = result["transition_events"][0]
        self.assertEqual(event["status"], "direct_transition")
        self.assertEqual(event["frame_delta"], 1)
        self.assertEqual(event["overlap_frames"], 0)
        self.assertEqual(result["summary"]["transition_events"], 1)

    def test_overlapping_tracklet_transition_is_still_auditable(self) -> None:
        rows = [tracklet("s1", 0, 4), tracklet("t1", 3, 6)]
        result = build_shadow_resolved_timeline(
            offline("s1", "t1"),
            rows,
            quality("s1", "t1"),
            fps=FPS,
            generated_at="fixed",
        )

        event = result["transition_events"][0]
        self.assertEqual(event["status"], "overlap_transition")
        self.assertEqual(event["frame_delta"], -1)
        self.assertEqual(event["overlap_frames"], 2)
        self.assertGreater(result["summary"]["duplicate_observation_frames_resolved"], 0)

    def test_output_is_deterministic_for_equal_input(self) -> None:
        rows = [tracklet("s1", 0, 4), tracklet("t1", 8, 12)]
        args = (offline("s1", "t1", occlusion=True), rows, quality("s1", "t1"))
        first = build_shadow_resolved_timeline(*args, fps=FPS, generated_at="fixed")
        second = build_shadow_resolved_timeline(*args, fps=FPS, generated_at="fixed")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
