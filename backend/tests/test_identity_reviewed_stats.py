from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.reviewed_match_report import build_reviewed_match_report


class ReviewedIdentityStatsTests(unittest.TestCase):
    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_coverage_readiness_blocks_new_stats_until_queue_is_complete(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 10,
            "duration_sec": 0.4,
            "source": "test",
            "filename": "video.mp4",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tracklets.json").write_text(
                json.dumps({"tracklets": [_tracklet("runner", list(range(10)))]}),
                encoding="utf-8",
            )
            progress = {
                "schema_version": "2.0.0",
                "source_snapshot_digest": "snapshot",
                "coverage_readiness": {
                    "status": "incomplete",
                    "allows_finalize": False,
                    "blockers": [{"code": "significant_named_coverage_debt"}],
                },
            }
            (root / "reviewed_identity_progress.json").write_text(
                json.dumps(progress), encoding="utf-8"
            )

            blocked = build_reviewed_stats(
                root, _confirmed_snapshot("runner"), _match_document()
            )
            self.assertEqual(
                blocked["reviewed_stats_readiness.json"]["status"],
                "incomplete_identity_coverage",
            )

            progress["coverage_readiness"] = {
                "status": "ready_with_review",
                "allows_finalize": True,
                "blockers": [],
            }
            (root / "reviewed_identity_progress.json").write_text(
                json.dumps(progress), encoding="utf-8"
            )
            ready = build_reviewed_stats(
                root, _confirmed_snapshot("runner"), _match_document()
            )
            self.assertEqual(ready["reviewed_stats_readiness.json"]["status"], "completed")
            self.assertIn("identity_coverage", ready["reviewed_player_stats.json"])

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_non_inside_observations_do_not_affect_reviewed_player_stats(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 5,
            "duration_sec": 0.2,
            "source": "test",
            "filename": "video.mp4",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracklet = _tracklet("runner", [0, 1, 2, 3, 4])
            tracklet["positions_m"][1]["play_area_status"] = "boundary_transient"
            tracklet["positions_m"][1]["pitch_m"] = [0.0, 20.0]
            tracklet["positions_m"][1]["smoothed_pitch_m"] = [0.0, 20.0]
            tracklet["positions_m"][2]["play_area_status"] = "outside_play"
            tracklet["positions_m"][2]["pitch_m"] = [30.0, 47.4]
            tracklet["positions_m"][2]["smoothed_pitch_m"] = [30.0, 47.4]
            (root / "tracklets.json").write_text(
                json.dumps({"tracklets": [tracklet]}), encoding="utf-8"
            )

            documents = build_reviewed_stats(root, _confirmed_snapshot("runner"), {})

            player = documents["reviewed_player_stats.json"]["players"][0]
            self.assertEqual(player["detected_frames"], 3)
            self.assertEqual(player["heatmap_samples"], 3)
            self.assertEqual(
                player["average_pitch_position_m"],
                [0.233, 10.0],
            )
            heatmap = documents["reviewed_player_heatmaps.json"]["heatmaps"][0]
            self.assertEqual(heatmap["samples"], 3)
            self.assertNotIn([0.0, 20.0], heatmap["positions_m"])
            self.assertNotIn([30.0, 47.4], heatmap["positions_m"])

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

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_reviewed_movement_preserves_speed_and_intensity_metrics(
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
            (root / "tracklets.json").write_text(
                json.dumps({"tracklets": [_tracklet("runner", list(range(25)))]}),
                encoding="utf-8",
            )
            snapshot = {
                "semantic_digest": "snapshot",
                "tracklet_assignments": [
                    _assignment("runner", "confirmed", "p1")
                ],
                "observation_overrides": [],
                "observation_demotions": [],
                "summary": {},
            }

            documents = build_reviewed_stats(root, snapshot, {})

            player = documents["reviewed_player_stats.json"]["players"][0]
            self.assertAlmostEqual(player["speed"]["avg_speed_kmh"], 8.64, places=2)
            self.assertAlmostEqual(
                player["speed"]["peak_sustained_speed_kmh"], 9.0, places=1
            )
            self.assertEqual(player["speed"]["top_speed_kmh"], player["speed"]["peak_sustained_speed_kmh"])
            self.assertEqual(player["intensity"]["high_intensity_distance_m"], 0.0)
            self.assertEqual(player["intensity"]["sprint_count"], 0)
            self.assertEqual(player["readiness"]["speed"], "experimental")

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_single_frame_position_spike_does_not_leak_into_reviewed_or_public_max_speed(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 50,
            "duration_sec": 2.0,
            "source": "test",
            "filename": "video.mp4",
        }
        positions = [
            (frame, 12.0 if frame == 20 else frame * 0.1)
            for frame in range(50)
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tracklets.json").write_text(
                json.dumps(
                    {"tracklets": [_tracklet_with_positions("runner", positions)]}
                ),
                encoding="utf-8",
            )

            documents = build_reviewed_stats(
                root,
                _confirmed_snapshot("runner"),
                {},
            )

            player = documents["reviewed_player_stats.json"]["players"][0]
            speed = player["speed"]
            self.assertGreaterEqual(player["skipped_outlier_segments"], 2)
            self.assertGreater(speed["peak_sustained_speed_kmh"], 8.5)
            self.assertLess(speed["peak_sustained_speed_kmh"], 9.5)
            self.assertEqual(
                speed["top_speed_kmh"], speed["peak_sustained_speed_kmh"]
            )
            self.assertLess(speed["raw_segment_top_speed_kmh"], 9.5)
            self.assertGreater(speed["sustained_speed_windows"], 0)
            self.assertEqual(player["readiness"]["speed"], "experimental")

            public_player = _build_public_report(root)["players"][0]
            self.assertEqual(
                public_player["peak_speed_kmh"],
                speed["peak_sustained_speed_kmh"],
            )

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_separate_tracklets_do_not_bridge_position_discontinuity_for_max_speed(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 50,
            "duration_sec": 2.0,
            "source": "test",
            "filename": "video.mp4",
        }
        first_tracklet = _tracklet_with_positions(
            "runner-a", [(frame, frame * 0.1) for frame in range(25)]
        )
        second_tracklet = _tracklet_with_positions(
            "runner-b",
            [(frame, 40.0 + (frame - 25) * 0.1) for frame in range(25, 50)],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tracklets.json").write_text(
                json.dumps({"tracklets": [first_tracklet, second_tracklet]}),
                encoding="utf-8",
            )

            documents = build_reviewed_stats(
                root,
                _confirmed_snapshot("runner-a", "runner-b"),
                {},
            )

            player = documents["reviewed_player_stats.json"]["players"][0]
            speed = player["speed"]
            self.assertEqual(player["confirmed_fragments"], 2)
            self.assertEqual(
                player["confirmed_tracklets"], ["runner-a", "runner-b"]
            )
            self.assertAlmostEqual(player["observed_distance_m"], 4.8, places=2)
            self.assertEqual(player["skipped_outlier_segments"], 0)
            self.assertGreater(speed["avg_speed_kmh"], 8.0)
            self.assertLess(speed["avg_speed_kmh"], 9.5)
            self.assertGreater(speed["peak_sustained_speed_kmh"], 8.5)
            self.assertLess(speed["peak_sustained_speed_kmh"], 9.5)
            self.assertEqual(
                speed["top_speed_kmh"], speed["peak_sustained_speed_kmh"]
            )
            self.assertLess(speed["raw_segment_top_speed_kmh"], 9.5)
            self.assertEqual(player["readiness"]["speed"], "experimental")

            public_player = _build_public_report(root)["players"][0]
            self.assertEqual(
                public_player["peak_speed_kmh"],
                speed["peak_sustained_speed_kmh"],
            )


def _tracklet(tracklet_id: str, frames: list[int]) -> dict:
    return _tracklet_with_positions(
        tracklet_id,
        [(frame, float(frame) * 0.1) for frame in frames],
    )


def _tracklet_with_positions(
    tracklet_id: str, positions: list[tuple[int, float]]
) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "positions_m": [
            {
                "frame": frame,
                "time_sec": frame / 25.0,
                "status": "detected",
                "pitch_m": [x, 10.0],
                "smoothed_pitch_m": [x, 10.0],
                "bbox_xyxy": [0, 0, 10, 20],
                "play_area_status": "inside_play",
            }
            for frame, x in positions
        ],
    }


def _confirmed_snapshot(*tracklet_ids: str) -> dict:
    return {
        "semantic_digest": "snapshot",
        "tracklet_assignments": [
            _assignment(tracklet_id, "confirmed", "p1")
            for tracklet_id in tracklet_ids
        ],
        "observation_overrides": [],
        "observation_demotions": [],
        "summary": {},
    }


def _build_public_report(root: Path) -> dict:
    (root / "match.json").write_text(
        json.dumps(
            {
                "id": "reviewed-speed-regression",
                "title": "Reviewed speed regression",
                "video": {"duration_sec": 2.0},
                "teams": [
                    {
                        "id": "team-a",
                        "name": "Team A",
                        "players": [{"id": "p1", "name": "Player One"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "reviewed_output_manifest.json").write_text(
        json.dumps(
            {
                "reviewed_identity": {"status": "fresh", "digest": "snapshot"},
                "stats": {
                    "status": "completed",
                    "source_snapshot_digest": "snapshot",
                },
                "stale": False,
            }
        ),
        encoding="utf-8",
    )
    return build_reviewed_match_report(root)


def _match_document() -> dict:
    return {
        "id": "match",
        "teams": [
            {
                "team_label": "A",
                "players": [{"id": "p1", "name": "Player One"}],
            },
            {"team_label": "B", "players": []},
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
