from __future__ import annotations

import unittest

from app.services.identity_roster_anchor_shadow import build_identity_roster_anchor_shadow


def subject(
    subject_id: str,
    *,
    team: str = "A",
    production_subject: str = "slot-A01",
    start: int = 0,
    end: int = 99,
) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "candidate_player_id": "A01",
        "team_label": team,
        "role": "field_player",
        "production_subject_ids": [production_subject],
        "start_frame": start,
        "end_frame": end,
        "detected_frames": end - start + 1,
        "quality_flags": [],
    }


def assignment(
    player_id: str,
    *,
    stable_subject: str = "slot-A01",
    start: int = 0,
    end: int = 99,
) -> dict:
    return {
        "stable_subject_id": stable_subject,
        "status": "assigned",
        "team_label": "A",
        "player_id": player_id,
        "player_name": player_id,
        "stint_id": f"stint-{player_id}-{start}",
        "start_frame": start,
        "end_frame": end,
        "anchor_confidence": 0.9,
        "anchor_artifacts": [f"{player_id}.jpg"],
    }


MATCH = {
    "teams": [
        {
            "id": "team-a",
            "name": "A",
            "players": [
                {"id": "p1", "name": "Player One", "role": "player"},
                {"id": "p2", "name": "Player Two", "role": "player"},
            ],
        },
        {"id": "team-b", "name": "B", "players": [{"id": "b1", "name": "Blue"}]},
    ]
}


def build(subjects: list[dict], assignments: list[dict], fusion: dict | None = None) -> dict:
    return build_identity_roster_anchor_shadow(
        {"algorithm": {"name": "candidate"}, "subjects": subjects},
        {"schema_version": "test", "assignments": assignments},
        MATCH,
        reid_fusion_doc=fusion,
        generated_at="fixed",
    )


class IdentityRosterAnchorShadowTests(unittest.TestCase):
    def test_full_manual_coverage_is_confirmed_but_never_automatic(self) -> None:
        documents = build([subject("s1")], [assignment("p1")])
        card = documents["identity_roster_anchor_shadow"]["cards"][0]

        self.assertEqual(card["status"], "confirmed_manual_anchor")
        self.assertEqual(card["recommended_player_id"], "p1")
        self.assertFalse(card["automatic_assignment"])
        self.assertFalse(card["eligible_for_player_stats"])
        self.assertEqual(documents["identity_roster_anchor_shadow"]["safety"]["automatic_assignments"], 0)

    def test_partial_manual_coverage_requires_review(self) -> None:
        card = build([subject("s1")], [assignment("p1", end=39)])["identity_roster_anchor_shadow"]["cards"][0]

        self.assertEqual(card["status"], "suggested_review")
        self.assertAlmostEqual(card["roster_candidates"][0]["direct_coverage_ratio"], 0.4)

    def test_multiple_manual_players_block_recommendation(self) -> None:
        card = build(
            [subject("s1")],
            [assignment("p1", end=49), assignment("p2", start=50)],
        )["identity_roster_anchor_shadow"]["cards"][0]

        self.assertEqual(card["status"], "conflict")
        self.assertIsNone(card["recommended_player_id"])

    def test_reid_path_can_only_create_review_suggestion(self) -> None:
        fusion = {
            "algorithm": {"name": "p114"},
            "proposals": [
                {
                    "proposal_key": "edge-1",
                    "source_candidate_subject_id": "s1",
                    "target_candidate_subject_id": "s2",
                    "strict_gate_passed": True,
                    "hard_constraint_reasons": [],
                    "fused_cost": 0.1,
                }
            ],
        }
        cards = build(
            [subject("s1", end=49), subject("s2", production_subject="slot-A02", start=50)],
            [assignment("p1", end=49)],
            fusion,
        )["identity_roster_anchor_shadow"]["cards"]
        by_id = {card["candidate_subject_id"]: card for card in cards}

        self.assertEqual(by_id["s2"]["status"], "suggested_review")
        self.assertEqual(by_id["s2"]["recommended_player_id"], "p1")
        self.assertIn("p114_ranking_only_suggestion", by_id["s2"]["reason_codes"])
        self.assertFalse(by_id["s2"]["automatic_assignment"])

    def test_hard_constraint_blocks_reid_propagation(self) -> None:
        fusion = {
            "proposals": [
                {
                    "proposal_key": "edge-1",
                    "source_candidate_subject_id": "s1",
                    "target_candidate_subject_id": "s2",
                    "strict_gate_passed": False,
                    "hard_constraint_reasons": ["parallel_temporal_overlap"],
                    "fused_cost": 0.0,
                }
            ]
        }
        cards = build(
            [subject("s1", end=49), subject("s2", production_subject="slot-A02", start=50)],
            [assignment("p1", end=49)],
            fusion,
        )["identity_roster_anchor_shadow"]["cards"]

        self.assertEqual(cards[1]["status"], "unresolved")

    def test_parallel_same_player_recommendations_become_conflicts(self) -> None:
        cards = build(
            [subject("s1", end=99), subject("s2", production_subject="slot-A02", start=20, end=80)],
            [assignment("p1"), assignment("p1", stable_subject="slot-A02", start=20, end=80)],
        )["identity_roster_anchor_shadow"]["cards"]

        self.assertTrue(all(card["status"] == "conflict" for card in cards))
        self.assertTrue(all(card["recommended_player_id"] is None for card in cards))

    def test_unknown_or_opponent_roster_is_not_ranked(self) -> None:
        card = build([subject("s1", team="B", production_subject="slot-B01")], [assignment("p1")])[
            "identity_roster_anchor_shadow"
        ]["cards"][0]

        self.assertEqual([row["player_id"] for row in card["roster_candidates"]], ["b1"])
        self.assertEqual(card["status"], "unresolved")

    def test_same_input_is_deterministic(self) -> None:
        first = build([subject("s1")], [assignment("p1")])
        second = build([subject("s1")], [assignment("p1")])
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
