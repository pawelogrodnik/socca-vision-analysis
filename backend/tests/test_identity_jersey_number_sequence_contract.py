from __future__ import annotations

import unittest

from app.services.identity_jersey_number_sequence_contract import (
    ALGORITHM_NAME,
    DIGIT_ALPHABET,
    MAX_DIGIT_LENGTH,
    VISUAL_STATES,
    build_sequence_training_eligibility_report,
    normalize_sequence_prediction,
    sequence_contract_metadata,
    validate_digit_string,
    validate_sequence_checkpoint_metadata,
)


class JerseyNumberSequenceContractTests(unittest.TestCase):
    def test_valid_and_invalid_digit_strings(self) -> None:
        self.assertEqual(validate_digit_string("007"), "007")
        self.assertIsNone(validate_digit_string(" "))
        for value in ("1234", "1a", 10):
            with self.assertRaises(ValueError):
                validate_digit_string(value)

    def test_all_visual_states_normalize(self) -> None:
        for state in VISUAL_STATES:
            digits = None if state == "none" else "10"
            self.assertEqual(
                normalize_sequence_prediction({"visual_state": state, "digit_string": digits})[
                    "visual_state"
                ],
                state,
            )
        with self.assertRaises(ValueError):
            normalize_sequence_prediction({"visual_state": "none", "digit_string": "10"})

    def test_candidate_and_roster_inputs_are_forbidden(self) -> None:
        for field in ("candidate_numbers", "roster"):
            with self.assertRaises(ValueError):
                normalize_sequence_prediction({field: ["10"]})

    def test_centroid_metadata_cannot_masquerade_as_sequence_checkpoint(self) -> None:
        metadata = sequence_contract_metadata()
        metadata["prototypes"] = {"10": [0.1]}

        with self.assertRaises(ValueError):
            validate_sequence_checkpoint_metadata(metadata)

    def test_valid_checkpoint_metadata_declares_unconstrained_digits(self) -> None:
        metadata = validate_sequence_checkpoint_metadata(sequence_contract_metadata())

        self.assertEqual(metadata["algorithm"]["name"], ALGORITHM_NAME)
        self.assertEqual(metadata["recognition_contract"]["alphabet"], [*DIGIT_ALPHABET, "<blank>"])
        self.assertEqual(metadata["recognition_contract"]["max_digit_length"], MAX_DIGIT_LENGTH)

    def test_sequence_experiment_stays_deferred_and_never_target_ready(self) -> None:
        metadata = sequence_contract_metadata()
        report = build_sequence_training_eligibility_report(
            {"split_contract": {"production_eligible": True}, "samples": []}
        )

        self.assertEqual(metadata["experiment_status"]["status"], "deferred_diagnostic")
        self.assertFalse(metadata["experiment_status"]["target_ready"])
        self.assertFalse(metadata["experiment_status"]["activation_eligible"])
        self.assertEqual(report["training_gate"]["status"], "deferred_diagnostic")
        self.assertFalse(report["training_gate"]["target_ready"])
        self.assertFalse(report["training_gate"]["activation_eligible"])

    def test_single_match_dataset_is_diagnostic_only(self) -> None:
        report = build_sequence_training_eligibility_report(
            {
                "split_contract": {"production_eligible": False},
                "samples": [
                    {
                        "source_match_key": "match-1",
                        "label_state": "number_confirmed",
                        "number": "10",
                    }
                ],
            }
        )

        gate = report["training_gate"]
        self.assertTrue(gate["mechanically_trainable"])
        self.assertTrue(gate["diagnostic_only"])
        self.assertFalse(gate["calibration_eligible"])
        self.assertFalse(gate["generalization_eligible"])
        self.assertIn("insufficient_independent_source_matches", gate["reason_codes"])


if __name__ == "__main__":
    unittest.main()
