from __future__ import annotations

import unittest

from app.services.identity_approved_appearance_gallery import (
    build_identity_approved_appearance_gallery,
)


def crop(
    subject_id: str,
    frame: int,
    time_sec: float,
    score: float,
) -> dict:
    return {
        "anchor_crop_id": f"{subject_id}-{frame}",
        "frame": frame,
        "time_sec": time_sec,
        "artifact": f"crops/{subject_id}-{frame}.jpg",
        "bbox_xyxy": [10.0, 10.0, 50.0, 130.0],
        "selection_score": score,
        "selection_eligible": True,
        "selection_reasons": [],
    }


class IdentityApprovedAppearanceGalleryTests(unittest.TestCase):
    def test_builds_cross_domain_gallery_without_operator_actions(self) -> None:
        seeded = {
            "algorithm": {"name": "seeded"},
            "accepted_assignments": [
                {
                    "candidate_subject_id": "subject-h1",
                    "assigned_player": {
                        "player_id": "p1",
                        "player_name": "Player One",
                        "team_label": "A",
                    },
                    "seed_observations": [],
                },
                {
                    "candidate_subject_id": "subject-h2",
                    "assigned_player": {
                        "player_id": "p1",
                        "player_name": "Player One",
                        "team_label": "A",
                    },
                    "seed_observations": [],
                },
            ],
        }
        crops = {
            "algorithm": {"name": "crops"},
            "cards": [
                {
                    "candidate_subject_id": "subject-h1",
                    "anchor_crops": [
                        crop("subject-h1", 30, 1.0, 0.9),
                        crop("subject-h1", 60, 2.0, 0.8),
                    ],
                },
                {
                    "candidate_subject_id": "subject-h2",
                    "anchor_crops": [
                        crop("subject-h2", 330, 11.0, 0.95),
                    ],
                },
            ],
        }

        result = build_identity_approved_appearance_gallery(
            seeded,
            crops,
            match_phase_config_doc={"second_half_start_time_sec": 10.0},
        )
        artifact = result["identity_approved_appearance_gallery"]

        self.assertEqual(artifact["summary"]["players"], 1)
        self.assertEqual(artifact["summary"]["h1_players"], 1)
        self.assertEqual(artifact["summary"]["h2_players"], 1)
        self.assertEqual(artifact["summary"]["cross_domain_players"], 1)
        self.assertEqual(
            artifact["summary"]["operator_actions_required"],
            0,
        )
        domains = {
            row["capture_domain"]: row
            for row in artifact["players"][0]["capture_domains"]
        }
        self.assertEqual(domains["H1"]["selected_crops"], 2)
        self.assertEqual(domains["H2"]["selected_crops"], 1)
        self.assertTrue(artifact["safety"]["mutates_production_identity"] is False)


if __name__ == "__main__":
    unittest.main()
