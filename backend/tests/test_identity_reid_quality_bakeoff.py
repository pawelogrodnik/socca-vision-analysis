from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np

from app.services.identity_reid_quality_bakeoff import (
    AuditCrop,
    embedding_health,
    evaluate_crop_loo,
    select_h1_winner,
)


def _crop(player_id: str, index: int) -> AuditCrop:
    return AuditCrop(
        player_id=player_id,
        player_name=player_id,
        team_label="A",
        anchor_crop_id=f"{player_id}-{index}",
        frame=index,
        bbox_xyxy=(0.0, 0.0, 10.0, 20.0),
        artifact=Path("unused.jpg"),
        selection_score=1.0,
        candidate_subject_id=f"subject-{player_id}",
        tracklet_id=f"tracklet-{player_id}-{index}",
        capture_domain="H1",
        source_workspace="h1_workspace",
        source_match="h1",
        source_frame=index,
        audit_source="test",
        audit_decision_digest="digest",
        crop_sha256=f"sha-{player_id}-{index}",
    )


class ReIdQualityBakeoffTests(unittest.TestCase):
    def test_crop_loo_excludes_the_query_from_its_truth_references(self) -> None:
        crops = [_crop("a", 0), _crop("a", 1), _crop("b", 0), _crop("b", 1)]
        vectors = np.asarray([
            [1.0, 0.0], [0.98, 0.02], [0.0, 1.0], [0.02, 0.98],
        ], dtype=np.float32)
        result = evaluate_crop_loo(crops, vectors, prototype="normalised_mean", ranking="prototype_cosine")
        self.assertEqual(result["queries"], 4)
        self.assertEqual(result["top1_accuracy"], 1.0)

    def test_selector_uses_h1_metric_and_explicitly_keeps_h2_out(self) -> None:
        candidates = [
            {"model": {"model_name": "z"}, "crop_variant": "A", "prototype": "medoid", "ranking": "prototype_cosine", "evaluation": {"top1_accuracy": .2, "top3_accuracy": .4}},
            {"model": {"model_name": "a"}, "crop_variant": "A", "prototype": "medoid", "ranking": "prototype_cosine", "evaluation": {"top1_accuracy": .5, "top3_accuracy": .5}},
        ]
        result = select_h1_winner(candidates)
        self.assertEqual(result["winner"]["model"]["model_name"], "a")
        self.assertFalse(result["h2_was_available_to_selector"])

    def test_embedding_health_reports_non_separability_without_identity_claims(self) -> None:
        crops = [_crop("a", 0), _crop("a", 1), _crop("b", 0), _crop("b", 1)]
        vectors = np.asarray([
            [1.0, 0.0], [.99, .01], [.98, .02], [.97, .03],
        ], dtype=np.float32)
        result = embedding_health(crops, vectors)
        self.assertTrue(result["finite"])
        self.assertLess(result["same_team_roc_auc"], .8)


if __name__ == "__main__":
    unittest.main()
