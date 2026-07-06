from __future__ import annotations

import unittest

from app.services.global_identity import (
    _slot_movement_stats,
    build_frame_detection_counts_from_global_identity,
    build_stable_players_from_global_identity,
    resolve_global_identity,
)


def position(frame: int, x: float, y: float, bbox: list[int] | None = None) -> dict:
    bbox_xyxy = bbox or [int(x * 10), int(y * 10), int(x * 10 + 10), int(y * 10 + 20)]
    return {
        "frame": frame,
        "time_sec": round(frame / 30, 3),
        "bbox_xyxy": bbox_xyxy,
        "footpoint": [(bbox_xyxy[0] + bbox_xyxy[2]) / 2, bbox_xyxy[3]],
        "pitch_m": [x, y],
        "smoothed_pitch_m": [x, y],
        "confidence": 0.8,
    }


def tracklet(tracklet_id: str, team_label: str, rows: list[dict]) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "source_track_id": int(tracklet_id.split(":", 1)[0]),
        "segment_index": int(tracklet_id.split(":", 1)[1]),
        "start_time_sec": rows[0]["time_sec"],
        "end_time_sec": rows[-1]["time_sec"],
        "duration_sec": rows[-1]["time_sec"] - rows[0]["time_sec"],
        "positions_count": len(rows),
        "mean_confidence": 0.8,
        "first_pitch_m": rows[0]["pitch_m"],
        "last_pitch_m": rows[-1]["pitch_m"],
        "positions": rows,
        "appearance_rgb": [240, 240, 240] if team_label == "A" else [245, 110, 30],
        "appearance_samples": 2,
        "team_label": team_label,
        "team_id": f"team-{team_label.lower()}",
        "team_name": f"Team {team_label}",
        "team_confidence": 0.9,
    }


