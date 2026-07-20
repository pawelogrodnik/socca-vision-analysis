from __future__ import annotations

import unittest

from scripts.evaluate_identity_roster_anchor_crops_shadow import evaluate_anchor_crops_shadow


def documents() -> dict:
    return {
        "identity_roster_anchor_crops_shadow": {
            "mode": "shadow_read_only",
            "safety": {
                "automatic_assignments": 0,
                "eligible_for_player_stats": False,
            },
            "cards": [
                {
                    "anchor_crops": [
                        {
                            "anchor_crop_id": "crop-1",
                            "artifact": "anchor_crops/subject/01.jpg",
                            "frame": 10,
                            "selection_eligible": True,
                        }
                    ]
                }
            ],
        },
        "identity_roster_anchor_crops_shadow_report": {
            "summary": {"cards": 1, "selected_crops": 1}
        },
    }


class EvaluateIdentityRosterAnchorCropsShadowTests(unittest.TestCase):
    def test_passes_for_read_only_deterministic_rendered_artifact(self) -> None:
        result = evaluate_anchor_crops_shadow(
            documents(),
            before_hashes={"identity": "same"},
            after_hashes={"identity": "same"},
            deterministic=True,
            rendered_artifacts={"anchor_crops/subject/01.jpg"},
        )

        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(result["gates"].values()))

    def test_fails_when_crop_is_missing_or_production_changes(self) -> None:
        result = evaluate_anchor_crops_shadow(
            documents(),
            before_hashes={"identity": "before"},
            after_hashes={"identity": "after"},
            deterministic=True,
            rendered_artifacts=set(),
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["gates"]["production_artifacts_unchanged"])
        self.assertFalse(result["gates"]["all_selected_crops_rendered"])


if __name__ == "__main__":
    unittest.main()
