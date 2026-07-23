from __future__ import annotations

import unittest

from app.services.identity_jersey_number_learned import (
    train_identity_jersey_number_learned_baseline,
)


class IdentityJerseyNumberLearnedTests(unittest.TestCase):
    def test_shadow_baseline_stays_non_production_with_eligible_dataset(self) -> None:
        result = train_identity_jersey_number_learned_baseline(
            {
                "dataset_version": "test",
                "split_contract": {"production_eligible": True},
                "samples": [
                    {
                        "sample_key": f"sample-{index}",
                        "split": "train",
                        "source_match_key": f"match-{index}",
                        "label_state": "number_confirmed",
                        "number": str(index),
                        "artifact_root": "/unavailable",
                        "artifact": "crop.jpg",
                    }
                    for index in range(1, 4)
                ],
            },
            generated_at="fixed",
        )

        self.assertFalse(result["production_gate"]["eligible"])
        self.assertIn(
            "shadow_baseline_not_production_validated",
            result["production_gate"]["reason_codes"],
        )
        self.assertTrue(result["algorithm"]["capabilities"]["diagnostic_only"])
        self.assertFalse(
            result["algorithm"]["capabilities"]["production_activation_eligible"]
        )


if __name__ == "__main__":
    unittest.main()
