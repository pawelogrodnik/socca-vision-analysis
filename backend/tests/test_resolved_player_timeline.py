from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.public_match_report import _exact_player_heatmap_rows, _real_player_heatmap_rows
from app.services.resolved_player_stats import build_resolved_player_stats_from_files
from app.services.resolved_player_timeline import build_resolved_player_timeline
from app.services.resolved_player_timeline import calculate_timeline_presence


def position(frame: int, x: float, y: float, source: str = "detected") -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 30.0,
        "pitch_m": [x, y],
        "source": source,
        "status": source,
        "play_area_status": "inside_play",
        "confidence": 0.9,
    }


def slot(subject: str, rows: list[dict], *, team: str = "A", goal_end: str | None = None) -> dict:
    return {
        "slot_id": subject.replace("slot-", ""),
        "stable_subject_id": subject,
        "stable_player_id": subject.replace("slot-", ""),
        "team_label": team,
        "role": "goalkeeper" if goal_end else "field_player",
        "goal_end": goal_end,
        "overlay_positions": rows,
        "stints": [],
    }


def assignment(subject: str, player_id: str, start: int | None, end: int | None, **extra) -> dict:
    row = {
        "stable_subject_id": subject,
        "stable_player_id": subject.replace("slot-", ""),
        "stint_id": f"{subject}-C001",
        "assignment_scope": "crop_derived_stint",
        "status": "assigned",
        "player_id": player_id,
        "player_name": player_id,
        "player_role": "player",
        "player_number": "7",
        "team_label": "A",
        "team_id": "team-a",
        "team_name": "A",
        "anchor_confidence": 0.9,
    }
    if start is not None:
        row["start_frame"] = start
    if end is not None:
        row["end_frame"] = end
    row.update(extra)
    return row


