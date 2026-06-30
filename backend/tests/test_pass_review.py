from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.pass_candidates import build_pass_candidates_document
from app.services.pass_review import load_pass_candidates_review, save_pass_candidate_reviews


def event(event_id: str, player_id: str, team: str, start: float, end: float) -> dict:
    return {
        "event_id": event_id,
        "event_type": "ball_contact",
        "review_status": "accepted",
        "confidence": 0.8,
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


class PassReviewTests(unittest.TestCase):
    def test_accepting_same_team_pass_marks_it_final_stat_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            match_path = Path(tmp)
            document = build_pass_candidates_document(
                {
                    "events": [
                        event("event-0001", "A01", "A", 1.0, 1.1),
                        event("event-0002", "A02", "A", 1.7, 1.8),
                    ]
                }
            )
            (match_path / "pass_candidates.json").write_text(json.dumps(document, indent=2), encoding="utf-8")

            saved = save_pass_candidate_reviews(
                match_path,
                [{"candidate_id": "pass-0001", "review_status": "accepted", "notes": "looks real"}],
            )

            candidate = saved["candidates"][0]
            self.assertEqual(candidate["review_status"], "accepted")
            self.assertTrue(candidate["final_stat_eligible"])
            self.assertEqual(saved["summary"]["final_stat_passes"], 1)
            self.assertEqual(saved["summary"]["accepted_pass_candidates"], 1)
            self.assertTrue((match_path / "pass_review_report.json").exists())

    def test_rejected_pass_is_not_final_stat_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            match_path = Path(tmp)
            document = build_pass_candidates_document(
                {
                    "events": [
                        event("event-0001", "A01", "A", 1.0, 1.1),
                        event("event-0002", "A02", "A", 1.7, 1.8),
                    ]
                }
            )
            (match_path / "pass_candidates.json").write_text(json.dumps(document, indent=2), encoding="utf-8")

            saved = save_pass_candidate_reviews(
                match_path,
                [{"candidate_id": "pass-0001", "review_status": "rejected"}],
            )

            self.assertFalse(saved["candidates"][0]["final_stat_eligible"])
            self.assertEqual(saved["summary"]["final_stat_passes"], 0)
            self.assertEqual(saved["summary"]["rejected_pass_candidates"], 1)

    def test_load_migrates_legacy_strong_candidate_to_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            match_path = Path(tmp)
            document = {
                "summary": {},
                "candidates": [
                    {
                        "candidate_id": "pass-0001",
                        "pass_type": "same_team_pass",
                        "review_status": "strong_candidate",
                    }
                ],
            }
            (match_path / "pass_candidates.json").write_text(json.dumps(document, indent=2), encoding="utf-8")

            loaded = load_pass_candidates_review(match_path)

            self.assertEqual(loaded["candidates"][0]["auto_review_status"], "strong_candidate")
            self.assertEqual(loaded["candidates"][0]["review_status"], "needs_review")
            self.assertFalse(loaded["candidates"][0]["final_stat_eligible"])


if __name__ == "__main__":
    unittest.main()
