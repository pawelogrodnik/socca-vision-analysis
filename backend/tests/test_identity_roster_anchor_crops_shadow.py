from __future__ import annotations

import unittest

from app.services.identity_roster_anchor_crops_shadow import (
    build_identity_roster_anchor_crops_shadow,
)


def observation(
    frame: int,
    *,
    tracklet_id: str = "track-1",
    status: str = "detected",
    confidence: float = 0.9,
    appearance_reliable: bool = True,
    footpoint_reliable: bool = True,
    play_area_status: str = "inside_play",
    bbox: list[float] | None = None,
) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 30.0,
        "tracklet_id": tracklet_id,
        "status": status,
        "confidence": confidence,
        "team_confidence": 0.9,
        "appearance_reliable_ratio": 0.95,
        "appearance_reliable": appearance_reliable,
        "footpoint_reliable": footpoint_reliable,
        "play_area_status": play_area_status,
        "quality_class": "trusted",
        "bbox_xyxy": bbox or [100.0, 100.0, 140.0, 210.0],
    }


def build(
    observations: list[dict],
    *,
    other_subjects: list[dict] | None = None,
    occlusion_events: list[dict] | None = None,
    generated_at: str = "fixed",
) -> dict[str, dict]:
    roster_anchor = {
        "algorithm": {"name": "p115"},
        "cards": [
            {
                "anchor_key": "anchor-1",
                "candidate_subject_id": "shadow-a-1",
                "team_label": "A",
                "role": "field_player",
                "start_frame": 0,
                "end_frame": 99,
                "status": "unresolved",
            }
        ],
    }
    timeline = {
        "algorithm": {"name": "timeline"},
        "subjects": [
            {
                "shadow_subject_id": "shadow-a-1",
                "observations": observations,
            },
            *(other_subjects or []),
        ],
    }
    occlusions = {
        "algorithm": {"name": "occlusions"},
        "events": occlusion_events or [],
    }
    return build_identity_roster_anchor_crops_shadow(
        roster_anchor,
        timeline,
        occlusion_doc=occlusions,
        generated_at=generated_at,
    )


class IdentityRosterAnchorCropsShadowTests(unittest.TestCase):
    def test_selects_five_reliable_temporally_diverse_crops(self) -> None:
        frames = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
        documents = build([observation(frame, tracklet_id=f"track-{frame // 30}") for frame in frames])
        artifact = documents["identity_roster_anchor_crops_shadow"]
        card = artifact["cards"][0]

        self.assertEqual(card["status"], "ready_for_visual_audit")
        self.assertEqual(card["selected_crop_count"], 5)
        selected_frames = [crop["frame"] for crop in card["anchor_crops"]]
        self.assertEqual(selected_frames, sorted(selected_frames))
        self.assertEqual(len(set(selected_frames)), 5)
        self.assertLessEqual(selected_frames[0], 20)
        self.assertGreaterEqual(selected_frames[-1], 80)
        self.assertEqual(artifact["safety"]["automatic_assignments"], 0)
        self.assertFalse(artifact["safety"]["eligible_for_player_stats"])

    def test_rejects_unreliable_outside_small_and_occluded_observations(self) -> None:
        rows = [
            observation(5, status="ambiguous"),
            observation(15, appearance_reliable=False),
            observation(25, footpoint_reliable=False),
            observation(35, play_area_status="outside_play"),
            observation(45, confidence=0.4),
            observation(55, bbox=[100.0, 100.0, 104.0, 110.0]),
            observation(65, tracklet_id="track-occluded"),
            observation(75),
        ]
        documents = build(
            rows,
            occlusion_events=[
                {"start_frame": 64, "end_frame": 66, "tracklet_ids": ["track-occluded"]}
            ],
        )
        card = documents["identity_roster_anchor_crops_shadow"]["cards"][0]

        self.assertEqual([crop["frame"] for crop in card["anchor_crops"]], [75])
        self.assertEqual(card["status"], "insufficient_reliable_crops")
        self.assertEqual(card["rejected_observations"]["not_detected"], 1)
        self.assertEqual(card["rejected_observations"]["appearance_unreliable"], 1)
        self.assertEqual(card["rejected_observations"]["footpoint_unreliable"], 1)
        self.assertEqual(card["rejected_observations"]["outside_play_area"], 1)
        self.assertEqual(card["rejected_observations"]["low_detection_confidence"], 1)
        self.assertEqual(card["rejected_observations"]["bbox_too_small"], 1)
        self.assertEqual(card["rejected_observations"]["near_occlusion_event"], 1)

    def test_rejects_crops_overlapping_another_person_in_same_frame(self) -> None:
        rows = [
            observation(10, bbox=[100.0, 100.0, 140.0, 210.0]),
            observation(20, bbox=[200.0, 100.0, 240.0, 210.0]),
            observation(30, bbox=[300.0, 100.0, 340.0, 210.0]),
        ]
        documents = build(
            rows,
            other_subjects=[
                {
                    "shadow_subject_id": "shadow-b-1",
                    "observations": [
                        observation(
                            20,
                            tracklet_id="other-track",
                            bbox=[222.0, 112.0, 262.0, 215.0],
                        )
                    ],
                }
            ],
        )
        card = documents["identity_roster_anchor_crops_shadow"]["cards"][0]

        self.assertEqual([crop["frame"] for crop in card["anchor_crops"]], [10, 30])
        self.assertEqual(card["rejected_observations"]["overlaps_nearby_person"], 1)

    def test_no_reliable_observation_is_explicit(self) -> None:
        documents = build([observation(10, status="predicted")])
        card = documents["identity_roster_anchor_crops_shadow"]["cards"][0]

        self.assertEqual(card["status"], "no_reliable_crops")
        self.assertEqual(card["anchor_crops"], [])
        self.assertEqual(
            documents["identity_roster_anchor_crops_shadow_report"]["status"],
            "no_reliable_crops",
        )

    def test_selection_is_deterministic_for_reordered_observations(self) -> None:
        rows = [observation(frame, tracklet_id=f"track-{frame // 20}") for frame in range(0, 100, 5)]
        first = build(rows)
        second = build(list(reversed(rows)))

        first_crops = first["identity_roster_anchor_crops_shadow"]["cards"][0]["anchor_crops"]
        second_crops = second["identity_roster_anchor_crops_shadow"]["cards"][0]["anchor_crops"]
        self.assertEqual(first_crops, second_crops)


if __name__ == "__main__":
    unittest.main()
