from __future__ import annotations

import unittest

import numpy as np
import torch
from torch import nn

from app.services.identity_jersey_number_panel_digitnet import BLANK_INDEX
from app.services.identity_jersey_number_panel_digitnet import PanelDigitNetV1
from app.services.identity_jersey_number_panel_digitnet import decode_digits
from app.services.identity_jersey_number_panel_digitnet import encode_digits
from app.services.identity_jersey_number_panel_digitnet import preprocess_number_panel
from app.services.identity_jersey_number_panel_digitnet_training import evaluate_panel_digitnet
from app.services.identity_jersey_number_panel_digitnet_training import resolve_panel_training_profile


class _UnreadableNumberModel(nn.Module):
    def forward(self, image: torch.Tensor) -> dict[str, torch.Tensor]:
        batch = image.shape[0]
        visual_logits = torch.tensor([[0.0, 0.0, 10.0]]).repeat(batch, 1)
        digit_logits = torch.zeros((batch, 3, 11))
        digit_logits[:, 0, 2] = 10.0
        digit_logits[:, 1, 1] = 10.0
        digit_logits[:, 2, BLANK_INDEX] = 10.0
        return {"visual_logits": visual_logits, "digit_logits": digit_logits}


class PanelDigitNetTest(unittest.TestCase):
    def test_encodes_and_decodes_fixed_digit_positions(self) -> None:
        self.assertEqual(encode_digits("7"), [8, BLANK_INDEX, BLANK_INDEX])
        self.assertEqual(encode_digits("10"), [2, 1, BLANK_INDEX])
        self.assertEqual(encode_digits("92"), [10, 3, BLANK_INDEX])
        self.assertEqual(decode_digits([2, 1, BLANK_INDEX]), "10")
        self.assertIsNone(decode_digits([2, BLANK_INDEX, 1]))

    def test_model_has_four_heads_and_canonical_shapes(self) -> None:
        output = PanelDigitNetV1()(torch.zeros((2, 1, 64, 96)))
        self.assertEqual(tuple(output["visual_logits"].shape), (2, 3))
        self.assertEqual(tuple(output["digit_logits"].shape), (2, 3, 11))

    def test_preprocessing_returns_canonical_shape(self) -> None:
        panel = preprocess_number_panel(np.zeros((13, 17, 3), dtype=np.uint8))
        self.assertIsNotNone(panel)
        assert panel is not None
        self.assertEqual(tuple(panel.shape), (1, 64, 96))
        self.assertIsNone(preprocess_number_panel(None))

    def test_evaluation_does_not_require_digits_for_negative_samples(self) -> None:
        model = PanelDigitNetV1()
        prepared = [
            ({"sample_key": "confirmed", "jersey_number_state": "number_confirmed", "jersey_number": "10"}, torch.zeros((1, 64, 96))),
            ({"sample_key": "negative", "jersey_number_state": "number_absent", "jersey_number": None}, torch.zeros((1, 64, 96))),
        ]
        report = evaluate_panel_digitnet(model, prepared, device="cpu")
        self.assertEqual(len(report["predictions"]), 2)
        self.assertIsNotNone(report["negative_specificity"])

    def test_evaluation_hides_digit_prediction_when_panel_is_unreadable(self) -> None:
        prepared = [
            (
                {"sample_key": "unreadable", "jersey_number_state": "number_unreadable", "jersey_number": None},
                torch.zeros((1, 64, 96)),
            ),
        ]
        report = evaluate_panel_digitnet(_UnreadableNumberModel(), prepared, device="cpu")
        prediction = report["predictions"][0]
        self.assertEqual(prediction["predicted_state"], "number_unreadable")
        self.assertIsNone(prediction["predicted_number"])
        self.assertEqual(prediction["raw_predicted_number"], "10")

    def test_generalization_profile_is_explicit_and_unknown_profile_is_rejected(self) -> None:
        profile = resolve_panel_training_profile("same_match_generalization_v1")
        self.assertTrue(profile["augment_panels"])
        self.assertTrue(profile["balance_digit_classes"])
        with self.assertRaises(ValueError):
            resolve_panel_training_profile("not-a-real-profile")


if __name__ == "__main__":
    unittest.main()
