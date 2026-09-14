from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "shot_goldset_v2.json"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
VALID_OUTCOMES = {"goal", "on_target", "off_target", "blocked"}
REQUIRED_SHOT_FIELDS = {
    "id", "timestamp_sec", "timestamp_display", "timestamp_precision", "team",
    "player", "outcome", "origin", "provenance",
}


class ShotGoldsetV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.goldset = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_canonical_schema_count_order_and_authoritative_splits(self) -> None:
        shots = self.goldset["shots"]
        self.assertEqual(self.goldset["schema_version"], "shot-goldset:v2")
        self.assertEqual(self.goldset["scope"], "evaluation_only")
        self.assertEqual(self.goldset["source_authority"], "canonical_shot_review")
        self.assertEqual(len(shots), 33)
        self.assertEqual(len(self.goldset["hard_negatives"]), 14)
        self.assertEqual(self.goldset["uncertain"], [])
        self.assertEqual([row["timestamp_sec"] for row in shots], sorted(row["timestamp_sec"] for row in shots))
        self.assertEqual(len({row["id"] for row in shots}), 33)
        self.assertEqual(Counter(row["team"] for row in shots), {"Verisk": 20, "Corgi": 13})
        self.assertEqual(Counter(row["outcome"] for row in shots), {
            "off_target": 13, "on_target": 8, "blocked": 7, "goal": 5,
        })
        self.assertEqual(Counter(row["origin"] for row in shots), {
            "manual": 18, "accepted_suggestion": 15,
        })

    def test_v2_rows_carry_only_canonical_evaluation_fields(self) -> None:
        for shot in self.goldset["shots"]:
            self.assertTrue(REQUIRED_SHOT_FIELDS <= shot.keys())
            self.assertEqual(shot["timestamp_precision"], "canonical")
            self.assertIn(shot["team"], {"Corgi", "Verisk"})
            self.assertIn(shot["outcome"], VALID_OUTCOMES)
            self.assertIn(shot["origin"], {"manual", "accepted_suggestion"})
            self.assertEqual(shot["provenance"], "canonical_shot_review")
            self.assertNotIn("confidence", shot)
            self.assertNotIn("revision", shot)

    def test_production_runtime_does_not_reference_evaluation_goldsets(self) -> None:
        for path in (BACKEND_ROOT / "app").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("shot_goldset_v1", source, path.as_posix())
            self.assertNotIn("shot_goldset_v2", source, path.as_posix())
            self.assertNotIn("shot-goldset:v1", source, path.as_posix())
            self.assertNotIn("shot-goldset:v2", source, path.as_posix())


if __name__ == "__main__":
    unittest.main()
