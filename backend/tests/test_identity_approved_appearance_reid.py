from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_approved_appearance_reid import (
    PortableAppearanceEmbedder,
    build_identity_approved_appearance_reid,
    load_approved_appearance_embedder,
)


class MeanColorEmbedder:
    model_name = "mean-color-test"
    model_version = "1"
    embedding_dimension = 3

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        return crop_bgr.mean(axis=(0, 1)).astype(np.float32)


def write_crop(root: Path, name: str, color: tuple[int, int, int]) -> str:
    relative = f"crops/{name}.jpg"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((80, 40, 3), color, dtype=np.uint8)
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"Failed to write test crop: {path}")
    return relative


class IdentityApprovedAppearanceReIdTests(unittest.TestCase):
    def test_portable_fallback_is_deterministic_and_has_stable_contract(
        self,
    ) -> None:
        image = np.zeros((90, 45, 3), dtype=np.uint8)
        image[:, :22] = (20, 40, 220)
        embedder = PortableAppearanceEmbedder()

        first = embedder.embed(image)
        second = embedder.embed(image.copy())

        self.assertEqual(first.shape, (embedder.embedding_dimension,))
        np.testing.assert_allclose(first, second)
        self.assertAlmostEqual(float(np.linalg.norm(first)), 1.0, places=5)

    def test_loader_uses_portable_fallback_when_model_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            embedder, status = load_approved_appearance_embedder(
                Path(temporary)
            )

        self.assertIsInstance(embedder, PortableAppearanceEmbedder)
        self.assertTrue(status["available"])
        self.assertEqual(status["quality_tier"], "baseline_fallback")

    def test_ranks_unresolved_subject_and_preserves_capture_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = {
                "p1-h1": write_crop(root, "p1-h1", (240, 10, 10)),
                "p1-h2": write_crop(root, "p1-h2", (220, 20, 10)),
                "p2-h1": write_crop(root, "p2-h1", (10, 240, 10)),
                "unresolved": write_crop(
                    root, "unresolved", (230, 15, 10)
                ),
            }
            gallery = {
                "algorithm": {"name": "gallery"},
                "players": [
                    {
                        "player_id": "p1",
                        "player_name": "Player One",
                        "team_label": "A",
                        "candidate_subject_ids": ["p1-h1", "p1-h2"],
                        "capture_domains": [
                            {
                                "capture_domain": "H1",
                                "crops": [
                                    {
                                        "candidate_subject_id": "p1-h1",
                                        "artifact": artifacts["p1-h1"],
                                    }
                                ],
                            },
                            {
                                "capture_domain": "H2",
                                "crops": [
                                    {
                                        "candidate_subject_id": "p1-h2",
                                        "artifact": artifacts["p1-h2"],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "player_id": "p2",
                        "player_name": "Player Two",
                        "team_label": "A",
                        "candidate_subject_ids": ["p2-h1"],
                        "capture_domains": [
                            {
                                "capture_domain": "H1",
                                "crops": [
                                    {
                                        "candidate_subject_id": "p2-h1",
                                        "artifact": artifacts["p2-h1"],
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
            anchor_crops = {
                "algorithm": {"name": "anchor-crops"},
                "cards": [
                    {
                        "candidate_subject_id": subject_id,
                        "team_label": "A",
                        "anchor_crops": [
                            {
                                "anchor_crop_id": subject_id,
                                "frame": index,
                                "artifact": artifact,
                                "selection_score": 1.0,
                            }
                        ],
                    }
                    for index, (subject_id, artifact) in enumerate(
                        artifacts.items()
                    )
                ],
            }

            result = build_identity_approved_appearance_reid(
                gallery,
                anchor_crops,
                match_path=root,
                embedder=MeanColorEmbedder(),
                model_status={"available": True, "runtime": "test"},
                parameters={
                    "min_embeddings_per_subject": 1,
                    "min_embeddings_per_player": 1,
                },
            )
            artifact = result[
                "identity_approved_appearance_reid_shadow"
            ]

            unresolved = next(
                row
                for row in artifact["unresolved_rankings"]
                if row["candidate_subject_id"] == "unresolved"
            )
            self.assertEqual(
                unresolved["suggestions"][0]["player_id"],
                "p1",
            )
            self.assertEqual(
                artifact["summary"]["cross_domain_players"],
                1,
            )
            p1 = next(
                row
                for row in artifact["player_profiles"]
                if row["player_id"] == "p1"
            )
            domain_counts = {
                row["capture_domain"]: row["embedding_count"]
                for row in p1["capture_domains"]
            }
            self.assertEqual(domain_counts, {"H1": 1, "H2": 1})
            self.assertEqual(artifact["summary"]["automatic_merges"], 0)
            self.assertFalse(
                artifact["safety"]["mutates_production_identity"]
            )


if __name__ == "__main__":
    unittest.main()
