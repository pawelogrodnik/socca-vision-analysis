from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.services.chunked_analysis import (
    analyze_match_chunked_yolo,
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

    def test_chunked_camera_motion_uses_pitch_polygon_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            video_path = path / "video.mp4"
            video_path.write_bytes(b"not a real video in this unit test")
            pitch_points = [[0, 0], [100, 0], [100, 100], [0, 100]]
            (path / "pitch_config.json").write_text(
                json.dumps(
                    {
                        "image_points": pitch_points,
                        "pitch_dimensions_m": {"width_m": 30, "length_m": 47.4},
                    }
                ),
                encoding="utf-8",
            )
            (path / "match.json").write_text(json.dumps({"teams": []}), encoding="utf-8")
            payload = {
                "max_seconds": 1.0,
                "frame_stride": 1,
                "chunk_duration_sec": 1.0,
                "chunk_overlap_sec": 0.0,
                "camera_motion_compensation": True,
                "camera_motion_interval_sec": 0.5,
                "camera_motion_min_inlier_ratio": 0.6,
                "yolo_tracker": "bytetrack.yaml",
                "render_stable_overlay": False,
                "include_ball": False,
            }
            manifest = build_analysis_chunk_manifest(
                video_metadata={"fps": 30.0, "frame_count": 30, "duration_sec": 1.0},
                payload=payload,
            )
            manifest["parameters"]["camera_motion_compensation"] = True
            manifest["parameters"]["camera_motion_interval_sec"] = 0.5
            manifest["parameters"]["camera_motion_min_inlier_ratio"] = 0.6
            manifest["parameters"]["include_ball"] = False
            manifest["parameters"]["yolo_tracker"] = "bytetrack.yaml"
            manifest["chunks"][0]["status"] = "completed"
            chunk_dir = path / "analysis_chunks" / "chunk-0001"
            chunk_dir.mkdir(parents=True)
            (chunk_dir / "chunk_analysis_report.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
            (chunk_dir / "tracks.json").write_text(
                json.dumps(
                    [
                        {
                            "track_id": 1,
                            "positions": [
                                {
                                    "frame": 0,
                                    "time_sec": 0.0,
                                    "bbox_xyxy": [10, 10, 20, 40],
                                    "footpoint": [15, 40],
                                    "pitch_m": [4.0, 8.0],
                                    "confidence": 0.9,
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            from app.services.camera_motion import CameraMotionModel

            camera_motion = CameraMotionModel.disabled(fps=30.0, frame_count=30)
            stable_result = {
                "stable_players": {"summary": {"stable_players": 0}, "players": []},
                "artifacts": {"stable_players": "stable_players.json"},
                "refined_ball_tracks": None,
            }

            with patch("app.services.video.read_video_metadata", return_value={"fps": 30.0, "width": 100, "height": 100, "frame_count": 30, "duration_sec": 1.0}), patch(
                "app.services.chunked_analysis._load_or_create_real_manifest",
                return_value=manifest,
            ), patch(
                "app.services.chunked_analysis.collect_runtime_info",
                return_value={},
            ), patch(
                "app.services.chunked_analysis.resolve_yolo_device",
                return_value=None,
            ), patch(
                "app.services.camera_motion.build_camera_motion_model",
                return_value=camera_motion,
            ) as build_motion, patch(
                "app.services.stabilization.stabilize_match",
                return_value=stable_result,
            ):
                analyze_match_chunked_yolo(path, video_path, payload=payload)

            build_motion.assert_called_once()
            np.testing.assert_array_equal(
                build_motion.call_args.kwargs["reference_pitch_polygon"],
                np.asarray(pitch_points, dtype=np.float32),
            )


if __name__ == "__main__":
    unittest.main()
