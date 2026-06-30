from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.analysis_quality_smoke import build_quality_smoke_report


class AnalysisQualitySmokeTests(unittest.TestCase):
    def test_smoke_report_passes_clean_quality_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            match_path = Path(tmp) / "match-1"
            match_path.mkdir()
            (match_path / "match.json").write_text(json.dumps({"title": "Clean"}), encoding="utf-8")
            (match_path / "analysis_quality_report.json").write_text(
                json.dumps(
                    {
                        "score": 88.0,
                        "quality": "high",
                        "summary": {
                            "low_visible_rate": 0.05,
                            "ghost_bbox_count": 0,
                            "predicted_visible_boxes": 0,
                            "visible_avg": 13.7,
                        },
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )

            report = build_quality_smoke_report(Path(tmp), min_score=70)

            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["summary"]["passed"], 1)
            self.assertEqual(report["matches"][0]["title"], "Clean")

    def test_smoke_report_fails_missing_or_regressed_quality_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            missing.mkdir()
            regressed = Path(tmp) / "regressed"
            regressed.mkdir()
            (regressed / "analysis_quality_report.json").write_text(
                json.dumps(
                    {
                        "score": 51.0,
                        "quality": "low",
                        "summary": {
                            "low_visible_rate": 0.6,
                            "ghost_bbox_count": 2,
                            "predicted_visible_boxes": 4,
                        },
                        "warnings": ["bad tracking"],
                    }
                ),
                encoding="utf-8",
            )

            report = build_quality_smoke_report(Path(tmp), min_score=70)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"]["failed"], 2)
            failures = {row["match_id"]: row["failures"] for row in report["matches"]}
            self.assertIn("analysis_quality_report.json is missing", failures["missing"])
            self.assertTrue(any("score" in failure for failure in failures["regressed"]))


if __name__ == "__main__":
    unittest.main()
