from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.identity_review_gallery import _migrate_crop_assignments, build_identity_review_gallery
from app.services.identity_review_segments import review_segments_for_player, save_identity_review_splits


class IdentityReviewGalleryTests(unittest.TestCase):
    def test_builds_sparse_crops_for_stint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            video_path = path / "video.avi"
            write_test_video(video_path)
            write_json(
                path / "stable_players.json",
                {
                    "schema_version": "0.1.0",
                    "identity_semantics": "stint_first",
                    "players": [
                        {
                            "stable_subject_id": "slot-a01",
                            "stable_player_id": "A01",
                            "slot_id": "A01",
                            "team_label": "A",
                            "team_id": "team-a",
                            "team_name": "White",
                            "status": "active",
                            "raw_track_ids": [1],
                            "tracklet_ids": ["1:1"],
                            "stints": [
                                {
                                    "stint_id": "A01-S01",
                                    "slot_id": "A01",
                                    "start_frame": 0,
                                    "end_frame": 20,
                                    "start_time_sec": 0.0,
                                    "end_time_sec": 0.8,
                                    "raw_track_ids": [1],
                                    "tracklet_ids": ["1:1"],
                                }
                            ],
                        }
                    ],
                    "summary": {"stable_players": 1},
                },
            )
            write_json(
                path / "tracks.json",
                [
                    {
                        "track_id": 1,
                        "positions": [
                            {"frame": 0, "time_sec": 0.0, "bbox_xyxy": [10, 10, 30, 50], "confidence": 0.8},
                            {"frame": 10, "time_sec": 0.4, "bbox_xyxy": [20, 12, 40, 52], "confidence": 0.9},
                            {"frame": 20, "time_sec": 0.8, "bbox_xyxy": [30, 14, 50, 54], "confidence": 0.7},
                        ],
                    }
                ],
            )

            gallery = build_identity_review_gallery(path, video_path, samples_per_stint=2, force=True)

            self.assertEqual(gallery["summary"]["stable_players"], 1)
            self.assertEqual(gallery["summary"]["stints"], 1)
            self.assertEqual(gallery["summary"]["crops"], 1)
            crops = gallery["players"][0]["stints"][0]["crops"]
            self.assertEqual(len(crops), 1)
            self.assertEqual(crops[0]["coverage_intervals"][0]["start_frame"], 0)
            self.assertEqual(crops[0]["coverage_intervals"][0]["end_frame"], 20)
            self.assertTrue((path / crops[0]["artifact"]).exists())
            self.assertTrue((path / "identity_review_gallery.json").exists())
            self.assertNotIn("_appearance_signature", crops[0])
            self.assertEqual(len(crops[0]["appearance_signature"]), 32)
            self.assertEqual(len(crops[0]["similarity_descriptor"]), 96)

    def test_splits_stint_at_confirmed_switch_and_manual_crop_boundary(self) -> None:
        player = {
            "stable_subject_id": "slot-a01",
            "stable_player_id": "A01",
            "stints": [
                {
                    "stint_id": "A01-S01",
                    "start_frame": 0,
                    "end_frame": 99,
                    "start_time_sec": 0.0,
                    "end_time_sec": 3.3,
                }
            ],
            "identity_events": [
                {"type": "confirmed_switch_with_competitor_accepted", "frame": 40}
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            split_doc = save_identity_review_splits(
                path,
                [
                    {
                        "stable_subject_id": "slot-a01",
                        "parent_stint_id": "A01-S01",
                        "frame": 70,
                    }
                ],
            )

            segments = review_segments_for_player(player, split_doc)

            self.assertEqual([segment["stint_id"] for segment in segments], ["A01-S01-R01", "A01-S01-R02", "A01-S01-R03"])
            self.assertEqual([(segment["start_frame"], segment["end_frame"]) for segment in segments], [(0, 39), (40, 69), (70, 99)])
            self.assertEqual(segments[1]["split_reasons"], ["confirmed_identity_switch"])
            self.assertEqual(segments[2]["split_reasons"], ["manual_crop_range"])

    def test_splits_stint_into_atomic_tracklet_fragments(self) -> None:
        player = {
            "stable_subject_id": "slot-a01",
            "stable_player_id": "A01",
            "stints": [
                {
                    "stint_id": "A01-S01",
                    "start_frame": 0,
                    "end_frame": 89,
                    "start_time_sec": 0.0,
                    "end_time_sec": 2.967,
                }
            ],
            "overlay_positions": [
                *[
                    {
                        "frame": frame,
                        "source": "detected",
                        "tracklet_id": "1:1",
                        "raw_track_id": 1,
                    }
                    for frame in range(0, 30)
                ],
                *[
                    {
                        "frame": frame,
                        "source": "detected",
                        "tracklet_id": "2:1",
                        "raw_track_id": 2,
                    }
                    for frame in range(30, 60)
                ],
                *[
                    {
                        "frame": frame,
                        "source": "detected",
                        "tracklet_id": "3:1",
                        "raw_track_id": 3,
                    }
                    for frame in range(60, 90)
                ],
            ],
        }

        segments = review_segments_for_player(player)

        self.assertEqual(
            [(segment["start_frame"], segment["end_frame"]) for segment in segments],
            [(0, 29), (30, 59), (60, 89)],
        )
        self.assertEqual(segments[0]["tracklet_ids"], ["1:1"])
        self.assertEqual(segments[1]["tracklet_ids"], ["2:1"])
        self.assertEqual(segments[2]["tracklet_ids"], ["3:1"])
        self.assertEqual(segments[1]["split_reasons"], ["tracklet_boundary"])

    def test_migrates_assignment_with_track_when_stable_slot_changes(self) -> None:
        old_crop = {
            "artifact": "identity_review/crops/slot-A01/old.jpg",
            "frame": 100,
            "time_sec": 3.333,
            "track_id": 42,
        }
        previous_gallery = {
            "players": [
                {
                    "stable_subject_id": "slot-A01",
                    "stable_player_id": "A01",
                    "stints": [{"stint_id": "A01-S01", "crops": [old_crop]}],
                }
            ]
        }
        previous_assignments = {
            "assignments": [
                {
                    "artifact": old_crop["artifact"],
                    "status": "assigned",
                    "player_id": "player-andrzej",
                    "player_name": "Andrzej",
                }
            ]
        }
        gallery = {
            "players": [
                {
                    "stable_subject_id": "slot-A02",
                    "stable_player_id": "A02",
                    "team_label": "A",
                    "stints": [
                        {
                            "stint_id": "A02-S03",
                            "crops": [
                                {
                                    "artifact": "identity_review/crops/slot-A02/new.jpg",
                                    "frame": 140,
                                    "time_sec": 4.667,
                                    "track_id": 42,
                                    "coverage_intervals": [{"start_frame": 90, "end_frame": 160}],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = _migrate_crop_assignments(
                Path(tmp),
                previous_gallery=previous_gallery,
                previous_assignments=previous_assignments,
                gallery=gallery,
            )
            migrated = json.loads((Path(tmp) / "identity_crop_assignments.json").read_text(encoding="utf-8"))

        self.assertEqual(result, {"migrated": 1, "unmatched": 0})
        self.assertEqual(migrated["assignments"][0]["stable_player_id"], "A02")
        self.assertEqual(migrated["assignments"][0]["player_name"], "Andrzej")


def write_test_video(path: Path) -> None:
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (80, 60))
    if not writer.isOpened():
        raise RuntimeError("Could not create test video")
    for frame_index in range(25):
        frame = np.zeros((60, 80, 3), dtype=np.uint8)
        frame[:, :] = (20, 30, 40)
        cv2.rectangle(frame, (10 + frame_index, 10), (30 + frame_index, 50), (240, 240, 240), -1)
        writer.write(frame)
    writer.release()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
