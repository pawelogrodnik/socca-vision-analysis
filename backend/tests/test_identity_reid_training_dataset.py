from __future__ import annotations

import unittest

from app.services.identity_reid_training_dataset import _assign_group_safe_split
from app.services.identity_reid_quality_bakeoff import cache_namespace


class _Embedder:
    model_name = "osnet"
    model_version = "v1"
    embedding_dimension = 512
    runtime_name = "isolated"
    architecture = "osnet_ain_x1_0"
    torch_version = "2.4.1"

    def __init__(self, digest: str) -> None:
        self.weights_sha256 = digest


class AuditedReIdDatasetTests(unittest.TestCase):
    def test_tracklet_groups_never_cross_train_validation(self) -> None:
        rows = [
            _row("a-1", "a", "subject-a", "tracklet-a-1", 100),
            _row("a-2", "a", "subject-a", "tracklet-a-2", 200),
            _row("b-1", "b", "subject-b", "tracklet-b-1", 100),
            _row("b-2", "b", "subject-b", "tracklet-b-2", 200),
        ]
        result = _assign_group_safe_split(rows)
        self.assertNotEqual(result["assignments"]["a-1"], result["assignments"]["a-2"])
        self.assertNotEqual(result["assignments"]["b-1"], result["assignments"]["b-2"])

    def test_checkpoint_digest_is_part_of_embedding_cache_namespace(self) -> None:
        first = cache_namespace(embedder=_Embedder("checkpoint-a"), crop_variant="full")
        second = cache_namespace(embedder=_Embedder("checkpoint-b"), crop_variant="full")
        self.assertNotEqual(first, second)


def _row(sample_id: str, player_id: str, subject: str, tracklet: str, frame: int) -> dict[str, object]:
    return {"sample_id": sample_id, "player_id": player_id, "candidate_subject_id": subject, "tracklet_id": tracklet, "frame": frame}


if __name__ == "__main__":
    unittest.main()
