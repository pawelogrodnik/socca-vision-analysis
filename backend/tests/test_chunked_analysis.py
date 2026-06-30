from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.chunked_analysis import (
    build_analysis_chunk_manifest,
    merge_completed_chunk_ball_observations,
    merge_completed_chunk_tracks,
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

    def test_chunk_manifest_allows_short_test_chunks(self) -> None:
        manifest = build_analysis_chunk_manifest(
            video_metadata={"fps": 25.0, "frame_count": 275, "duration_sec": 11.0},
            payload={"max_seconds": 0, "frame_stride": 1, "chunk_duration_sec": 3, "chunk_overlap_sec": 0.5},
            job_id="job-1",
        )

        self.assertEqual(manifest["summary"]["chunks"], 5)
        self.assertEqual(manifest["chunks"][0]["end_time_sec"], 3.0)
        self.assertEqual(manifest["chunks"][1]["start_time_sec"], 2.5)
        self.assertEqual(manifest["chunks"][-1]["end_time_sec"], 11.0)

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

    def test_merge_completed_chunk_tracks_trims_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            manifest = {
                "chunks": [
                    {"chunk_id": "chunk-0001", "index": 1, "end_time_sec": 3.0, "status": "completed"},
                    {"chunk_id": "chunk-0002", "index": 2, "end_time_sec": 5.0, "status": "completed"},
                ]
            }
            first_dir = path / "analysis_chunks" / "chunk-0001"
            second_dir = path / "analysis_chunks" / "chunk-0002"
            first_dir.mkdir(parents=True)
            second_dir.mkdir(parents=True)
            (first_dir / "tracks.json").write_text(
                """
                [
                  {
                    "track_id": 100001,
                    "positions": [
                      {"frame": 50, "time_sec": 2.0, "bbox_xyxy": [0,0,1,1]},
                      {"frame": 75, "time_sec": 3.0, "bbox_xyxy": [0,0,1,1]}
                    ]
                  }
                ]
                """,
                encoding="utf-8",
            )
            (second_dir / "tracks.json").write_text(
                """
                [
                  {
                    "track_id": 200001,
                    "positions": [
                      {"frame": 63, "time_sec": 2.52, "bbox_xyxy": [0,0,1,1]},
                      {"frame": 88, "time_sec": 3.52, "bbox_xyxy": [0,0,1,1]},
                      {"frame": 113, "time_sec": 4.52, "bbox_xyxy": [0,0,1,1]}
                    ]
                  }
                ]
                """,
                encoding="utf-8",
            )

            merged = merge_completed_chunk_tracks(path, manifest)

            self.assertEqual(len(merged), 2)
            second_positions = merged[1]["positions"]
            self.assertEqual([pos["time_sec"] for pos in second_positions], [3.52, 4.52])

    def test_merge_completed_chunk_ball_observations_trims_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            manifest = {
                "video": {"fps": 30.0},
                "chunks": [
                    {"chunk_id": "chunk-0001", "index": 1, "end_time_sec": 2.0, "status": "completed"},
                    {"chunk_id": "chunk-0002", "index": 2, "end_time_sec": 4.0, "status": "completed"},
                ],
            }
            first_dir = path / "analysis_chunks" / "chunk-0001"
            second_dir = path / "analysis_chunks" / "chunk-0002"
            first_dir.mkdir(parents=True)
            second_dir.mkdir(parents=True)
            (first_dir / "ball_observations.json").write_text(
                """
                {
                  "frames": [
                    {"frame": 30, "time_sec": 1.0, "candidates": []},
                    {"frame": 60, "time_sec": 2.0, "candidates": [{"candidate_id": "a"}]}
                  ],
                  "processed_frames": [30, 60],
                  "rejected_summary": {"outside_pitch": 1},
                  "parameters": {"frame_stride": 30},
                  "warnings": []
                }
                """,
                encoding="utf-8",
            )
            (second_dir / "ball_observations.json").write_text(
                """
                {
                  "frames": [
                    {"frame": 45, "time_sec": 1.5, "candidates": [{"candidate_id": "overlap"}]},
                    {"frame": 75, "time_sec": 2.5, "candidates": [{"candidate_id": "b"}]}
                  ],
                  "processed_frames": [45, 75],
                  "rejected_summary": {"too_small": 2},
                  "parameters": {"frame_stride": 30},
                  "warnings": ["low coverage"]
                }
                """,
                encoding="utf-8",
            )

            merged = merge_completed_chunk_ball_observations(path, manifest)

            self.assertEqual([frame["frame"] for frame in merged["frames"]], [30, 60, 75])
            self.assertEqual(merged["processed_frames"], [30, 60, 75])
            self.assertEqual(merged["rejected_summary"], {"outside_pitch": 1, "too_small": 2})
            self.assertEqual(merged["warnings"], ["low coverage"])


if __name__ == "__main__":
    unittest.main()
