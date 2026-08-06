from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_stats import build_reviewed_stats


class ReviewedIdentityStatsTests(unittest.TestCase):
    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_detected_positions_produce_movement_after_final_frame_safety(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 100,
            "duration_sec": 4.0,
            "source": "test",
            "filename": "video.mp4",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracklets = {
                "tracklets": [
                    _tracklet("confirmed", [0, 1, 2, 3, 4, 5, 6]),
                    _tracklet("unconfirmed", [0, 1]),
                    _tracklet("conflicted", [0, 1]),
                ]
            }
            tracklets["tracklets"][0]["positions_m"][-1]["visual_trusted"] = False
            tracklets["tracklets"][0]["positions_m"][-2]["pitch_m"] = None
            tracklets["tracklets"][0]["positions_m"][-2]["smoothed_pitch_m"] = None
            (root / "tracklets.json").write_text(
                json.dumps(tracklets), encoding="utf-8"
            )
            snapshot = {
                "semantic_digest": "snapshot",
                "tracklet_assignments": [
                    _assignment("confirmed", "confirmed", "p1"),
                    _assignment("unconfirmed", "unresolved", None),
                    _assignment("conflicted", "conflicted", None),
                ],
                "observation_overrides": [
                    _override("confirmed", 3, "false_detection"),
                    _override("confirmed", 4, "referee"),
                ],
                "observation_demotions": [
                    {
                        "tracklet_id": "confirmed",
                        "frame": 2,
                        "identity_status": "conflicted",
                        "canonical_player_id": None,
                    }
                ],
                "summary": {},
            }

            documents = build_reviewed_stats(root, snapshot, {})

            players = documents["reviewed_player_stats.json"]["players"]
            self.assertEqual(len(players), 1)
            player = players[0]
            self.assertEqual(player["player_id"], "p1")
            self.assertEqual(player["detected_frames"], 2)
            self.assertEqual(player["detected_time_sec"], 0.08)
            self.assertEqual(player["heatmap_samples"], 2)
            self.assertGreater(player["observed_distance_m"], 0)
            self.assertGreater(player["total_distance_m"], 0)
            self.assertAlmostEqual(player["total_distance_m"], 0.1)
            self.assertGreater(player["accepted_movement_segments"], 0)
            heatmap = documents["reviewed_player_heatmaps.json"]["heatmaps"][0]
            self.assertEqual(heatmap["samples"], 2)


def _tracklet(tracklet_id: str, frames: list[int]) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "positions_m": [
            {
                "frame": frame,
                "time_sec": frame / 25.0,
                "status": "detected",
                "pitch_m": [float(frame), 10.0],
                "smoothed_pitch_m": [float(frame) * 0.1, 10.0],
                "bbox_xyxy": [0, 0, 10, 20],
            }
            for frame in frames
        ],
    }


def _assignment(tracklet_id: str, status: str, player_id: str | None) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "candidate_subject_id": f"subject-{tracklet_id}",
        "team_label": "A",
        "identity_status": status,
        "canonical_player_id": player_id,
        "player_name": "Player One" if player_id else None,
        "eligible_for_player_stats": bool(player_id),
    }


def _override(tracklet_id: str, frame: int, status: str) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "frame": frame,
        "identity_status": status,
        "canonical_player_id": None,
        "identity_source": "operator_seed_exact_observation",
    }


if __name__ == "__main__":
    unittest.main()
