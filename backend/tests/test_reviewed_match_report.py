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
            self.assertEqual(report["players"][0]["avg_speed_kmh"], 4.5)
            self.assertEqual(report["players"][0]["peak_speed_kmh"], 18.4)
            self.assertEqual(report["players"][0]["high_intensity_distance_m"], 11.2)
            self.assertEqual(report["players"][0]["sprint_count"], 1)
            self.assertEqual(report["players"][0]["max_sprint_speed_kmh"], 21.0)
            self.assertGreater(len(report["players"][0]["heatmap"]["interactive"]["points"]), 0)
            self.assertEqual(report["players"][0]["heatmap"]["path"], "")
            self.assertEqual(report["stats_semantics"]["team_time"], "source_video_duration")
            self.assertEqual(report["teams"][0]["total_distance_m"], 140.0)
            self.assertEqual(report["teams"][0]["high_intensity_distance_m"], 100.0)
            self.assertEqual(report["teams"][0]["sprint_count"], 2)
            self.assertLessEqual(
                report["teams"][0]["high_intensity_distance_m"],
                report["teams"][0]["total_distance_m"],
            )
            self.assertEqual(report["teams"][0]["movement_authority"], "reviewed_safe_team_observations")

    def test_report_rejects_mismatched_reviewed_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)
            heatmaps = json.loads((root / "reviewed_player_heatmaps.json").read_text(encoding="utf-8"))
            heatmaps["source_snapshot_digest"] = "stale"
            self._write(root / "reviewed_player_heatmaps.json", heatmaps)

            with self.assertRaisesRegex(ValueError, "same identity snapshot"):
                build_reviewed_match_report(root)

    def test_report_exposes_coverage_for_new_reviewed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)
            stats = json.loads(
                (root / "reviewed_player_stats.json").read_text(encoding="utf-8")
            )
            stats["identity_coverage"] = {
                "coverage_unit": "unique_detected_tracklet_frame_observation",
                "named_observation_coverage": 0.4,
                "per_team": {
                    "A": {
                        "named_observation_coverage": 0.5,
                        "team_known_observation_coverage": 1.0,
                    }
                },
            }
            self._write(root / "reviewed_player_stats.json", stats)
            readiness = json.loads(
                (root / "reviewed_stats_readiness.json").read_text(encoding="utf-8")
            )
            readiness["coverage_readiness"] = {
                "status": "ready_with_review",
                "allows_finalize": True,
                "blockers": [],
                "team_attribution_residual": {
                    "status": "accepted_within_tolerance",
                    "observations": 3,
                    "residual_budget_observations": 10,
                    "within_tolerance": True,
                    "units": 1,
                    "evidence_status_counts": {
                        "no_team_attribution_evidence": 1,
                    },
                },
            }
            self._write(root / "reviewed_stats_readiness.json", readiness)

            report = build_reviewed_match_report(root)

            self.assertEqual(report["identity_coverage"]["named_observation_coverage"], 0.4)
            self.assertEqual(
                report["identity_coverage_readiness"]["status"],
                "ready_with_review",
            )
            self.assertEqual(
                report["identity_coverage_readiness"]["team_attribution_residual"]["status"],
                "accepted_within_tolerance",
            )

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
                    {
                        "player_id": "pa",
                        "player_name": "Paweł",
                        "team_label": "A",
                        "detected_time_sec": 72.0,
                        "total_distance_m": 100.0,
                        "speed": {
                            "avg_speed_mps": 1.25,
                            "avg_speed_kmh": 4.5,
                            "peak_sustained_speed_mps": 5.111,
                            "peak_sustained_speed_kmh": 18.4,
                            "top_speed_mps": 5.111,
                            "top_speed_kmh": 18.4,
                            "speed_quality": "medium",
                        },
                        "intensity": {
                            "high_intensity_distance_m": 11.2,
                            "sprint_count": 1,
                            "sprint_time_sec": 0.7,
                            "sprint_distance_m": 4.1,
                            "max_sprint_speed_kmh": 21.0,
                        },
                    },
                    {"player_id": "B03", "player_name": "B03", "team_label": "B", "detected_time_sec": 60.0, "total_distance_m": 90.0},
                ],
                "teams": [
                    {
                        "team_label": "A",
                        "movement_authority": "reviewed_safe_team_observations",
                        "total_distance_m": 140.0,
                        "observed_distance_m": 130.0,
                        "estimated_short_gap_distance_m": 10.0,
                        "high_intensity_distance_m": 100.0,
                        "sprint_count": 2,
                    },
                    {
                        "team_label": "B",
                        "movement_authority": "reviewed_safe_team_observations",
                        "total_distance_m": 90.0,
                        "observed_distance_m": 90.0,
                        "estimated_short_gap_distance_m": 0.0,
                        "high_intensity_distance_m": 40.0,
                        "sprint_count": 1,
                    },
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
