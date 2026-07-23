from __future__ import annotations

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
