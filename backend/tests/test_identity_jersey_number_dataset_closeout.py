from __future__ import annotations

import copy
import tempfile
from pathlib import Path
import unittest

from app.services.identity_jersey_number_dataset import (
    build_identity_jersey_number_dataset_manifest,
)
from app.services.identity_jersey_number_offline_evaluation import _metrics
from scripts.evaluate_identity_jersey_number_dataset_closeout import (
    REAL_NUMBER_10_SUBJECT_ID,
    REAL_NUMBER_10_TRACKLET_ID,
    _real_number_10_fixture,
)


class JerseyNumberDatasetCloseoutTests(unittest.TestCase):
    def test_one_physical_match_uses_subject_split_and_blocks_production(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_a = _source(Path(directory), video_key="clip-a", subject_id="s1")
            source_b = _source(Path(directory), video_key="clip-b", subject_id="s2")

            first = build_identity_jersey_number_dataset_manifest(
                [source_a, source_b],
                generated_at="fixed",
            )
            second = build_identity_jersey_number_dataset_manifest(
                [source_a, source_b],
                generated_at="fixed",
            )

        self.assertEqual(first["dataset_digest"], second["dataset_digest"])
        self.assertEqual(first["split_contract"]["method"], "subject_group_fallback")
        self.assertEqual(first["split_contract"]["independent_source_matches"], 1)
        self.assertFalse(first["production_gate"]["eligible"])
        self.assertIn(
            "insufficient_independent_source_matches",
            first["production_gate"]["reason_codes"],
        )
        splits_by_subject: dict[str, set[str]] = {}
        for row in first["samples"]:
            splits_by_subject.setdefault(row["candidate_subject_id"], set()).add(
                row["split"]
            )
        self.assertTrue(all(len(values) == 1 for values in splits_by_subject.values()))

    def test_real_number_10_fixture_ignores_other_people_on_same_frames(self) -> None:
        target_rows = [
            {
                "frame": frame,
                "tracklet_id": REAL_NUMBER_10_TRACKLET_ID,
                "candidate_subject_id": REAL_NUMBER_10_SUBJECT_ID,
                "visibility_episode_id": "episode-10",
                "number": "10",
                "state": "number_confirmed",
            }
            for frame in (3509, 3510, 3512)
        ]
        unrelated_rows = [
            {
                "frame": frame,
                "tracklet_id": "other-tracklet",
                "candidate_subject_id": "other-subject",
                "visibility_episode_id": "other-episode",
                "number": "15",
                "state": "number_confirmed",
            }
            for frame in (3509, 3510, 3512)
        ]

        result = _real_number_10_fixture(
            {"observations": target_rows + unrelated_rows}
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["observed_frames"], [3509, 3510, 3512])
        self.assertEqual(len(result["observations"]), 3)

    def test_false_read_metrics_are_disjoint(self) -> None:
        rows = [
            _prediction("wrong_confirmed_number", expected_number="10", predicted="15"),
            _prediction("false_number_on_plain_shirt", expected_state="number_absent", predicted="8"),
            _prediction("false_number_on_unreadable", predicted="6"),
            _prediction("correct_number", expected_number="92", predicted="92"),
        ]

        metrics = _metrics(rows, unit="crop")

        self.assertEqual(metrics["false_confirmed_reads_total"], 3)
        self.assertEqual(metrics["false_confirmed_reads_numbered_player"], 1)
        self.assertEqual(metrics["false_confirmed_reads_plain_shirt"], 1)
        self.assertEqual(metrics["false_confirmed_reads_unreadable"], 1)
        self.assertEqual(metrics["wrong_reads"], 3)

    def test_contiguous_scoped_frames_receive_one_visibility_episode_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = {
                "source_match_key": "match-3509",
                "source_video_key": "video-3509",
                "crop_root": Path(directory),
                "cards_doc": {
                    "cards": [
                        {
                            "anchor_crop_id": f"crop-{frame}",
                            "candidate_subject_id": "subject-10",
                            "tracklet_id": "tracklet-10",
                            "frame": frame,
                            "team_id": "team-a",
                            "team_label": "A",
                            "artifact": "missing.jpg",
                        }
                        for frame in (3509, 3510, 3512)
                    ]
                },
                "reviewed_observations_doc": {
                    "observations": [
                        {
                            "anchor_crop_id": f"crop-{frame}",
                            "state": "number_confirmed",
                            "number": "10",
                            "confidence": 1.0,
                            "view": "back",
                        }
                        for frame in (3509, 3510, 3512)
                    ]
                },
            }
            manifest = build_identity_jersey_number_dataset_manifest(
                [source], generated_at="fixed"
            )

        self.assertEqual(len(manifest["samples"]), 3)
        self.assertEqual(
            len({row["visibility_episode_id"] for row in manifest["samples"]}), 1
        )

    def test_quality_annotations_round_trip_and_change_dataset_digest(self) -> None:
        annotations = {
            "digit_visibility": "full",
            "occlusion_state": "partial",
            "blur_level": "mild",
            "perspective_state": "angled",
            "panel_height_ratio": 0.42,
            "kit_profile": "home-blue",
        }
        changed_values = {
            "digit_visibility": "partial",
            "occlusion_state": "heavy",
            "blur_level": "heavy",
            "perspective_state": "severe",
            "panel_height_ratio": 0.43,
            "kit_profile": "away-white",
        }
        with tempfile.TemporaryDirectory() as directory:
            source = _source(Path(directory), video_key="clip-a", subject_id="s1")
            source["reviewed_observations_doc"]["observations"][0].update(annotations)
            baseline = build_identity_jersey_number_dataset_manifest([source], generated_at="fixed")

            self.assertEqual(
                {field: baseline["samples"][0][field] for field in annotations},
                annotations,
            )
            for field, value in changed_values.items():
                changed = copy.deepcopy(source)
                changed["reviewed_observations_doc"]["observations"][0][field] = value
                manifest = build_identity_jersey_number_dataset_manifest(
                    [changed], generated_at="fixed"
                )
                self.assertNotEqual(manifest["dataset_digest"], baseline["dataset_digest"])

    def test_invalid_quality_annotations_normalize_to_unknown_or_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = _source(Path(directory), video_key="clip-a", subject_id="s1")
            source["reviewed_observations_doc"]["observations"][0].update(
                {
                    "digit_visibility": "visible",
                    "occlusion_state": "occluded",
                    "blur_level": "extreme",
                    "perspective_state": "sideways",
                    "panel_height_ratio": 1.1,
                    "kit_profile": 17,
                }
            )
            sample = build_identity_jersey_number_dataset_manifest(
                [source], generated_at="fixed"
            )["samples"][0]

        self.assertEqual(sample["digit_visibility"], "unknown")
        self.assertEqual(sample["occlusion_state"], "unknown")
        self.assertEqual(sample["blur_level"], "unknown")
        self.assertEqual(sample["perspective_state"], "unknown")
        self.assertIsNone(sample["panel_height_ratio"])
        self.assertIsNone(sample["kit_profile"])

    def test_fresh_subject_review_crop_annotation_merges_with_provenance(self) -> None:
        annotation = {
            "digit_visibility": "full",
            "occlusion_state": "partial",
            "blur_level": "mild",
            "perspective_state": "angled",
            "panel_height_ratio": 0.42,
            "kit_profile": "home-blue",
        }
        with tempfile.TemporaryDirectory() as directory:
            source = _source(Path(directory), video_key="clip-a", subject_id="s1")
            source["subject_review_doc"] = {
                "decisions_fresh": True,
                "cards": [
                    {
                        "operator_decision": {"decision": "assign_roster_player"},
                        "visual_evidence": {
                            "anchor_crops": [
                                {
                                    "anchor_crop_id": "clip-a-1",
                                    "jersey_number_annotation": annotation,
                                }
                            ]
                        },
                    }
                ],
            }
            manifest = build_identity_jersey_number_dataset_manifest(
                [source], generated_at="fixed"
            )
            changed = copy.deepcopy(source)
            changed["subject_review_doc"]["cards"][0]["visual_evidence"]["anchor_crops"][0][
                "jersey_number_annotation"
            ]["kit_profile"] = "away-white"
            changed_manifest = build_identity_jersey_number_dataset_manifest(
                [changed], generated_at="fixed"
            )
            stale = copy.deepcopy(source)
            stale["subject_review_doc"]["decisions_fresh"] = False
            stale_manifest = build_identity_jersey_number_dataset_manifest(
                [stale], generated_at="fixed"
            )
            mismatched = copy.deepcopy(source)
            mismatched["subject_review_doc"]["cards"][0]["visual_evidence"]["anchor_crops"][0][
                "anchor_crop_id"
            ] = "other-crop"
            mismatched_manifest = build_identity_jersey_number_dataset_manifest(
                [mismatched], generated_at="fixed"
            )

        sample = manifest["samples"][0]
        provenance = manifest["sources"][0]
        self.assertEqual({field: sample[field] for field in annotation}, annotation)
        self.assertEqual(sample["label_state"], "number_confirmed")
        self.assertEqual(sample["number"], "10")
        self.assertTrue(provenance["subject_review_decisions_fresh"])
        self.assertEqual(provenance["subject_review_annotations_applied"], 1)
        self.assertIsNotNone(provenance["subject_review_digest"])
        self.assertNotEqual(manifest["dataset_digest"], changed_manifest["dataset_digest"])
        self.assertEqual(stale_manifest["sources"][0]["subject_review_annotations_applied"], 0)
        self.assertEqual(mismatched_manifest["sources"][0]["subject_review_annotations_applied"], 0)
        self.assertEqual(stale_manifest["samples"][0]["number"], "10")
        self.assertEqual(mismatched_manifest["samples"][0]["number"], "10")


def _source(root: Path, *, video_key: str, subject_id: str) -> dict:
    cards = {
        "cards": [
            {
                "anchor_crop_id": f"{video_key}-1",
                "candidate_subject_id": subject_id,
                "tracklet_id": f"{subject_id}-tracklet",
                "frame": 10,
                "team_label": "A",
                "artifact": "missing.jpg",
            }
        ]
    }
    reviews = {
        "observations": [
            {
                "anchor_crop_id": f"{video_key}-1",
                "state": "number_confirmed",
                "number": "10",
                "confidence": 1.0,
                "view": "back",
                "number_panel_visible": True,
            }
        ]
    }
    return {
        "source_match_key": "same-physical-match",
        "source_video_key": video_key,
        "crop_root": root,
        "cards_doc": cards,
        "reviewed_observations_doc": reviews,
    }


def _prediction(
    result: str,
    *,
    expected_state: str = "number_unreadable",
    expected_number: str | None = None,
    predicted: str | None = None,
) -> dict:
    return {
        "result": result,
        "expected_state": (
            "number_confirmed" if expected_number is not None else expected_state
        ),
        "expected_number": expected_number,
        "predicted_number": predicted,
        "expected_number_panel_visible": expected_number is not None,
        "predicted_number_panel_visible": predicted is not None,
        "predicted_readable_number": predicted is not None,
    }


if __name__ == "__main__":
    unittest.main()
