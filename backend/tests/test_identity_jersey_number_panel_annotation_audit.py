from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_jersey_number_dataset import (
    identity_jersey_number_dataset_digest,
)
from app.services.identity_jersey_number_panel_annotation_audit import (
    INDEX_FILENAME,
    MANIFEST_FILENAME,
    apply_panel_annotation_audit,
    prepare_panel_annotation_audit,
)


class JerseyNumberPanelAnnotationAuditTests(unittest.TestCase):
    def test_prepare_builds_bounded_resumable_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "source"
            artifact_root.mkdir()
            (artifact_root / "crop.jpg").write_bytes(b"jpeg-placeholder")
            dataset = _dataset(artifact_root)
            output_root = root / "audit"

            manifest = prepare_panel_annotation_audit(
                dataset,
                output_root=output_root,
                selection_doc=_selection("sample-confirmed", "sample-negative"),
                generated_at="2026-07-27T10:00:00+00:00",
            )

            self.assertEqual(manifest["summary"]["selected_samples"], 2)
            self.assertEqual(manifest["summary"]["available_images"], 2)
            self.assertTrue((output_root / MANIFEST_FILENAME).is_file())
            self.assertTrue((output_root / INDEX_FILENAME).is_file())
            self.assertEqual(len(list((output_root / "images").glob("*.jpg"))), 2)
            html = (output_root / INDEX_FILENAME).read_text(encoding="utf-8")
            self.assertIn("Skip / not sure", html)
            self.assertIn("Finish audit", html)
            self.assertIn('id="zoomIn"', html)
            self.assertIn('id="zoomOut"', html)
            self.assertIn('id="zoomFit"', html)
            self.assertIn("function fitZoom()", html)
            self.assertIn("canvas.getBoundingClientRect()", html)
            self.assertNotIn("number_panel_bbox_normalized:</", html)

    def test_apply_updates_only_confirmed_box_and_recomputes_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "source"
            artifact_root.mkdir()
            (artifact_root / "crop.jpg").write_bytes(b"jpeg-placeholder")
            dataset = _dataset(artifact_root)
            original = deepcopy(dataset)
            reviewed = prepare_panel_annotation_audit(
                dataset,
                output_root=root / "audit",
                selection_doc=_selection("sample-confirmed", "sample-negative"),
            )
            reviewed["items"][0]["manual_review"] = {
                "status": "panel_confirmed",
                "number_panel_bbox_normalized": [0.2, 0.1, 0.8, 0.7],
                "reviewed_at": "2026-07-27T10:00:00+00:00",
            }
            reviewed["items"][1]["manual_review"] = {
                "status": "skipped",
                "number_panel_bbox_normalized": None,
                "reviewed_at": "2026-07-27T10:00:01+00:00",
            }

            updated = apply_panel_annotation_audit(
                dataset,
                reviewed,
                generated_at="2026-07-27T10:05:00+00:00",
            )

        rows = {row["sample_key"]: row for row in updated["samples"]}
        self.assertEqual(
            rows["sample-confirmed"]["number_panel_bbox_normalized"],
            [0.2, 0.1, 0.8, 0.7],
        )
        self.assertIsNone(rows["sample-negative"]["number_panel_bbox_normalized"])
        self.assertEqual(updated["panel_annotation_import"]["applied"], 1)
        self.assertEqual(updated["panel_annotation_import"]["skipped"], 1)
        self.assertNotEqual(updated["dataset_digest"], dataset["dataset_digest"])
        self.assertEqual(
            updated["dataset_digest"],
            identity_jersey_number_dataset_digest(updated["samples"]),
        )
        self.assertEqual(dataset, original)

    def test_apply_rejects_stale_or_tampered_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "source"
            artifact_root.mkdir()
            (artifact_root / "crop.jpg").write_bytes(b"jpeg-placeholder")
            dataset = _dataset(artifact_root)
            reviewed = prepare_panel_annotation_audit(
                dataset,
                output_root=root / "audit",
                selection_doc=_selection("sample-confirmed"),
            )
            reviewed["items"][0]["frame"] = 999

            with self.assertRaisesRegex(ValueError, "contract digest mismatch"):
                apply_panel_annotation_audit(dataset, reviewed)


def _dataset(artifact_root: Path) -> dict[str, object]:
    samples = [
        _sample(
            artifact_root,
            sample_key="sample-confirmed",
            state="number_confirmed",
            number="10",
            frame=10,
        ),
        _sample(
            artifact_root,
            sample_key="sample-negative",
            state="number_absent",
            number=None,
            frame=20,
        ),
    ]
    digest = identity_jersey_number_dataset_digest(samples)
    return {
        "dataset_digest": digest,
        "dataset_version": f"jersey-number-dataset:v3:{digest}",
        "summary": {"samples": 2},
        "samples": samples,
    }


def _sample(
    artifact_root: Path,
    *,
    sample_key: str,
    state: str,
    number: str | None,
    frame: int,
) -> dict[str, object]:
    return {
        "sample_key": sample_key,
        "anchor_crop_id": f"crop-{sample_key}",
        "source_match_key": "match",
        "source_video_key": "video",
        "candidate_subject_id": "subject",
        "tracklet_id": "tracklet",
        "frame": frame,
        "team_label": "A",
        "jersey_number_state": state,
        "jersey_number": number,
        "label_state": state,
        "number": number,
        "view": "back",
        "digit_visibility": "full" if number else "none",
        "occlusion_state": "none",
        "blur_level": "none",
        "perspective_state": "frontal",
        "panel_height_ratio": None,
        "kit_profile": None,
        "number_panel_bbox_normalized": None,
        "number_panel_artifact": None,
        "visibility_episode_id": f"episode-{sample_key}",
        "split": "train",
        "artifact": "crop.jpg",
        "artifact_root": str(artifact_root),
        "artifact_digest": "digest",
    }


def _selection(*sample_keys: str) -> dict[str, object]:
    keys = sorted(sample_keys)
    payload = {
        "selection_version": "panel-experiment-selection-v1",
        "sample_keys": keys,
    }
    return {**payload, "selection_digest": canonical_digest(payload)}


if __name__ == "__main__":
    unittest.main()
