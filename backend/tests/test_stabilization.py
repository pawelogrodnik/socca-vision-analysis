from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.services.stabilization import (
    _ball_overlay_positions_by_frame,
    _live_movement_by_frame,
    _stable_overlay_frame_rows,
    _visual_counts,
    apply_stable_overlay_visual_counts,
    build_frame_detection_counts,
    build_player_heatmaps_document,
    build_player_stats_document,
    build_stable_players,
    build_stable_players_document,
    build_team_config_document,
    build_team_stats_document,
    build_tracklets_document,
    build_tracking_quality_report,
    cluster_tracklet_teams,
    split_tracklets_by_appearance_changes,
    split_tracks_into_tracklets,
)


def position(frame: int, time_sec: float, x: float, y: float) -> dict:
    return {
        "frame": frame,
        "time_sec": time_sec,
        "bbox_xyxy": [10, 10, 20, 30],
        "footpoint": [15, 30],
        "pitch_m": [x, y],
        "confidence": 0.8,
    }


def tracklet(tracklet_id: str, start: float, end: float, first: list[float], last: list[float], color: list[float] | None = None) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "source_track_id": int(tracklet_id.split(":", 1)[0]),
        "segment_index": int(tracklet_id.split(":", 1)[1]),
        "start_time_sec": start,
        "end_time_sec": end,
        "duration_sec": max(0.0, end - start),
        "positions_count": 10,
        "mean_confidence": 0.8,
        "first_pitch_m": first,
        "last_pitch_m": last,
        "positions": [
            {"frame": int(start * 30), "time_sec": start, "bbox_xyxy": [0, 0, 10, 20], "pitch_m": first, "smoothed_pitch_m": first, "confidence": 0.8},
            {"frame": int(end * 30), "time_sec": end, "bbox_xyxy": [1, 0, 11, 20], "pitch_m": last, "smoothed_pitch_m": last, "confidence": 0.8},
        ],
        "appearance_rgb": color,
        "appearance_samples": 3 if color else 0,
        "team_label": "A" if color == [240, 30, 30] else "B" if color == [40, 70, 230] else "U",
        "team_id": None,
        "team_name": "Test",
        "team_confidence": 0.9 if color else 0.0,
    }


