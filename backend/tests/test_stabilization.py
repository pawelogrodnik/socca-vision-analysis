from __future__ import annotations

import unittest

from app.services.stabilization import (
    _live_movement_by_frame,
    build_frame_detection_counts,
    build_stable_players,
    build_stable_players_document,
    cluster_tracklet_teams,
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
        self.assertEqual(cluster_doc["method"], "torso_color_white_vs_bib_v3")

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

        self.assertEqual(tracklets[4]["team_label"], "A")
        self.assertEqual(tracklets[5]["team_label"], "B")
        self.assertEqual(cluster_doc["goalkeeper_color_outliers_count"], 2)
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
            ],
            fps=30,
        )

        self.assertAlmostEqual(live[1]["cumulative_distance_m"], 0.1, places=2)
        self.assertEqual(live[1]["segment_source"], "observed")
        self.assertAlmostEqual(live[6]["cumulative_distance_m"], 1.0, places=2)
        self.assertAlmostEqual(live[6]["current_speed_kmh"], 19.4, places=2)
        self.assertEqual(live[6]["segment_source"], "estimated")

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


if __name__ == "__main__":
    unittest.main()
