from __future__ import annotations

import unittest
from unittest.mock import patch
from typing import Any

from app.services.identity_jersey_number_offline_evaluation import (
    _result,
    _subject_predictions,
    evaluate_identity_jersey_number_learned,
)


class OfflineEvaluationTests(unittest.TestCase):
    def test_contiguous_scoped_frames_count_as_one_episode_with_three_support(self) -> None:
        samples = [_sample(frame) for frame in (3509, 3510, 3512)]

        with patch(
            "app.services.identity_jersey_number_offline_evaluation._predict_sample",
            side_effect=_predict_sample,
        ):
            report = evaluate_identity_jersey_number_learned(
                _dataset(samples), _model(), generated_at="fixed"
            )

        self.assertEqual(report["episode_metrics"]["reviewed"], 1)
        self.assertEqual(report["episodes"][0]["support"], 3)

    def test_competing_episode_numbers_abstain_at_subject_scope(self) -> None:
        rows = [
            _episode("episode-1", "10"),
            _episode("episode-2", "10"),
            _episode("episode-3", "15"),
        ]

        subjects = _subject_predictions(rows, minimum_support=2)

        self.assertIsNone(subjects[0]["predicted_number"])
        self.assertEqual(subjects[0]["competing_numbers"], 1)

    def test_incomplete_episode_scope_blocks_production_eligibility(self) -> None:
        sample = _sample(3509)
        sample["candidate_subject_id"] = ""

        with patch(
            "app.services.identity_jersey_number_offline_evaluation._predict_sample",
            side_effect=_predict_sample,
        ):
            report = evaluate_identity_jersey_number_learned(
                _dataset([sample]), _model(), generated_at="fixed"
            )

        self.assertFalse(report["production_gate"]["eligible"])
        self.assertIn("episode_scope_invalid", report["safety"]["evaluation_safety_blockers"])

    def test_heldout_plain_and_unreadable_false_reads_block_production(self) -> None:
        plain = _sample(3509, expected_state="number_absent", predicted_number="8")
        unreadable = _sample(
            3600, expected_state="number_unreadable", predicted_number="6"
        )

        with patch(
            "app.services.identity_jersey_number_offline_evaluation._predict_sample",
            side_effect=_predict_sample,
        ):
            report = evaluate_identity_jersey_number_learned(
                _dataset([plain, unreadable]),
                _model(),
                generated_at="fixed",
                parameters={"minimum_episode_support": 1},
            )

        self.assertFalse(report["production_gate"]["eligible"])
        self.assertIn(
            "heldout_false_confirmed_episode_read",
            report["production_gate"]["reason_codes"],
        )

    def test_annotation_numbers_do_not_define_candidate_vocabulary(self) -> None:
        baseline_samples = [_sample(3509), _sample(3600)]
        changed_samples = [_sample(3509), _sample(3600)]
        changed_samples[0]["number"] = "99"
        changed_samples[1]["split"] = "validation"
        changed_samples[1]["number"] = "88"
        candidate_lists: list[list[str]] = []

        def capture_candidates(sample: dict, **kwargs: Any) -> dict:
            candidate_lists.append(list(kwargs["candidate_numbers"]))
            return _predict_sample(sample)

        with patch(
            "app.services.identity_jersey_number_offline_evaluation._predict_sample",
            side_effect=capture_candidates,
        ):
            baseline = evaluate_identity_jersey_number_learned(
                _dataset(baseline_samples), _model(), generated_at="fixed"
            )
            changed = evaluate_identity_jersey_number_learned(
                _dataset(changed_samples), _model(), generated_at="fixed"
            )

        self.assertTrue(all(values == ["10", "15"] for values in candidate_lists))
        self.assertEqual(
            baseline["evaluation_contract"]["candidate_vocabulary"],
            changed["evaluation_contract"]["candidate_vocabulary"],
        )
        self.assertEqual(
            baseline["evaluation_contract"]["candidate_vocabulary_digest"],
            changed["evaluation_contract"]["candidate_vocabulary_digest"],
        )
        self.assertEqual(
            changed["evaluation_contract"]["recognition_mode"],
            "closed_set_diagnostic_v1",
        )
        self.assertFalse(changed["evaluation_contract"]["heldout_annotation_vocabulary_used"])

    def test_quality_annotations_survive_predictions_and_expose_slices(self) -> None:
        sample = _sample(3509)
        quality = {
            "digit_visibility": "full",
            "occlusion_state": "partial",
            "blur_level": "mild",
            "perspective_state": "angled",
            "panel_height_ratio": 0.42,
            "kit_profile": "home-blue",
        }
        sample.update(quality)

        with patch(
            "app.services.identity_jersey_number_offline_evaluation._predict_sample",
            side_effect=_predict_sample,
        ):
            report = evaluate_identity_jersey_number_learned(
                _dataset([sample]), _model(), generated_at="fixed", parameters={"minimum_episode_support": 1}
            )

        self.assertEqual(
            {field: report["predictions"][0][field] for field in quality}, quality
        )
        for field, value in quality.items():
            self.assertIn(str(value), report["quality_slice_coverage"][field])


def _dataset(samples: list[dict]) -> dict:
    return {"samples": samples, "split_contract": {"production_eligible": True}}


def _model() -> dict:
    return {
        "production_gate": {"eligible": 1},
        "prototypes": {"10": [1.0], "15": [1.0]},
    }


def _sample(
    frame: int,
    *,
    expected_state: str = "number_confirmed",
    predicted_number: str = "10",
) -> dict:
    return {
        "sample_key": f"sample-{frame}",
        "anchor_crop_id": f"crop-{frame}",
        "source_match_key": "match-3509",
        "source_video_key": "video-3509",
        "team_id": "team-a",
        "team_label": "A",
        "candidate_subject_id": "subject-10",
        "tracklet_id": "tracklet-10",
        "frame": frame,
        "split": "heldout",
        "label_state": expected_state,
        "number": "10" if expected_state == "number_confirmed" else None,
        "predicted_number": predicted_number,
    }


def _predict_sample(sample: dict, **_: object) -> dict:
    expected_state = str(sample["label_state"])
    expected_number = sample["number"]
    predicted_number = sample["predicted_number"]
    return {
        **sample,
        "expected_state": expected_state,
        "expected_number": expected_number,
        "expected_number_panel_visible": expected_state == "number_confirmed",
        "expected_clean_jersey_visible": True,
        "predicted_number": predicted_number,
        "predicted_number_panel_visible": predicted_number is not None,
        "predicted_readable_number": predicted_number is not None,
        "result": _result(expected_state, expected_number, predicted_number),
        "calibrated_confidence": 1.0,
    }


def _episode(episode_id: str, predicted_number: str) -> dict:
    return {
        "visibility_episode_id": episode_id,
        "source_match_key": "match-3509",
        "source_video_key": "video-3509",
        "team_id": "team-a",
        "candidate_subject_id": "subject-10",
        "team_label": "A",
        "expected_number": "10",
        "predicted_number": predicted_number,
    }


if __name__ == "__main__":
    unittest.main()
