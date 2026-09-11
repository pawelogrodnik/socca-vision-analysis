from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "shot_goldset_v1.json"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
VALID_OUTCOMES = {"goal", "on_target", "off_target", "blocked"}
REQUIRED_SHOT_FIELDS = {
    "id",
    "timestamp_sec",
    "timestamp_display",
    "timestamp_precision",
    "team",
    "player",
    "outcome",
    "confidence",
    "operator_notes",
}
REQUIRED_NEGATIVE_FIELDS = {
    "id",
    "timestamp_sec",
    "timestamp_display",
    "timestamp_precision",
    "team",
    "negative_type",
    "operator_notes",
}


class ShotGoldsetV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.goldset = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_frozen_schema_and_summary_invariants(self) -> None:
        shots = self.goldset["shots"]
        self.assertEqual(self.goldset["schema_version"], "shot-goldset:v1")
        self.assertEqual(self.goldset["scope"], "evaluation_only")
        self.assertEqual(len(shots), 31)
        self.assertEqual(len(self.goldset["hard_negatives"]), 14)
        self.assertEqual(self.goldset["uncertain"], [])
        self.assertEqual(Counter(shot["team"] for shot in shots), {"Corgi": 12, "Verisk": 19})
        self.assertEqual(Counter(shot["outcome"] for shot in shots), {
            "goal": 5,
            "on_target": 8,
            "blocked": 7,
            "off_target": 11,
        })
        self.assertEqual(
            Counter(shot["team"] for shot in shots if shot["outcome"] == "goal"),
            {"Corgi": 2, "Verisk": 3},
        )

    def test_manual_rows_have_valid_required_fields_and_stable_order(self) -> None:
        shots = self.goldset["shots"]
        negatives = self.goldset["hard_negatives"]
        self.assertEqual(len({row["id"] for row in shots + negatives}), len(shots) + len(negatives))
        self.assertEqual([row["timestamp_sec"] for row in shots], sorted(row["timestamp_sec"] for row in shots))
        self.assertEqual([row["timestamp_sec"] for row in negatives], sorted(row["timestamp_sec"] for row in negatives))

        for shot in shots:
            self.assertTrue(REQUIRED_SHOT_FIELDS <= shot.keys())
            self.assertIn(shot["outcome"], VALID_OUTCOMES)
            self.assertIn(shot["team"], {"Corgi", "Verisk"})
            self.assertIsInstance(shot["timestamp_sec"], int)
            self.assertGreaterEqual(shot["timestamp_sec"], 0)
            self.assertEqual(shot["timestamp_precision"], "approximate")
            self.assertIn(shot["player"], {None, "B01", "B02", "B03", "B04", "B05", "B07", "B08", "B13", "Krzysiek", "Mateusz", "Paweł", "Patryk", "Przemek"})
            self.assertNotEqual(shot["outcome"], "unknown")

        for negative in negatives:
            self.assertTrue(REQUIRED_NEGATIVE_FIELDS <= negative.keys())
            self.assertIn(negative["team"], {None, "Corgi", "Verisk"})
            self.assertIsInstance(negative["timestamp_sec"], int)
            self.assertGreaterEqual(negative["timestamp_sec"], 0)
            self.assertEqual(negative["timestamp_precision"], "approximate")
            self.assertTrue(negative["negative_type"])

    def test_factual_outcome_corrections_are_frozen_in_v1(self) -> None:
        by_timestamp = {shot["timestamp_display"]: shot["outcome"] for shot in self.goldset["shots"]}
        self.assertEqual(by_timestamp["03:43~"], "off_target")
        self.assertEqual(by_timestamp["07:40~"], "blocked")
        self.assertEqual(by_timestamp["22:48~"], "blocked")
        self.assertEqual(by_timestamp["23:21~"], "off_target")

    def test_production_runtime_does_not_reference_evaluation_fixture(self) -> None:
        runtime_sources = (BACKEND_ROOT / "app").rglob("*.py")
        for path in runtime_sources:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("shot_goldset_v1", source, path.as_posix())
            self.assertNotIn("shot-goldset:v1", source, path.as_posix())


if __name__ == "__main__":
    unittest.main()
