from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_jersey_number_visual_pre_audit import (
    build_identity_jersey_number_visual_pre_audit,
)


def review(artifact: str = "crop.jpg") -> dict:
    return {
        "cards": [
            {
                "review_card_key": "card-1",
                "candidate_subject_id": "subject-1",
                "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "crop-1", "artifact": artifact}]},
            }
        ]
    }


class JerseyNumberVisualPreAuditTests(unittest.TestCase):
    def test_audit_is_deterministic_bounded_and_shadow_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = np.full((120, 80, 3), 100, dtype=np.uint8)
            cv2.rectangle(image, (22, 20), (58, 100), (245, 245, 245), thickness=-1)
            cv2.putText(image, "10", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
            cv2.imwrite(str(root / "crop.jpg"), image)
            source = review()
            first = build_identity_jersey_number_visual_pre_audit(source, crop_root=root, generated_at="fixed")
            second = build_identity_jersey_number_visual_pre_audit(source, crop_root=root, generated_at="fixed")

        suggestion = first["suggestions"][0]
        diagnostics = suggestion["jersey_number_visual_diagnostics"]
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], "0.2.0")
        self.assertEqual(suggestion["status"], "audited")
        self.assertIsNotNone(suggestion["crop_sha256"])
        self.assertIn(diagnostics["digit_signal"], {"likely_full", "likely_partial", "indeterminate"})
        self.assertNotIn("number", suggestion)
        self.assertNotIn("number_absent", suggestion)
        self.assertNotIn("state", suggestion)
        self.assertNotIn("label_state", suggestion)
        self.assertNotIn("visual_state", suggestion)
        self.assertNotIn("digit_visibility", suggestion)
        self.assertNotIn("jersey_number_annotation_suggestion", suggestion)
        self.assertFalse(first["safety"]["eligible_for_training"])
        self.assertFalse(first["safety"]["eligible_for_player_stats"])

    def test_missing_and_corrupt_crops_abstain_without_mutating_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "corrupt.jpg").write_text("not-an-image", encoding="utf-8")
            source = review("missing.jpg")
            source["cards"][0]["visual_evidence"]["anchor_crops"].append(
                {"anchor_crop_id": "crop-2", "artifact": "corrupt.jpg"}
            )
            original = deepcopy(source)
            result = build_identity_jersey_number_visual_pre_audit(source, crop_root=root, generated_at="fixed")

        by_id = {row["anchor_crop_id"]: row for row in result["suggestions"]}
        self.assertEqual(source, original)
        self.assertEqual(by_id["crop-1"]["status"], "missing_crop")
        self.assertEqual(by_id["crop-2"]["status"], "corrupt_crop")
        self.assertEqual(
            by_id["crop-1"]["jersey_number_visual_diagnostics"]["digit_signal"],
            "indeterminate",
        )
        self.assertEqual(
            by_id["crop-2"]["jersey_number_visual_diagnostics"]["digit_signal"],
            "indeterminate",
        )


if __name__ == "__main__":
    unittest.main()
