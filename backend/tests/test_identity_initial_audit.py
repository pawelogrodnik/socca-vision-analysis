from __future__ import annotations

import copy
import unittest

from app.services.identity_initial_audit import (
    build_initial_identity_audit_document,
)


class InitialIdentityAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = {
            "selection_digest": "selection-1",
            "video": {
                "fps": 30.0,
                "frame_count": 300,
                "duration_sec": 10.0,
                "width": 1920,
                "height": 1080,
            },
            "selected_frames": [
                {
                    "frame": 30,
                    "time_sec": 1.0,
                    "full_frame_artifact": (
                        "identity_initial_audit/frames/frame-000030.jpg"
                    ),
                    "thumbnail_artifact": (
                        "identity_initial_audit/frames/frame-000030-thumb.jpg"
                    ),
                    "visible_detections": [
                        {
                            "stable_subject_id": "subject-a",
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
            ],
        }
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
                        },
                        {
                            "id": "player-2",
                            "name": "Mati GK",
                            "number": "goalkeeper",
                            "role": "goalkeeper",
                        },
                    ],
                },
                {
                    "id": "team-b",
                    "name": "Verisk",
                    "players": [],
                },
            ]
        }

    def test_build_is_deterministic_and_does_not_mutate_inputs(self) -> None:
        selection_before = copy.deepcopy(self.selection)
        first = build_initial_identity_audit_document(
            self.selection,
            self.match,
        )
        second = build_initial_identity_audit_document(
            self.selection,
            self.match,
        )
        self.assertEqual(first, second)
        self.assertEqual(self.selection, selection_before)

    def test_exposes_operator_actions_without_technical_input(self) -> None:
        document = build_initial_identity_audit_document(
            self.selection,
            self.match,
        )
        self.assertFalse(document["read_only"])
        self.assertIn("assign_roster_player", document["actions"])
        self.assertIn("skip", document["actions"])
        self.assertFalse(
            document["operator_contract"]["raw_coordinates_required"]
        )
        self.assertTrue(
            document["operator_contract"]["finish_before_full_coverage"]
        )
        self.assertTrue(document["operator_contract"]["decisions_persisted"])

    def test_roster_hides_placeholder_numbers(self) -> None:
        document = build_initial_identity_audit_document(
            self.selection,
            self.match,
        )
        players = document["roster"][0]["players"]
        self.assertEqual(players[0]["player_number"], "92")
        self.assertIsNone(players[1]["player_number"])

    def test_observation_has_stable_key_and_provenance(self) -> None:
        document = build_initial_identity_audit_document(
            self.selection,
            self.match,
        )
        observation = document["frames"][0]["observations"][0]
        self.assertTrue(
            observation["observation_key"].startswith("observation:v1:")
        )
        self.assertEqual(
            observation["provenance"]["tracklet_id"],
            "tracklet-a",
        )
        self.assertEqual(observation["bbox_xyxy"], [100.0, 200.0, 180.0, 400.0])

    def test_frame_budget_remains_below_operator_limit(self) -> None:
        selection = copy.deepcopy(self.selection)
        selection["selected_frames"] *= 12
        document = build_initial_identity_audit_document(selection, self.match)
        self.assertEqual(document["summary"]["maximum_frames"], 10)
        self.assertEqual(len(document["frames"]), 10)


if __name__ == "__main__":
    unittest.main()
