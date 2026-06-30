from __future__ import annotations

import unittest

from app.services.event_candidates import build_event_candidate_artifacts


def contact_doc() -> dict:
    return {
        "schema_version": "0.1.0",
        "candidates": [
            {
                "candidate_id": "contact-0001",
                "stable_player_id": "A01",
                "team_label": "A",
                "start_frame": 10,
                "end_frame": 12,
                "start_time_sec": 0.33,
                "end_time_sec": 0.4,
                "start_ball_position_m": [5.0, 12.0],
                "end_ball_position_m": [5.4, 12.2],
                "start_player_position_m": [5.1, 12.1],
                "end_player_position_m": [5.5, 12.3],
                "mean_confidence": 0.42,
                "status": "accepted",
                "review_notes": "clear touch",
            },
            {
                "candidate_id": "contact-0002",
                "stable_player_id": "B01",
                "team_label": "B",
                "mean_confidence": 0.38,
                "status": "uncertain",
            },
            {
                "candidate_id": "contact-0003",
                "stable_player_id": "A02",
                "team_label": "A",
                "mean_confidence": 0.2,
                "status": "rejected",
            },
        ],
    }


class EventCandidatesTests(unittest.TestCase):
    def test_build_event_candidates_excludes_rejected_contacts(self) -> None:
        artifacts = build_event_candidate_artifacts(contact_doc())
        document = artifacts["event_candidates"]

        self.assertEqual(document["summary"]["source_contact_candidates"], 3)
        self.assertEqual(document["summary"]["events_total"], 2)
        self.assertEqual(document["summary"]["accepted_events"], 1)
        self.assertEqual(document["summary"]["uncertain_events"], 1)
        self.assertEqual(document["summary"]["rejected_contacts"], 1)
        self.assertEqual(document["events"][0]["event_type"], "ball_contact")
        self.assertEqual(document["events"][0]["start_position_m"], [5.0, 12.0])
        self.assertEqual(document["events"][0]["end_position_m"], [5.4, 12.2])
        self.assertTrue(document["events"][0]["final_stat_eligible"])
        self.assertFalse(document["events"][1]["final_stat_eligible"])

    def test_event_review_report_warns_when_review_is_incomplete(self) -> None:
        artifacts = build_event_candidate_artifacts(contact_doc())
        report = artifacts["event_review_report"]

        self.assertEqual(report["summary"]["review_required_events"], 1)
        self.assertTrue(any("need review" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
