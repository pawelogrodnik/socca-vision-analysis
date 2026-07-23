from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import cv2
import numpy as np

from app.services.identity_jersey_number_sequence import (
    JerseyNumberSequenceCRNN,
    sequence_architecture_metadata,
    sequence_checkpoint_digest,
    sequence_preprocessing_metadata,
    sequence_visual_state_mapping,
)
from app.services.identity_jersey_number_sequence_contract import sequence_contract_metadata
from app.services.identity_jersey_number_sequence_evaluation import (
    evaluate_jersey_number_sequence_shadow,
)


class JerseyNumberSequenceEvaluationTests(unittest.TestCase):
    def test_no_candidate_vocabulary_and_missing_artifacts_abstain(self) -> None:
        result = evaluate_jersey_number_sequence_shadow(_manifest(), _checkpoint())

        crop = result["crops"][0]
        self.assertNotIn("candidate_numbers", result)
        self.assertNotIn("candidate_vocabulary", crop)
        self.assertIsNone(crop["raw_digit_string"])
        self.assertFalse(crop["accepted"])

    def test_contiguous_frames_fuse_to_one_canonical_episode(self) -> None:
        result = evaluate_jersey_number_sequence_shadow(_manifest(frames=[3509, 3510, 3512]), _checkpoint())

        self.assertEqual(len(result["episodes"]), 1)
        self.assertEqual(result["episodes"][0]["observations"], 3)
        self.assertEqual(result["episodes"][0]["start_frame"], 3509)
        self.assertEqual(result["episodes"][0]["end_frame"], 3512)

    def test_single_match_gates_remain_false(self) -> None:
        result = evaluate_jersey_number_sequence_shadow(_manifest(), _checkpoint())

        self.assertFalse(result["gates"]["production_eligible"])
        self.assertFalse(result["gates"]["candidate_eligible"])
        self.assertIn("single_match_diagnostic_only", result["gates"]["reason_codes"])

    def test_raw_sequence_metrics_stay_unaccepted_and_activation_ineligible(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "crop.jpg"
            self.assertTrue(cv2.imwrite(str(path), np.zeros((32, 96, 3), dtype=np.uint8)))
            result = evaluate_jersey_number_sequence_shadow(
                _manifest(artifact_root=directory), _raw_checkpoint()
            )

        crop = result["crops"][0]
        self.assertEqual(crop["raw_digit_string"], "1")
        self.assertGreater(crop["raw_sequence_confidence"], 0.0)
        self.assertFalse(crop["accepted"])
        self.assertIn("diagnostic_single_match_uncalibrated", crop["reason_codes"])
        self.assertFalse(result["gates"]["production_eligible"])
        self.assertFalse(result["gates"]["candidate_eligible"])
        self.assertFalse(result["gates"]["activation_eligible"])


def _checkpoint() -> dict[str, Any]:
    model = JerseyNumberSequenceCRNN()
    metadata = {
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
    state_dict = model.state_dict()
    return {
        "metadata": metadata,
        "state_dict": state_dict,
        "checkpoint_digest": sequence_checkpoint_digest(metadata, state_dict),
    }


def _raw_checkpoint() -> dict[str, Any]:
    model = JerseyNumberSequenceCRNN()
    state_dict = model.state_dict()
    for value in state_dict.values():
        value.zero_()
    state_dict["sequence_head.bias"][1] = 1.0
    state_dict["visual_head.bias"][0] = 1.0
    metadata = _checkpoint()["metadata"]
    return {
        "metadata": metadata,
        "state_dict": state_dict,
        "checkpoint_digest": sequence_checkpoint_digest(metadata, state_dict),
    }


def _manifest(frames: list[int] | None = None, artifact_root: str = "/missing") -> dict[str, object]:
    return {
        "split_contract": {"production_eligible": False},
        "samples": [
            {
                "sample_key": f"crop-{frame}",
                "split": "validation",
                "source_match_key": "match-1",
                "source_video_key": "video-1",
                "candidate_subject_id": "subject-1",
                "tracklet_id": "tracklet-1",
                "team_label": "A",
                "frame": frame,
                "label_state": "number_confirmed",
                "number": "10",
                "artifact_root": artifact_root,
                "artifact": "crop.jpg",
            }
            for frame in frames or [3509]
        ],
    }


if __name__ == "__main__":
    unittest.main()
