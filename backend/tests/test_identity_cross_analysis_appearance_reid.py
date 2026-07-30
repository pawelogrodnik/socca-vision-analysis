from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_approved_appearance_reid import (
    PortableAppearanceEmbedder,
)
from app.services.identity_cross_analysis_appearance_reid import (
    build_cross_analysis_appearance_reid,
)


class CrossAnalysisAppearanceReidTests(unittest.TestCase):
    def test_embeds_reference_and_target_from_separate_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference_root = root / "h1"
            target_root = root / "h2"
            reference_root.mkdir()
            target_root.mkdir()
            reference_crops = _write_crops(reference_root, "red", (0, 0, 220))
            target_a_crops = _write_crops(target_root, "a", (0, 0, 220))
            target_b_crops = _write_crops(target_root, "b", (220, 0, 0))
            gallery = {
                "players": [
                    {
                        "player_id": "player-a",
                        "player_name": "Player A",
                        "team_label": "A",
                        "capture_domains": [
                            {
                                "capture_domain": "H1",
                                "crops": reference_crops,
                            }
                        ],
                    }
                ]
            }
            target = {
                "cards": [
                    {
                        "candidate_subject_id": "target-a",
                        "team_label": "A",
                        "anchor_crops": target_a_crops,
                    },
                    {
                        "candidate_subject_id": "target-b",
                        "team_label": "B",
                        "anchor_crops": target_b_crops,
                    },
                ]
            }

            result = build_cross_analysis_appearance_reid(
                gallery,
                target,
                {"accepted_assignments": []},
                reference_match_path=reference_root,
                target_match_path=target_root,
                embedder=PortableAppearanceEmbedder(),
                model_status={"available": True},
            )

        artifact = result["identity_cross_analysis_appearance_reid"]
        rankings = {
            row["candidate_subject_id"]: row
            for row in artifact["unresolved_rankings"]
        }
        self.assertEqual(artifact["summary"]["players_with_prototype"], 1)
        self.assertEqual(rankings["target-a"]["status"], "ranked")
        self.assertEqual(
            rankings["target-a"]["suggestions"][0]["player_id"],
            "player-a",
        )
        self.assertEqual(rankings["target-b"]["status"], "unavailable")
        self.assertEqual(
            rankings["target-b"]["reason_codes"],
            ["no_confirmed_player_prototypes_for_team"],
        )

    def test_baseline_descriptor_rankings_are_hidden_from_operator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference_root = root / "h1"
            target_root = root / "h2"
            reference_root.mkdir()
            target_root.mkdir()
            gallery = {
                "players": [
                    {
                        "player_id": "player-a",
                        "player_name": "Player A",
                        "team_label": "A",
                        "capture_domains": [{
                            "capture_domain": "H1",
                            "crops": _write_crops(
                                reference_root, "red", (0, 0, 220)
                            ),
                        }],
                    },
                    {
                        "player_id": "player-b",
                        "player_name": "Player B",
                        "team_label": "A",
                        "capture_domains": [{
                            "capture_domain": "H1",
                            "crops": _write_crops(
                                reference_root, "blue", (220, 0, 0)
                            ),
                        }],
                    },
                ]
            }
            target = {
                "cards": [{
                    "candidate_subject_id": "target-a",
                    "team_label": "A",
                    "anchor_crops": _write_crops(
                        target_root, "target", (0, 0, 220)
                    ),
                }]
            }

            result = build_cross_analysis_appearance_reid(
                gallery,
                target,
                {"accepted_assignments": []},
                reference_match_path=reference_root,
                target_match_path=target_root,
                embedder=PortableAppearanceEmbedder(),
                model_status={"available": True, "quality_tier": "baseline_fallback"},
            )

        artifact = result["identity_cross_analysis_appearance_reid"]
        self.assertFalse(artifact["ranking_display"]["display_eligible"])
        self.assertIn(
            "baseline_descriptor_not_validated_for_cross_capture",
            artifact["ranking_display"]["suppression_reason_codes"],
        )
        self.assertEqual(
            artifact["summary"]["operator_visible_ranked_subjects"],
            0,
        )


def _write_crops(
    root: Path,
    prefix: str,
    color: tuple[int, int, int],
) -> list[dict[str, object]]:
    crops = []
    for index in range(3):
        artifact = f"anchor_crops/{prefix}-{index}.jpg"
        path = root / artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        image = np.full((96, 48, 3), color, dtype=np.uint8)
        image[:, index * 4 : index * 4 + 4] = (255, 255, 255)
        cv2.imwrite(str(path), image)
        crops.append(
            {
                "anchor_crop_id": f"{prefix}-{index}",
                "artifact": artifact,
                "frame": index,
                "selection_score": 1.0 - index * 0.1,
                "selection_eligible": True,
            }
        )
    return crops


if __name__ == "__main__":
    unittest.main()
