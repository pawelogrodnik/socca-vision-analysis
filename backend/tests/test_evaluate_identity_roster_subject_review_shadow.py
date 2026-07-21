from __future__ import annotations

import unittest

from backend.scripts.evaluate_identity_roster_subject_review_shadow import (
    evaluate_identity_roster_subject_review_shadow,
    materialize_visual_evidence,
)
from pathlib import Path
import tempfile


def documents() -> dict[str, dict]:
    return {
        "identity_roster_subject_review_shadow": {
            "mode": "shadow_read_only",
            "safety": {
                "automatic_assignments": 0,
                "eligible_for_player_stats": False,
            },
            "cards": [
                {
                    "review_card_key": "card-1",
                    "review_unit": "candidate_stable_subject",
                    "review_status": "ready_for_operator_review",
                    "allowed_actions": ["assign_roster_player"],
                },
                {
                    "review_card_key": "card-2",
                    "review_unit": "candidate_stable_subject",
                    "review_status": "blocked_conflict",
                    "allowed_actions": ["mark_unresolved"],
                },
            ],
        },
        "identity_roster_subject_review_shadow_report": {
            "summary": {
                "cards": 2,
                "ready_for_operator_review": 1,
                "blocked_conflicts": 1,
                "selected_crops": 3,
            }
        },
    }


class EvaluateIdentityRosterSubjectReviewShadowTests(unittest.TestCase):
    def test_passes_when_shadow_contract_is_read_only(self) -> None:
        evaluation = evaluate_identity_roster_subject_review_shadow(
            documents(),
            before_hashes={"global_identity.json": "abc"},
            after_hashes={"global_identity.json": "abc"},
            deterministic=True,
        )

        self.assertEqual(evaluation["status"], "passed")
        self.assertTrue(evaluation["gates"]["whole_subject_review_unit"])
        self.assertTrue(evaluation["gates"]["production_artifacts_unchanged"])

    def test_fails_when_conflict_can_be_confirmed(self) -> None:
        docs = documents()
        docs["identity_roster_subject_review_shadow"]["cards"][1]["allowed_actions"] = [
            "confirm_recommended_player"
        ]
        evaluation = evaluate_identity_roster_subject_review_shadow(
            docs,
            before_hashes={"global_identity.json": "abc"},
            after_hashes={"global_identity.json": "abc"},
            deterministic=True,
        )

        self.assertEqual(evaluation["status"], "failed")
        self.assertFalse(evaluation["gates"]["conflicts_block_confirmation"])

    def test_fails_when_production_hash_changes(self) -> None:
        evaluation = evaluate_identity_roster_subject_review_shadow(
            documents(),
            before_hashes={"global_identity.json": "abc"},
            after_hashes={"global_identity.json": "def"},
            deterministic=True,
        )

        self.assertEqual(evaluation["status"], "failed")
        self.assertFalse(evaluation["gates"]["production_artifacts_unchanged"])

    def test_materializes_visual_evidence_for_self_contained_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            crop = source / "anchor_crops" / "subject-1" / "10.jpg"
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"image")
            output.mkdir()
            artifact = {
                "cards": [
                    {
                        "visual_evidence": {
                            "anchor_crops": [{"artifact": "anchor_crops/subject-1/10.jpg"}]
                        }
                    }
                ]
            }

            materialized = materialize_visual_evidence(source, output, artifact)

            self.assertEqual(materialized, {"anchor_crops/subject-1/10.jpg"})
            self.assertEqual((output / "anchor_crops/subject-1/10.jpg").read_bytes(), b"image")


if __name__ == "__main__":
    unittest.main()
