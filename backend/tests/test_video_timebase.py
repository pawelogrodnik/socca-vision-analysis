from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app.services.video import VideoTimebaseError, inspect_video_timebase


class VideoTimebaseTests(unittest.TestCase):
    def test_decoded_cfr_timebase_prefers_decoded_frames_over_nominal_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "video.mp4"
            video.write_bytes(b"video")
            with (
                patch("app.services.video.read_video_metadata", return_value={"fps": 25.0, "frame_count": 110, "width": 320, "height": 180, "duration_sec": 4.4}),
                patch("app.services.video._decode_frame_count", return_value=100),
                patch("app.services.video._inspect_pts", return_value={"classification": "effectively_cfr", "frame_timestamp_count": 100, "first_timestamp_sec": 0.0, "last_timestamp_sec": 3.96, "estimated_media_span_sec": 4.0}),
            ):
                result = inspect_video_timebase(video)
        self.assertEqual(result["frame_count"], 100)
        self.assertEqual(result["nominal_frame_count"], 110)
        self.assertEqual(result["duration_sec"], 4.0)
        self.assertEqual(result["timing_mode"], "decoded_cfr_frame_index")

    def test_irregular_pts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "video.mp4"
            video.write_bytes(b"video")
            with (
                patch("app.services.video.read_video_metadata", return_value={"fps": 25.0, "frame_count": 100, "width": 320, "height": 180, "duration_sec": 4.0}),
                patch("app.services.video._decode_frame_count", return_value=100),
                patch("app.services.video._inspect_pts", return_value={"classification": "irregular_timestamps", "frame_timestamp_count": 100}),
            ):
                with self.assertRaisesRegex(VideoTimebaseError, "video_timestamp_discontinuous"):
                    inspect_video_timebase(video)
