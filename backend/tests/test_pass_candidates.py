from __future__ import annotations

import unittest
from pathlib import Path

from app.services.pass_candidates import build_pass_candidates_document
from app.services.pass_quality import evaluate_pass_candidates_against_gold, load_pass_goldset


MATCH_PHASE_CONFIG = {
    "periods": [
        {
            "period_id": "first_half",
            "start_time_sec": 0.0,
            "end_time_sec": 2.0,
            "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
            "direction_source": "test",
        },
        {
            "period_id": "second_half",
            "start_time_sec": 2.0,
            "end_time_sec": 4.0,
            "team_attack_directions": {"A": "towards_y_max", "B": "towards_y_min"},
            "direction_source": "test_switch",
        },
    ]
}


def event(
    event_id: str,
    player_id: str,
    team: str,
    start: float,
    end: float,
    status: str = "accepted",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "ball_contact",
        "review_status": status,
        "confidence": 0.8 if status == "accepted" else 0.45,
        "stable_player_id": player_id,
        "stable_subject_id": f"slot-{player_id}",
        "team_label": team,
        "team_id": f"team-{team}",
        "team_name": f"Team {team}",
        "start_frame": int(start * 30),
        "end_frame": int(end * 30),
        "start_time_sec": start,
        "end_time_sec": end,
        "start_position_m": [round(start * 2, 3), 10.0],
        "end_position_m": [round(start * 2 + 0.5, 3), 10.4],
        "source_candidate_id": f"contact-{event_id}",
    }


def possession_frames(rows: list[tuple[int, float, float, str, str | None, str | None]]) -> dict:
    return {
        "frames": [
            {
                "frame": frame,
                "time_sec": round(frame / 30, 3),
                "ball_position_m": [x, y],
                "status": status,
                "stable_player_id": player_id,
                "team_label": team,
            }
            for frame, x, y, status, player_id, team in rows
        ]
    }


