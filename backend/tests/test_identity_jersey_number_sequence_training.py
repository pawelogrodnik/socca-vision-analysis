from __future__ import annotations

import unittest
from typing import Any

import torch

from app.services.identity_jersey_number_sequence_training import (
    MAX_EPOCHS,
    train_jersey_number_sequence,
)


class JerseyNumberSequenceTrainingTests(unittest.TestCase):
    def test_training_uses_train_split_only(self) -> None:
        result = train_jersey_number_sequence(
            _manifest(), image_tensors={"train": torch.zeros(1, 32, 96)}, device="cpu"
        )

        report = result["report"]
        self.assertEqual(report["train_samples"], 1)
        self.assertEqual(report["usable_train_samples"], 1)
        self.assertEqual(report["optimization_steps"], 1)

    def test_single_match_gate_stays_diagnostic_only(self) -> None:
        result = train_jersey_number_sequence(
            _manifest(), image_tensors={"train": torch.zeros(1, 32, 96)}, device="cpu"
        )

        gate = result["report"]["training_gate"]
        self.assertEqual(result["report"]["training_status"], "diagnostic_training_only")
        self.assertFalse(gate["calibration_eligible"])
        self.assertFalse(gate["generalization_eligible"])

    def test_report_is_deterministic_and_unconstrained(self) -> None:
        images = {"train": torch.zeros(1, 32, 96)}
        first = train_jersey_number_sequence(_manifest(), image_tensors=images, seed=7, device="cpu")
        second = train_jersey_number_sequence(_manifest(), image_tensors=images, seed=7, device="cpu")

        self.assertEqual(first["report"]["dataset_digest"], second["report"]["dataset_digest"])
        self.assertEqual(first["report"]["model_digest"], second["report"]["model_digest"])
        self.assertNotIn("candidate_numbers", first["report"])
        self.assertNotIn("roster", first["checkpoint"]["metadata"])
        self.assertEqual(
            first["checkpoint"]["metadata"]["training"]["train_dataset_digest"],
            first["report"]["dataset_digest"],
        )
        self.assertEqual(
            first["checkpoint"]["metadata"]["training"]["train_split_digest"],
            first["report"]["split_digest"],
        )

    def test_zero_usable_readable_samples_prevents_publication(self) -> None:
        with self.assertRaisesRegex(ValueError, "checkpoint publication refused"):
            train_jersey_number_sequence(_manifest(), image_tensors={}, device="cpu")

    def test_partial_visible_digits_train_safely(self) -> None:
        manifest = _manifest()
        train = manifest["samples"][0]
        train["digit_visibility"] = "partial"
        train["visible_digits"] = "1"

        result = train_jersey_number_sequence(
            manifest, image_tensors={"train": torch.zeros(1, 32, 96)}, device="cpu"
        )

        self.assertEqual(result["report"]["optimization_steps"], 1)

    def test_epoch_cap_and_blank_regularizer_telemetry(self) -> None:
        with self.assertRaises(ValueError):
            train_jersey_number_sequence(
                _manifest(), image_tensors={"train": torch.zeros(1, 32, 96)}, epochs=MAX_EPOCHS + 1
            )
        result = train_jersey_number_sequence(
            _manifest(), image_tensors={"train": torch.zeros(1, 32, 96)}, device="cpu"
        )
        self.assertIsNotNone(result["report"]["training_telemetry"]["epochs"][0]["blank_regularizer_loss"])

    def test_train_only_filtering_has_no_sample_truncation(self) -> None:
        samples = [
            {
                "sample_key": f"train-{index}",
                "split": "train",
                "source_match_key": "match-1",
                "label_state": "number_confirmed",
                "number": "1",
            }
            for index in range(129)
        ]
        samples.append(
            {
                "sample_key": "heldout-invalid",
                "split": "heldout",
                "source_match_key": "match-2",
                "label_state": "number_confirmed",
                "number": "9999",
            }
        )
        tensors = {row["sample_key"]: torch.empty(0) for row in samples if row["split"] == "train"}
        tensors["train-0"] = torch.zeros(1, 32, 96)

        result = train_jersey_number_sequence(
            {"split_contract": {"production_eligible": False}, "samples": samples},
            image_tensors=tensors,
            device="cpu",
        )

        self.assertEqual(result["report"]["train_samples"], 129)
        self.assertEqual(result["report"]["usable_train_samples"], 1)


def _manifest() -> dict[str, Any]:
    return {
        "split_contract": {"production_eligible": False},
        "samples": [
            {
                "sample_key": "train",
                "split": "train",
                "source_match_key": "match-1",
                "label_state": "number_confirmed",
                "number": "10",
            },
            {
                "sample_key": "validation",
                "split": "validation",
                "source_match_key": "match-2",
                "label_state": "number_confirmed",
                "number": "9999",
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
