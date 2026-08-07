from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.services.identity_minimap import reviewed_ball_pitch_point
from app.services.identity_reviewed_output_jobs import (
    ReviewedOutputBusyError,
    _log_render,
    _reusable_job,
    generate_reviewed_output,
    reviewed_output_status,
)
from app.services.identity_reviewed_video import _ProgressEmitter, _parse_ffmpeg_progress
from app.services.video import resolve_match_video_path


class ReviewedOutputContractTests(unittest.TestCase):
    def test_terminal_render_progress_has_safe_eta_and_throttles_frames(self) -> None:
        emitted: list[dict] = []
        emitter = _ProgressEmitter(emitted.append)
        emitter.emit("render_frames", processed_frames=0, total_frames=100, force=True)
        emitter.emit("render_frames", processed_frames=1, total_frames=100)
        self.assertEqual(len(emitted), 1)
        self.assertIsNone(emitted[0]["eta_sec"])
        emitter.last_at -= 6
        emitter.started_at -= 6
        emitter.emit("render_frames", processed_frames=60, total_frames=100)
        self.assertEqual(len(emitted), 2)
        self.assertIsNotNone(emitted[-1]["eta_sec"])

    def test_ffmpeg_progress_parser_and_terminal_log_are_compact(self) -> None:
        parsed = _parse_ffmpeg_progress(["frame=2080\n", "out_time_us=70000000\n", "progress=continue\n"])
        self.assertEqual(parsed["frame"], 2080)
        self.assertEqual(parsed["progress"], "continue")
        with tempfile.TemporaryDirectory() as temporary, patch("app.services.identity_reviewed_output_jobs.logger.info") as info:
            _log_render(
                {"job_key": "f81023a1long"},
                Path(temporary) / "461e4dd9",
                {"stage": "render_frames", "processed_frames": 1430, "total_frames": 2692, "progress": .531, "elapsed_sec": 252.4, "frames_per_sec": 5.67, "eta_sec": 222.5},
            )
            line = str(info.call_args.args[0])
            self.assertIn("[reviewed-render]", line)
            self.assertIn("match=461e4dd9", line)
            self.assertIn("job=f81023a1", line)
            self.assertIn("progress=53.1%", line)
    def test_match_video_resolver_supports_non_mp4_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "video.mov"
            video.touch()
            self.assertEqual(resolve_match_video_path(root), video.resolve())

    def test_ball_minimap_uses_detected_position_m_only(self) -> None:
        self.assertEqual(
            reviewed_ball_pitch_point({"source": "detected", "position_m": [1, 2]}),
            [1.0, 2.0],
        )
        self.assertIsNone(reviewed_ball_pitch_point({"source": "unknown", "position_m": [1, 2]}))
        self.assertIsNone(reviewed_ball_pitch_point({"source": "interpolated", "position_m": [1, 2]}))
        self.assertIsNone(reviewed_ball_pitch_point({"source": "detected", "pitch_m": [1, 2]}))

    @patch("app.services.identity_reviewed_output_jobs.threading.Thread")
    @patch("app.services.identity_reviewed_output_jobs.reviewed_source_video_digest", return_value="video-digest")
    def test_one_active_render_per_match_and_same_request_is_idempotent(self, _digest, thread_class) -> None:
        thread_class.return_value.start.return_value = None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = {"semantic_digest": "snapshot-1"}
            first = generate_reviewed_output(root, snapshot, {}, {"include_minimap": True})
            same = generate_reviewed_output(root, snapshot, {}, {"include_minimap": True})
            self.assertEqual(first["job_key"], same["job_key"])
            with self.assertRaises(ReviewedOutputBusyError):
                generate_reviewed_output(root, snapshot, {}, {"include_minimap": False})

    def test_abandoned_persisted_job_is_recovered_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reviewed_video_job.json").write_text(json.dumps({"job_key": "old", "status": "running"}))
            (root / "reviewed_video_job.lock").write_text(json.dumps({"job_key": "old", "pid": 99999999}))
            status = reviewed_output_status(root)
            self.assertEqual(status["status"], "failed")
            self.assertFalse((root / "reviewed_video_job.lock").exists())

    def test_completed_job_is_not_reused_when_video_digest_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reviewed_video.mp4").write_bytes(b"different output")
            job = {
                "job_key": "same",
                "status": "completed",
                "video_digest": "not-the-file-digest",
            }
            self.assertFalse(_reusable_job(job, "same", root))

    @patch("app.services.identity_reviewed_output_jobs.threading.Thread")
    @patch("app.services.identity_reviewed_output_jobs.reviewed_source_video_digest", return_value="video-digest")
    @patch("app.services.identity_reviewed_output_jobs.canonical_digest", return_value="same")
    def test_abandoned_same_key_request_starts_a_new_job(self, _key, _digest, thread_class) -> None:
        thread_class.return_value.start.return_value = None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = {"semantic_digest": "snapshot-1"}
            (root / "reviewed_video_job.json").write_text(
                json.dumps({"job_key": "same", "status": "running"}),
                encoding="utf-8",
            )
            second = generate_reviewed_output(root, snapshot, {}, {"include_minimap": True})
            self.assertEqual(second["job_key"], "same")
            self.assertEqual(thread_class.return_value.start.call_count, 1)

    def test_video_api_rejects_stale_digest_and_serves_current_digest(self) -> None:
        from app.main import get_match_reviewed_video

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reviewed_video.mp4").touch()
            snapshot = {"status": "partial_reviewed", "semantic_digest": "snapshot"}
            job = {"status": "completed", "source_snapshot_digest": "snapshot", "video_digest": "video"}
            with patch("app.main.match_dir", return_value=root), patch(
                "app.main.get_reviewed_identity_status", return_value=snapshot
            ), patch("app.main.reviewed_output_status", return_value=job):
                response = get_match_reviewed_video("m1", "video")
                self.assertEqual(response.headers["etag"], "video")
                with self.assertRaises(HTTPException) as raised:
                    get_match_reviewed_video("m1", "older-video")
                self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
