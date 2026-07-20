from __future__ import annotations

import unittest

from scripts.evaluate_identity_roster_anchor_shadow import evaluate_roster_anchor_shadow


class EvaluateIdentityRosterAnchorShadowTests(unittest.TestCase):
    def test_passes_only_for_read_only_shadow_artifact(self) -> None:
        documents = {
            "identity_roster_anchor_shadow": {
                "mode": "shadow_read_only",
                "safety": {
                    "automatic_assignments": 0,
                    "eligible_for_player_stats": False,
                    "reid_is_ranking_only": True,
                },
                "cards": [
                    {
                        "anchor_key": "key-1",
                        "automatic_assignment": False,
                        "eligible_for_player_stats": False,
                        "reason_codes": [],
                    }
                ],
            },
            "identity_roster_anchor_shadow_report": {"summary": {"cards": 1}},
        }

        result = evaluate_roster_anchor_shadow(
            documents,
            before_hashes={"identity": "same"},
            after_hashes={"identity": "same"},
        )

        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(result["gates"].values()))

    def test_detects_production_artifact_change(self) -> None:
        documents = {
            "identity_roster_anchor_shadow": {
                "mode": "shadow_read_only",
                "safety": {
                    "automatic_assignments": 0,
                    "eligible_for_player_stats": False,
                    "reid_is_ranking_only": True,
                },
                "cards": [],
            },
            "identity_roster_anchor_shadow_report": {"summary": {}},
        }

        result = evaluate_roster_anchor_shadow(
            documents,
            before_hashes={"identity": "before"},
            after_hashes={"identity": "after"},
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["gates"]["production_artifacts_unchanged"])


if __name__ == "__main__":
    unittest.main()