class PassCandidatesTests(unittest.TestCase):
    def test_builds_same_team_pass_candidate_from_consecutive_contacts(self) -> None:
        document = build_pass_candidates_document(
            {
                "events": [
                    event("event-0001", "A01", "A", 1.0, 1.1),
                    event("event-0002", "A02", "A", 1.7, 1.8),
                ]
            }
        )

        self.assertEqual(document["summary"]["pass_candidates"], 1)
        self.assertEqual(document["candidates"][0]["pass_type"], "same_team_pass")
        self.assertEqual(document["candidates"][0]["outcome"], "completed_pass")
        self.assertTrue(document["candidates"][0]["completed"])
        self.assertFalse(document["candidates"][0]["failed"])
        self.assertEqual(document["summary"]["pass_attempts"], 1)
        self.assertEqual(document["summary"]["completed_passes"], 1)
        self.assertEqual(document["candidates"][0]["auto_review_status"], "strong_candidate")
        self.assertEqual(document["candidates"][0]["review_status"], "needs_review")
        self.assertEqual(document["candidates"][0]["start_position_m"], [2.5, 10.4])
        self.assertEqual(document["candidates"][0]["end_position_m"], [3.4, 10.0])
        self.assertEqual(document["candidates"][0]["displacement_m"], [0.9, -0.4])
        self.assertEqual(document["summary"]["candidates_with_positions"], 1)
        self.assertEqual(document["summary"]["needs_review_pass_candidates"], 1)
        self.assertEqual(document["summary"]["final_stat_passes"], 0)
        self.assertEqual(document["candidates"][0]["direction"], "unknown")
        self.assertFalse(document["candidates"][0]["final_stat_eligible"])

    def test_classifies_forward_and_progressive_pass_with_match_phase_direction(self) -> None:
        first = event("event-0001", "A01", "A", 1.0, 1.1)
        second = event("event-0002", "A02", "A", 1.7, 1.8)
        first["end_position_m"] = [8.0, 35.0]
        second["start_position_m"] = [10.0, 28.5]

        document = build_pass_candidates_document({"events": [first, second]}, MATCH_PHASE_CONFIG)
        candidate = document["candidates"][0]

        self.assertEqual(candidate["match_phase_period_id"], "first_half")
        self.assertEqual(candidate["attack_direction"], "towards_y_min")
        self.assertEqual(candidate["forward_progress_m"], 6.5)
        self.assertEqual(candidate["direction"], "forward")
        self.assertTrue(candidate["is_progressive"])
        self.assertEqual(document["summary"]["forward_pass_candidates"], 1)
        self.assertEqual(document["summary"]["progressive_pass_candidates"], 1)

    def test_switches_attack_direction_after_second_half_start(self) -> None:
        first = event("event-0001", "A01", "A", 2.1, 2.2)
        second = event("event-0002", "A02", "A", 2.6, 2.7)
        first["end_position_m"] = [8.0, 28.5]
        second["start_position_m"] = [10.0, 35.0]

        document = build_pass_candidates_document({"events": [first, second]}, MATCH_PHASE_CONFIG)
        candidate = document["candidates"][0]

        self.assertEqual(candidate["match_phase_period_id"], "second_half")
        self.assertEqual(candidate["attack_direction"], "towards_y_max")
        self.assertEqual(candidate["forward_progress_m"], 6.5)
        self.assertEqual(candidate["direction"], "forward")

    def test_marks_team_switch_as_turnover_or_interception_candidate(self) -> None:
        document = build_pass_candidates_document(
            {
                "events": [
                    event("event-0001", "A01", "A", 1.0, 1.1),
                    event("event-0002", "B01", "B", 1.7, 1.8, status="uncertain"),
                ]
            }
        )

        self.assertEqual(document["summary"]["turnover_or_interception_candidates"], 1)
        self.assertEqual(document["summary"]["failed_passes"], 1)
        self.assertEqual(document["candidates"][0]["outcome"], "failed_pass")
        self.assertEqual(document["candidates"][0]["auto_review_status"], "uncertain")
        self.assertEqual(document["candidates"][0]["review_status"], "uncertain")

    def test_excludes_non_pass_turnover_when_ball_never_releases(self) -> None:
        first = event("event-0001", "A01", "A", 1.0, 1.1)
        second = event("event-0002", "B01", "B", 1.16, 1.2)
        first["end_position_m"] = [10.0, 10.0]
        first["end_player_position_m"] = [10.1, 10.0]
        second["start_position_m"] = [10.35, 10.1]
        doc = build_pass_candidates_document(
            {"events": [first, second]},
            possession_doc=possession_frames(
                [
                    (33, 10.0, 10.0, "controlled", "A01", "A"),
                    (34, 10.2, 10.0, "contested", None, None),
                    (35, 10.35, 10.1, "controlled", "B01", "B"),
                ]
            ),
        )

        candidate = doc["candidates"][0]
        self.assertEqual(candidate["outcome"], "excluded_non_pass")
        self.assertEqual(candidate["review_status"], "rejected")
        self.assertIn("ball_displacement_too_short", candidate["rejection_reasons"])
        self.assertEqual(doc["summary"]["pass_attempts"], 0)
        self.assertEqual(doc["summary"]["excluded_non_pass_candidates"], 1)

    def test_uses_possession_frames_to_accept_real_release(self) -> None:
        first = event("event-0001", "A01", "A", 1.0, 1.1)
        second = event("event-0002", "A02", "A", 1.8, 1.9)
        first["end_position_m"] = [10.0, 10.0]
        first["end_player_position_m"] = [10.0, 10.1]
        second["start_position_m"] = [14.0, 10.0]
        doc = build_pass_candidates_document(
            {"events": [first, second]},
            possession_doc=possession_frames(
                [
                    (33, 10.0, 10.0, "controlled", "A01", "A"),
                    (42, 12.0, 10.0, "free", None, None),
                    (51, 14.0, 10.0, "controlled", "A02", "A"),
                ]
            ),
        )

        candidate = doc["candidates"][0]
        self.assertEqual(candidate["outcome"], "completed_pass")
        self.assertEqual(candidate["count_for_team_label"], "A")
        self.assertGreater(candidate["trajectory_evidence"]["ball_path_distance_m"], 3.5)
        self.assertEqual(doc["summary"]["team_completed_passes"]["A"], 1)

    def test_goldset_evaluator_reports_match_miss_and_false_positive(self) -> None:
        pass_doc = {
            "candidates": [
                {
                    "candidate_id": "pass-0001",
                    "end_frame": 110,
                    "outcome": "failed_pass",
                    "count_for_team_label": "A",
                },
                {
                    "candidate_id": "pass-0002",
                    "end_frame": 900,
                    "outcome": "completed_pass",
                    "count_for_team_label": "B",
                },
            ]
        }
        gold = {
            "events": [
                {"id": "gold-1", "frame": 107, "team_label": "A", "expected_outcome": "failed_pass"},
                {"id": "gold-2", "frame": 470, "team_label": "B", "expected_outcome": "completed_pass"},
            ]
        }

        report = evaluate_pass_candidates_against_gold(pass_doc, gold, tolerance_frames=45)

        self.assertEqual(report["summary"]["true_positives"], 1)
        self.assertEqual(report["summary"]["missed_passes"], 1)
        self.assertEqual(report["summary"]["false_positives"], 0)

    def test_manual_first_analysis_goldset_loads(self) -> None:
        goldset = load_pass_goldset(Path(__file__).parent / "fixtures" / "pass_goldset_1st_analysis.json")

        self.assertGreaterEqual(len(goldset["events"]), 8)

    def test_skips_same_player_consecutive_contacts(self) -> None:
        document = build_pass_candidates_document(
            {
                "events": [
                    event("event-0001", "A01", "A", 1.0, 1.1),
                    event("event-0002", "A01", "A", 1.7, 1.8),
                ]
            }
        )

        self.assertEqual(document["summary"]["pass_candidates"], 0)
        self.assertEqual(document["summary"]["skipped_reasons"]["same_player_consecutive_contacts"], 1)

    def test_skips_candidates_without_positions(self) -> None:
        first = event("event-0001", "A01", "A", 1.0, 1.1)
        second = event("event-0002", "A02", "A", 1.7, 1.8)
        second["start_position_m"] = None

        document = build_pass_candidates_document({"events": [first, second]})

        self.assertEqual(document["summary"]["pass_candidates"], 0)
        self.assertEqual(document["summary"]["skipped_reasons"]["missing_position"], 1)


if __name__ == "__main__":
    unittest.main()
