from __future__ import annotations

import unittest

from app.services.contact_auto_review import apply_auto_contact_review, auto_review_contact_candidate


class ContactAutoReviewTests(unittest.TestCase):
    def test_auto_accepts_strong_multi_frame_contact(self) -> None:
        decision = auto_review_contact_candidate(
            {
                "stable_player_id": "A01",
                "detected_ball_frames": 4,
                "detected_player_frames": 3,
                "interpolated_player_frames": 1,
                "mean_confidence": 0.62,
                "min_distance_m": 0.42,
                "mean_distance_m": 0.74,
            }
        )

        self.assertEqual(decision["review_status"], "accepted")

    def test_auto_rejects_far_or_missing_contact(self) -> None:
        decision = auto_review_contact_candidate(
            {
                "stable_player_id": "A01",
                "detected_ball_frames": 1,
                "mean_confidence": 0.31,
                "min_distance_m": 3.1,
                "mean_distance_m": 3.5,
            }
        )

        self.assertEqual(decision["review_status"], "rejected")

    def test_auto_marks_plausible_but_weak_contact_uncertain(self) -> None:
        decision = auto_review_contact_candidate(
            {
                "stable_player_id": "A01",
                "detected_ball_frames": 1,
                "detected_player_frames": 1,
                "interpolated_player_frames": 2,
                "mean_confidence": 0.36,
                "min_distance_m": 1.2,
                "mean_distance_m": 1.6,
            }
        )

        self.assertEqual(decision["review_status"], "uncertain")

    def test_apply_auto_review_preserves_manual_override(self) -> None:
        document = {
            "candidates": [
                {
                    "candidate_id": "contact-0001",
                    "stable_player_id": "A01",
                    "detected_ball_frames": 4,
                    "mean_confidence": 0.62,
                    "min_distance_m": 0.42,
                    "mean_distance_m": 0.74,
                    "review_status": "rejected",
                    "status": "rejected",
                    "review_source": "manual",
                }
            ]
        }

        reviewed = apply_auto_contact_review(document)

        self.assertEqual(reviewed["candidates"][0]["review_status"], "rejected")
        self.assertEqual(reviewed["candidates"][0]["review_source"], "manual")


if __name__ == "__main__":
    unittest.main()
