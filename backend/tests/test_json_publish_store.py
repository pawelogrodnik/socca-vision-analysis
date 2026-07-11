from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import json_publish_store


def package_fixture(match_id: str = "match-1") -> dict:
    return {
        "schema_version": "0.2.0",
        "match": {
            "id": match_id,
            "title": "Test match",
            "match_date": "2026-07-06",
            "season": "2026",
            "venue": "Orlik",
            "format": "7v7",
            "teams": [
                {
                    "id": "team-a",
                    "name": "Team A",
                    "color": "#ffffff",
                    "players": [
                        {"id": "p-a-1", "name": "Pawel", "number": "7", "role": "player"},
                    ],
                }
            ],
        },
        "team_count": 1,
        "player_count": 1,
        "analysis_report": {
            "tracks_count": 12,
            "frames_processed": 300,
            "detections_kept": 180,
            "warnings": ["low_ball_confidence"],
        },
        "stable_players": {
            "players": [
                {
                    "stable_player_id": "A01",
                    "stable_subject_id": "slot-a01",
                    "team_id": "team-a",
                    "team_label": "A",
                    "team_name": "Team A",
                    "duration_sec": 60.0,
                    "confidence": "high",
                    "confidence_score": 0.91,
                    "tracklet_ids": ["t1", "t2"],
                }
            ]
        },
    }


class JsonPublishStoreTests(unittest.TestCase):
    def _public_report_stub(self, package: dict, *, target_dir: Path, source_match_dir: Path | None = None) -> dict:
        report = {
            "schema_version": "0.1.0",
            "id": f"published-{package['match']['id']}",
            "match": {"id": package["match"]["id"], "title": package["match"]["title"]},
            "teams": [],
            "players": [],
        }
        (target_dir / "public_report.json").write_text(json.dumps(report), encoding="utf-8")
        return report

    def test_import_list_get_and_delete_match_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            published_dir = Path(tmp) / "published" / "matches"
            with (
                patch.object(json_publish_store, "PUBLISHED_MATCHES_DIR", published_dir),
                patch.object(json_publish_store, "write_public_match_report_bundle", side_effect=self._public_report_stub),
            ):
                imported = json_publish_store.import_match_package(package_fixture(), replace=False)

                self.assertEqual(imported["id"], "published-match-1")
                self.assertEqual(imported["storage"], "json")
                self.assertEqual(imported["package"]["match"]["id"], "match-1")
                self.assertEqual(imported["public_report"]["id"], "published-match-1")
                self.assertEqual(imported["teams"][0]["players_json"][0]["id"], "p-a-1")
                self.assertEqual(imported["players"][0]["name"], "Pawel")
                self.assertEqual(imported["stable_players"][0]["tracklet_ids"], ["t1", "t2"])

                listed = json_publish_store.list_published_matches()
                self.assertEqual([row["id"] for row in listed], ["published-match-1"])
                self.assertNotIn("package", listed[0])

                fetched = json_publish_store.get_published_match("published-match-1")
                self.assertEqual(fetched["warnings_count"], 1)
                self.assertEqual(fetched["tracks_count"], 12)

                deleted = json_publish_store.delete_published_match("published-match-1")
                self.assertEqual(deleted["id"], "published-match-1")
                self.assertEqual(json_publish_store.list_published_matches(), [])

    def test_import_blocks_duplicate_without_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            published_dir = Path(tmp) / "published" / "matches"
            with (
                patch.object(json_publish_store, "PUBLISHED_MATCHES_DIR", published_dir),
                patch.object(json_publish_store, "write_public_match_report_bundle", side_effect=self._public_report_stub),
            ):
                json_publish_store.import_match_package(package_fixture(), replace=False)
                with self.assertRaises(FileExistsError):
                    json_publish_store.import_match_package(package_fixture(), replace=False)

    def test_replace_preserves_created_at_and_updates_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            published_dir = Path(tmp) / "published" / "matches"
            with (
                patch.object(json_publish_store, "PUBLISHED_MATCHES_DIR", published_dir),
                patch.object(json_publish_store, "write_public_match_report_bundle", side_effect=self._public_report_stub),
            ):
                first = json_publish_store.import_match_package(package_fixture(), replace=False)
                replacement = package_fixture()
                replacement["match"]["title"] = "Updated match"

                second = json_publish_store.import_match_package(replacement, replace=True)

                self.assertEqual(second["created_at"], first["created_at"])
                self.assertIn("T", second["updated_at"])
                self.assertEqual(second["title"], "Updated match")


if __name__ == "__main__":
    unittest.main()
