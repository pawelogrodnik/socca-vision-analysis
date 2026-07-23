from __future__ import annotations

import unittest
from typing import Any, cast

import numpy as np
import torch

from app.services.identity_jersey_number_sequence import (
    BLANK_INDEX,
    JerseyNumberSequenceCRNN,
    decode_ctc_greedy,
    load_sequence_checkpoint,
    predict_jersey_number_sequence,
    preprocess_jersey_number_sequence,
    sequence_architecture_metadata,
    sequence_checkpoint_digest,
    sequence_preprocessing_metadata,
    sequence_visual_state_mapping,
)
from app.services.identity_jersey_number_sequence_contract import sequence_contract_metadata


class JerseyNumberSequenceTests(unittest.TestCase):
    def test_preprocessing_shape_and_determinism(self) -> None:
        image = np.arange(20 * 40 * 3, dtype=np.uint8).reshape(20, 40, 3)
        first = preprocess_jersey_number_sequence(image)
        second = preprocess_jersey_number_sequence(image)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertEqual(first.shape, (1, 32, 96))
        self.assertTrue(torch.equal(first, second))

    def test_ctc_decoder_collapses_blanks_and_rejects_overlength(self) -> None:
        logits = torch.full((7, 11), -1.0)
        logits[range(7), [1, 1, BLANK_INDEX, 2, 2, BLANK_INDEX, 3]] = 1.0
        self.assertEqual(decode_ctc_greedy(logits), "123")
        self.assertIsNone(decode_ctc_greedy(torch.eye(11)[[BLANK_INDEX, BLANK_INDEX]]))
        self.assertIsNone(decode_ctc_greedy(torch.eye(11)[[1, 2, 3, 4]]))

    def test_model_output_shapes(self) -> None:
        sequence_logits, visual_logits = JerseyNumberSequenceCRNN()(torch.zeros(2, 1, 32, 96))

        self.assertEqual(sequence_logits.shape, (2, 24, 11))
        self.assertEqual(visual_logits.shape, (2, 5))

    def test_cpu_inference_and_corrupt_crop_abstention(self) -> None:
        model = JerseyNumberSequenceCRNN().cpu().eval()

        prediction = predict_jersey_number_sequence(model, np.zeros((32, 96, 3), dtype=np.uint8))
        abstention = predict_jersey_number_sequence(model, np.array([], dtype=np.uint8))

        self.assertIn(prediction["visual_state"], {"full", "partial", "none", "occluded", "unknown"})
        self.assertNotIn("candidate_number", prediction)
        self.assertEqual(abstention["digit_string"], None)
        self.assertEqual(abstention["visual_state"], "unknown")
        self.assertEqual(abstention["confidence"], 0.0)
        self.assertFalse(abstention["accepted"])
        self.assertEqual(abstention["reason_codes"], ["diagnostic_single_match_uncalibrated"])

    def test_mixed_artifact_preprocessing_contract_is_safe_and_deterministic(self) -> None:
        gray = np.arange(60 * 80, dtype=np.uint8).reshape(60, 80)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        bgra = np.concatenate((bgr, np.full((60, 80, 1), 255, dtype=np.uint8)), axis=2)

        for image in (gray, bgr, bgra):
            first = preprocess_jersey_number_sequence(
                image, artifact_kind="anchor_crop", bbox_xyxy=[10, 10, 50, 50]
            )
            second = preprocess_jersey_number_sequence(
                image, artifact_kind="anchor_crop", bbox_xyxy=[10, 10, 50, 50]
            )
            self.assertIsNotNone(first)
            self.assertTrue(torch.equal(first, second))
        self.assertIsNone(preprocess_jersey_number_sequence(bgr, artifact_kind="unknown"))
        self.assertIsNone(
            preprocess_jersey_number_sequence(
                bgr, artifact_kind="anchor_crop", bbox_xyxy=cast(list[float], ["x"] * 4)
            )
        )

    def test_sequence_confidence_combines_digit_and_visual_state_scores(self) -> None:
        prediction = predict_jersey_number_sequence(_FixedSequenceModel(), np.zeros((32, 96), dtype=np.uint8))

        self.assertIsNone(prediction["digit_string"])
        self.assertEqual(prediction["raw_digit_string"], "1")
        self.assertGreater(prediction["raw_sequence_confidence"], 0.0)
        self.assertLess(prediction["raw_sequence_confidence"], 1.0)
        self.assertFalse(prediction["accepted"])
        self.assertIn("diagnostic_single_match_uncalibrated", prediction["reason_codes"])

    def test_incompatible_and_tampered_checkpoint_metadata_is_rejected(self) -> None:
        model = JerseyNumberSequenceCRNN()
        state_dict = model.state_dict()
        metadata = _checkpoint_metadata()
        checkpoint = {
            "metadata": metadata,
            "state_dict": state_dict,
            "checkpoint_digest": sequence_checkpoint_digest(metadata, state_dict),
        }
        self.assertIsInstance(load_sequence_checkpoint(checkpoint), JerseyNumberSequenceCRNN)

        incompatible = {**metadata, "architecture": {**metadata["architecture"], "sequence_steps": 23}}
        checkpoint["metadata"] = incompatible
        checkpoint["checkpoint_digest"] = sequence_checkpoint_digest(incompatible, state_dict)
        with self.assertRaises(ValueError):
            load_sequence_checkpoint(checkpoint)
        checkpoint["metadata"] = metadata
        checkpoint["checkpoint_digest"] = sequence_checkpoint_digest(metadata, state_dict)
        checkpoint["metadata"]["training"]["parameters"]["seed"] = 99
        with self.assertRaises(ValueError):
            load_sequence_checkpoint(checkpoint)


class _FixedSequenceModel(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.anchor = torch.nn.Parameter(torch.zeros(1))

    def forward(self, image: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        sequence = torch.full((image.shape[0], 2, 11), -4.0, device=image.device)
        sequence[:, :, 1] = 1.0
        visual = torch.full((image.shape[0], 5), -4.0, device=image.device)
        visual[:, 0] = 1.0
        return sequence + self.anchor, visual + self.anchor


def _checkpoint_metadata() -> dict[str, Any]:
    return {
        **sequence_contract_metadata(),
        "architecture": sequence_architecture_metadata(),
        "preprocessing": sequence_preprocessing_metadata(),
        "visual_state_mapping": sequence_visual_state_mapping(),
        "training": {
            "train_dataset_digest": "dataset",
            "train_split_digest": "split",
            "parameters": {"epochs": 1, "learning_rate": 0.001, "seed": 0},
        },
    }


if __name__ == "__main__":
    unittest.main()