class StabilizationTests(unittest.TestCase):
    def test_ball_overlay_positions_ignore_unknown_rows(self) -> None:
        rows = _ball_overlay_positions_by_frame(
            {
                "positions": [
                    {"frame": 1, "position_px": [10, 20], "source": "detected"},
                    {"frame": 2, "position_px": None, "source": "unknown"},
                    {"frame": 3, "position_px": [12, 21], "source": "interpolated"},
                ]
            }
        )

        self.assertEqual(sorted(rows), [1, 3])

    def test_unmatched_raw_overlay_rows_are_debug_only(self) -> None:
        stable_doc = {
            "players": [
                {
                    "stable_player_id": "A01",
                    "team_label": "A",
                    "overlay_positions": [
                        {
                            "frame": 1,
                            "time_sec": 1 / 30,
                            "bbox_xyxy": [10, 10, 20, 30],
                            "pitch_m": [1, 1],
                            "source": "detected",
                        }
                    ],
                }
            ],
            "unmatched_observations": [
                {
                    "frame": 1,
                    "time_sec": 1 / 30,
                    "bbox_xyxy": [30, 10, 40, 30],
                    "pitch_m": [2, 1],
                    "source": "unmatched_raw",
                    "team_label": "A",
                }
            ],
        }
        rows = _stable_overlay_frame_rows(
            stable_doc,
            np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32),
            fps=30,
            include_unmatched_raw=True,
        )[1]

        self.assertEqual(len(rows), 2)
        self.assertEqual(_visual_counts(rows)["visible_boxes"], 1)
        self.assertEqual(_visual_counts(rows)["visible_unmatched_raw"], 1)

    def test_split_tracks_breaks_unrealistic_jump(self) -> None:
        tracks = [
            {
                "track_id": 1,
                "positions": [
                    position(0, 0.0, 0.0, 0.0),
                    position(1, 0.1, 0.2, 0.0),
                    position(2, 0.2, 8.0, 0.0),
                    position(3, 0.3, 8.2, 0.0),
                ],
            }
        ]
        clean, rejected = split_tracks_into_tracklets(
            tracks,
            split_speed_mps=8.5,
            min_duration_sec=0.0,
            min_positions=2,
        )
        self.assertEqual(len(clean), 2)
        self.assertEqual(len(rejected), 0)

    def test_split_tracklets_breaks_blue_to_neutral_appearance_switch(self) -> None:
        positions = [
            position(frame=index, time_sec=index / 10, x=float(index), y=0.0)
            for index in range(10)
        ]
        source = {
            "tracklet_id": "97:1",
            "source_track_id": 97,
            "segment_index": 1,
            "start_time_sec": 0.0,
            "end_time_sec": 0.9,
            "duration_sec": 0.9,
            "positions_count": len(positions),
            "positions": positions,
            "appearance_sample_rows": [
                {
                    "frame": index,
                    "time_sec": index / 10,
                    "position_index": index,
                    "rgb": [52.0, 75.0, 122.0],
                    "hsv": [112.0, 145.0, 122.0],
                    "lab": [82.0, 134.0, 102.0],
                    "feature": [31.0, 6.0, -22.0],
                    "quality": 0.7,
                }
                for index in (0, 2, 4)
            ]
            + [
                {
                    "frame": index,
                    "time_sec": index / 10,
                    "position_index": index,
                    "rgb": [196.0, 194.0, 197.0],
                    "hsv": [125.0, 4.0, 197.0],
                    "lab": [199.0, 128.0, 129.0],
                    "feature": [77.0, 0.0, 1.0],
                    "quality": 0.7,
                }
                for index in (5, 7, 9)
            ],
        }

        split = split_tracklets_by_appearance_changes([source], min_run_samples=2)

        self.assertEqual([item["tracklet_id"] for item in split], ["97:1", "97:2"])
        self.assertEqual([item["positions_count"] for item in split], [5, 5])
        self.assertLess(split[0]["appearance_rgb"][2], split[1]["appearance_rgb"][2])
        self.assertGreater(split[1]["appearance_rgb"][0], 180)
        self.assertEqual(split[1]["parent_tracklet_id"], "97:1")
        self.assertEqual(split[1]["appearance_split_reason"], "torso_color_change")
        doc = build_tracklets_document(split, [], raw_tracks_count=1, parameters={})
        self.assertEqual(doc["tracklets"][1]["parent_tracklet_id"], "97:1")
        self.assertEqual(doc["tracklets"][1]["appearance_split_reason"], "torso_color_change")

    def test_build_stable_players_links_short_gap_same_team(self) -> None:
        players = build_stable_players(
            [
                tracklet("1:1", 0.0, 2.0, [0, 0], [4, 0], [240, 30, 30]),
                tracklet("2:1", 2.4, 4.0, [4.5, 0], [8, 0], [242, 35, 32]),
            ]
        )
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0]["tracklet_ids"], ["1:1", "2:1"])

    def test_build_stable_players_rejects_overlap(self) -> None:
        players = build_stable_players(
            [
                tracklet("1:1", 0.0, 2.0, [0, 0], [4, 0], [240, 30, 30]),
                tracklet("2:1", 1.5, 3.0, [4.1, 0], [8, 0], [242, 35, 32]),
            ]
        )
        self.assertEqual(len(players), 2)

    def test_build_stable_players_rejects_unrealistic_speed(self) -> None:
        players = build_stable_players(
            [
                tracklet("1:1", 0.0, 2.0, [0, 0], [0, 0], [240, 30, 30]),
                tracklet("2:1", 2.4, 4.0, [20, 0], [22, 0], [242, 35, 32]),
            ]
        )
        self.assertEqual(len(players), 2)

    def test_build_stable_players_rejects_long_distance_merge(self) -> None:
        players = build_stable_players(
            [
                tracklet("1:1", 0.0, 2.0, [0, 0], [0, 0], [240, 30, 30]),
                tracklet("2:1", 4.6, 6.0, [18.9, 0], [20, 0], [242, 35, 32]),
            ]
        )
        self.assertEqual(len(players), 2)

    def test_team_clustering_assigns_two_team_labels(self) -> None:
        tracklets = [
            tracklet("1:1", 0.0, 2.0, [0, 0], [1, 0], [240, 30, 30]),
            tracklet("2:1", 0.0, 2.0, [0, 1], [1, 1], [235, 40, 35]),
            tracklet("3:1", 0.0, 2.0, [0, 2], [1, 2], [40, 70, 230]),
            tracklet("4:1", 0.0, 2.0, [0, 3], [1, 3], [35, 75, 225]),
        ]
        cluster_tracklet_teams(
            tracklets,
            [
                {"id": "team-a", "name": "Team A", "color": "#ef4444"},
                {"id": "team-b", "name": "Team B", "color": "#2563eb"},
            ],
        )
        labels = {item["team_label"] for item in tracklets}
        self.assertEqual(labels, {"A", "B"})

    def test_team_clustering_maps_white_cluster_to_team_a_over_default_ui_colors(self) -> None:
        tracklets = [
            tracklet("1:1", 0.0, 2.0, [0, 0], [1, 0], [220, 220, 215]),
            tracklet("2:1", 0.0, 2.0, [0, 1], [1, 1], [215, 215, 210]),
            tracklet("3:1", 0.0, 2.0, [0, 2], [1, 2], [235, 105, 25]),
            tracklet("4:1", 0.0, 2.0, [0, 3], [1, 3], [240, 115, 35]),
        ]
        for item in tracklets[:2]:
            item["appearance_hsv"] = [20, 20, 220]
            item["appearance_lab"] = [220, 128, 132]
            item["appearance_feature"] = [77, 0, 4, 5, 0, 4, 26]
            item["appearance_quality"] = 0.7
        for item in tracklets[2:]:
            item["appearance_hsv"] = [12, 190, 220]
            item["appearance_lab"] = [160, 165, 180]
            item["appearance_feature"] = [56, 44, 62, 35, 16, 42, 26]
            item["appearance_quality"] = 0.7

        cluster_doc = cluster_tracklet_teams(
            tracklets,
            [
                {"id": "team-a", "name": "Team A", "color": "#ef4444"},
                {"id": "team-b", "name": "Team B", "color": "#2563eb"},
            ],
        )

        white_labels = {item["team_label"] for item in tracklets[:2]}
        orange_labels = {item["team_label"] for item in tracklets[2:]}
        self.assertEqual(white_labels, {"A"})
        self.assertEqual(orange_labels, {"B"})
        self.assertEqual(cluster_doc["method"], "torso_color_neutral_vs_colored_v1")

    def test_team_clustering_separates_neutral_from_blue_without_green_outlier_seed(self) -> None:
        neutral_team = [
            tracklet("1:1", 0.0, 2.0, [0, 0], [1, 0], [196, 194, 197]),
            tracklet("2:1", 0.0, 2.0, [0, 1], [1, 1], [187, 186, 186]),
            tracklet("3:1", 0.0, 2.0, [0, 2], [1, 2], [199, 202, 212]),
        ]
        blue_team = [
            tracklet("4:1", 0.0, 2.0, [0, 3], [1, 3], [52, 75, 122]),
            tracklet("5:1", 0.0, 2.0, [0, 4], [1, 4], [47, 67, 112]),
            tracklet("6:1", 0.0, 2.0, [0, 5], [1, 5], [60, 88, 133]),
        ]
        outliers = [
            tracklet("7:1", 0.0, 2.0, [0, 6], [1, 6], [93, 216, 134]),
        ]
        tracklets = [*neutral_team, *blue_team, *outliers]

        cluster_doc = cluster_tracklet_teams(tracklets, [])

        self.assertEqual({item["team_label"] for item in neutral_team}, {"A"})
        self.assertEqual({item["team_label"] for item in blue_team}, {"B"})
        self.assertEqual(outliers[0]["team_label"], "U")
        self.assertEqual(cluster_doc["method"], "torso_color_neutral_vs_colored_v1")
        self.assertEqual(cluster_doc["team_color_outliers_count"], 1)

    def test_team_clustering_maps_goalkeeper_outliers_without_using_them_as_prototypes(self) -> None:
        tracklets = [
            tracklet("1:1", 0.0, 2.0, [0, 0], [1, 0], [220, 220, 215]),
            tracklet("2:1", 0.0, 2.0, [0, 1], [1, 1], [215, 215, 210]),
            tracklet("3:1", 0.0, 2.0, [0, 2], [1, 2], [235, 105, 25]),
            tracklet("4:1", 0.0, 2.0, [0, 3], [1, 3], [240, 115, 35]),
            tracklet("5:1", 0.0, 2.0, [0, 4], [1, 4], [140, 205, 80]),
            tracklet("6:1", 0.0, 2.0, [0, 5], [1, 5], [45, 40, 65]),
        ]
        for item in tracklets[:2]:
            item["appearance_hsv"] = [20, 20, 220]
            item["appearance_lab"] = [220, 128, 132]
            item["appearance_feature"] = [77, 0, 4, 5, 0, 4, 26]
            item["appearance_quality"] = 0.7
        for item in tracklets[2:4]:
            item["appearance_hsv"] = [12, 190, 220]
            item["appearance_lab"] = [160, 165, 180]
            item["appearance_feature"] = [56, 44, 62, 35, 16, 42, 26]
            item["appearance_quality"] = 0.7
        tracklets[4]["appearance_hsv"] = [42, 140, 205]
        tracklets[4]["appearance_lab"] = [190, 100, 160]
        tracklets[4]["appearance_feature"] = [66, -33, 38, 6, 24, 31, 25]
        tracklets[4]["appearance_quality"] = 0.7
        tracklets[5]["appearance_hsv"] = [125, 95, 65]
        tracklets[5]["appearance_lab"] = [65, 135, 112]
        tracklets[5]["appearance_feature"] = [23, 8, -19, -15, -9, 21, 8]
        tracklets[5]["appearance_quality"] = 0.7

        cluster_doc = cluster_tracklet_teams(
            tracklets,
            [
                {"id": "team-a", "name": "Team A", "color": "#ef4444"},
                {"id": "team-b", "name": "Team B", "color": "#2563eb"},
            ],
        )

        self.assertEqual(tracklets[4]["team_label"], "U")
        self.assertEqual(tracklets[5]["team_label"], "U")
        self.assertEqual(cluster_doc["team_color_outliers_count"], 2)
        self.assertEqual(cluster_doc["clusters"][0]["reference_tracklets_count"], 2)
        self.assertEqual(cluster_doc["clusters"][1]["reference_tracklets_count"], 2)

    def test_low_confidence_link_is_reported(self) -> None:
        players = build_stable_players(
            [
                tracklet("1:1", 0.0, 2.0, [0, 0], [0, 0], [240, 30, 30]),
                tracklet("2:1", 3.5, 5.0, [3.5, 0], [4, 0], [242, 35, 32]),
            ]
        )
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0]["risky_links"][0]["confidence"], "low")

    def test_short_detection_gap_is_interpolated_for_overlay(self) -> None:
        players = build_stable_players(
            [
                tracklet("1:1", 0.0, 0.2, [0, 0], [1, 0], [240, 30, 30]),
            ]
        )
        self.assertEqual(len(players), 1)
        player = players[0]
        self.assertEqual(player["interpolated_positions_count"], 5)
        self.assertEqual(player["interpolated_gaps_count"], 1)
        sources = [position["source"] for position in player["overlay_positions"]]
        self.assertEqual(sources.count("interpolated"), 5)
        self.assertEqual(sources.count("detected"), 2)

    def test_live_movement_stats_accumulate_observed_distance(self) -> None:
        live = _live_movement_by_frame(
            [
                position(0, 0.0, 0.0, 0.0),
                position(1, 1 / 30, 0.1, 0.0),
                position(6, 0.2, 1.0, 0.0),
                position(18, 0.6, 1.8, 0.0),
            ],
            fps=30,
        )

        self.assertAlmostEqual(live[1]["cumulative_distance_m"], 0.1, places=2)
        self.assertEqual(live[1]["segment_source"], "observed")
        self.assertAlmostEqual(live[6]["cumulative_distance_m"], 1.0, places=2)
        self.assertIsNone(live[6]["current_speed_kmh"])
        self.assertEqual(live[6]["segment_source"], "estimated")
        self.assertAlmostEqual(live[18]["cumulative_distance_m"], 1.8, places=2)
        self.assertAlmostEqual(live[18]["current_speed_kmh"], 10.8, places=2)

    def test_live_movement_stats_skip_unrealistic_jump(self) -> None:
        live = _live_movement_by_frame(
            [
                position(0, 0.0, 0.0, 0.0),
                position(30, 1.0, 30.0, 0.0),
            ],
            fps=30,
        )

        self.assertEqual(live[30]["cumulative_distance_m"], 0.0)
        self.assertEqual(live[30]["segment_source"], "skipped")

    def test_long_detection_gap_is_not_interpolated(self) -> None:
        players = build_stable_players(
            [
                tracklet("1:1", 0.0, 1.0, [0, 0], [1, 0], [240, 30, 30]),
            ]
        )
        self.assertEqual(len(players), 1)
        player = players[0]
        self.assertEqual(player["interpolated_positions_count"], 0)
        self.assertEqual(player["interpolated_gaps_count"], 0)
        self.assertEqual(player["skipped_interpolation_gaps_count"], 1)

    def test_stable_players_document_suppresses_extra_candidates_for_7v7_clip(self) -> None:
        players = []
        for index in range(16):
            team_label = "A" if index < 8 else "B"
            players.append(
                {
                    "stable_subject_id": f"sp-{index:03d}",
                    "stable_player_id": f"{team_label}{index + 1:02d}",
                    "team_label": team_label,
                    "duration_sec": 16 - index if team_label == "A" else 24 - index,
                    "positions_count": 100 - index,
                    "confidence_score": 0.8,
                    "mean_detection_confidence": 0.7,
                    "confidence": "high",
                    "risky_links": [],
                    "interpolated_positions_count": 0,
                    "interpolated_gaps_count": 0,
                    "skipped_interpolation_gaps_count": 0,
                    "longest_interpolated_gap_frames": 0,
                }
            )
        doc = build_stable_players_document(
            stable_players=players,
            raw_tracks_count=16,
            tracklets_count=16,
            rejected_tracklets=[],
            pitch_width_m=30,
            pitch_length_m=47,
        )
        self.assertEqual(doc["summary"]["stable_players"], 14)
        self.assertEqual(doc["summary"]["stable_player_candidates"], 16)
        self.assertEqual(doc["summary"]["suppressed_extra_candidates"], 2)
        self.assertEqual(doc["summary"]["team_counts"], {"A": 7, "B": 7})

    def test_frame_detection_counts_reports_raw_and_stable_counts(self) -> None:
        stable_doc = {
            "players": [
                {
                    "stable_player_id": "A01",
                    "overlay_positions": [
                        {"frame": 0, "source": "detected"},
                        {"frame": 1, "source": "interpolated"},
                    ],
                }
            ]
        }
        counts = build_frame_detection_counts(
            [
                {"track_id": 1, "positions": [{"frame": 0}, {"frame": 1}]},
                {"track_id": 2, "positions": [{"frame": 1}]},
            ],
            stable_doc,
            fps=30,
            target_players=2,
        )
        self.assertEqual(counts["frames"][0]["raw_detections"], 1)
        self.assertEqual(counts["frames"][0]["stable_detected"], 1)
        self.assertEqual(counts["frames"][1]["raw_detections"], 2)
        self.assertEqual(counts["frames"][1]["stable_interpolated"], 1)
        self.assertEqual(counts["summary"]["raw_frames_below_target"], 1)

    def test_tracklets_document_exports_formal_contract(self) -> None:
        clean = [
            tracklet("1:1", 0.0, 1.0, [0, 0], [1, 0], [240, 30, 30]),
        ]
        clean[0]["appearance_hsv"] = [10.1234, 210.5678, 240.4321]
        clean[0]["appearance_lab"] = [130.1234, 160.5678, 170.4321]
        clean[0]["appearance_feature"] = [45.12345, 12.56789, -8.43219]
        clean[0]["appearance_quality"] = 0.81234
        rejected = [
            {
                **tracklet("2:1", 0.0, 0.05, [2, 0], [2.1, 0], None),
                "reject_reason": "too_short",
            }
        ]

        doc = build_tracklets_document(
            clean,
            rejected,
            raw_tracks_count=2,
            parameters={"min_duration_sec": 0.2},
        )

        self.assertEqual(doc["schema_version"], "0.1.0")
        self.assertEqual(doc["summary"]["raw_tracks"], 2)
        self.assertEqual(doc["summary"]["clean_tracklets"], 1)
        self.assertEqual(doc["summary"]["rejected_tracklets"], 1)
        exported = doc["tracklets"][0]
        self.assertEqual(exported["tracklet_id"], "1:1")
        self.assertEqual(exported["source_tracker_id"], 1)
        self.assertEqual(exported["frames_count"], exported["positions_count"])
        self.assertEqual(exported["missing_frames_count"], 29)
        self.assertEqual(exported["team_candidate"], "A")
        self.assertEqual(exported["appearance_rgb"], [240.0, 30.0, 30.0])
        self.assertEqual(exported["appearance_hsv"], [10.12, 210.57, 240.43])
        self.assertEqual(exported["appearance_lab"], [130.12, 160.57, 170.43])
        self.assertEqual(exported["appearance_feature"], [45.123, 12.568, -8.432])
        self.assertEqual(exported["appearance_quality"], 0.8123)
        self.assertEqual(exported["appearance_samples"], 3)
        self.assertIn("positions_m", exported)
        self.assertEqual(doc["rejected_tracklets"][0]["reject_reason"], "too_short")

    def test_tracking_quality_report_summarizes_gaps_and_team_over_cap(self) -> None:
        clean = [
            tracklet(f"{index + 1}:1", 0.0, 1.0, [index, 0], [index + 0.1, 0], [240, 30, 30])
            for index in range(8)
        ]
        report = build_tracking_quality_report(
            clean,
            [],
            raw_tracks_count=8,
            parameters={"min_positions": 4},
            target_players_per_team=7,
        )

        self.assertEqual(report["schema_version"], "0.1.0")
        self.assertEqual(report["summary"]["clean_tracklets"], 8)
        self.assertEqual(report["summary"]["frames_with_team_over_cap"], 2)
        self.assertGreater(report["summary"]["suspicious_events"], 0)
        self.assertTrue(any(row["team_over_cap"] for row in report["frame_team_counts"]))

    def test_player_stats_document_exports_tracking_only_contract(self) -> None:
        stable_doc = {
            "source": "conservative_identity_v2",
            "identity_semantics": "stint_first",
            "players": [
                {
                    "stable_player_id": "A01",
                    "stable_subject_id": "slot-a01",
                    "slot_id": "A01",
                    "status": "active",
                    "team_label": "A",
                    "confidence": "high",
                    "confidence_score": 0.9,
                    "tracklet_ids": ["1:1"],
                    "raw_track_ids": [1],
                    "stint_count": 1,
                    "movement_stats": {
                        "playing_time_sec": 10.0,
                        "detected_time_sec": 8.0,
                        "missing_time_sec": 1.5,
                        "ambiguous_time_sec": 0.5,
                        "observed_distance_m": 25.0,
                        "estimated_gap_distance_m": 5.0,
                        "total_distance_m": 30.0,
                        "estimated_distance_ratio": 0.1667,
                        "distance_quality": "high",
                        "avg_speed_mps": 3.0,
                        "avg_speed_kmh": 10.8,
                        "observed_avg_speed_mps": 3.125,
                        "peak_sustained_speed_mps": 5.5,
                        "peak_sustained_speed_kmh": 19.8,
                        "top_speed_mps": 6.0,
                        "top_speed_kmh": 21.6,
                        "raw_segment_top_speed_mps": 6.8,
                        "raw_segment_top_speed_kmh": 24.48,
                        "speed_quality": "medium",
                        "intensity": {
                            "high_intensity_threshold_kmh": 15.0,
                            "sprint_threshold_kmh": 20.0,
                            "min_sprint_duration_sec": 0.5,
                            "high_intensity_time_sec": 1.2,
                            "high_intensity_distance_m": 7.0,
                            "high_intensity_segments": 12,
                            "high_intensity_distance_ratio": 0.2333,
                            "sprint_count": 1,
                            "sprint_time_sec": 0.7,
                            "sprint_distance_m": 4.2,
                            "sprint_distance_ratio": 0.14,
                            "longest_sprint_time_sec": 0.7,
                            "longest_sprint_distance_m": 4.2,
                            "max_sprint_speed_kmh": 23.1,
                            "trusted_speed_segments": 40,
                            "sprint_candidate_count": 2,
                            "rejected_sprint_candidate_count": 1,
                            "best_sprint_candidate_speed_kmh": 28.0,
                            "best_sprint_candidate_duration_sec": 0.18,
                            "best_sprint_candidate_distance_m": 1.4,
                            "best_sprint_candidate_reason": "too_short",
                            "best_rejected_sprint_candidate": {
                                "start_frame": 44,
                                "end_frame": 49,
                                "duration_sec": 0.18,
                                "distance_m": 1.4,
                                "max_speed_kmh": 28.0,
                                "reason": "too_short",
                            },
                            "rejected_sprint_candidates": [
                                {
                                    "start_frame": 44,
                                    "end_frame": 49,
                                    "duration_sec": 0.18,
                                    "distance_m": 1.4,
                                    "max_speed_kmh": 28.0,
                                    "reason": "too_short",
                                }
                            ],
                        },
                        "active_frames": 300,
                        "detected_frames": 240,
                        "missing_frames": 45,
                        "ambiguous_frames": 15,
                        "predicted_frames": 0,
                        "samples_used": 240,
                        "observed_segments": 40,
                        "estimated_gap_segments": 2,
                        "skipped_outlier_segments": 1,
                        "skipped_speed_outlier_segments": 1,
                        "skipped_long_gap_segments": 0,
                        "sustained_speed_windows": 4,
                    },
                }
            ],
        }

        doc = build_player_stats_document(stable_doc)

        self.assertEqual(doc["schema_version"], "0.1.0")
        self.assertEqual(doc["scope"], "tracking_only_no_ball")
        self.assertEqual(doc["summary"]["players"], 1)
        self.assertEqual(doc["summary"]["total_distance_m"], 30.0)
        self.assertEqual(doc["summary"]["estimated_short_gap_distance_m"], 5.0)
        self.assertEqual(doc["teams"][0]["team_label"], "A")
        player = doc["players"][0]
        self.assertEqual(player["stable_player_id"], "A01")
        self.assertTrue(player["tracking_only"])
        self.assertEqual(player["distance"]["observed_distance_m"], 25.0)
        self.assertEqual(player["distance"]["estimated_short_gap_distance_m"], 5.0)
        self.assertEqual(player["speed"]["peak_sustained_speed_kmh"], 19.8)
        self.assertEqual(player["speed"]["top_speed_kmh"], 21.6)
        self.assertEqual(player["speed"]["raw_segment_top_speed_kmh"], 24.48)
        self.assertEqual(player["speed"]["quality"], "medium")
        self.assertEqual(player["intensity"]["sprint_count"], 1)
        self.assertEqual(player["intensity"]["sprint_distance_m"], 4.2)
        self.assertEqual(player["intensity"]["sprint_candidate_count"], 2)
        self.assertEqual(player["intensity"]["rejected_sprint_candidate_count"], 1)
        self.assertEqual(player["intensity"]["best_sprint_candidate_reason"], "too_short")
        self.assertEqual(doc["summary"]["sprint_count"], 1)
        self.assertEqual(doc["summary"]["sprint_candidate_count"], 2)
        self.assertEqual(doc["summary"]["best_rejected_sprint_candidate"]["reason"], "too_short")
        self.assertEqual(doc["summary"]["high_intensity_distance_m"], 7.0)
        self.assertEqual(doc["teams"][0]["sprint_count"], 1)
        self.assertEqual(doc["teams"][0]["sprint_candidate_count"], 2)

    def test_player_heatmaps_document_uses_trusted_positions_only(self) -> None:
        stable_doc = {
            "source": "conservative_identity_v2",
            "identity_semantics": "stint_first",
            "pitch_dimensions_m": {"width_m": 30, "length_m": 47.4},
            "players": [
                {
                    "stable_player_id": "A01",
                    "stable_subject_id": "slot-a01",
                    "slot_id": "A01",
                    "team_label": "A",
                    "team_id": "team-a",
                    "team_name": "Team A",
                    "detected_frames": 2,
                    "ambiguous_frames": 1,
                    "overlay_positions": [
                        {"pitch_m": [1.0, 2.0], "source": "detected"},
                        {"pitch_m": [1.2, 2.2], "source": "interpolated"},
                        {"pitch_m": [9.0, 9.0], "source": "ambiguous"},
                        {"pitch_m": [10.0, 10.0], "source": "missing"},
                    ],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            doc = build_player_heatmaps_document(stable_doc, Path(tmp))

            self.assertEqual(doc["schema_version"], "0.1.0")
            self.assertEqual(doc["summary"]["players"], 1)
            self.assertEqual(doc["summary"]["samples_total"], 2)
            heatmap = doc["heatmaps"][0]
            self.assertEqual(heatmap["samples"], 2)
            self.assertEqual(heatmap["detected_samples"], 1)
            self.assertEqual(heatmap["interpolated_samples"], 1)
            self.assertTrue((Path(tmp) / heatmap["path"]).exists())
            self.assertEqual(stable_doc["players"][0]["heatmap_path"], heatmap["path"])

    def test_stable_overlay_visual_counts_fill_short_stride_gap(self) -> None:
        pitch_polygon = np.array([[0, 0], [200, 0], [200, 200], [0, 200]], dtype=np.float32)
        stable_doc = {
            "players": [
                {
                    "stable_player_id": "A01",
                    "team_label": "A",
                    "overlay_positions": [
                        {
                            "frame": 0,
                            "time_sec": 0.0,
                            "bbox_xyxy": [10, 10, 20, 30],
                            "pitch_m": [1.0, 1.0],
                            "confidence": 0.8,
                            "source": "detected",
                        },
                        {
                            "frame": 4,
                            "time_sec": 4 / 30,
                            "bbox_xyxy": [14, 10, 24, 30],
                            "pitch_m": [1.4, 1.0],
                            "confidence": 0.8,
                            "source": "detected",
                        },
                    ],
                }
            ]
        }
        counts = {
            "target_players": 1,
            "summary": {},
            "frames": [{"frame": frame, "time_sec": round(frame / 30, 3), "raw_detections": 0} for frame in range(5)],
        }

        updated = apply_stable_overlay_visual_counts(counts, stable_doc, pitch_polygon, fps=30.0)

        self.assertEqual([row["visible_stable_boxes"] for row in updated["frames"]], [1, 1, 1, 1, 1])
        self.assertEqual([row["trusted_detected"] for row in updated["frames"]], [1, 0, 0, 0, 1])
        self.assertEqual([row["visual_interpolated_boxes"] for row in updated["frames"]], [0, 1, 1, 1, 0])
        self.assertEqual(updated["summary"]["visual_interpolated_boxes"], 3)
        self.assertEqual(updated["summary"]["predicted_visible_boxes"], 0)
        self.assertEqual(updated["summary"]["ghost_bbox_count"], 0)

    def test_stable_overlay_visual_counts_reject_large_stride_jump(self) -> None:
        pitch_polygon = np.array([[0, 0], [200, 0], [200, 200], [0, 200]], dtype=np.float32)
        stable_doc = {
            "players": [
                {
                    "stable_player_id": "A01",
                    "team_label": "A",
                    "overlay_positions": [
                        {
                            "frame": 0,
                            "time_sec": 0.0,
                            "bbox_xyxy": [10, 10, 20, 30],
                            "pitch_m": [1.0, 1.0],
                            "confidence": 0.8,
                            "source": "detected",
                        },
                        {
                            "frame": 4,
                            "time_sec": 4 / 30,
                            "bbox_xyxy": [80, 10, 90, 30],
                            "pitch_m": [12.0, 1.0],
                            "confidence": 0.8,
                            "source": "detected",
                        },
                    ],
                }
            ]
        }
        counts = {
            "target_players": 1,
            "summary": {},
            "frames": [{"frame": frame, "time_sec": round(frame / 30, 3), "raw_detections": 0} for frame in range(5)],
        }

        updated = apply_stable_overlay_visual_counts(counts, stable_doc, pitch_polygon, fps=30.0)

        self.assertEqual([row["visible_stable_boxes"] for row in updated["frames"]], [1, 0, 0, 0, 1])
        self.assertEqual(updated["summary"]["visual_interpolated_boxes"], 0)
        self.assertEqual(updated["summary"]["ghost_bbox_count"], 0)

    def test_team_config_document_preserves_manual_lock(self) -> None:
        meta = {
            "id": "match-1",
            "teams": [
                {"id": "team-white", "name": "White", "color": "#f8fafc"},
                {"id": "team-orange", "name": "Orange", "color": "#f97316"},
            ],
        }
        team_clusters = {
            "method": "torso_color_white_vs_bib_v3",
            "reference_tracklets_count": 4,
            "candidate_tracklets_count": 6,
            "unknown_tracklets": ["9:1"],
            "clusters": [
                {
                    "cluster_id": "cluster-1",
                    "team_label": "A",
                    "color_hex": "#eeeeee",
                    "confidence": 0.8,
                    "reference_tracklets_count": 2,
                    "candidate_tracklets_count": 3,
                },
                {
                    "cluster_id": "cluster-2",
                    "team_label": "B",
                    "color_hex": "#f97316",
                    "confidence": 0.77,
                    "reference_tracklets_count": 2,
                    "candidate_tracklets_count": 3,
                },
            ],
        }
        stable_doc = {
            "players": [
                {"stable_player_id": "A01", "team_label": "A"},
                {"stable_player_id": "B01", "team_label": "B"},
            ]
        }
        existing = {
            "teams": [
                {
                    "team_label": "A",
                    "team_id": "team-white",
                    "team_name": "White locked",
                    "locked": True,
                    "assignment_source": "manual_lock",
                }
            ]
        }

        config = build_team_config_document(meta, team_clusters, stable_doc, existing)

        self.assertTrue(config["locked"])
        self.assertEqual(config["teams"][0]["team_name"], "White locked")
        self.assertTrue(config["teams"][0]["locked"])
        self.assertEqual(config["teams"][0]["stable_players_count"], 1)
        self.assertEqual(config["teams"][1]["detected_color_hex"], "#f97316")
        self.assertEqual(config["team_clusters_summary"]["unknown_tracklets_count"], 1)

    def test_team_stats_document_enriches_player_stats_with_team_config(self) -> None:
        player_stats = {
            "source": "conservative_identity_v2",
            "scope": "tracking_only_no_ball",
            "units": {"distance": "meters"},
            "teams": [
                {
                    "team_label": "A",
                    "players": 2,
                    "playing_time_sec": 20.0,
                    "detected_time_sec": 18.0,
                    "missing_time_sec": 2.0,
                    "ambiguous_time_sec": 0.0,
                    "total_distance_m": 100.0,
                    "observed_distance_m": 90.0,
                    "estimated_short_gap_distance_m": 10.0,
                    "top_speed_kmh": 22.5,
                    "high_intensity_time_sec": 2.5,
                    "high_intensity_distance_m": 18.0,
                    "sprint_count": 2,
                    "sprint_time_sec": 1.1,
                    "sprint_distance_m": 8.0,
                    "longest_sprint_distance_m": 5.0,
                    "max_sprint_speed_kmh": 24.0,
                    "sprint_candidate_count": 3,
                    "rejected_sprint_candidate_count": 1,
                    "best_sprint_candidate_speed_kmh": 26.0,
                    "best_sprint_candidate_duration_sec": 0.22,
                    "best_rejected_sprint_candidate": {
                        "duration_sec": 0.22,
                        "distance_m": 1.6,
                        "max_speed_kmh": 26.0,
                        "reason": "too_short",
                    },
                    "players_low_quality": 0,
                    "players_medium_quality": 1,
                    "players_high_quality": 1,
                }
            ],
        }
        team_config = {
            "teams": [
                {
                    "team_label": "A",
                    "team_id": "team-a",
                    "team_name": "White",
                    "display_color": "#ffffff",
                    "detected_color_hex": "#eeeeee",
                    "locked": True,
                }
            ]
        }

        doc = build_team_stats_document(player_stats, team_config)

        self.assertEqual(doc["schema_version"], "0.1.0")
        self.assertEqual(doc["summary"]["teams"], 1)
        self.assertEqual(doc["summary"]["total_distance_m"], 100.0)
        self.assertEqual(doc["teams"][0]["team_name"], "White")
        self.assertTrue(doc["teams"][0]["locked"])
        self.assertEqual(doc["teams"][0]["players_medium_quality"], 1)
        self.assertEqual(doc["teams"][0]["sprint_count"], 2)
        self.assertEqual(doc["teams"][0]["sprint_candidate_count"], 3)
        self.assertEqual(doc["teams"][0]["rejected_sprint_candidate_count"], 1)
        self.assertEqual(doc["summary"]["sprint_distance_m"], 8.0)
        self.assertEqual(doc["summary"]["sprint_candidate_count"], 3)
        self.assertEqual(doc["summary"]["best_rejected_sprint_candidate"]["reason"], "too_short")


if __name__ == "__main__":
    unittest.main()
