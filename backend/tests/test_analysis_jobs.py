from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.services.analysis_jobs import list_analysis_jobs, load_analysis_job, start_analysis_job


class AnalysisJobsTests(unittest.TestCase):
    def test_background_job_persists_completed_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            matches_dir = Path(tmp)
            match_path = matches_dir / "match-1"
            match_path.mkdir()

            def runner(job_id, update):
                update("test", 50, f"running {job_id}", None)
                return {"status": "completed", "analysis_type": "test", "run_id": "run-1"}

            job = start_analysis_job(
                match_id="match-1",
                match_path=match_path,
                payload={"adapter": "test"},
                runner=runner,
            )

            loaded = job
            for _ in range(30):
                loaded = load_analysis_job(matches_dir, job["job_id"])
                if loaded["status"] == "completed":
                    break
                time.sleep(0.05)

            self.assertEqual(loaded["status"], "completed")
            self.assertEqual(loaded["result"]["run_id"], "run-1")
            self.assertEqual(list_analysis_jobs(match_path)[0]["job_id"], job["job_id"])


if __name__ == "__main__":
    unittest.main()
