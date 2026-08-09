from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.reviewed_match_report import build_reviewed_match_report


class ReviewedMatchReportTests(unittest.TestCase):
    def test_report_uses_video_duration_and_only_named_player_heatmaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)

            report = build_reviewed_match_report(root)

            self.assertEqual([team["playing_time_sec"] for team in report["teams"]], [90.0, 90.0])
            self.assertEqual([player["player_name"] for player in report["players"]], ["Paweł"])
            self.assertEqual(report["players"][0]["detected_time_sec"], 72.0)
            self.assertEqual(report["players"][0]["playing_time_sec"], 72.0)
            self.assertGreater(len(report["players"][0]["heatmap"]["interactive"]["points"]), 0)
            self.assertEqual(report["players"][0]["heatmap"]["path"], "")
            self.assertEqual(report["stats_semantics"]["team_time"], "source_video_duration")

    def test_report_rejects_mismatched_reviewed_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)
            heatmaps = json.loads((root / "reviewed_player_heatmaps.json").read_text(encoding="utf-8"))
            heatmaps["source_snapshot_digest"] = "stale"
            self._write(root / "reviewed_player_heatmaps.json", heatmaps)

            with self.assertRaisesRegex(ValueError, "same identity snapshot"):
                build_reviewed_match_report(root)

    def _write_fixture(self, root: Path) -> None:
        self._write(
            root / "match.json",
            {
                "id": "match-1",
                "title": "Test",
                "video": {"duration_sec": 90.0},
                "teams": [
                    {
                        "id": "team-a",
                        "name": "Corgi",
                        "players": [{"id": "pa", "name": "Paweł", "number": "7", "role": "player"}],
                    },
                    {"id": "team-b", "name": "Verisk", "players": []},
                ],
            },
        )
        self._write(
            root / "team_config.json",
            {
                "teams": [
                    {"team_label": "A", "team_id": "team-a", "team_name": "Corgi", "detected_color_hex": "#eeeeee"},
                    {"team_label": "B", "team_id": "team-b", "team_name": "Verisk", "detected_color_hex": "#1d4ed8"},
                ]
            },
        )
        self._write(
            root / "team_stats.json",
            {
                "teams": [
                    {"team_label": "A", "team_id": "team-a", "playing_time_sec": 630.0, "total_distance_m": 700.0},
                    {"team_label": "B", "team_id": "team-b", "playing_time_sec": 540.0, "total_distance_m": 680.0},
                ]
            },
        )
        self._write(
            root / "reviewed_player_stats.json",
            {
                "source_snapshot_digest": "digest-1",
                "players": [
                    {"player_id": "pa", "player_name": "Paweł", "team_label": "A", "detected_time_sec": 72.0, "total_distance_m": 100.0},
                    {"player_id": "B03", "player_name": "B03", "team_label": "B", "detected_time_sec": 60.0, "total_distance_m": 90.0},
                ],
            },
        )
        self._write(
            root / "reviewed_player_heatmaps.json",
            {
                "source_snapshot_digest": "digest-1",
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "heatmaps": [
                    {"player_id": "pa", "positions_m": [[10.0, 20.0], [10.5, 20.5]]},
                    {"player_id": "B03", "positions_m": [[5.0, 5.0]]},
                ],
            },
        )
        self._write(
            root / "reviewed_stats_readiness.json",
            {"source_snapshot_digest": "digest-1", "status": "completed"},
        )
        self._write(
            root / "reviewed_output_manifest.json",
            {
                "reviewed_identity": {"status": "fresh", "digest": "digest-1"},
                "stats": {"status": "completed", "source_snapshot_digest": "digest-1"},
                "stale": False,
            },
        )
        self._write(root / "pitch_config.json", {"width_m": 30.0, "length_m": 47.4})

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
