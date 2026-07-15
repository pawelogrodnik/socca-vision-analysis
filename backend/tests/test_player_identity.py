from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.player_identity import build_player_identity_review, save_player_identity_assignments
from app.services.resolved_player_stats import build_resolved_player_stats_from_files


def stable_doc() -> dict:
    return {
        "schema_version": "0.1.0",
        "identity_semantics": "stint_first",
        "players": [
            {
                "stable_subject_id": "slot-a01",
                "stable_player_id": "A01",
                "slot_id": "A01",
                "status": "active",
                "team_label": "A",
                "team_id": "team-a",
                "team_name": "White",
                "stints": [
                    {
                        "stint_id": "slot-a01-stint-001",
                        "start_time_sec": 0.0,
                        "end_time_sec": 10.0,
                        "duration_sec": 10.0,
                    }
                ],
            },
            {
                "stable_subject_id": "slot-b01",
                "stable_player_id": "B01",
                "slot_id": "B01",
                "status": "active",
                "team_label": "B",
                "team_id": "team-b",
                "team_name": "Orange",
                "stints": [],
            },
        ],
        "summary": {"stable_players": 2},
    }


def match_meta() -> dict:
    return {
        "id": "match-1",
        "teams": [
            {
                "id": "team-a",
                "name": "White",
                "players": [
                    {"id": "p-a-1", "name": "Pawel", "number": "7", "role": "player"},
                ],
            },
            {
                "id": "team-b",
                "name": "Orange",
                "players": [
                    {"id": "p-b-1", "name": "Tomek", "number": "10", "role": "player"},
                ],
            },
        ],
    }


def player_stats_doc() -> dict:
    return {
        "schema_version": "0.1.0",
        "identity_semantics": "stint_first",
        "scope": "tracking_only_no_ball",
        "units": {"distance": "meters", "speed": "mps_and_kmh", "time": "seconds"},
        "summary": {"players": 2},
        "teams": [],
        "players": [
            {
                "stable_subject_id": "slot-a01",
                "stable_player_id": "A01",
                "slot_id": "A01",
                "team_label": "A",
                "team_id": "team-a",
                "team_name": "White",
                "tracklet_ids": ["1:1"],
                "raw_track_ids": [1],
                "time": {
                    "playing_time_sec": 10.0,
                    "detected_time_sec": 8.0,
                    "missing_time_sec": 1.5,
                    "ambiguous_time_sec": 0.5,
                },
                "distance": {
                    "observed_distance_m": 20.0,
                    "estimated_short_gap_distance_m": 3.0,
                    "total_distance_m": 23.0,
                    "estimated_distance_ratio": 0.1304,
                    "quality": "medium",
                },
                "speed": {
                    "avg_speed_mps": 2.3,
                    "avg_speed_kmh": 8.28,
                    "observed_avg_speed_mps": 2.5,
                    "peak_sustained_speed_mps": 5.0,
                    "peak_sustained_speed_kmh": 18.0,
                    "top_speed_mps": 5.0,
                    "top_speed_kmh": 18.0,
                    "quality": "medium",
                },
                "intensity": {
                    "high_intensity_time_sec": 1.0,
                    "high_intensity_distance_m": 5.0,
                    "sprint_count": 1,
                    "sprint_time_sec": 0.6,
                    "sprint_distance_m": 3.5,
                    "longest_sprint_distance_m": 3.5,
                    "max_sprint_speed_kmh": 21.0,
                    "sprint_candidate_count": 2,
                    "rejected_sprint_candidate_count": 1,
                    "best_sprint_candidate_speed_kmh": 27.0,
                    "best_sprint_candidate_duration_sec": 0.13,
                    "best_sprint_candidate_distance_m": 1.0,
                    "best_sprint_candidate_reason": "too_short",
                    "best_rejected_sprint_candidate": {
                        "start_frame": 80,
                        "end_frame": 84,
                        "duration_sec": 0.13,
                        "distance_m": 1.0,
                        "max_speed_kmh": 27.0,
                        "reason": "too_short",
                    },
                },
                "frames": {"active_frames": 300, "detected_frames": 240, "missing_frames": 45, "ambiguous_frames": 15, "predicted_frames": 0, "samples_used": 240},
                "segments": {"observed_segments": 10, "estimated_gap_segments": 1, "skipped_outlier_segments": 0, "skipped_speed_outlier_segments": 0, "skipped_long_gap_segments": 0, "sustained_speed_windows": 5},
            },
            {
                "stable_subject_id": "slot-b01",
                "stable_player_id": "B01",
                "slot_id": "B01",
                "team_label": "B",
                "team_id": "team-b",
                "team_name": "Orange",
                "time": {"playing_time_sec": 6.0, "detected_time_sec": 5.0, "missing_time_sec": 1.0, "ambiguous_time_sec": 0.0},
                "distance": {"observed_distance_m": 12.0, "estimated_short_gap_distance_m": 0.0, "total_distance_m": 12.0, "estimated_distance_ratio": 0.0, "quality": "high"},
                "speed": {"avg_speed_mps": 2.0, "avg_speed_kmh": 7.2, "observed_avg_speed_mps": 2.4, "peak_sustained_speed_mps": 4.0, "peak_sustained_speed_kmh": 14.4, "top_speed_mps": 4.0, "top_speed_kmh": 14.4, "quality": "high"},
                "frames": {"active_frames": 180, "detected_frames": 150, "missing_frames": 30, "ambiguous_frames": 0, "predicted_frames": 0, "samples_used": 150},
                "segments": {"observed_segments": 8, "estimated_gap_segments": 0, "skipped_outlier_segments": 0, "skipped_speed_outlier_segments": 0, "skipped_long_gap_segments": 0, "sustained_speed_windows": 2},
            },
        ],
    }


