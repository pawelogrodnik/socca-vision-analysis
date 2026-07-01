from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from pathlib import Path


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is required for app.main package tests")
class MatchPackageTests(unittest.TestCase):
    def test_build_match_package_020_contains_required_contract(self) -> None:
        from app.main import build_match_package, ensure_package_publishable

        with tempfile.TemporaryDirectory() as tmp:
            match_dir = Path(tmp)
            write_ready_match_fixture(match_dir)

            package = build_match_package(match_dir)

            self.assertEqual(package["schema_version"], "0.2.0")
            self.assertEqual(package["package_validation"]["status"], "ready")
            self.assertIn("analysis_report", package["required"])
            self.assertIn("stable_players", package["required"])
            self.assertIn("player_identity_assignments", package["required"])
            self.assertIn("resolved_player_stats", package["required"])
            self.assertIn("team_config", package["required"])
            self.assertIn("team_stats", package["required"])
            ensure_package_publishable(package)

    def test_build_match_package_blocks_when_required_doc_is_missing(self) -> None:
        from app.main import build_match_package, ensure_package_publishable

        with tempfile.TemporaryDirectory() as tmp:
            match_dir = Path(tmp)
            write_ready_match_fixture(match_dir)
            (match_dir / "resolved_player_stats.json").unlink()

            package = build_match_package(match_dir)

            self.assertEqual(package["package_validation"]["status"], "blocked")
            self.assertIn("resolved_player_stats", package["package_validation"]["missing_required"])
            with self.assertRaises(ValueError):
                ensure_package_publishable(package)


def write_ready_match_fixture(match_dir: Path) -> None:
    write_json(
        match_dir / "match.json",
        {
            "id": "match-1",
            "title": "Test match",
            "status": "reviewed",
            "format": "7v7",
            "video_filename": "video.mp4",
            "video": {"fps": 25, "frame_count": 250, "duration_sec": 10, "width": 1280, "height": 720},
            "teams": [{"id": "team-a", "name": "Team A", "players": [{"id": "p1", "name": "Player 1", "role": "player", "is_guest": False}]}],
        },
    )
    write_json(
        match_dir / "pitch_config.json",
        {"image_points": [[0, 0], [100, 0], [100, 100], [0, 100]], "width_m": 30, "length_m": 47.4, "source": "manual"},
    )
    write_json(
        match_dir / "analysis_report.json",
        {"status": "completed", "analysis_type": "test", "artifacts": {"stable_overlay_preview": "stable_overlay_preview.mp4"}},
    )
    write_json(match_dir / "stable_players.json", {"schema_version": "0.1.0", "players": [], "summary": {"stable_players": 0}})
    write_json(
        match_dir / "player_identity_assignments.json",
        {
            "schema_version": "0.1.0",
            "assignments": [{"stable_subject_id": "A01", "status": "assigned", "player_id": "p1"}],
            "summary": {"conflicts_total": 0},
        },
    )
    write_json(match_dir / "resolved_player_stats.json", {"schema_version": "0.1.0", "players": []})
    write_json(match_dir / "team_config.json", {"schema_version": "0.1.0", "teams": []})
    write_json(match_dir / "team_stats.json", {"schema_version": "0.1.0", "teams": []})


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
