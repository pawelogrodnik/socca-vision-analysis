from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.identity_crop_review import (
    build_identity_crop_review,
    save_identity_crop_assignments,
)


class IdentityCropReviewTests(unittest.TestCase):
    def test_assignments_are_additive_and_create_crop_derived_stints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            write_json(path / "stable_players.json", stable_doc())
            write_json(path / "identity_review_gallery.json", gallery_doc())

            save_identity_crop_assignments(
                path,
                match_meta(),
                [{"artifact": "identity_review/c1.jpg", "status": "assigned", "player_id": "p1"}],
            )
            state = save_identity_crop_assignments(
                path,
                match_meta(),
                [
                    {"artifact": "identity_review/c2.jpg", "status": "assigned", "player_id": "p2"},
                    {"artifact": "identity_review/c3.jpg", "status": "assigned", "player_id": "p1"},
                ],
            )

            self.assertEqual(state["summary"]["reviewed"], 3)
            self.assertEqual(state["summary"]["remaining"], 0)
            self.assertEqual(state["summary"]["by_player"], {"p1": 2, "p2": 1})
            self.assertEqual(state["summary"]["derived_stints"], 3)
            assignments = json.loads((path / "player_identity_assignments.json").read_text(encoding="utf-8"))
            crop_derived = [
                item for item in assignments["assignments"] if item.get("assignment_source") == "identity_crop_gallery"
            ]
            self.assertEqual(len(crop_derived), 3)
            self.assertTrue(all(item.get("start_frame") is not None for item in crop_derived))

    def test_unassign_removes_crop_from_reviewed_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            write_json(path / "stable_players.json", stable_doc())
            write_json(path / "identity_review_gallery.json", gallery_doc())
            save_identity_crop_assignments(
                path,
                match_meta(),
                [{"artifact": "identity_review/c1.jpg", "status": "assigned", "player_id": "p1"}],
            )

            state = save_identity_crop_assignments(
                path,
                match_meta(),
                [{"artifact": "identity_review/c1.jpg", "status": "unassigned"}],
            )

            self.assertEqual(state["summary"]["reviewed"], 0)
            self.assertEqual(state["summary"]["remaining"], 3)
            self.assertEqual(build_identity_crop_review(path, match_meta())["summary"]["derived_stints"], 0)

    def test_representative_crop_propagates_to_non_contiguous_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            write_json(path / "stable_players.json", stable_doc())
            gallery = gallery_doc()
            gallery["players"][0]["stints"][0]["crops"] = [
                {
                    "artifact": "identity_review/c1.jpg",
                    "frame": 5,
                    "time_sec": 0.5,
                    "confidence": 0.9,
                    "coverage_intervals": [
                        {"start_frame": 0, "end_frame": 9, "start_time_sec": 0.0, "end_time_sec": 0.9},
                        {"start_frame": 20, "end_frame": 29, "start_time_sec": 2.0, "end_time_sec": 2.9},
                    ],
                },
                {
                    "artifact": "identity_review/c2.jpg",
                    "frame": 15,
                    "time_sec": 1.5,
                    "confidence": 0.8,
                    "coverage_intervals": [
                        {"start_frame": 10, "end_frame": 19, "start_time_sec": 1.0, "end_time_sec": 1.9},
                    ],
                },
            ]
            write_json(path / "identity_review_gallery.json", gallery)

            state = save_identity_crop_assignments(
                path,
                match_meta(),
                [{"artifact": "identity_review/c1.jpg", "status": "assigned", "player_id": "p1"}],
            )

            self.assertEqual(state["summary"]["derived_stints"], 2)
            self.assertEqual(state["summary"]["covered_frames"], 20)


def stable_doc() -> dict:
    return {
        "identity_semantics": "stint_first",
        "players": [
            {
                "stable_subject_id": "slot-a01",
                "stable_player_id": "A01",
                "slot_id": "A01",
                "team_label": "A",
                "team_id": "team-a",
                "team_name": "Corgi",
                "stints": [{"stint_id": "A01-S01", "start_frame": 0, "end_frame": 29}],
            }
        ],
    }


def gallery_doc() -> dict:
    return {
        "players": [
            {
                "stable_subject_id": "slot-a01",
                "stable_player_id": "A01",
                "slot_id": "A01",
                "team_label": "A",
                "team_id": "team-a",
                "team_name": "Corgi",
                "stints": [
                    {
                        "stint_id": "A01-S01",
                        "parent_stint_id": "A01-S01",
                        "start_frame": 0,
                        "end_frame": 29,
                        "start_time_sec": 0.0,
                        "end_time_sec": 2.9,
                        "crops": [
                            {"artifact": "identity_review/c1.jpg", "frame": 5, "time_sec": 0.5, "confidence": 0.9},
                            {"artifact": "identity_review/c2.jpg", "frame": 15, "time_sec": 1.5, "confidence": 0.8},
                            {"artifact": "identity_review/c3.jpg", "frame": 25, "time_sec": 2.5, "confidence": 0.95},
                        ],
                    }
                ],
            }
        ]
    }


def match_meta() -> dict:
    return {
        "teams": [
            {
                "id": "team-a",
                "name": "Corgi",
                "players": [
                    {"id": "p1", "name": "Piotrek"},
                    {"id": "p2", "name": "Andrzej"},
                ],
            }
        ]
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