class PlayerIdentityTests(unittest.TestCase):
    def test_review_builds_default_unassigned_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "stable_players.json").write_text(json.dumps(stable_doc()), encoding="utf-8")

            review = build_player_identity_review(path, match_meta())
            doc = review["player_identity_assignments"]

            self.assertEqual(doc["schema_version"], "0.1.0")
            self.assertEqual(doc["summary"]["stable_slots"], 2)
            self.assertEqual(doc["summary"]["unassigned_slots"], 2)
            self.assertEqual(doc["expanded_stint_assignments"][0]["stint_id"], "slot-a01-stint-001")

    def test_save_assigns_stable_slot_to_roster_player(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "stable_players.json").write_text(json.dumps(stable_doc()), encoding="utf-8")

            review = save_player_identity_assignments(
                path,
                match_meta(),
                [
                    {
                        "stable_subject_id": "slot-a01",
                        "status": "assigned",
                        "player_id": "p-a-1",
                    }
                ],
            )
            doc = review["player_identity_assignments"]
            assigned = [item for item in doc["assignments"] if item["stable_subject_id"] == "slot-a01"][0]

            self.assertEqual(assigned["player_name"], "Pawel")
            self.assertEqual(assigned["team_id"], "team-a")
            self.assertEqual(doc["summary"]["assigned_slots"], 1)
            self.assertEqual(doc["summary"]["assigned_stints"], 1)
            self.assertTrue((path / "player_identity_assignments.json").exists())

    def test_team_mismatch_is_reported_not_silently_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "stable_players.json").write_text(json.dumps(stable_doc()), encoding="utf-8")

            review = save_player_identity_assignments(
                path,
                match_meta(),
                [
                    {
                        "stable_subject_id": "slot-a01",
                        "status": "assigned",
                        "player_id": "p-b-1",
                    }
                ],
            )
            assigned = [
                item
                for item in review["player_identity_assignments"]["assignments"]
                if item["stable_subject_id"] == "slot-a01"
            ][0]

            self.assertIn("team_id_mismatch", assigned["review_warnings"])
            self.assertIn("team_label_mismatch", assigned["review_warnings"])
            self.assertEqual(review["player_identity_assignments"]["summary"]["conflicts_total"], 1)

    def test_resolved_player_stats_use_only_assigned_roster_players(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "stable_players.json").write_text(json.dumps(stable_doc()), encoding="utf-8")
            (path / "player_stats.json").write_text(json.dumps(player_stats_doc()), encoding="utf-8")
            save_player_identity_assignments(
                path,
                match_meta(),
                [
                    {
                        "stable_subject_id": "slot-a01",
                        "status": "assigned",
                        "player_id": "p-a-1",
                    }
                ],
            )

            doc = build_resolved_player_stats_from_files(path, persist=True)

            self.assertEqual(doc["schema_version"], "0.1.0")
            self.assertEqual(doc["summary"]["players"], 1)
            self.assertEqual(doc["summary"]["assigned_slots"], 1)
            self.assertEqual(doc["summary"]["unresolved_slots"], 1)
            self.assertEqual(doc["players"][0]["player_id"], "p-a-1")
            self.assertEqual(doc["players"][0]["distance"]["total_distance_m"], 23.0)
            self.assertEqual(doc["players"][0]["speed"]["peak_sustained_speed_kmh"], 18.0)
            self.assertEqual(doc["players"][0]["intensity"]["sprint_count"], 1)
            self.assertEqual(doc["players"][0]["intensity"]["sprint_candidate_count"], 2)
            self.assertEqual(doc["players"][0]["intensity"]["rejected_sprint_candidate_count"], 1)
            self.assertEqual(doc["players"][0]["intensity"]["best_sprint_candidate_reason"], "too_short")
            self.assertEqual(doc["players"][0]["intensity"]["best_rejected_sprint_candidate"]["reason"], "too_short")
            self.assertEqual(doc["summary"]["sprint_distance_m"], 3.5)
            self.assertEqual(doc["summary"]["sprint_candidate_count"], 2)
            self.assertTrue((path / "resolved_player_stats.json").exists())

    def test_resolved_player_stats_clip_repeated_slot_stint_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            stable = stable_doc()
            stable["players"][0]["stints"] = [
                {
                    "stint_id": "slot-a01-stint-001",
                    "start_frame": 0,
                    "end_frame": 99,
                    "detected_frames": 80,
                    "missing_frames": 15,
                    "ambiguous_frames": 5,
                    "predicted_frames": 0,
                    "tracklet_ids": ["1:1"],
                    "raw_track_ids": [1],
                },
                {
                    "stint_id": "slot-a01-stint-002",
                    "start_frame": 100,
                    "end_frame": 299,
                    "detected_frames": 160,
                    "missing_frames": 30,
                    "ambiguous_frames": 10,
                    "predicted_frames": 0,
                    "tracklet_ids": ["2:1"],
                    "raw_track_ids": [2],
                },
            ]
            (path / "stable_players.json").write_text(json.dumps(stable), encoding="utf-8")
            (path / "player_stats.json").write_text(json.dumps(player_stats_doc()), encoding="utf-8")
            save_player_identity_assignments(
                path,
                match_meta(),
                [
                    {
                        "stable_subject_id": "slot-a01",
                        "stint_id": "slot-a01-stint-001",
                        "status": "assigned",
                        "player_id": "p-a-1",
                    },
                    {
                        "stable_subject_id": "slot-a01",
                        "stint_id": "slot-a01-stint-002",
                        "status": "assigned",
                        "player_id": "p-a-1",
                    },
                ],
            )

            doc = build_resolved_player_stats_from_files(path, persist=True)

            self.assertEqual(doc["summary"]["assigned_stints"], 2)
            self.assertEqual(doc["players"][0]["player_id"], "p-a-1")
            self.assertEqual(len(doc["players"][0]["source_stable_slots"]), 2)
            self.assertEqual(doc["players"][0]["time"]["playing_time_sec"], 10.0)
            self.assertEqual(doc["players"][0]["time"]["detected_time_sec"], 8.0)
            self.assertEqual(doc["players"][0]["distance"]["total_distance_m"], 23.0)
            self.assertEqual(doc["summary"]["total_distance_m"], 23.0)

    def test_save_blocks_overlapping_stints_for_one_player(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            stable = stable_doc()
            stable["players"][0]["stints"] = [
                {
                    "stint_id": "slot-a01-stint-001",
                    "start_time_sec": 0.0,
                    "end_time_sec": 20.0,
                    "start_frame": 0,
                    "end_frame": 199,
                    "detected_frames": 200,
                    "missing_frames": 0,
                    "ambiguous_frames": 0,
                    "predicted_frames": 0,
                },
                {
                    "stint_id": "slot-a01-stint-002",
                    "start_time_sec": 10.0,
                    "end_time_sec": 30.0,
                    "start_frame": 100,
                    "end_frame": 299,
                    "detected_frames": 200,
                    "missing_frames": 0,
                    "ambiguous_frames": 0,
                    "predicted_frames": 0,
                },
            ]
            (path / "stable_players.json").write_text(json.dumps(stable), encoding="utf-8")
            (path / "player_stats.json").write_text(json.dumps(player_stats_doc()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlapping stints"):
                save_player_identity_assignments(
                    path,
                    match_meta(),
                    [
                        {
                            "stable_subject_id": "slot-a01",
                            "stint_id": "slot-a01-stint-001",
                            "status": "assigned",
                            "player_id": "p-a-1",
                        },
                        {
                            "stable_subject_id": "slot-a01",
                            "stint_id": "slot-a01-stint-002",
                            "status": "assigned",
                            "player_id": "p-a-1",
                        },
                    ],
                )


if __name__ == "__main__":
    unittest.main()
