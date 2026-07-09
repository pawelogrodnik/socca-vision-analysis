from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.identity_review_gallery import build_identity_review_gallery


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
            self.assertEqual(gallery["summary"]["crops"], 2)
            crops = gallery["players"][0]["stints"][0]["crops"]
            self.assertEqual([crop["frame"] for crop in crops], [0, 20])
            self.assertTrue((path / crops[0]["artifact"]).exists())
            self.assertTrue((path / "identity_review_gallery.json").exists())


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
