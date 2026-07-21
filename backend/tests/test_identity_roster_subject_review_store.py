from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_roster_subject_review_store import (
    REVIEW_ARTIFACT_FILENAME,
    REVIEW_DECISIONS_FILENAME,
    load_identity_roster_subject_review,
    save_identity_roster_subject_review,
)


def artifact() -> dict:
    return {
        "schema_version": "0.1.0",
        "generated_at": "ignored-for-digest",
        "mode": "shadow_read_only",
        "summary": {"cards": 2},
        "cards": [
            {
                "review_card_key": "card-1",
                "candidate_subject_id": "subject-1",
                "team_label": "A",
                "review_status": "ready_for_operator_review",
                "recommended_player": {"player_id": "p1", "player_name": "One"},
                "roster_candidates": [{"player_id": "p1"}, {"player_id": "p2"}],
                "allowed_actions": [
                    "confirm_recommended_player",
                    "assign_roster_player",
                    "mark_unresolved",
                    "open_debug_context",
                ],
            },
            {
                "review_card_key": "card-2",
                "candidate_subject_id": "subject-2",
                "team_label": "A",
                "review_status": "blocked_conflict",
                "recommended_player": {"player_id": "p2"},
                "roster_candidates": [{"player_id": "p2"}],
                "allowed_actions": ["mark_unresolved", "open_debug_context"],
            },
        ],
    }


def match_doc() -> dict:
    return {
        "teams": [
            {
                "id": "team-a",
                "name": "Alpha",
                "players": [
                    {"id": "p1", "name": "One"},
                    {"id": "p2", "name": "Two"},
                    {"id": "p9", "name": "Nine"},
                ],
            },
            {
                "id": "team-b",
                "name": "Beta",
                "players": [{"id": "b1", "name": "Opponent"}],
            },
        ]
    }


class IdentityRosterSubjectReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        (self.path / REVIEW_ARTIFACT_FILENAME).write_text(json.dumps(artifact()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_saves_whole_subject_decision_without_touching_production_artifacts(self) -> None:
        production = self.path / "player_identity_assignments.json"
        production.write_text('{"keep": true}', encoding="utf-8")

        state = save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "assign_roster_player", "player_id": "p2"}],
            updated_at="fixed",
        )

        self.assertEqual(state["summary"]["reviewed_cards"], 1)
        self.assertEqual(state["cards"][0]["operator_decision"]["player_id"], "p2")
        self.assertEqual(production.read_text(encoding="utf-8"), '{"keep": true}')
        self.assertTrue((self.path / REVIEW_DECISIONS_FILENAME).exists())
        self.assertFalse(state["safety"]["writes_player_identity_assignments"])

    def test_blocked_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "blocked"):
            save_identity_roster_subject_review(
                self.path,
                [{"review_card_key": "card-2", "decision": "confirm_recommended_player"}],
            )

    def test_conflict_allows_explicit_roster_assignment_for_legacy_contract(self) -> None:
        state = save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-2", "decision": "assign_roster_player", "player_id": "p2"}],
            updated_at="fixed",
        )

        card = state["cards"][1]
        self.assertIn("assign_roster_player", card["allowed_actions"])
        self.assertNotIn("confirm_recommended_player", card["allowed_actions"])
        self.assertEqual(card["operator_decision"]["player_id"], "p2")

    def test_assignment_requires_card_roster_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "operator roster options"):
            save_identity_roster_subject_review(
                self.path,
                [{"review_card_key": "card-1", "decision": "assign_roster_player", "player_id": "p9"}],
            )

    def test_full_same_team_roster_is_available_without_expanding_ranked_candidates(self) -> None:
        state = load_identity_roster_subject_review(self.path, match_doc=match_doc())

        card = state["cards"][0]
        self.assertEqual([row["player_id"] for row in card["roster_candidates"]], ["p1", "p2"])
        self.assertEqual(
            [row["player_id"] for row in card["operator_roster_options"]],
            ["p9", "p1", "p2"],
        )
        self.assertEqual(card["decision_contract"]["decision_schema"]["player_id"], ["p9", "p1", "p2"])

    def test_explicit_assignment_accepts_full_same_team_roster(self) -> None:
        state = save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "assign_roster_player", "player_id": "p9"}],
            match_doc=match_doc(),
            updated_at="fixed",
        )

        self.assertEqual(state["cards"][0]["operator_decision"]["player_id"], "p9")

    def test_explicit_assignment_rejects_other_team_player(self) -> None:
        with self.assertRaisesRegex(ValueError, "operator roster options"):
            save_identity_roster_subject_review(
                self.path,
                [{"review_card_key": "card-1", "decision": "assign_roster_player", "player_id": "b1"}],
                match_doc=match_doc(),
            )

    def test_changed_contract_marks_previous_decisions_stale(self) -> None:
        save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "mark_unresolved"}],
            updated_at="fixed",
        )
        changed = artifact()
        changed["cards"][0]["allowed_actions"] = ["mark_unresolved"]
        (self.path / REVIEW_ARTIFACT_FILENAME).write_text(json.dumps(changed), encoding="utf-8")

        state = load_identity_roster_subject_review(self.path)

        self.assertFalse(state["decisions_fresh"])
        self.assertEqual(state["summary"]["reviewed_cards"], 0)
        self.assertEqual(state["summary"]["stale_decisions"], 1)

    def test_clear_decision_is_idempotent(self) -> None:
        save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "mark_unresolved"}],
            updated_at="first",
        )
        state = save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "clear_decision"}],
            updated_at="second",
        )

        self.assertEqual(state["summary"]["reviewed_cards"], 0)
        self.assertEqual(state["summary"]["pending_cards"], 2)


if __name__ == "__main__":
    unittest.main()
