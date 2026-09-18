from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import evaluate_targeted_ball_gap_recovery as cli


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {item.relative_to(root).as_posix(): item.read_bytes() for item in sorted(root.rglob("*")) if item.is_file()}


def _candidate(frame: int, x: float) -> dict:
    return {"candidate_id": f"low-{frame}", "frame": frame, "time_sec": frame / 10, "position_m": [x, 0.0], "position_px": [x * 10, 100.0], "confidence": .2, "width_px": 10.0, "height_px": 10.0}


class TargetedBallGapRecoveryCliTests(unittest.TestCase):
    def test_experiment_reads_source_artifacts_without_mutating_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            matches = Path(temporary) / "matches"
            source = matches / "source"
            _write(source / "match.json", {"video": {"fps": 10}})
            _write(source / "ball_candidates.json", {"parameters": {"ball_conf": .03, "frame_stride": 1, "imgsz": 960, "device": "cpu"}})
            _write(source / "ball_tracks.json", {
                "parameters": {"max_link_speed_mps": 22.0, "min_start_conf": .08, "ball_selection_policy": "ball-selection:v1"},
                "positions": [
                    {"frame": 0, "time_sec": 0, "source": "detected", "candidate_id": "left", "position_m": [0, 0], "position_px": [0, 100], "confidence": .8},
                    {"frame": 1, "time_sec": .1, "source": "unknown", "candidate_id": None, "position_m": None, "position_px": None, "confidence": 0},
                    {"frame": 2, "time_sec": .2, "source": "detected", "candidate_id": "right", "position_m": [2, 0], "position_px": [20, 100], "confidence": .8},
                ],
            })
            before = _snapshot(source)
            evidence = {"frames": [{"frame": 1, "time_sec": .1, "raw_prediction_rows": [_candidate(1, 1)], "candidates": [_candidate(1, 1)], "rejected_candidates": []}]}
            with patch.object(cli.config, "MATCHES_DIR", matches), patch.object(cli, "_production_model_identity", return_value={"resolved_local_path": "/model.pt"}), patch.object(cli, "_production_run_parameters", return_value={"max_seconds": 0, "camera_motion_compensation": False, "camera_motion_interval_sec": .5, "camera_motion_min_inlier_ratio": .6}), patch.object(cli, "read_match_video_metadata", return_value={"fps": 10, "frame_count": 3}), patch.object(cli, "load_pitch_config", return_value=SimpleNamespace(calibration_frame_time_sec=0.0, polygon_np=[])), patch.object(cli, "resolve_match_video_path", return_value=Path(temporary) / "video.mp4"), patch.object(cli, "build_camera_motion_model", return_value=object()), patch.object(cli, "_load_yolo_model", return_value=object()), patch.object(cli, "_resolve_ball_model_classes", return_value={"source": "test"}), patch.object(cli, "collect_ball_candidates_range", return_value=evidence):
                result = cli.run_experiment(source_match_id="source", target_time_sec=.1, recovery_conf=.001, context_sec=.25, device=None)
            self.assertEqual(result["recovery"]["outcome"], "UNIQUE_BRIDGE")
            self.assertEqual(_snapshot(source), before)
