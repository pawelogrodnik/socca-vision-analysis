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
    generate_reviewed_output,
    reviewed_output_status,
)
from app.services.video import resolve_match_video_path


class ReviewedOutputContractTests(unittest.TestCase):
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
