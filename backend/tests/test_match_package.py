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

    def test_attacking_momentum_is_optional_and_embedded_when_present(self) -> None:
        from app.main import build_match_package
        from app.services.artifact_lineage import canonical_json_sha256

        with tempfile.TemporaryDirectory() as tmp:
            match_dir = Path(tmp)
            write_ready_match_fixture(match_dir)
            legacy_package = build_match_package(match_dir)
            self.assertIsNone(legacy_package["attacking_momentum"])
            self.assertFalse(legacy_package["optional"]["attacking_momentum"])

            possession = {"frames": [{"frame": 0, "time_sec": 0.0}]}
            write_json(match_dir / "possession_candidates.json", possession)
            write_json(
                match_dir / "attacking_momentum.json",
                {
                    "status": "completed",
                    "experimental": True,
                    "summary": {"quality": "medium"},
                    "warnings": [],
                    "generated_from": [
                        {
                            "artifact": "possession_candidates.json",
                            "sha256": canonical_json_sha256(possession),
                        }
                    ],
                    "points": [{"index": 0, "time_sec": 2.5, "start_time_sec": 0.0, "end_time_sec": 5.0, "signed_score": 25.0, "team_a_value": 25.0, "team_b_value": 0.0}],
                },
            )
            package = build_match_package(match_dir)
            self.assertTrue(package["optional"]["attacking_momentum"])
            self.assertEqual(package["attacking_momentum"]["summary"]["quality"], "medium")

    def test_public_report_contains_simplified_momentum_timeline(self) -> None:
        from app.services.public_match_report import build_public_match_report

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            package = {
                "match": {"id": "match-1", "title": "Test", "video": {"duration_sec": 5.0}},
                "team_stats": {"teams": []},
                "resolved_player_stats": {"players": []},
                "attacking_momentum": {
                    "summary": {"quality": "medium"},
                    "warnings": ["review direction"],
                    "points": [
                        {
                            "index": 0,
                            "time_sec": 2.5,
                            "start_time_sec": 0.0,
                            "end_time_sec": 5.0,
                            "signed_score": 25.0,
                            "team_a_value": 25.0,
                            "team_b_value": 0.0,
                            "dominant_team_label": "A",
                            "confidence": 0.7,
                            "controlled_coverage": 0.5,
                            "intensity": 0.25,
                            "signed_raw": 999.0,
                        }
                    ],
                },
            }
            report = build_public_match_report(
                package,
                published_id="published-test",
                source_match_dir=None,
                heatmap_dir=output_dir,
                public_heatmap_base="heatmaps",
            )
            momentum = report["ball"]["attacking_momentum"]
            self.assertEqual(momentum["quality"], "medium")
            self.assertEqual(momentum["timeline"][0]["label"], "0:05")
            self.assertNotIn("signed_raw", momentum["timeline"][0])


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
