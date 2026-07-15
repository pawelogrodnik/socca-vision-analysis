from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.services.camera_motion import CameraMotionModel, CameraMotionSample
from app.services.post_yolo_reprocess import reprocess_match_from_artifacts, resolve_reprocess_video


def _write_reprocess_inputs(source_dir: Path) -> Path:
    source_dir.mkdir(parents=True, exist_ok=True)
    video_path = source_dir / "video.mp4"
    video_path.write_bytes(b"not a real video in this unit test")
    (source_dir / "pitch_config.json").write_text(
        json.dumps(
            {
                "image_points": [[0, 0], [100, 0], [100, 100], [0, 100]],
                "pitch_dimensions_m": {"width_m": 30, "length_m": 47.4},
            }
        ),
        encoding="utf-8",
    )
    (source_dir / "match.json").write_text(json.dumps({"teams": []}), encoding="utf-8")
    (source_dir / "tracks.json").write_text(
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
                        },
                        {
                            "frame": 3,
                            "time_sec": 0.1,
                            "bbox_xyxy": [13, 10, 23, 40],
                            "footpoint": [18, 40],
                            "pitch_m": [4.2, 8.1],
                            "confidence": 0.9,
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return video_path


def _stable_result(refined_ball_tracks=None, *, include_stable_overlay: bool = True) -> dict:
    artifacts = {
        "stable_players": "stable_players.json",
    }
    if include_stable_overlay:
        artifacts["stable_overlay_preview"] = "stable_overlay_preview.mp4"
    return {
        "stable_players": {"summary": {"stable_players": 1}, "players": []},
        "artifacts": artifacts,
        "refined_ball_tracks": refined_ball_tracks,
    }


def _translated_camera_motion_model() -> CameraMotionModel:
    current_to_reference = np.eye(3, dtype=np.float32)
    current_to_reference[0, 2] = 10.0
    reference_to_current = np.linalg.inv(current_to_reference).astype(np.float32)
    return CameraMotionModel(
        enabled=True,
        reference_frame=0,
        reference_time_sec=0.0,
        frame_count=30,
        fps=30.0,
        interval_sec=0.5,
        min_inlier_ratio=0.6,
        samples=[
            CameraMotionSample(
                frame=0,
                time_sec=0.0,
                status="ok",
                matrix_current_to_reference=current_to_reference.tolist(),
                matrix_reference_to_current=reference_to_current.tolist(),
                inlier_ratio=0.9,
                inliers=30,
                matches=40,
            )
        ],
    )


class PostYoloReprocessTests(unittest.TestCase):
    def test_resolve_video_from_benchmark_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            video_path = Path(tmp) / "clip.mp4"
            video_path.write_bytes(b"video")
            (source_dir / "benchmark_input.json").write_text(
                json.dumps({"video_path": str(video_path)}),
                encoding="utf-8",
            )

            self.assertEqual(resolve_reprocess_video(source_dir), video_path.resolve())

    def test_reprocess_uses_stored_tracks_without_yolo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            output_dir = Path(tmp) / "output"
            video_path = _write_reprocess_inputs(source_dir)
            match_phase_config = {
                "teams": {
                    "A": {"attacking_direction": "towards_y_min"},
                    "B": {"attacking_direction": "towards_y_max"},
                }
            }
            (source_dir / "match_phase_config.json").write_text(
                json.dumps(match_phase_config),
                encoding="utf-8",
            )
            metadata = {"fps": 30.0, "width": 100, "height": 100, "frame_count": 30, "duration_sec": 1.0}

            with patch("app.services.post_yolo_reprocess.read_video_metadata", return_value=metadata), patch(
                "app.services.post_yolo_reprocess.stabilize_match",
                return_value=_stable_result(),
            ) as stabilize:
                report = reprocess_match_from_artifacts(source_dir, video_path, output_dir=output_dir)

            self.assertEqual(report["analysis_type"], "post-yolo-reprocess")
            self.assertTrue(report["parameters"]["yolo_skipped"])
            self.assertEqual(report["frames_processed"], 2)
            self.assertTrue((output_dir / "tracks.json").exists())
            self.assertEqual(
                json.loads((output_dir / "match_phase_config.json").read_text(encoding="utf-8")),
                match_phase_config,
            )
            self.assertTrue((output_dir / "analysis_report.json").exists())
            stabilize.assert_called_once()
            self.assertEqual(stabilize.call_args.args[3][0]["track_id"], 1)
            self.assertIsNone(stabilize.call_args.kwargs["ball_tracks_doc"])

    def test_reprocess_can_skip_stable_overlay_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            output_dir = Path(tmp) / "output"
            video_path = _write_reprocess_inputs(source_dir)
            metadata = {"fps": 30.0, "width": 100, "height": 100, "frame_count": 30, "duration_sec": 1.0}

            with patch("app.services.post_yolo_reprocess.read_video_metadata", return_value=metadata), patch(
                "app.services.post_yolo_reprocess.stabilize_match",
                return_value=_stable_result(include_stable_overlay=False),
            ) as stabilize:
                report = reprocess_match_from_artifacts(
                    source_dir,
                    video_path,
                    output_dir=output_dir,
                    render_stable_overlay=False,
                )

            self.assertFalse(report["parameters"]["render_stable_overlay"])
            self.assertNotIn("stable_overlay_preview", report["artifacts"])
            self.assertFalse(stabilize.call_args.kwargs["render_stable_overlay"])
            self.assertFalse(stabilize.call_args.kwargs["defer_stable_overlay_render"])

    def test_reprocess_renders_final_stable_overlay_once_after_possession(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            output_dir = Path(tmp) / "output"
            video_path = _write_reprocess_inputs(source_dir)
            metadata = {"fps": 30.0, "width": 100, "height": 100, "frame_count": 30, "duration_sec": 1.0}
            ball_tracking = {
                "input_source": "ball_tracks",
                "ball_tracks": {"positions": [], "summary": {}},
                "ball_candidates": {"frames": [], "summary": {}},
                "ball_tracking_report": {"summary": {}},
                "ball_quality_report": {"summary": {}},
                "artifacts": {"ball_tracks": "ball_tracks.json"},
            }
            possession = {
                "possession_candidates": {"frames": [], "summary": {}},
                "pass_candidates": {"candidates": [], "summary": {}},
                "possession_report": {"summary": {}, "warnings": []},
                "artifacts": {"possession_candidates": "possession_candidates.json"},
            }

            with patch("app.services.post_yolo_reprocess.read_video_metadata", return_value=metadata), patch(
                "app.services.post_yolo_reprocess._load_or_rebuild_ball_tracking",
                return_value=ball_tracking,
            ), patch(
                "app.services.post_yolo_reprocess.stabilize_match",
                return_value=_stable_result(include_stable_overlay=False),
            ) as stabilize, patch(
                "app.services.post_yolo_reprocess._build_ball_possession_artifacts",
                return_value=possession,
            ) as build_possession, patch(
                "app.services.post_yolo_reprocess._render_final_stable_overlay",
                return_value={"stable_overlay_preview": "stable_overlay_preview.mp4"},
            ) as render:
                report = reprocess_match_from_artifacts(source_dir, video_path, output_dir=output_dir)

            self.assertIn("stable_overlay_preview", report["artifacts"])
            self.assertEqual(report["artifacts"]["stable_overlay_preview"], "stable_overlay_preview.mp4")
            self.assertTrue(stabilize.call_args.kwargs["render_stable_overlay"])
            self.assertTrue(stabilize.call_args.kwargs["defer_stable_overlay_render"])
            build_possession.assert_called_once()
            render.assert_called_once()
            self.assertIs(render.call_args.args[6], possession)

    def test_reprocess_recalibrates_tracks_with_camera_motion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            output_dir = Path(tmp) / "output"
            video_path = _write_reprocess_inputs(source_dir)
            metadata = {"fps": 30.0, "width": 100, "height": 100, "frame_count": 30, "duration_sec": 1.0}

            with patch("app.services.post_yolo_reprocess.read_video_metadata", return_value=metadata), patch(
                "app.services.post_yolo_reprocess.build_camera_motion_model",
                return_value=_translated_camera_motion_model(),
            ), patch(
                "app.services.post_yolo_reprocess.stabilize_match",
                return_value=_stable_result(),
            ) as stabilize:
                reprocess_match_from_artifacts(source_dir, video_path, output_dir=output_dir)

            recalibrated_tracks = stabilize.call_args.args[3]
            first_position = recalibrated_tracks[0]["positions"][0]
            self.assertEqual(first_position["calibrated_footpoint"], [25.0, 40.0])
            self.assertEqual(first_position["pitch_m_source"], "reprocess_camera_motion_calibrated_footpoint")
            self.assertAlmostEqual(first_position["pitch_m"][0], 7.5, places=2)
            self.assertEqual(first_position["play_area_status"], "inside_play")
            self.assertFalse(first_position["pitch_m_clamped"])

    def test_reprocess_rebuilds_ball_tracks_from_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            output_dir = Path(tmp) / "output"
            video_path = _write_reprocess_inputs(source_dir)
            (source_dir / "ball_candidates.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame": 0,
                                "time_sec": 0.0,
                                "candidates": [
                                    {
                                        "candidate_id": "ball-0",
                                        "frame": 0,
                                        "time_sec": 0.0,
                                        "bbox_xyxy": [40, 40, 44, 44],
                                        "position_px": [42.0, 42.0],
                                        "position_m": [12.0, 12.0],
                                        "confidence": 0.8,
                                        "source": "detected",
                                    }
                                ],
                            }
                        ],
                        "processed_frames": [0],
                        "summary": {
                            "candidate_count": 1,
                            "frames_with_candidates": 1,
                            "rejected_candidate_count": 0,
                            "rejected_summary": {},
                        },
                        "parameters": {"max_link_speed_mps": 22.0},
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )
            metadata = {"fps": 30.0, "width": 100, "height": 100, "frame_count": 30, "duration_sec": 1.0}
            captured_ball_docs: list[dict] = []

            def fake_stabilize(*args, **kwargs):
                ball_doc = kwargs["ball_tracks_doc"]
                captured_ball_docs.append(ball_doc)
                return _stable_result(refined_ball_tracks=ball_doc)

            with patch("app.services.post_yolo_reprocess.read_video_metadata", return_value=metadata), patch(
                "app.services.post_yolo_reprocess.stabilize_match",
                side_effect=fake_stabilize,
            ):
                report = reprocess_match_from_artifacts(source_dir, video_path, output_dir=output_dir)

            self.assertEqual(report["parameters"]["ball_input"], "ball_candidates")
            self.assertEqual(captured_ball_docs[0]["positions"][0]["source"], "detected")
            self.assertTrue((output_dir / "ball_tracks.json").exists())
            self.assertTrue((output_dir / "ball_quality_report.json").exists())


if __name__ == "__main__":
    unittest.main()
