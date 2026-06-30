from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.chunked_analysis import (
    build_analysis_chunk_manifest,
    mark_chunk_manifest_single_pass_completed,
    write_analysis_chunk_manifest,
)


class ChunkedAnalysisTests(unittest.TestCase):
    def test_chunk_manifest_splits_video_with_overlap(self) -> None:
        manifest = build_analysis_chunk_manifest(
            video_metadata={"fps": 30.0, "frame_count": 3600, "duration_sec": 120.0},
            payload={"max_seconds": 0, "frame_stride": 2, "chunk_duration_sec": 45, "chunk_overlap_sec": 5},
            job_id="job-1",
        )

        self.assertEqual(manifest["schema_version"], "0.1.0")
        self.assertEqual(manifest["job_id"], "job-1")
        self.assertEqual(manifest["summary"]["chunks"], 3)
        self.assertEqual(manifest["chunks"][0]["start_time_sec"], 0.0)
        self.assertEqual(manifest["chunks"][0]["end_time_sec"], 45.0)
        self.assertEqual(manifest["chunks"][1]["start_time_sec"], 40.0)
        self.assertEqual(manifest["chunks"][-1]["end_time_sec"], 120.0)

    def test_mark_chunk_manifest_single_pass_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            manifest = build_analysis_chunk_manifest(
                video_metadata={"fps": 25.0, "frame_count": 2500, "duration_sec": 100.0},
                payload={"max_seconds": 60, "frame_stride": 1, "chunk_duration_sec": 30, "chunk_overlap_sec": 2},
            )
            write_analysis_chunk_manifest(path, manifest)

            updated = mark_chunk_manifest_single_pass_completed(
                path,
                {"run_id": "run-1", "status": "completed", "frames_processed": 60},
            )

            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "single_pass_completed")
            self.assertEqual(updated["single_pass_run"]["run_id"], "run-1")


if __name__ == "__main__":
    unittest.main()
