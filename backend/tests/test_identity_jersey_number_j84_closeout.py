from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.identity_jersey_number_j84_closeout import build_j84_closeout_report


class J84CloseoutTests(unittest.TestCase):
    def test_failed_r3_closes_the_cycle_without_identity_mutation(self) -> None:
        dataset = {
            "samples": [
                {"sample_key": "train", "visibility_episode_id": "episode-train"},
                {"sample_key": "holdout", "visibility_episode_id": "episode-heldout"},
            ]
        }
        selection = {"sample_keys": ["train", "holdout"], "selection_digest": "approved"}
        r2 = {
            "evaluation": {
                "readable_recall": 1.0,
                "negative_specificity": 1.0,
                "exact_sequence_accuracy": 1.0,
            }
        }
        r3 = {
            "split": {
                "train_sample_count": 1,
                "heldout_sample_count": 1,
                "heldout_episode_ids": ["episode-heldout"],
            },
            "heldout_evaluation": {
                "crop_exact_sequence_accuracy": 0.0,
                "episode_exact_sequence_accuracy": 0.0,
                "episode_precision": 0.0,
                "episode_recall": 0.0,
                "plain_shirt_false_confirmed_reads": 0,
                "real10_episode_result": {
                    "target_numbers": ["10"],
                    "predicted_numbers": ["8"],
                    "exact_sequence": False,
                },
                "predictions": [
                    {
                        "sample_key": "holdout",
                        "target_state": "number_confirmed",
                        "target_number": "10",
                        "predicted_state": "number_confirmed",
                        "predicted_number": "8",
                        "raw_predicted_number": "8",
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "panel_digitnet_r3.pt"
            checkpoint.write_bytes(b"diagnostic checkpoint")
            report = build_j84_closeout_report(dataset, selection, r2, r3, checkpoint_path=checkpoint)

        self.assertEqual("passed", report["r2"]["status"])
        self.assertEqual("failed", report["r3"]["status"])
        self.assertFalse(report["r3"]["episode_leakage_detected"])
        self.assertEqual(1, len(report["r3"]["incorrect_confirmed_predictions"]))
        self.assertEqual("DIAGNOSTIC_COMPLETE_NOT_ELIGIBLE", report["final_decision"])
        self.assertEqual(0, report["identity_mutation_confirmation"]["automatic_assignments"])
        self.assertEqual("FROZEN_UNTIL_NEW_INDEPENDENT_CAPTURE_DOMAIN", report["freeze"]["status"])


if __name__ == "__main__":
    unittest.main()