class ResolvedPlayerTimelineTests(unittest.TestCase):
    def test_crop_assignment_uses_only_exact_frame_interval(self) -> None:
        global_identity = {
            "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
            "parameters": {"players_per_team": 7},
            "slots": [slot("slot-A01", [position(frame, frame, 10.0) for frame in range(10)])],
        }
        timeline = build_resolved_player_timeline(
            global_identity=global_identity,
            identity_assignments={"assignments": [assignment("slot-A01", "p1", 2, 4)]},
            fps=30.0,
        )

        self.assertEqual([row["frame"] for row in timeline["players"]["p1"]["rows"]], [2, 3, 4])
        self.assertEqual(timeline["quality"]["assignments_resolved"], 1)

    def test_crop_assignment_without_interval_is_reported_not_expanded(self) -> None:
        timeline = build_resolved_player_timeline(
            global_identity={
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "slots": [slot("slot-A01", [position(frame, frame, 10.0) for frame in range(10)])],
            },
            identity_assignments={"assignments": [assignment("slot-A01", "p1", None, None)]},
            fps=30.0,
        )

        self.assertNotIn("p1", timeline["players"])
        self.assertEqual(timeline["quality"]["assignments_unresolved"], 1)
        self.assertEqual(timeline["quality"]["unresolved_assignments"][0]["reason"], "missing_exact_frame_interval")

    def test_overlapping_slots_for_one_player_are_deduplicated(self) -> None:
        timeline = build_resolved_player_timeline(
            global_identity={
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "parameters": {"players_per_team": 7},
                "slots": [
                    slot("slot-A01", [position(frame, 1.0, 10.0) for frame in range(3)]),
                    slot("slot-A02", [position(frame, 2.0, 10.0) for frame in range(3)]),
                ],
            },
            identity_assignments={
                "assignments": [
                    assignment("slot-A01", "p1", 0, 2, anchor_confidence=0.8),
                    assignment("slot-A02", "p1", 0, 2, anchor_confidence=0.95),
                ]
            },
            fps=30.0,
        )

        rows = timeline["players"]["p1"]["rows"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["pitch_m"][0] == 2.0 for row in rows))
        self.assertEqual(timeline["quality"]["duplicate_frames_removed"], 3)

    def test_goalkeeper_opposite_end_fragment_with_team_conflict_is_excluded(self) -> None:
        goalkeeper = assignment(
            "slot-A07",
            "gk",
            0,
            1,
            player_role="goalkeeper",
            player_number="1",
        )
        wrong = assignment(
            "slot-B06",
            "gk",
            10,
            11,
            player_role="goalkeeper",
            player_number="1",
        )
        timeline = build_resolved_player_timeline(
            global_identity={
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "parameters": {"players_per_team": 7},
                "slots": [
                    slot("slot-A07", [position(0, 15.0, 45.0), position(1, 15.0, 44.0)], goal_end="near"),
                    slot("slot-B06", [position(10, 15.0, 2.0), position(11, 15.0, 3.0)], team="B", goal_end="far"),
                ],
            },
            identity_assignments={"assignments": [goalkeeper, wrong]},
            fps=30.0,
        )

        self.assertEqual([row["frame"] for row in timeline["players"]["gk"]["rows"]], [0, 1])
        self.assertEqual(len(timeline["quality"]["goalkeeper_anomalous_fragments_excluded"]), 1)

    def test_team_frame_capacity_prevents_more_than_seven_parallel_players(self) -> None:
        slots = []
        assignments = []
        for index in range(8):
            subject = f"slot-A{index + 1:02d}"
            slots.append(slot(subject, [position(0, float(index), 10.0)]))
            assignments.append(assignment(subject, f"p{index}", 0, 0, anchor_confidence=0.5 + index * 0.01))
        timeline = build_resolved_player_timeline(
            global_identity={
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "parameters": {"players_per_team": 7},
                "slots": slots,
            },
            identity_assignments={"assignments": assignments},
            fps=30.0,
        )

        counted = sum(len(player["rows"]) for player in timeline["players"].values())
        self.assertEqual(counted, 7)
        self.assertEqual(timeline["quality"]["team_capacity_frames_removed"], 1)

    def test_exact_stats_do_not_bridge_separate_manual_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            global_identity = {
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "parameters": {"players_per_team": 7},
                "slots": [
                    slot(
                        "slot-A01",
                        [
                            position(0, 0.0, 10.0),
                            position(3, 0.5, 10.0),
                            position(10, 20.0, 10.0),
                            position(13, 20.5, 10.0),
                            position(14, 20.5, 10.0, source="ambiguous"),
                        ],
                    )
                ],
                "frames": [],
            }
            identity = {
                "identity_semantics": "stint_first",
                "assignments": [
                    assignment("slot-A01", "p1", 0, 3),
                    assignment("slot-A01", "p1", 10, 14),
                ],
                "summary": {"assigned_stints": 2, "unassigned_slots": 0},
            }
            (path / "global_identity.json").write_text(json.dumps(global_identity), encoding="utf-8")
            (path / "player_identity_assignments.json").write_text(json.dumps(identity), encoding="utf-8")
            (path / "player_stats.json").write_text(
                json.dumps({"players": [], "teams": [], "units": {"distance": "meters"}}),
                encoding="utf-8",
            )
            (path / "match.json").write_text(
                json.dumps({"video": {"fps": 30.0, "duration_sec": 1.0}}),
                encoding="utf-8",
            )

            doc = build_resolved_player_stats_from_files(path)

            self.assertEqual(doc["calculation_method"], "exact_identity_coverage")
            self.assertEqual(doc["players"][0]["distance"]["total_distance_m"], 1.0)
            self.assertEqual(doc["players"][0]["frames"]["ambiguous_frames"], 1)
            self.assertEqual(doc["players"][0]["frames"]["samples_used"], 4)
            self.assertEqual(doc["players"][0]["unique_detected_frames"], 4)

    def test_playing_time_counts_ambiguous_rows_and_short_internal_holes(self) -> None:
        rows = [
            position(0, 1.0, 10.0),
            position(1, 1.1, 10.0, source="ambiguous"),
            position(3, 1.3, 10.0),
        ]
        timeline = build_resolved_player_timeline(
            global_identity={
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "parameters": {"players_per_team": 7},
                "slots": [slot("slot-A01", rows)],
            },
            identity_assignments={"assignments": [assignment("slot-A01", "p1", 0, 3)]},
            fps=30.0,
        )

        presence = calculate_timeline_presence(timeline)

        player = presence["players"]["p1"]
        self.assertEqual(player["frame_numbers"], [0, 1, 2, 3])
        self.assertEqual(player["ambiguous_presence_frames"], 1)
        self.assertEqual(player["assignment_short_gap_frames"], 1)

    def test_playing_time_bridges_short_gap_between_same_subject_fragments(self) -> None:
        rows = [position(frame, 1.0 + frame * 0.05, 10.0) for frame in range(10)]
        timeline = build_resolved_player_timeline(
            global_identity={
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "parameters": {"players_per_team": 7},
                "slots": [slot("slot-A01", rows)],
            },
            identity_assignments={
                "assignments": [
                    assignment("slot-A01", "p1", 0, 2),
                    assignment("slot-A01", "p1", 7, 9),
                ]
            },
            fps=30.0,
        )

        presence = calculate_timeline_presence(timeline)

        player = presence["players"]["p1"]
        self.assertEqual(player["frame_numbers"], list(range(10)))
        self.assertEqual(player["same_subject_bridge_frames"], 4)

    def test_playing_time_does_not_bridge_gap_owned_by_another_player(self) -> None:
        rows = [position(frame, 1.0 + frame * 0.05, 10.0) for frame in range(10)]
        timeline = build_resolved_player_timeline(
            global_identity={
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "parameters": {"players_per_team": 7},
                "slots": [slot("slot-A01", rows)],
            },
            identity_assignments={
                "assignments": [
                    assignment("slot-A01", "p1", 0, 2, anchor_confidence=0.95),
                    assignment("slot-A01", "p2", 3, 6, anchor_confidence=0.95),
                    assignment("slot-A01", "p1", 7, 9, anchor_confidence=0.95),
                ]
            },
            fps=30.0,
        )

        presence = calculate_timeline_presence(timeline)

        self.assertEqual(presence["players"]["p1"]["frame_numbers"], [0, 1, 2, 7, 8, 9])
        self.assertEqual(presence["players"]["p2"]["frame_numbers"], [3, 4, 5, 6])

    def test_playing_time_marks_well_observed_long_same_subject_gap_as_possible(self) -> None:
        rows = [position(frame, 1.0 + frame * 0.05, 10.0) for frame in range(22)]
        timeline = build_resolved_player_timeline(
            global_identity={
                "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
                "parameters": {"players_per_team": 7},
                "slots": [slot("slot-A01", rows)],
            },
            identity_assignments={
                "assignments": [
                    assignment("slot-A01", "p1", 0, 1),
                    assignment("slot-A01", "p1", 20, 21),
                ]
            },
            fps=1.0,
        )

        presence = calculate_timeline_presence(timeline)

        player = presence["players"]["p1"]
        self.assertEqual(player["frame_numbers"], list(range(22)))
        self.assertEqual(player["same_subject_long_bridge_frames"], 18)

    def test_public_heatmap_uses_unique_exact_detected_rows(self) -> None:
        rows = _exact_player_heatmap_rows(
            {
                "rows": [
                    position(1, 1.0, 2.0),
                    position(1, 1.0, 2.0),
                    position(2, 2.0, 3.0, source="ambiguous"),
                ]
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["frame"], 1)

    def test_legacy_heatmap_does_not_expand_unknown_crop_stint(self) -> None:
        player = {
            "source_stable_slots": [
                {"stable_subject_id": "slot-A01", "stint_id": "A01-C001"}
            ]
        }
        stable_players = {
            "slot-A01": {
                "stints": [{"stint_id": "A01-S01", "start_frame": 0, "end_frame": 10}],
                "trajectory_m": [position(1, 1.0, 2.0)],
            }
        }
        self.assertEqual(_real_player_heatmap_rows(player, stable_players), [])


if __name__ == "__main__":
    unittest.main()
