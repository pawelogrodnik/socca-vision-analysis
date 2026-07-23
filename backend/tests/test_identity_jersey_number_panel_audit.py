from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.services.identity_jersey_number_panel_audit import (
    MONTAGE_FILENAME,
    READINESS_FILENAME,
    audit_identity_jersey_number_panels,
)

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - validation environment may lack OpenCV
    cv2 = None
    np = None


class JerseyNumberPanelAuditTests(unittest.TestCase):
    @unittest.skipUnless(cv2 is not None and np is not None, "OpenCV test dependency unavailable")
    def test_builds_readiness_report_and_montage_from_deterministic_panel_crops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_torso(root / "torso-read-10.jpg", digits="10")
            _write_torso(root / "torso-plain.jpg", digits=None)
            _write_torso(root / "torso-read-92.jpg", digits="92")
            dataset = {
                "dataset_digest": "dataset-digest",
                "dataset_version": "dataset-version",
                "summary": {"samples": 4},
                "samples": [
                    _sample(
                        root,
                        "sample-10",
                        "torso-read-10.jpg",
                        state="number_confirmed",
                        number="10",
                        frame=3509,
                        visibility_episode_id="episode-10",
                    ),
                    _sample(
                        root,
                        "sample-92",
                        "torso-read-92.jpg",
                        state="number_confirmed",
                        number="92",
                        frame=4200,
                        visibility_episode_id="episode-92",
                    ),
                    _sample(
                        root,
                        "sample-plain",
                        "torso-plain.jpg",
                        state="number_absent",
                        number=None,
                        frame=100,
                        visibility_episode_id="episode-plain",
                    ),
                    {
                        **_sample(
                            root,
                            "sample-missing",
                            "torso-read-10.jpg",
                            state="number_unreadable",
                            number=None,
                            frame=200,
                            visibility_episode_id="episode-missing",
                        ),
                        "number_panel_bbox_normalized": None,
                    },
                ],
            }

            first = audit_identity_jersey_number_panels(dataset, output_root=root / "audit")
            second = audit_identity_jersey_number_panels(dataset, output_root=root / "audit-repeat")
            montage_exists = (root / "audit" / MONTAGE_FILENAME).is_file()

        self.assertEqual(first["status"], "insufficient_panel_readiness")
        self.assertEqual(first["summary"]["total_samples"], 4)
        self.assertEqual(first["summary"]["total_panel_crops"], 3)
        self.assertEqual(first["summary"]["readable_full_number_crops"], 2)
        self.assertEqual(first["summary"]["plain_shirt_crops"], 1)
        self.assertEqual(first["summary"]["missing_panel_bbox_count"], 1)
        self.assertEqual(first["summary"]["counts_per_number"], {"10": 1, "92": 1})
        self.assertEqual(first["summary"]["counts_per_digit"]["0"], 1)
        self.assertEqual(first["summary"]["counts_per_digit"]["1"], 1)
        self.assertEqual(first["summary"]["counts_per_digit"]["2"], 1)
        self.assertEqual(first["summary"]["counts_per_digit"]["9"], 1)
        self.assertTrue(montage_exists)
        self.assertEqual(first["outputs"]["number_panel_dataset_readiness"], READINESS_FILENAME)
        first_digests = {
            row["anchor_crop_id"]: row["panel_digest"]
            for row in first["samples"]
            if row["panel_digest"] is not None
        }
        second_digests = {
            row["anchor_crop_id"]: row["panel_digest"]
            for row in second["samples"]
            if row["panel_digest"] is not None
        }
        self.assertEqual(first_digests, second_digests)
        self.assertGreaterEqual(first["summary"]["estimated_digit_height_px"]["median"], 8.0)


def _sample(
    root: Path,
    sample_key: str,
    artifact: str,
    *,
    state: str,
    number: str | None,
    frame: int,
    visibility_episode_id: str,
) -> dict[str, object]:
    return {
        "sample_key": sample_key,
        "anchor_crop_id": sample_key,
        "source_match_key": "match-1",
        "source_video_key": "video-1",
        "candidate_subject_id": f"subject-{sample_key}",
        "tracklet_id": f"tracklet-{sample_key}",
        "visibility_episode_id": visibility_episode_id,
        "frame": frame,
        "view": "back",
        "label_state": state,
        "number": number,
        "artifact_root": str(root),
        "artifact": artifact,
        "number_panel_bbox_normalized": [0.25, 0.2, 0.75, 0.78],
    }


def _write_torso(path: Path, *, digits: str | None) -> None:
    image = np.full((180, 120, 3), 40, dtype=np.uint8)
    cv2.rectangle(image, (28, 22), (92, 146), (235, 235, 235), thickness=-1)
    if digits is not None:
        cv2.putText(image, digits, (34, 98), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3)
    cv2.imwrite(str(path), image)


if __name__ == "__main__":
    unittest.main()
