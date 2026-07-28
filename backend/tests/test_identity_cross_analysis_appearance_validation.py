from __future__ import annotations

import unittest

import numpy as np

from app.services.identity_cross_analysis_appearance_validation import _summary, rank_player_profiles


class CrossAnalysisAppearanceValidationTests(unittest.TestCase):
    def test_ranking_is_same_team_only_and_deterministic(self) -> None:
        profiles = {
            "a-pawel": np.asarray([1.0, 0.0], dtype=np.float32),
            "a-piotrek": np.asarray([0.8, 0.6], dtype=np.float32),
            "b-similar": np.asarray([1.0, 0.0], dtype=np.float32),
        }
        rows = rank_player_profiles(
            np.asarray([0.99, 0.01], dtype=np.float32),
            profiles,
            {"a-pawel": "A", "a-piotrek": "A", "b-similar": "B"},
            "A",
            top_k=3,
        )
        self.assertEqual([row["player_id"] for row in rows], ["a-pawel", "a-piotrek"])
        self.assertEqual([row["rank"] for row in rows], [1, 2])

    def test_tie_breaks_by_player_id(self) -> None:
        rows = rank_player_profiles(
            np.asarray([1.0, 0.0], dtype=np.float32),
            {"b": np.asarray([1.0, 0.0]), "a": np.asarray([1.0, 0.0])},
            {"a": "A", "b": "A"},
            "A",
            top_k=2,
        )
        self.assertEqual([row["player_id"] for row in rows], ["a", "b"])

    def test_summary_compares_ranking_with_same_team_random_baseline(self) -> None:
        profiles = [
            {"player_id": f"player-{index}", "team_label": "A", "status": "ready"}
            for index in range(4)
        ]
        rows = [
            {"status": "ranked", "team_label": "A", "top1_correct": index < 3, "top3_correct": True}
            for index in range(4)
        ]

        summary = _summary(
            rows,
            profiles,
            {"source": [np.asarray([1.0, 0.0], dtype=np.float32)]},
            {"target": [np.asarray([1.0, 0.0], dtype=np.float32)]},
            {"ranking_top_k": 3},
        )

        self.assertEqual(summary["same_team_random_baseline"]["top1_accuracy"], 0.25)
        self.assertEqual(summary["same_team_random_baseline"]["top_k_accuracy"], 0.75)
        self.assertEqual(summary["ranking_vs_random_baseline"]["top1_lift"], 3.0)
        self.assertTrue(summary["ranking_vs_random_baseline"]["outperforms_random_same_team_baseline"])


if __name__ == "__main__":
    unittest.main()
