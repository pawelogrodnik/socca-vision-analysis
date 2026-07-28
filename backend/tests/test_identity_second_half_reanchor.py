from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from app.services.identity_second_half_reanchor import (
    _second_half_start_time,
    build_second_half_identity_reanchor_document,
    prepare_second_half_identity_reanchor,
)


class SecondHalfIdentityReanchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match = {
            "teams": [
                {
                    "id": "team-a",
                    "name": "Corgi",
                    "players": [
                        {
                            "id": "player-1",
                            "name": "Pawel",
                            "number": "92",
                            "role": "player",
                        }
                    ],
                }
            ]
        }
        self.selection = {
            "selection_digest": "selection-h2",
            "video": {
                "fps": 30.0,
                "frame_count": 3000,
                "duration_sec": 100.0,
                "width": 1920,
                "height": 1080,
            },
            "second_half": {
                "start_time_sec": 50.0,
                "start_frame": 1500,
            },
            "selected_frames": [
                {
                    "frame": 1500 + index * 30,
                    "time_sec": 50.0 + index,
                    "full_frame_artifact": (
                        "identity_second_half_reanchor/frames/"
                        f"frame-{1500 + index * 30:06d}.jpg"
                    ),
                    "visible_detections": [
                        {
                            "stable_subject_id": f"subject-{index}",
                            "stable_player_id": "A01",
                            "slot_id": "A01",
                            "tracklet_id": "tracklet-a",
                            "raw_track_id": 12,
                            "stint_id": "A01-S01",
                            "team_label": "A",
                            "role": "field_player",
                            "source": "detected",
                            "bbox_xyxy": [100, 200, 180, 400],
                        }
                    ],
                }
                for index in range(5)
            ],
        }

    def test_second_half_start_requires_explicit_configuration(self) -> None:
        self.assertIsNone(_second_half_start_time(None))
        self.assertIsNone(_second_half_start_time({}))
        self.assertEqual(
            _second_half_start_time(
                {"second_half_start_time_sec": 1200.5}
            ),
            1200.5,
        )
        self.assertEqual(
            _second_half_start_time(
                {
                    "periods": [
                        {
                            "period_id": "h2",
                            "start_time_sec": 1220.0,
                        }
                    ]
                }
            ),
            1220.0,
        )

    def test_missing_phase_config_is_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            match_path = Path(temporary_directory)
            document = prepare_second_half_identity_reanchor(
                match_path,
                match_path / "video.mp4",
                self.match,
            )
        self.assertEqual(document["status"], "not_applicable")
        self.assertEqual(
            document["reason"],
            "second_half_not_configured",
        )
        self.assertEqual(document["frames"], [])

    def test_document_caps_frames_and_prefers_current_suggestions(self) -> None:
        selection_before = copy.deepcopy(self.selection)
        safely_resolved_players = [
            {
                "player_id": "player-1",
                "player_name": "Pawel",
                "team_label": "A",
                "tracklet_ids": ["tracklet-a"],
            }
        ]
        document = build_second_half_identity_reanchor_document(
            self.selection,
            self.match,
            safely_resolved_players=safely_resolved_players,
        )

        self.assertEqual(len(document["frames"]), 3)
        self.assertEqual(document["status"], "ready")
        self.assertTrue(document["summary"]["confirmation_first"])
        self.assertFalse(
            document["operator_contract"]["second_full_lineup_audit"]
        )
        self.assertEqual(
            document["frames"][0]["observations"][0]["suggested_player"][
                "player_id"
            ],
            "player-1",
        )
        self.assertEqual(self.selection, selection_before)


if __name__ == "__main__":
    unittest.main()
