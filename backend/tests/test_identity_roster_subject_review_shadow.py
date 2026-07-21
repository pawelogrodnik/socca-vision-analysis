from __future__ import annotations

import unittest

from app.services.identity_roster_subject_review_shadow import (
    build_identity_roster_subject_review_shadow,
)


def roster_card(
    subject_id: str = "subject-1",
    *,
    status: str = "unresolved",
    recommended_player_id: str | None = None,
    reason_codes: list[str] | None = None,
) -> dict:
    return {
        "anchor_key": f"anchor-{subject_id}",
        "candidate_subject_id": subject_id,
        "team_label": "A",
        "role": "field_player",
        "start_frame": 0,
        "end_frame": 99,
        "detected_frames": 80,
        "status": status,
        "reason_codes": reason_codes or [],
        "quality_flags": [],
        "recommended_player_id": recommended_player_id,
        "recommended_player_name": "Player One" if recommended_player_id else None,
        "recommendation_confidence": 0.91 if recommended_player_id else None,
        "roster_candidates": [
            {
                "player_id": "p1",
                "player_name": "Player One",
                "team_label": "A",
                "direct_coverage_ratio": 0.9,
            }
        ],
    }


def crop(frame: int) -> dict:
    return {
        "anchor_crop_id": f"crop-{frame}",
        "artifact": f"anchor_crops/subject-1/{frame}.jpg",
        "frame": frame,
        "time_sec": frame / 30,
        "tracklet_id": "track-1",
        "bbox_xyxy": [10.0, 20.0, 40.0, 100.0],
        "selection_eligible": True,
        "selection_score": 0.9,
        "selection_reasons": ["detected"],
    }


def build(cards: list[dict], crop_cards: list[dict]) -> dict[str, dict]:
    return build_identity_roster_subject_review_shadow(
        {
            "algorithm": {"name": "p115"},
            "cards": cards,
        },
        {
            "algorithm": {"name": "p116"},
            "cards": crop_cards,
        },
        generated_at="fixed",
    )


class IdentityRosterSubjectReviewShadowTests(unittest.TestCase):
    def test_ready_card_reviews_whole_subject_not_single_crop(self) -> None:
        documents = build(
            [roster_card(recommended_player_id="p1")],
            [
                {
                    "candidate_subject_id": "subject-1",
                    "status": "ready_for_visual_audit",
                    "anchor_crops": [crop(10), crop(20), crop(30)],
                }
            ],
        )
        card = documents["identity_roster_subject_review_shadow"]["cards"][0]

        self.assertEqual(card["review_status"], "ready_for_operator_review")
        self.assertEqual(card["review_unit"], "candidate_stable_subject")
        self.assertIn("confirm_recommended_player", card["allowed_actions"])
        self.assertNotIn("assign_single_crop", card["allowed_actions"])
        self.assertEqual(card["decision_contract"]["decision_scope"], "entire_candidate_stable_subject")
        self.assertFalse(card["automatic_assignment"])
        self.assertFalse(card["eligible_for_player_stats"])

    def test_conflict_card_blocks_confirmation(self) -> None:
        documents = build(
            [
                roster_card(
                    status="conflict",
                    recommended_player_id="p1",
                    reason_codes=["parallel_roster_candidate_conflict"],
                )
            ],
            [
                {
                    "candidate_subject_id": "subject-1",
                    "status": "ready_for_visual_audit",
                    "anchor_crops": [crop(10), crop(20), crop(30)],
                }
            ],
        )
        card = documents["identity_roster_subject_review_shadow"]["cards"][0]
        report = documents["identity_roster_subject_review_shadow_report"]

        self.assertEqual(card["review_status"], "blocked_conflict")
        self.assertIn("roster_identity_conflict", card["blockers"])
        self.assertNotIn("confirm_recommended_player", card["allowed_actions"])
        self.assertIn("assign_roster_player", card["allowed_actions"])
        self.assertTrue(report["gates"]["conflicts_block_confirmation"])

    def test_insufficient_visual_evidence_is_explicit(self) -> None:
        card = build(
            [roster_card()],
            [
                {
                    "candidate_subject_id": "subject-1",
                    "status": "insufficient_reliable_crops",
                    "anchor_crops": [crop(10), crop(20)],
                }
            ],
        )["identity_roster_subject_review_shadow"]["cards"][0]

        self.assertEqual(card["review_status"], "needs_more_visual_evidence")
        self.assertEqual(card["allowed_actions"], ["mark_unresolved", "open_debug_context"])
        self.assertIn("insufficient_visual_evidence", card["blockers"])

    def test_missing_crop_card_does_not_drop_roster_card(self) -> None:
        card = build([roster_card("subject-without-crops")], [])["identity_roster_subject_review_shadow"]["cards"][0]

        self.assertEqual(card["candidate_subject_id"], "subject-without-crops")
        self.assertEqual(card["review_status"], "no_visual_evidence")
        self.assertEqual(card["visual_evidence"]["selected_crop_count"], 0)

    def test_output_is_deterministic(self) -> None:
        first = build(
            [roster_card(recommended_player_id="p1")],
            [{"candidate_subject_id": "subject-1", "anchor_crops": [crop(10), crop(20), crop(30)]}],
        )
        second = build(
            [roster_card(recommended_player_id="p1")],
            [{"candidate_subject_id": "subject-1", "anchor_crops": [crop(10), crop(20), crop(30)]}],
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
