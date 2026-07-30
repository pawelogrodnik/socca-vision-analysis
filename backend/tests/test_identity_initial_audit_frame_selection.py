from __future__ import annotations

import copy
import unittest

from app.services.identity_initial_audit_frame_selection import (
    build_initial_identity_audit_frame_selection,
    collect_candidate_frame_numbers,
    filter_identity_audit_observations,
)


class InitialIdentityAuditFrameSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fps = 10.0
        self.global_identity = _global_identity()
        self.tracklets = _tracklets()
        self.analysis_report = {
            "run_id": "frozen-test",
            "video": {
                "fps": self.fps,
                "frame_count": 1000,
                "duration_sec": 100.0,
                "width": 1000,
                "height": 600,
            },
        }
        self.visual_metrics = {
            frame: {"blur_variance": 200.0}
            for frame in range(0, 1000, 10)
        }
        self.visual_metrics[200] = {"blur_variance": 1.0}

    def build(self, **overrides):
        return build_initial_identity_audit_frame_selection(
            self.global_identity,
            self.tracklets,
            self.analysis_report,
            frame_visual_metrics=self.visual_metrics,
            parameters={
                "candidate_stride_frames": 10,
                "target_frame_count": 8,
                "minimum_visible_players": 4,
                **overrides,
            },
            generated_at="2026-01-01T00:00:00+00:00",
        )

    def test_is_deterministic_and_does_not_mutate_inputs(self) -> None:
        identity_before = copy.deepcopy(self.global_identity)
        first = self.build()
        second = self.build()
        self.assertEqual(first["selected_frames"], second["selected_frames"])
        self.assertEqual(first["selection_digest"], second["selection_digest"])
        self.assertEqual(self.global_identity, identity_before)

    def test_respects_default_and_hard_frame_budgets(self) -> None:
        default = self.build()
        hard_cap = self.build(target_frame_count=20, maximum_frame_count=25)
        self.assertLessEqual(len(default["selected_frames"]), 8)
        self.assertLessEqual(len(hard_cap["selected_frames"]), 10)
        self.assertEqual(hard_cap["summary"]["maximum_frame_count"], 10)

    def test_selected_frames_are_spaced_and_beat_random_baseline(self) -> None:
        document = self.build()
        times = sorted(row["time_sec"] for row in document["selected_frames"])
        self.assertTrue(
            all(right - left >= 4.0 for left, right in zip(times, times[1:]))
        )
        self.assertEqual(document["summary"]["near_duplicate_pairs"], 0)
        self.assertTrue(document["summary"]["easier_than_random_baseline"])

    def test_avoids_blurry_and_overlapping_frame(self) -> None:
        document = self.build()
        selected_frames = {row["frame"] for row in document["selected_frames"]}
        self.assertNotIn(200, selected_frames)
        self.assertNotIn(400, selected_frames)

    def test_artifact_contains_provenance_and_operator_safety(self) -> None:
        document = self.build()
        row = document["selected_frames"][0]
        self.assertTrue(row["visible_detections"])
        self.assertIn("stable_subject_id", row["visible_detections"][0])
        self.assertIn("tracklet_id", row["visible_detections"][0])
        self.assertTrue(row["thumbnail_artifact"].endswith("-thumb.jpg"))
        self.assertFalse(document["safety"]["raw_coordinates_required_from_operator"])
        self.assertTrue(document["safety"]["production_identity_untouched"])

    def test_candidate_frame_collection_uses_stride(self) -> None:
        frames = collect_candidate_frame_numbers(
            self.global_identity,
            stride_frames=30,
        )
        self.assertTrue(frames)
        self.assertTrue(all(frame % 30 == 0 for frame in frames))

    def test_excludes_low_confidence_and_same_team_nested_duplicates(self) -> None:
        observations = [
            {
                "stable_subject_id": "team-a-player",
                "team_label": "A",
                "bbox_xyxy": [100.0, 100.0, 140.0, 200.0],
                "confidence": 0.90,
            },
            {
                "stable_subject_id": "team-a-shadow",
                "team_label": "A",
                "bbox_xyxy": [300.0, 300.0, 350.0, 340.0],
                "confidence": 0.05,
            },
            {
                "stable_subject_id": "team-b-player",
                "team_label": "B",
                "bbox_xyxy": [500.0, 100.0, 550.0, 210.0],
                "confidence": 0.80,
            },
            {
                "stable_subject_id": "team-b-duplicate",
                "team_label": "B",
                "bbox_xyxy": [505.0, 105.0, 540.0, 190.0],
                "confidence": 0.95,
            },
        ]

        filtered, summary = filter_identity_audit_observations(
            observations,
            minimum_confidence=0.15,
            duplicate_containment_threshold=0.80,
        )

        self.assertEqual(summary["low_confidence"], 1)
        self.assertEqual(summary["same_team_duplicate"], 1)
        self.assertEqual(
            [(row["stable_subject_id"], row["team_label"]) for row in filtered],
            [("team-a-player", "A"), ("team-b-player", "B")],
        )


def _global_identity() -> dict:
    slots = []
    frame_rows = [{"frame": frame} for frame in range(0, 1000, 10)]
    for subject_index in range(8):
        positions = []
        for frame in range(0, 1000, 10):
            x1 = 70 + subject_index * 105
            y1 = 160 + (subject_index % 2) * 80
            if frame == 400 and subject_index in {0, 1}:
                x1 = 100
                y1 = 200
            positions.append(
                {
                    "frame": frame,
                    "status": "detected",
                    "visual_trusted": True,
                    "play_area_status": "inside",
                    "bbox_xyxy": [x1, y1, x1 + 45, y1 + 110],
                    "tracklet_id": f"tracklet-{subject_index}",
                    "raw_track_id": subject_index,
                    "confidence": 0.9,
                    "source": "detected",
                    "stint_id": f"stint-{subject_index}",
                }
            )
        slots.append(
            {
                "slot_id": f"slot-{subject_index}",
                "stable_subject_id": f"subject-{subject_index}",
                "stable_player_id": f"player-{subject_index}",
                "team_label": "A" if subject_index < 4 else "B",
                "role": "field_player",
                "overlay_positions": positions,
                "blocked_identity_switches": (
                    [{"frame": 600}] if subject_index == 0 else []
                ),
            }
        )
    return {"slots": slots, "frames": frame_rows}


def _tracklets() -> dict:
    return {
        "tracklets": [
            {
                "tracklet_id": f"tracklet-{subject_index}",
                "positions_m": [
                    {"frame": frame}
                    for frame in range(0, 1000, 10)
                ],
            }
            for subject_index in range(8)
        ]
    }


if __name__ == "__main__":
    unittest.main()
