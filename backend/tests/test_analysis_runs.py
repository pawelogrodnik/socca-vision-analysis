from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.analysis_runs import finalize_analysis_report


class AnalysisRunTests(unittest.TestCase):
    def test_finalize_analysis_report_creates_run_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            match_dir = Path(tmp)
            (match_dir / "pitch_config.json").write_text(
                json.dumps({"image_points": [[0, 0], [1, 0], [1, 1], [0, 1]]}),
                encoding="utf-8",
            )
            (match_dir / "tracks.json").write_text("[]", encoding="utf-8")
            (match_dir / "tracklets.json").write_text(
                json.dumps({"schema_version": "0.1.0", "tracklets": []}),
                encoding="utf-8",
            )

            report = finalize_analysis_report(
                match_dir,
                {
                    "run_id": "test-run",
                    "status": "completed",
                    "analysis_type": "yolo-ultralytics",
                    "parameters": {"frame_stride": 1},
                    "artifacts": {
                        "tracks_json": "tracks.json",
                        "tracklets": "tracklets.json",
                    },
                },
            )

            run_dir = match_dir / "analysis_runs" / "test-run"
            metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
            latest_report = json.loads((match_dir / "analysis_report.json").read_text(encoding="utf-8"))

            self.assertEqual(report["run_id"], "test-run")
            self.assertEqual(report["run_directory"], "analysis_runs/test-run")
            self.assertEqual(report["run_artifacts"]["tracklets"], "analysis_runs/test-run/tracklets.json")
            self.assertTrue((run_dir / "pitch_config.json").exists())
            self.assertTrue((run_dir / "tracks.json").exists())
            self.assertTrue((run_dir / "tracklets.json").exists())
            self.assertEqual(metadata["run_id"], "test-run")
            self.assertEqual(latest_report["run_id"], "test-run")


if __name__ == "__main__":
    unittest.main()
