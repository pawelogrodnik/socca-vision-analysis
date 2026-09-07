from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.video import (
    VIDEO_TIMEBASE_SCHEMA_VERSION,
    VideoTimebaseError,
    ensure_current_video_timebase,
    inspect_video_timebase,
    probe_media_duration,
)


def _pts(*, frames: int, fps: float) -> dict[str, float | int | str]:
    interval = 1.0 / fps
    return {
        "classification": "effectively_cfr",
        "frame_timestamp_count": frames,
        "first_timestamp_sec": 0.0,
        "last_timestamp_sec": (frames - 1) * interval,
        "estimated_media_span_sec": frames * interval,
        "typical_frame_interval_sec": interval,
        "pts_derived_fps": fps,
    }


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
class VideoTimebaseTests(unittest.TestCase):
    @staticmethod
    def _cfr_video(path: Path, *, color: str = "blue", fps: int = 25, frames: int = 50) -> None:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r={fps}", "-frames:v", str(frames), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            check=True,
        )

    def test_real_cfr_source_has_proven_pts_timebase_and_compatibility_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "video.mp4"
            self._cfr_video(video)
            result = inspect_video_timebase(video)
        self.assertEqual(result["source"], "decoded_cfr_timebase")
        self.assertEqual(result["timebase_schema_version"], VIDEO_TIMEBASE_SCHEMA_VERSION)
        self.assertEqual(result["frame_count"], 50)
        self.assertAlmostEqual(float(result["fps"]), 25.0, places=3)
        self.assertAlmostEqual(float(result["analysis_duration_sec"]), 2.0, places=3)
        self.assertAlmostEqual(float(result["media_duration_sec"]), 2.0, places=3)

    def test_decoded_cfr_timebase_prefers_decoded_frames_over_nominal_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "video.mp4"
            video.write_bytes(b"video")
            with (
                patch("app.services.video.read_video_metadata", return_value={"fps": 25.0, "frame_count": 110, "width": 320, "height": 180, "duration_sec": 4.4}),
                patch("app.services.video._decode_frame_count", return_value=100),
                patch("app.services.video._inspect_pts", return_value=_pts(frames=100, fps=25.0)),
            ):
                result = inspect_video_timebase(video)
        self.assertEqual(result["frame_count"], 100)
        self.assertEqual(result["nominal_frame_count"], 110)
        self.assertEqual(result["duration_sec"], 4.0)
        self.assertEqual(result["timing_mode"], "decoded_cfr_frame_index")
        self.assertEqual(result["source"], "decoded_cfr_timebase")

    def test_regular_pts_with_incompatible_nominal_fps_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "video.mp4"
            video.write_bytes(b"video")
            with (
                patch("app.services.video.read_video_metadata", return_value={"fps": 25.0, "frame_count": 2400, "width": 320, "height": 180, "duration_sec": 96.0}),
                patch("app.services.video._decode_frame_count", return_value=2400),
                patch("app.services.video._inspect_pts", return_value=_pts(frames=2400, fps=24.0)),
            ):
                with self.assertRaisesRegex(VideoTimebaseError, "video_fps_mismatch"):
                    inspect_video_timebase(video)

    def test_normal_rational_rates_are_accepted(self) -> None:
        for nominal, pts_fps in ((25.0, 25.0), (30.0, 30.0), (29.970, 30000 / 1001)):
            with self.subTest(nominal=nominal):
                with tempfile.TemporaryDirectory() as temporary:
                    video = Path(temporary) / "video.mp4"
                    video.write_bytes(b"video")
                    with (
                        patch("app.services.video.read_video_metadata", return_value={"fps": nominal, "frame_count": 300, "width": 320, "height": 180, "duration_sec": 10.0}),
                        patch("app.services.video._decode_frame_count", return_value=300),
                        patch("app.services.video._inspect_pts", return_value=_pts(frames=300, fps=pts_fps)),
                    ):
                        result = inspect_video_timebase(video)
                self.assertAlmostEqual(float(result["fps"]), pts_fps, places=4)

    def test_changed_bytes_with_identical_technical_properties_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "video.mp4"
            self._cfr_video(video, color="blue")
            first = inspect_video_timebase(video)
            self._cfr_video(video, color="red")
            with self.assertRaisesRegex(VideoTimebaseError, "source_video_changed_requires_reanalysis"):
                ensure_current_video_timebase(root, {"video_filename": "video.mp4", "video": first})

    def test_same_source_bytes_with_changed_mtime_remain_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "video.mp4"
            self._cfr_video(video)
            first = inspect_video_timebase(video)
            stat = video.stat()
            video.touch()
            if video.stat().st_mtime_ns == stat.st_mtime_ns:
                import os
                os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
            current = ensure_current_video_timebase(root, {"video_filename": "video.mp4", "video": first})
        self.assertEqual(current["source_fingerprint"]["sha256"], first["source_fingerprint"]["sha256"])

    def test_real_cfr_timebase_renders_a_reconciled_reviewed_video(self) -> None:
        from app.services.identity_reviewed_video import render_reviewed_video

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "video.mp4"
            self._cfr_video(source)
            timebase = inspect_video_timebase(source)
            manifest = render_reviewed_video(
                root,
                {"semantic_digest": "tiny-cfr", "tracklet_assignments": []},
                {"video_filename": source.name, "video": timebase},
                include_minimap=False,
                include_ball=False,
            )
            actual_duration = probe_media_duration(root / "reviewed_video.mp4")

        self.assertEqual(manifest["frames"], timebase["frame_count"])
        self.assertAlmostEqual(manifest["duration_sec"], timebase["analysis_duration_sec"], places=3)
        self.assertAlmostEqual(manifest["media_duration_sec"], actual_duration, places=3)

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
