from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_identity_roster_subject_promotion import (
    evaluate_promotion_plan,
    production_hashes,
)


def ready_plan() -> dict:
    return {
        "status": "ready_for_controlled_apply",
        "audit": {
            "team_cards": 2,
            "reviewed_cards": 2,
            "pending_cards": 0,
            "decisions_fresh": True,
            "recommendation_metrics": {"precision": 1.0},
        },
        "summary": {
            "resolved_subjects": 2,
            "unresolved_subjects": 0,
            "hard_conflicts": 0,
        },
        "canonical_coverage": [
            {"player_id": "p1", "frame_records": [{"frame": 10}]},
        ],
        "errors": [],
        "safety": {"requires_explicit_apply_step": True},
    }


class IdentityRosterSubjectPromotionEvaluationTests(unittest.TestCase):
    def test_passes_only_when_all_safety_gates_hold(self) -> None:
        hashes = {"global_identity.json": "same"}

        evaluation = evaluate_promotion_plan(
            ready_plan(),
            deterministic=True,
            before_hashes=hashes,
            after_hashes=hashes,
        )

        self.assertEqual(evaluation["status"], "passed")
        self.assertTrue(all(evaluation["gates"].values()))

    def test_fails_when_a_production_artifact_changes(self) -> None:
        evaluation = evaluate_promotion_plan(
            ready_plan(),
            deterministic=True,
            before_hashes={"global_identity.json": "before"},
            after_hashes={"global_identity.json": "after"},
        )

        self.assertEqual(evaluation["status"], "failed")
        self.assertFalse(evaluation["gates"]["production_artifacts_unchanged"])

    def test_fails_for_incomplete_operator_audit(self) -> None:
        plan = ready_plan()
        plan["audit"]["pending_cards"] = 1

        evaluation = evaluate_promotion_plan(
            plan,
            deterministic=True,
            before_hashes={},
            after_hashes={},
        )

        self.assertEqual(evaluation["status"], "failed")
        self.assertFalse(evaluation["gates"]["operator_audit_complete"])

    def test_production_hashes_include_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            match_dir = Path(temporary_directory)
            (match_dir / "global_identity.json").write_text("{}", encoding="utf-8")

            hashes = production_hashes(match_dir)

        self.assertIsNotNone(hashes["global_identity.json"])
        self.assertIsNone(hashes["stable_players.json"])


if __name__ == "__main__":
    unittest.main()
