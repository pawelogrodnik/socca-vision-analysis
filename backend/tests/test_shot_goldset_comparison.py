from __future__ import annotations

import json
import unittest
from pathlib import Path

from evaluation.shot_goldset_comparison import compare_shot_goldsets


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class ShotGoldsetComparisonTests(unittest.TestCase):
    def test_v1_v2_comparison_is_deterministic_and_one_to_one(self) -> None:
        v1 = json.loads((FIXTURES_DIR / "shot_goldset_v1.json").read_text(encoding="utf-8"))
        v2 = json.loads((FIXTURES_DIR / "shot_goldset_v2.json").read_text(encoding="utf-8"))

        first = compare_shot_goldsets(v1, v2)
        second = compare_shot_goldsets(v1, v2)

        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["v1_shots"], 31)
        self.assertEqual(first["summary"]["v2_shots"], 33)
        self.assertEqual(len({row["v1_id"] for row in first["same_action_matches"]}), len(first["same_action_matches"]))
        self.assertEqual(len({row["v2_id"] for row in first["same_action_matches"]}), len(first["same_action_matches"]))

    def test_comparison_reports_team_outcome_and_timestamp_corrections(self) -> None:
        v1 = {"schema_version": "shot-goldset:v1", "shots": [
            {"id": "old", "timestamp_sec": 10.0, "team": "Corgi", "outcome": "goal", "player": "A"},
        ]}
        v2 = {"schema_version": "shot-goldset:v2", "shots": [
            {"id": "new", "timestamp_sec": 10.5, "team": "Verisk", "outcome": "blocked", "player": "B", "origin": "manual"},
        ]}

        report = compare_shot_goldsets(v1, v2)

        self.assertEqual(report["summary"]["same_action_pairs"], 1)
        self.assertEqual(report["summary"]["timestamp_materially_corrected"], 1)
        self.assertEqual(report["summary"]["team_corrected"], 1)
        self.assertEqual(report["summary"]["outcome_corrected"], 1)
        self.assertEqual(report["summary"]["player_attribution_changed"], 1)


if __name__ == "__main__":
    unittest.main()
