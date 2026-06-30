from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from app.services.change_candidates import (
    build_change_candidates_document,
    load_change_candidates_review,
    save_change_candidate_reviews,
    write_change_candidate_artifacts,
)


def player(slot: str, team: str, start: float, end: float, color: str = "#ffffff") -> dict:
    return {
        "slot_id": slot,
        "stable_subject_id": f"slot-{slot}",
        "stable_player_id": slot,
        "team_label": team,
        "team_id": f"team-{team}",
        "team_name": f"Team {team}",
        "start_time_sec": start,
        "end_time_sec": end,
        "duration_sec": end - start,
        "confidence": "medium",
        "confidence_score": 0.7,
        "team_confidence": 0.9,
        "jersey_color_hex": color,
        "movement_stats": {
            "detected_time_sec": 120.0,
            "missing_time_sec": 0.0,
        },
    }


def stable_doc(players: list[dict]) -> dict:
    return {
        "schema_version": "0.1.0",
        "summary": {"target_active_players": 14},
        "players": players,
    }


class ChangeCandidatesTests(unittest.TestCase):
    def test_builds_change_candidate_when_new_team_slot_starts_after_prior_slot_ends(self) -> None:
        starters = [player(f"A{idx:02d}", "A", 0.0, 1200.0) for idx in range(1, 8)]
        starters[0]["end_time_sec"] = 420.0
        incoming = player("A08", "A", 780.0, 1200.0)
        document = build_change_candidates_document(stable_doc([*starters, incoming]))

        self.assertEqual(document["summary"]["change_candidates"], 1)
        candidate = document["candidates"][0]
        self.assertEqual(candidate["candidate_id"], "change-slot-a08")
        self.assertEqual(candidate["team_label"], "A")
        self.assertEqual(candidate["out_stable_player_id"], "A01")
        self.assertEqual(candidate["in_stable_player_id"], "A08")
        self.assertEqual(candidate["review_status"], "needs_review")
        self.assertGreaterEqual(len(candidate["reid_candidates"]), 1)

    def test_does_not_mark_initial_seven_slots_as_changes(self) -> None:
        document = build_change_candidates_document(
            stable_doc([player(f"A{idx:02d}", "A", 0.0, 1200.0) for idx in range(1, 8)])
        )

        self.assertEqual(document["summary"]["change_candidates"], 0)
        self.assertEqual(document["skipped_reasons"]["initial_or_warmup_slot"], 7)

    def test_persists_manual_review(self) -> None:
        starters = [player(f"A{idx:02d}", "A", 0.0, 1200.0) for idx in range(1, 8)]
        starters[0]["end_time_sec"] = 420.0
        incoming = player("A08", "A", 780.0, 1200.0)

        with tempfile.TemporaryDirectory() as tmp:
            match_path = Path(tmp)
            (match_path / "stable_players.json").write_text(
                json.dumps(stable_doc([*starters, incoming])),
                encoding="utf-8",
            )
            write_change_candidate_artifacts(match_path)
            saved = save_change_candidate_reviews(
                match_path,
                [
                    {
                        "candidate_id": "change-slot-a08",
                        "review_status": "confirmed",
                        "linked_existing_stable_subject_id": "slot-A01",
                        "notes": "A01 returned.",
                    }
                ],
            )
            loaded = load_change_candidates_review(match_path)

        self.assertEqual(saved["summary"]["confirmed_candidates"], 1)
        self.assertEqual(loaded["candidates"][0]["review_status"], "confirmed")
        self.assertEqual(loaded["candidates"][0]["linked_existing_stable_subject_id"], "slot-A01")


if __name__ == "__main__":
    unittest.main()
