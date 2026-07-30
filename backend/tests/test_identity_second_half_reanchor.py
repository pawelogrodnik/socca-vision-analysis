from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_cached_reanchor_keeps_pre_reanchor_suggestions(self) -> None:
        selection = copy.deepcopy(self.selection)
        selection["second_half"][
            "safely_resolved_players_before_reanchor"
        ] = [
            {
                "player_id": "player-1",
                "player_name": "Pawel",
                "team_label": "A",
                "tracklet_ids": ["tracklet-a"],
            }
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            match_path = Path(temporary_directory)
            selection_path = (
                match_path
                / "identity_second_half_reanchor"
                / "identity_second_half_reanchor_selection.json"
            )
            selection_path.parent.mkdir(parents=True)
            selection_path.write_text("{}", encoding="utf-8")
            with (
                patch(
                    "app.services.identity_second_half_reanchor."
                    "_load_phase_config",
                    return_value={"second_half_start_time_sec": 50.0},
                ),
                patch(
                    "app.services.identity_second_half_reanchor."
                    "_load_required_artifact",
                    return_value={"video": {"fps": 30.0}},
                ),
                patch(
                    "app.services.identity_second_half_reanchor."
                    "_safely_resolved_h2_players",
                    return_value=[],
                ),
                patch(
                    "app.services.identity_second_half_reanchor."
                    "load_identity_json",
                    return_value=selection,
                ),
                patch(
                    "app.services.identity_second_half_reanchor."
                    "identity_audit_selection_artifacts_exist",
                    return_value=True,
                ),
            ):
                document = prepare_second_half_identity_reanchor(
                    match_path,
                    match_path / "video.mp4",
                    self.match,
                )

        suggestion = document["frames"][0]["observations"][0][
            "suggested_player"
        ]
        self.assertEqual(suggestion["player_id"], "player-1")
        self.assertEqual(suggestion["suggestion_source"], "h1_safe_lineage")

    def test_cross_capture_domain_blocks_tracklet_id_name_suggestion(
        self,
    ) -> None:
        selection = copy.deepcopy(self.selection)
        selection["second_half"].update(
            {
                "h1_safe_lineage_allowed": False,
                "h1_safe_lineage_block_reason": (
                    "independent_capture_domains_have_unrelated_tracklet_ids"
                ),
                "safely_resolved_players_before_reanchor": [
                    {
                        "player_id": "player-1",
                        "player_name": "Pawel",
                        "team_label": "A",
                        "tracklet_ids": ["tracklet-a"],
                    }
                ],
            }
        )

        document = build_second_half_identity_reanchor_document(
            selection,
            self.match,
        )

        self.assertIsNone(
            document["frames"][0]["observations"][0]["suggested_player"]
        )
        self.assertFalse(
            document["second_half"]["h1_safe_lineage_allowed"]
        )

    def test_team_a_player_is_never_suggested_for_team_b_observation(
        self,
    ) -> None:
        selection = copy.deepcopy(self.selection)
        selection["selected_frames"][0]["visible_detections"][0][
            "team_label"
        ] = "B"
        safely_resolved_players = [
            {
                "player_id": "player-1",
                "player_name": "Pawel",
                "team_label": "A",
                "tracklet_ids": ["tracklet-a"],
            }
        ]

        document = build_second_half_identity_reanchor_document(
            selection,
            self.match,
            safely_resolved_players=safely_resolved_players,
        )

        self.assertIsNone(
            document["frames"][0]["observations"][0]["suggested_player"]
        )

    def test_cross_team_reid_suggestion_is_filtered(self) -> None:
        selection = copy.deepcopy(self.selection)
        selection["selected_frames"][0]["visible_detections"][0][
            "team_label"
        ] = "B"
        selection["reid_advisory_suggestions"] = [
            {
                "candidate_subject_id": "subject-b",
                "team_label": "B",
                "tracklet_ids": ["tracklet-a"],
                "suggestions": [
                    {
                        "player_id": "player-1",
                        "player_name": "Pawel",
                        "rank": 1,
                    }
                ],
            }
        ]

        document = build_second_half_identity_reanchor_document(
            selection,
            self.match,
            safely_resolved_players=[],
        )
        observation = document["frames"][0]["observations"][0]

        self.assertIsNone(observation["suggested_player"])
        self.assertEqual(observation["reid_suggestions"], [])

    def test_suppressed_reid_suggestion_is_not_shown(self) -> None:
        selection = copy.deepcopy(self.selection)
        selection["reid_advisory_suggestions"] = [
            {
                "candidate_subject_id": "subject-a",
                "team_label": "A",
                "tracklet_ids": ["tracklet-a"],
                "suggestions": [
                    {
                        "player_id": "player-1",
                        "player_name": "Pawel",
                        "rank": 1,
                        "display_eligible": False,
                    }
                ],
            }
        ]

        document = build_second_half_identity_reanchor_document(
            selection,
            self.match,
            safely_resolved_players=[],
        )
        observation = document["frames"][0]["observations"][0]

        self.assertIsNone(observation["suggested_player"])
        self.assertEqual(observation["reid_suggestions"], [])
        self.assertEqual(
            observation["reid_suggestion_notice"]["status"],
            "hidden_low_quality",
        )


if __name__ == "__main__":
    unittest.main()