class GlobalIdentityTests(unittest.TestCase):
    def resolve(self, tracklets: list[dict]) -> dict:
        return resolve_global_identity(
            tracklets,
            raw_tracks_count=len(tracklets),
            rejected_tracklets_count=0,
            pitch_width_m=30,
            pitch_length_m=47.4,
            fps=30,
        )

    def test_short_gap_keeps_same_slot_without_team_switch(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "A", [position(0, 1, 1), position(1, 1.1, 1)]),
                tracklet("2:1", "B", [position(2, 1.2, 1)]),
                tracklet("3:1", "A", [position(3, 1.3, 1), position(4, 1.4, 1)]),
            ]
        )

        a01 = next(slot for slot in identity["slots"] if slot["slot_id"] == "A01")
        self.assertEqual(a01["team_label"], "A")
        self.assertEqual(a01["tracklet_ids"], ["1:1", "3:1"])
        self.assertNotIn("2:1", a01["tracklet_ids"])
        self.assertGreater(a01["missing_frames"], 0)

    def test_rejects_unrealistic_jump_and_starts_second_slot(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "A", [position(0, 0, 0)]),
                tracklet("2:1", "A", [position(1, 20, 0)]),
            ]
        )

        a_slots = [slot for slot in identity["slots"] if slot["team_label"] == "A"]
        self.assertEqual(len(a_slots), 2)
        self.assertEqual(a_slots[0]["tracklet_ids"], ["1:1"])
        self.assertEqual(a_slots[1]["tracklet_ids"], ["2:1"])

    def test_caps_active_slots_to_seven_per_team(self) -> None:
        tracklets = [
            tracklet(f"{index + 1}:1", "A", [position(0, index, 1)])
            for index in range(8)
        ]
        identity = self.resolve(tracklets)
        self.assertEqual(identity["summary"]["team_counts"]["A"], 7)

    def test_new_entry_after_long_absence_uses_bench_slot_instead_of_recycling_starter(self) -> None:
        tracklets = [
            tracklet("1:1", "A", [position(frame, 1 + frame * 0.01, 1) for frame in range(6)])
        ]
        for index in range(2, 8):
            tracklets.append(
                tracklet(
                    f"{index}:1",
                    "A",
                    [
                        position(0, index, 2),
                        position(120, index + 0.1, 2),
                        position(240, index + 0.2, 2),
                        position(300, index + 0.3, 2),
                    ],
                )
            )
        tracklets.append(tracklet("8:1", "A", [position(300, 20, 2), position(301, 20.1, 2)]))

        identity = self.resolve(tracklets)

        a01 = next(slot for slot in identity["slots"] if slot["slot_id"] == "A01")
        a08 = next(slot for slot in identity["slots"] if slot["slot_id"] == "A08")
        self.assertEqual(a01["tracklet_ids"], ["1:1"])
        self.assertEqual(a08["tracklet_ids"], ["8:1"])
        self.assertEqual(identity["summary"]["team_counts"]["A"], 8)

    def test_stable_players_view_keeps_extra_roster_candidates(self) -> None:
        tracklets = [
            tracklet("1:1", "A", [position(frame, 1 + frame * 0.01, 1) for frame in range(6)])
        ]
        for index in range(2, 8):
            tracklets.append(
                tracklet(
                    f"{index}:1",
                    "A",
                    [
                        position(0, index, 2),
                        position(120, index + 0.1, 2),
                        position(240, index + 0.2, 2),
                        position(300, index + 0.3, 2),
                    ],
                )
            )
        tracklets.append(tracklet("8:1", "A", [position(300, 20, 2), position(301, 20.1, 2)]))

        identity = self.resolve(tracklets)
        stable_doc = build_stable_players_from_global_identity(identity)

        self.assertEqual(identity["summary"]["team_counts"]["A"], 8)
        self.assertEqual(stable_doc["summary"]["team_counts"]["A"], 8)
        self.assertEqual(stable_doc["summary"]["stable_players"], 8)
        self.assertEqual(stable_doc["summary"]["stable_player_candidates"], 8)
        self.assertEqual(stable_doc["summary"]["suppressed_extra_candidates"], 0)
        self.assertEqual([player["stable_player_id"] for player in stable_doc["players"]], [f"A{index:02d}" for index in range(1, 9)])
        self.assertEqual(stable_doc["suppressed_candidates"], [])

    def test_outputs_slot_frame_counts(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "A", [position(0, 1, 1), position(2, 1.2, 1)]),
            ]
        )
        counts = build_frame_detection_counts_from_global_identity(identity, fps=30, target_players=1)
        self.assertEqual(counts["frames"][0]["slot_detected"], 1)
        self.assertEqual(counts["frames"][1]["slot_missing"], 1)
        self.assertEqual(counts["frames"][1]["visible_stable_boxes"], 0)
        self.assertEqual(counts["frames"][2]["slot_detected"], 1)

    def test_out_of_pitch_prediction_has_no_visible_bbox(self) -> None:
        rows = [position(frame, 28.0 + frame * 0.35, 10) for frame in range(6)]
        rows.append(position(9, 29.9, 10))
        identity = self.resolve([tracklet("1:1", "A", rows)])
        stable_doc = build_stable_players_from_global_identity(identity)
        a01 = next(player for player in stable_doc["players"] if player["stable_player_id"] == "A01")
        predicted_overlay_frames = [
            row["frame"]
            for row in a01["overlay_positions"]
            if row.get("source") in {"predicted", "interpolated"}
        ]
        self.assertEqual(predicted_overlay_frames, [])
        missing_counts = [
            frame
            for frame in build_frame_detection_counts_from_global_identity(identity, fps=30, target_players=1)["frames"]
            if frame["slot_missing"] > 0
        ]
        self.assertGreater(len(missing_counts), 0)

    def test_stable_players_view_is_compatibility_layer(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "B", [position(0, 5, 1), position(1, 5.1, 1)]),
            ]
        )
        stable_doc = build_stable_players_from_global_identity(identity)
        self.assertEqual(stable_doc["source"], "conservative_identity_v2")
        self.assertEqual(stable_doc["players"][0]["stable_player_id"], "B01")
        self.assertEqual(stable_doc["players"][0]["identity_semantics"], "stint_first")

    def test_new_tracklet_requires_confirmation_before_switch(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "A", [position(frame, 4 + frame * 0.05, 4) for frame in range(6)]),
                tracklet("2:1", "A", [position(6, 4.35, 4), position(7, 4.4, 4)]),
            ]
        )

        a01 = next(slot for slot in identity["slots"] if slot["slot_id"] == "A01")
        self.assertEqual(a01["tracklet_ids"], ["1:1"])
        self.assertGreater(a01["ambiguous_frames"], 0)
        self.assertGreater(a01["blocked_identity_switches"], 0)
        ambiguous_rows = [
            row
            for row in a01["overlay_positions"]
            if row.get("source") == "ambiguous"
        ]
        self.assertGreater(len(ambiguous_rows), 0)

    def test_confirmed_switch_with_nearby_competitor_becomes_detected(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "A", [position(frame, 4 + frame * 0.05, 4) for frame in range(6)]),
                tracklet("2:1", "A", [position(frame, 4 + frame * 0.05, 4) for frame in range(6, 21)]),
                tracklet("3:1", "A", [position(frame, 4.25 + frame * 0.05, 4.1) for frame in range(6, 21)]),
            ]
        )

        a01 = next(slot for slot in identity["slots"] if slot["slot_id"] == "A01")
        self.assertIn("2:1", a01["tracklet_ids"])
        self.assertTrue(
            any(event.get("type") == "confirmed_switch_with_competitor_accepted" for event in a01["identity_events"])
        )
        self.assertGreater(a01["ambiguous_frames"], 0)

    def test_recent_missing_same_team_slot_is_reused_before_spawn(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "A", [position(frame, 1 + frame * 0.01, 1) for frame in range(3)]),
                tracklet("2:1", "A", [position(frame, 1.4 + (frame - 270) * 0.01, 1) for frame in range(270, 273)]),
            ]
        )

        a01 = next(slot for slot in identity["slots"] if slot["slot_id"] == "A01")
        self.assertEqual(a01["tracklet_ids"], ["1:1", "2:1"])
        self.assertEqual(a01["reused_from_slot_id"], "A01")
        self.assertEqual(a01["slot_reuse_events"][0]["reason"], "recent_same_team_slot_reused")
        self.assertEqual([slot["slot_id"] for slot in identity["slots"]], ["A01"])

    def test_confirmed_unmatched_raw_backfills_missing_active_slot(self) -> None:
        tracklets = [
            tracklet(f"{100 + index}:1", "A", [position(frame, index, 8) for frame in range(8)])
            for index in range(7)
        ]
        tracklets.extend(
            [
                tracklet(f"{index + 1}:1", "B", [position(frame, index, 2) for frame in range(8)])
                for index in range(6)
            ]
        )
        tracklets.append(tracklet("7:1", "B", [position(frame, 6, 2) for frame in range(3)]))
        tracklets.append(tracklet("8:1", "B", [position(frame, 10 + frame * 0.05, 2) for frame in range(3, 7)]))

        identity = self.resolve(tracklets)

        b07 = next(slot for slot in identity["slots"] if slot["slot_id"] == "B07")
        self.assertIn("8:1", b07["tracklet_ids"])
        repaired_frames = [
            row["frame"]
            for row in b07["overlay_positions"]
            if row.get("tracklet_id") == "8:1" and row.get("repair_reason") == "unmatched_raw_confirmed"
        ]
        self.assertEqual(repaired_frames, [3, 4, 5, 6])
        self.assertEqual(identity["summary"]["unmatched_raw_backfilled"], 4)
        self.assertEqual(identity["summary"]["unmatched_raw_remaining"], 0)

        counts = build_frame_detection_counts_from_global_identity(identity, fps=30, target_players=14)
        self.assertEqual(counts["frames"][3]["slot_detected"], 14)
        self.assertEqual(counts["frames"][3]["slot_missing"], 0)
        self.assertEqual(counts["frames"][3]["unmatched_raw_detections"], 0)

    def test_short_unmatched_raw_remains_debug_only(self) -> None:
        tracklets = [
            tracklet(f"{100 + index}:1", "A", [position(frame, index, 8) for frame in range(5)])
            for index in range(7)
        ]
        tracklets.extend(
            [
                tracklet(f"{index + 1}:1", "B", [position(frame, index, 2) for frame in range(5)])
                for index in range(6)
            ]
        )
        tracklets.append(tracklet("7:1", "B", [position(frame, 6, 2) for frame in range(3)]))
        tracklets.append(tracklet("8:1", "B", [position(frame, 10 + frame * 0.05, 2) for frame in range(3, 5)]))

        identity = self.resolve(tracklets)

        b07 = next(slot for slot in identity["slots"] if slot["slot_id"] == "B07")
        self.assertNotIn("8:1", b07["tracklet_ids"])
        self.assertEqual(identity["summary"]["unmatched_raw_backfilled"], 0)
        self.assertEqual(identity["summary"]["unmatched_raw_remaining"], 2)
        self.assertEqual([row["frame"] for row in identity["unmatched_observations"]], [3, 4])

        counts = build_frame_detection_counts_from_global_identity(identity, fps=30, target_players=14)
        self.assertEqual(counts["frames"][3]["slot_detected"], 13)
        self.assertEqual(counts["frames"][3]["slot_missing"], 1)
        self.assertEqual(counts["frames"][3]["unmatched_raw_detections"], 1)

    def test_ambiguous_candidate_is_not_counted_as_visible_stable_box(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "A", [position(frame, 4 + frame * 0.05, 4) for frame in range(6)]),
                tracklet("2:1", "A", [position(6, 4.35, 4)]),
            ]
        )
        counts = build_frame_detection_counts_from_global_identity(identity, fps=30, target_players=1)
        self.assertEqual(counts["frames"][6]["slot_ambiguous"], 1)
        self.assertEqual(counts["frames"][6]["visible_stable_boxes"], 0)
        self.assertEqual(counts["summary"]["ghost_bbox_count"], 0)

    def test_shadow_like_low_confidence_bbox_does_not_start_slot(self) -> None:
        shadow = position(0, 4, 4, bbox=[100, 100, 200, 140])
        shadow["confidence"] = 0.09
        identity = self.resolve([tracklet("1:1", "A", [shadow])])
        self.assertEqual(identity["summary"]["stable_players"], 0)
        self.assertEqual(identity["summary"]["rejected_start_candidates"], 1)
        self.assertEqual(identity["rejected_start_candidates"][0]["reason"], "shadow_like_wide_low_confidence_bbox")

    def test_movement_stats_count_short_gap_as_estimated_distance(self) -> None:
        identity = self.resolve(
            [
                tracklet(
                    "1:1",
                    "A",
                    [
                        position(0, 0, 0),
                        position(1, 0.1, 0),
                        position(30, 3.1, 0),
                    ],
                ),
            ]
        )
        a01 = next(slot for slot in identity["slots"] if slot["slot_id"] == "A01")
        stats = a01["movement_stats"]
        self.assertAlmostEqual(stats["observed_distance_m"], 0.1, places=2)
        self.assertAlmostEqual(stats["estimated_gap_distance_m"], 3.0, places=2)
        self.assertAlmostEqual(stats["total_distance_m"], 3.1, places=2)
        self.assertEqual(stats["estimated_gap_segments"], 1)

    def test_movement_stats_do_not_count_long_missing_gap_as_playing_time(self) -> None:
        identity = self.resolve(
            [
                tracklet(
                    "1:1",
                    "A",
                    [
                        position(0, 0, 0),
                        position(1, 0.1, 0),
                        position(300, 0.2, 0),
                        position(301, 0.3, 0),
                    ],
                ),
            ]
        )

        a01 = next(slot for slot in identity["slots"] if slot["slot_id"] == "A01")
        stats = a01["movement_stats"]
        self.assertGreater(a01["missing_frames"], 200)
        self.assertLess(stats["playing_time_sec"], 5.0)
        self.assertLess(stats["missing_time_sec"], 2.1)
        self.assertGreater(len(a01["stints"]), 1)

    def test_movement_stats_skip_unrealistic_speed_segments(self) -> None:
        stats = _slot_movement_stats(
            [
                {"frame": 0, "time_sec": 0.0, "pitch_m": [0, 0], "source": "detected"},
                {"frame": 30, "time_sec": 1.0, "pitch_m": [30, 0], "source": "detected"},
            ],
            fps=30,
        )

        self.assertEqual(stats["total_distance_m"], 0.0)
        self.assertEqual(stats["skipped_outlier_segments"], 1)

    def test_movement_stats_peak_sustained_speed_for_steady_runner(self) -> None:
        stats = _slot_movement_stats(
            [
                {
                    "frame": frame,
                    "time_sec": frame / 30,
                    "pitch_m": [frame * 0.1, 0],
                    "source": "detected",
                }
                for frame in range(31)
            ],
            fps=30,
        )

        self.assertAlmostEqual(stats["peak_sustained_speed_kmh"], 10.8, places=1)
        self.assertAlmostEqual(stats["top_speed_kmh"], 10.8, places=1)
        self.assertEqual(stats["speed_quality"], "high")
        self.assertGreater(stats["sustained_speed_windows"], 0)

    def test_movement_stats_counts_conservative_sprint_run(self) -> None:
        stats = _slot_movement_stats(
            [
                {
                    "frame": frame,
                    "time_sec": frame / 30,
                    "pitch_m": [frame * 0.2, 0],
                    "source": "detected",
                }
                for frame in range(31)
            ],
            fps=30,
        )

        intensity = stats["intensity"]
        self.assertEqual(intensity["sprint_count"], 1)
        self.assertAlmostEqual(intensity["sprint_distance_m"], 6.0, places=1)
        self.assertAlmostEqual(intensity["sprint_time_sec"], 1.0, places=1)
        self.assertAlmostEqual(intensity["high_intensity_distance_m"], 6.0, places=1)
        self.assertAlmostEqual(intensity["max_sprint_speed_kmh"], 21.6, places=1)
        self.assertEqual(intensity["sprint_candidate_count"], 1)
        self.assertEqual(intensity["rejected_sprint_candidate_count"], 0)
        self.assertAlmostEqual(intensity["best_sprint_candidate_speed_kmh"], 21.6, places=1)
        self.assertAlmostEqual(intensity["best_sprint_candidate_duration_sec"], 1.0, places=1)
        self.assertEqual(intensity["best_sprint_candidate_reason"], "accepted")

    def test_movement_stats_short_spike_does_not_set_peak_speed(self) -> None:
        stats = _slot_movement_stats(
            [
                {"frame": 0, "time_sec": 0.0, "pitch_m": [0, 0], "source": "detected"},
                {"frame": 1, "time_sec": 1 / 30, "pitch_m": [0.25, 0], "source": "detected"},
            ],
            fps=30,
        )

        self.assertGreater(stats["raw_segment_top_speed_kmh"], 25.0)
        self.assertEqual(stats["peak_sustained_speed_kmh"], 0.0)
        self.assertEqual(stats["top_speed_kmh"], 0.0)
        self.assertEqual(stats["speed_quality"], "low")
        self.assertEqual(stats["intensity"]["sprint_count"], 0)
        self.assertEqual(stats["intensity"]["sprint_candidate_count"], 1)
        self.assertEqual(stats["intensity"]["rejected_sprint_candidate_count"], 1)
        self.assertEqual(stats["intensity"]["best_sprint_candidate_reason"], "too_short")
        self.assertEqual(stats["intensity"]["best_rejected_sprint_candidate"]["reason"], "too_short")
        self.assertGreater(stats["intensity"]["best_rejected_sprint_candidate"]["max_speed_kmh"], 25.0)
        self.assertLess(stats["intensity"]["best_rejected_sprint_candidate"]["duration_sec"], 0.5)

    def test_movement_stats_reports_gap_sprint_candidate_as_rejected(self) -> None:
        stats = _slot_movement_stats(
            [
                {"frame": 0, "time_sec": 0.0, "pitch_m": [0, 0], "source": "detected"},
                {"frame": 10, "time_sec": 10 / 30, "pitch_m": [2.0, 0], "source": "detected"},
            ],
            fps=30,
        )

        intensity = stats["intensity"]
        self.assertEqual(intensity["sprint_count"], 0)
        self.assertEqual(intensity["sprint_candidate_count"], 1)
        self.assertEqual(intensity["rejected_sprint_candidate_count"], 1)
        self.assertEqual(intensity["best_sprint_candidate_reason"], "gap_too_large")
        self.assertEqual(intensity["best_rejected_sprint_candidate"]["reason"], "gap_too_large")

    def test_movement_stats_single_outlier_does_not_inflate_peak_speed(self) -> None:
        rows = []
        for frame in range(31):
            x = frame * 0.1
            if frame == 10:
                x = 3.5
            rows.append(
                {
                    "frame": frame,
                    "time_sec": frame / 30,
                    "pitch_m": [x, 0],
                    "source": "detected",
                }
            )

        stats = _slot_movement_stats(rows, fps=30)

        self.assertLessEqual(stats["peak_sustained_speed_kmh"], 11.0)
        self.assertLessEqual(stats["top_speed_kmh"], 11.0)
        self.assertGreaterEqual(stats["skipped_outlier_segments"], 1)

    def test_stable_players_include_movement_stats(self) -> None:
        identity = self.resolve(
            [
                tracklet("1:1", "B", [position(0, 5, 1), position(1, 5.1, 1)]),
            ]
        )
        stable_doc = build_stable_players_from_global_identity(identity)
        stats = stable_doc["players"][0]["movement_stats"]
        self.assertIn("total_distance_m", stats)
        self.assertIn("top_speed_kmh", stats)
        self.assertIn("peak_sustained_speed_kmh", stats)


if __name__ == "__main__":
    unittest.main()
