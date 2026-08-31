from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_stats import (
    _sprint_reference,
    build_reviewed_stats,
    reviewed_team_movement_exclusion_reason,
)
from app.services.reviewed_sprint_policy import reviewed_sprint_policy
from app.services.identity_reviewed_progress import PROGRESS_SCHEMA_VERSION
from app.services.identity_review_scope import identity_review_scope_digest
from app.services.public_match_report import PUBLIC_MATCH_REPORT_SCHEMA_VERSION
from app.services.reviewed_match_report import build_reviewed_match_report


class ReviewedIdentityStatsTests(unittest.TestCase):
    def test_sprint_reference_uses_the_peak_fragment_not_unrelated_low_quality_fragment(self) -> None:
        reference = _sprint_reference(
            [
                {
                    "peak_sustained_speed_kmh": 24.0,
                    "speed_quality": "high",
                    "detected_time_sec": 180.0,
                },
                {
                    "peak_sustained_speed_kmh": 8.0,
                    "speed_quality": "low",
                    "detected_time_sec": 2.0,
                },
            ]
        )
        policy = reviewed_sprint_policy(detected_time_sec=182.0, **reference)
        self.assertEqual(policy["reference_source"], "current_match_peak_sustained")
        self.assertEqual(policy["start_threshold_kmh"], 19.68)

    def test_unreliable_peak_fragment_still_uses_the_absolute_fallback(self) -> None:
        reference = _sprint_reference(
            [{"peak_sustained_speed_kmh": 24.0, "speed_quality": "low", "detected_time_sec": 2.0}]
        )
        policy = reviewed_sprint_policy(detected_time_sec=180.0, **reference)
        self.assertEqual(policy["reference_source"], "fallback_absolute")
        self.assertEqual(policy["start_threshold_kmh"], 18.0)

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_team_distance_uses_safe_anonymous_evidence_without_u_or_double_counting(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 20,
            "duration_sec": 0.8,
            "source": "test",
            "filename": "video.mp4",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tracklets.json").write_text(
                json.dumps(
                    {
                        "tracklets": [
                            _tracklet("named", [0, 1, 2]),
                            _tracklet("anonymous", [3, 4, 5]),
                            _tracklet("named-two", [6, 7, 8]),
                            _tracklet("unknown-team", [9, 10, 11]),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            anonymous = _assignment("anonymous", "stable_anonymous", None)
            unknown_team = _assignment("unknown-team", "stable_anonymous", None)
            unknown_team["team_label"] = "U"
            documents = build_reviewed_stats(
                root,
                {
                    "semantic_digest": "snapshot",
                    "tracklet_assignments": [
                        _assignment("named", "confirmed", "p1"),
                        _assignment("named-two", "confirmed", "p2"),
                        anonymous,
                        unknown_team,
                    ],
                    "observation_overrides": [],
                    "observation_demotions": [],
                    "summary": {},
                },
                _match_document(),
            )
            players = documents["reviewed_player_stats.json"]["players"]
            teams = {
                row["team_label"]: row
                for row in documents["reviewed_player_stats.json"]["teams"]
            }
            named_total = sum(player["total_distance_m"] for player in players)
            self.assertGreater(teams["A"]["total_distance_m"], named_total)
            self.assertGreaterEqual(teams["A"]["total_distance_m"] + 0.01, named_total)
            self.assertAlmostEqual(teams["A"]["total_distance_m"], 0.6, places=2)
            self.assertEqual(teams["A"]["safe_observation_count"], 9)
            self.assertEqual(teams["B"]["total_distance_m"], 0.0)

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_team_stats_only_omits_opponent_player_rows_but_keeps_scope_metadata(
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
                json.dumps({"tracklets": [_tracklet("a", list(range(5))), _tracklet("b", list(range(5, 10)))]}),
                encoding="utf-8",
            )
            b_assignment = _assignment("b", "confirmed", "p2")
            b_assignment.update({
                "team_label": "B",
                "player_name": "Opponent",
                "reviewed_team_attribution_state": "certain_B",
            })
            snapshot = {
                "semantic_digest": "snapshot",
                "tracklet_assignments": [_assignment("a", "confirmed", "p1"), b_assignment],
                "observation_overrides": [],
                "observation_demotions": [],
                "summary": {},
            }
            match = {
                "teams": [
                    {"team_label": "A", "players": [{"id": "p1", "name": "Player One"}]},
                    {"team_label": "B", "players": [{"id": "p2", "name": "Opponent"}]},
                ],
                "identity_review_scope": {
                    "teams": {"A": "complete_roster", "B": "team_stats_only"},
                },
            }

            documents = build_reviewed_stats(root, snapshot, match)

            self.assertEqual(
                [row["player_id"] for row in documents["reviewed_player_stats.json"]["players"]],
                ["p1"],
            )
            scope = documents["reviewed_stats_readiness.json"]["identity_review_scope"]
            self.assertEqual(scope["teams"]["B"]["player_stats_status"], "not_reviewed_by_scope")
            self.assertTrue(scope["teams"]["B"]["team_stats_required"])
            teams = {
                row["team_label"]: row
                for row in documents["reviewed_player_stats.json"]["teams"]
            }
            self.assertGreater(teams["B"]["total_distance_m"], 0.0)
            self.assertEqual(teams["B"]["movement_authority"], "reviewed_safe_team_observations")

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_team_stats_only_unnamed_certain_b_observations_contribute_team_movement(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 30,
            "duration_sec": 1.2,
            "source": "test",
            "filename": "video.mp4",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracklets = [
                _tracklet_with_positions(tracklet_id, [(frame, frame * 0.2) for frame in range(5)])
                for tracklet_id in ("b-unnamed", "b-and-u", "b-player-conflict", "u-only", "a-b-conflict")
            ]
            (root / "tracklets.json").write_text(
                json.dumps({"tracklets": tracklets}), encoding="utf-8"
            )
            b_unnamed = _assignment("b-unnamed", "unresolved", None)
            b_unnamed.update({
                "team_label": "B",
                "reviewed_team_attribution_state": "certain_B",
            })
            b_and_u = _assignment("b-and-u", "unresolved", None)
            b_and_u.update({
                "team_label": "B",
                "reviewed_team_attribution_state": "certain_B",
            })
            b_player_conflict = _assignment("b-player-conflict", "conflicted", None)
            b_player_conflict.update({
                "team_label": "B",
                "reviewed_team_attribution_state": "certain_B",
            })
            u_only = _assignment("u-only", "unresolved", None)
            u_only.update({
                "team_label": "U",
                "reviewed_team_attribution_state": "unknown",
            })
            a_b_conflict = _assignment("a-b-conflict", "conflicted", None)
            a_b_conflict.update({
                "team_label": "B",
                "reviewed_team_attribution_state": "cross_team",
            })
            documents = build_reviewed_stats(
                root,
                {
                    "semantic_digest": "snapshot",
                    "tracklet_assignments": [
                        b_unnamed,
                        b_and_u,
                        b_player_conflict,
                        u_only,
                        a_b_conflict,
                    ],
                    "observation_overrides": [],
                    "observation_demotions": [],
                    "summary": {},
                },
                {
                    "identity_review_scope": {
                        "teams": {"A": "complete_roster", "B": "team_stats_only"}
                    }
                },
            )

            stats = documents["reviewed_player_stats.json"]
            teams = {row["team_label"]: row for row in stats["teams"]}
            self.assertEqual(stats["players"], [])
            self.assertEqual(teams["B"]["safe_observation_count"], 15)
            self.assertGreater(teams["B"]["total_distance_m"], 0.0)
            self.assertGreater(teams["B"]["observed_distance_m"], 0.0)
            self.assertGreater(teams["B"]["high_intensity_distance_m"], 0.0)
            self.assertLessEqual(
                teams["B"]["high_intensity_distance_m"],
                teams["B"]["total_distance_m"],
            )
            self.assertNotIn("sprint_count", teams["B"])
            self.assertNotIn("sprint_distance_m", teams["B"])

    def test_team_movement_requires_safe_team_attribution_not_named_player_identity(
        self,
    ) -> None:
        base = {
            "team_label": "B",
            "reviewed_team_attribution_state": "certain_B",
            "identity_status": "unresolved",
            "pitch_m": [5.0, 10.0],
            "smoothed_pitch_m": [5.0, 10.0],
            "play_area_status": "inside_play",
        }
        self.assertIsNone(reviewed_team_movement_exclusion_reason(base))
        self.assertIsNone(
            reviewed_team_movement_exclusion_reason(
                {**base, "identity_status": "conflicted"}
            )
        )
        self.assertEqual(
            reviewed_team_movement_exclusion_reason({**base, "team_label": "U"}),
            "team_unknown",
        )
        self.assertEqual(
            reviewed_team_movement_exclusion_reason(
                {**base, "reviewed_team_attribution_state": "cross_team"}
            ),
            "cross_team_conflict",
        )
        self.assertEqual(
            reviewed_team_movement_exclusion_reason(
                {**base, "identity_status": "team_unknown"}
            ),
            "team_unknown",
        )
        self.assertEqual(
            reviewed_team_movement_exclusion_reason(
                {**base, "identity_status": "referee"}
            ),
            "non_player",
        )
        self.assertEqual(
            reviewed_team_movement_exclusion_reason(
                {**base, "visual_trusted": False}
            ),
            "visually_untrusted",
        )
        self.assertEqual(
            reviewed_team_movement_exclusion_reason(
                {**base, "play_area_status": "outside_play"}
            ),
            "outside_play",
        )
        self.assertEqual(
            reviewed_team_movement_exclusion_reason(
                {**base, "smoothed_pitch_m": None, "pitch_m": None}
            ),
            "invalid_pitch_point",
        )

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
                "schema_version": PROGRESS_SCHEMA_VERSION,
                "source_snapshot_digest": "snapshot",
                "source_review_scope_digest": identity_review_scope_digest(
                    _match_document()
                ),
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
            self.assertGreater(player["movement_time_sec"], 0)
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
            self.assertNotIn("sprint_threshold_kmh", player["intensity"])
            self.assertNotIn("min_sprint_duration_sec", player["intensity"])
            self.assertEqual(player["intensity"]["sprint_detection"]["policy"], "player_relative_v2")
            self.assertEqual(player["intensity"]["sprint_detection"]["minimum_duration_sec"], 0.4)
            self.assertEqual(player["readiness"]["speed"], "experimental")

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_player_and_workload_share_the_validated_sprint_event_totals(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 200,
            "duration_sec": 8.0,
            "source": "test",
            "filename": "video.mp4",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tracklets.json").write_text(
                json.dumps(
                    {
                        "tracklets": [
                            _tracklet_with_positions(
                                "runner",
                                [(frame, frame * 0.25) for frame in range(200)],
                            )
                        ]
                    }
                ),
                encoding="utf-8",
            )

            documents = build_reviewed_stats(root, _confirmed_snapshot("runner"), {})

            player = documents["reviewed_player_stats.json"]["players"][0]
            intensity = player["intensity"]
            workload = player["workload"]
            self.assertGreater(intensity["sprint_count"], 0)
            self.assertLessEqual(
                intensity["max_sprint_speed_kmh"],
                player["speed"]["peak_sustained_speed_kmh"] + 0.01,
            )
            self.assertEqual(workload["sprint_count"], intensity["sprint_count"])
            self.assertEqual(workload["sprint_time_sec"], intensity["sprint_time_sec"])
            self.assertEqual(workload["sprint_distance_m"], intensity["sprint_distance_m"])
            self.assertEqual(
                workload["max_sprint_speed_kmh"], intensity["max_sprint_speed_kmh"]
            )
            self.assertEqual(
                intensity["validated_sprint_peak_kmh"], intensity["max_sprint_speed_kmh"]
            )

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

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_reviewed_public_report_keeps_optional_workload_and_average_position_without_schema_bump(
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
                json.dumps({"tracklets": [_tracklet("runner", list(range(100)))]}),
                encoding="utf-8",
            )
            build_reviewed_stats(root, _confirmed_snapshot("runner"), {})

            public_player = _build_public_report(root)["players"][0]

            self.assertEqual(PUBLIC_MATCH_REPORT_SCHEMA_VERSION, "0.1.0")
            self.assertIsNotNone(public_player["workload"])
            self.assertEqual(public_player["workload"]["distance_per_5min_m"], None)
            self.assertEqual(public_player["heatmap"]["average_position"]["pitch_m"], [4.95, 10.0])

    @patch("app.services.identity_reviewed_stats.read_match_video_metadata")
    def test_workload_normalized_distance_uses_canonical_reviewed_total_for_sub_centimeter_steps(
        self, metadata
    ) -> None:
        metadata.return_value = {
            "fps": 25.0,
            "frame_count": 3000,
            "duration_sec": 300.0,
            "source": "test",
            "filename": "video.mp4",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tracklets.json").write_text(
                json.dumps(
                    {
                        "tracklets": [
                            _tracklet_with_positions(
                                "slow-runner",
                                [
                                    (frame, frame * 0.009)
                                    for frame in range(3000)
                                ],
                            )
                        ]
                    }
                ),
                encoding="utf-8",
            )

            documents = build_reviewed_stats(
                root, _confirmed_snapshot("slow-runner"), {}
            )

            player = documents["reviewed_player_stats.json"]["players"][0]
            workload = player["workload"]
            self.assertEqual(player["total_distance_m"], 26.99)
            self.assertEqual(workload["distance_per_5min_m"], 67.47)
            self.assertEqual(
                workload["distance_per_5min_m"],
                round(player["total_distance_m"] / player["detected_time_sec"] * 300, 2),
            )
            self.assertEqual(workload["activity_windows"][0]["total_distance_m"], 26.99)


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
        "reviewed_team_attribution_state": "certain_A",
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
