from __future__ import annotations

import unittest

from app.services.identity_shadow_timeline import build_shadow_resolved_timeline


FPS = 30.0


def tracklet(tracklet_id: str, frames: list[int], *, team: str = "A") -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team,
        "positions": [
            {
                "frame": frame,
                "time_sec": frame / FPS,
                "pitch_m": [frame / 30.0, 10.0],
                "bbox_xyxy": [10, 10, 30, 70],
                "confidence": 0.9,
                "play_area_status": "inside_play",
            }
            for frame in frames
        ],
    }


def offline_subject(tracklet_ids: list[str]) -> dict:
    return {
        "accepted_edges": [],
        "subjects": [
            {
                "shadow_subject_id": "shadow-a-1",
                "team_label": "A",
                "tracklet_ids": tracklet_ids,
            }
        ],
    }


def quality(tracklet_id: str, **overrides: object) -> dict:
    row = {
        "tracklet_id": tracklet_id,
        "quality_class": "recoverable",
        "team_confidence": 1.0,
        "appearance_reliable_ratio": 1.0,
        "unreliable_footpoint_ranges": [],
        "unreliable_appearance_ranges": [],
    }
    row.update(overrides)
    return {"tracklets": [row]}


class IdentityShadowTimelineCalibrationTests(unittest.TestCase):
    def test_internal_gap_uses_nearby_cross_team_occlusion(self) -> None:
        rows = [tracklet("t1", [0, 1, 5, 6])]
        document = build_shadow_resolved_timeline(
            offline_subject(["t1"]),
            rows,
            quality("t1"),
            fps=FPS,
            occlusion_doc={
                "events": [
                    {
                        "event_id": "occ-1",
                        "start_frame": 4,
                        "end_frame": 4,
                        "tracklet_ids": ["t1", "other"],
                        "team_labels": ["A", "B"],
                        "evidence": ["bbox_overlap"],
                    }
                ]
            },
            generated_at="fixed",
        )

        gap = next(
            row
            for row in document["subjects"][0]["state_runs"]
            if row["status"] != "detected"
        )
        self.assertEqual(gap["status"], "occluded")
        self.assertEqual(gap["occlusion_event_ids"], ["occ-1"])

    def test_short_same_raw_gap_is_predicted_without_counting_for_stats(self) -> None:
        rows = [tracklet("t1", [0, 1, 10, 11])]
        document = build_shadow_resolved_timeline(
            offline_subject(["t1"]),
            rows,
            quality(
                "t1",
                unreliable_footpoint_ranges=[{"start_frame": 1, "end_frame": 10}],
            ),
            fps=FPS,
            generated_at="fixed",
        )

        gap = next(
            row
            for row in document["subjects"][0]["state_runs"]
            if row["status"] != "detected"
        )
        self.assertEqual(gap["status"], "predicted")
        self.assertFalse(gap["eligible_for_distance"])
        self.assertFalse(gap["eligible_for_heatmap"])

    def test_cross_team_occlusion_with_unreliable_appearance_requires_review(self) -> None:
        rows = [tracklet("t1", [0, 1, 18, 19])]
        document = build_shadow_resolved_timeline(
            offline_subject(["t1"]),
            rows,
            quality(
                "t1",
                team_confidence=0.6,
                appearance_reliable_ratio=0.6,
                unreliable_appearance_ranges=[{"start_frame": 18, "end_frame": 19}],
            ),
            fps=FPS,
            occlusion_doc={
                "events": [
                    {
                        "event_id": "occ-risk",
                        "start_frame": 18,
                        "end_frame": 18,
                        "tracklet_ids": ["t1", "other"],
                        "team_labels": ["A", "B"],
                    }
                ]
            },
            generated_at="fixed",
        )

        gap = next(
            row
            for row in document["subjects"][0]["state_runs"]
            if row["status"] != "detected"
        )
        self.assertEqual(gap["identity_continuity_status"], "uncertain")
        self.assertEqual(document["summary"]["gap_identity_reviews_required"], 1)


if __name__ == "__main__":
    unittest.main()
