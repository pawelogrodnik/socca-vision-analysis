from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

import numpy as np
import torch
from torch import nn


_SCRIPT = Path(__file__).parents[1] / "scripts" / "train_osnet_audited_reid.py"
_SPEC = importlib.util.spec_from_file_location("train_osnet_audited_reid", _SCRIPT)
_MODULE = importlib.util.module_from_spec(_SPEC) if _SPEC else None
try:
    if _SPEC and _SPEC.loader:
        _SPEC.loader.exec_module(_MODULE)
except ModuleNotFoundError:
    _MODULE = None


@unittest.skipIf(_MODULE is None, "isolated OSNet training runtime is required")
class OsnetReidTrainingProtocolTests(unittest.TestCase):
    def test_epoch_seed_is_repeatable_and_changes_between_epochs(self) -> None:
        sampler = _MODULE.TeamAwarePKSampler(_rows(), p=2, k=2, steps=3, seed=11)
        sampler.set_epoch(1); list(sampler); first = sampler.epoch_metrics["batch_digest"]
        sampler.set_epoch(2); list(sampler); second = sampler.epoch_metrics["batch_digest"]
        sampler.set_epoch(1); list(sampler); replay = sampler.epoch_metrics["batch_digest"]
        self.assertNotEqual(first, second)
        self.assertEqual(first, replay)

    def test_repeated_sample_fallback_is_explicit(self) -> None:
        sampler = _MODULE.TeamAwarePKSampler(_rows(one_crop=True), p=2, k=4, steps=1, seed=11)
        list(sampler)
        self.assertGreater(sampler.epoch_metrics["repeated_sample_fallbacks"], 0)
        self.assertTrue(sampler.epoch_metrics["identities_without_k_unique_samples"])

    def test_stage_two_unfreezes_only_expected_layers(self) -> None:
        model = _ToyModel()
        _MODULE.configure_stage(model, "stage_2")
        self.assertTrue(all(value.requires_grad for value in model.conv4.parameters()))
        self.assertTrue(all(value.requires_grad for value in model.conv5.parameters()))
        self.assertTrue(all(value.requires_grad for value in model.classifier.parameters()))
        self.assertTrue(all(not value.requires_grad for value in model.other.parameters()))

    def test_same_team_rank_excludes_identical_opponent_embedding(self) -> None:
        # Player 2 is an identical vector but belongs to team 1, so it is rejected.
        train_vectors = np.array([[1., 0.], [0., 1.], [1., 0.]], dtype=np.float32)
        train_meta = [(0, 0, 0, 0), (1, 0, 1, 1), (2, 1, 2, 2)]
        valid_vectors = np.array([[1., 0.]], dtype=np.float32)
        valid_meta = [(0, 0, 0, 3)]
        original = _MODULE._vectors
        calls = [(train_vectors, train_meta), (valid_vectors, valid_meta)]
        _MODULE._vectors = lambda *args, **kwargs: calls.pop(0)
        try:
            result = _MODULE._tracklet_validation(nn.Identity(), None, None, "cpu")
        finally:
            _MODULE._vectors = original
        row = result["rows"][0]
        self.assertNotIn(2, row["ranked_player_ids"])
        self.assertIn(2, row["cross_team_candidates_rejected"])


class _ToyModel(nn.Module):
    def __init__(self) -> None:
        super().__init__(); self.conv4 = nn.Linear(2, 2); self.conv5 = nn.Linear(2, 2); self.classifier = nn.Linear(2, 2); self.other = nn.Linear(2, 2)


def _rows(one_crop: bool = False) -> list[dict]:
    rows = []
    for player in ("p1", "p2"):
        count = 1 if one_crop else 3
        for index in range(count):
            rows.append({"sample_id": f"{player}-{index}", "player_id": player, "team_label": "A", "candidate_subject_id": f"s{player}-{index}", "tracklet_id": f"t{player}-{index}", "frame": index * 100})
    return rows


if __name__ == "__main__":
    unittest.main()
