from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.services.identity_reviewed_output_jobs import _log_render
from app.services.identity_reviewed_video import (
    RENDERER_VERSION,
    _ProgressEmitter,
    _RenderTimingProfile,
    _encode,
    render_reviewed_video,
)


class ReviewedVideoProfileTests(unittest.TestCase):
    def test_profile_starts_at_zero_and_accumulates(self) -> None:
        profile = _RenderTimingProfile()
        self.assertEqual(profile.decode_sec, 0)
        self.assertEqual(profile.as_dict()["raw_avi_bytes"], 0)
        self.assertEqual(RENDERER_VERSION, "reviewed_video:v6-profile")

        profile.decode_sec += 0.2
        profile.decode_sec += 0.3

        self.assertAlmostEqual(profile.decode_sec, 0.5)
        self.assertEqual(profile.average_timings_ms(0)["decode_avg_ms"], 0)
        self.assertAlmostEqual(profile.average_timings_ms(2)["decode_avg_ms"], 250)

    def test_profile_derived_values_are_safe(self) -> None:
        profile = _RenderTimingProfile(
            decode_sec=2,
            render_frames_wall_sec=1,
            raw_avi_bytes=10 * 1024 * 1024,
        )

        self.assertEqual(profile.render_other_sec, 0)
        self.assertIsNone(profile.raw_avi_write_mb_per_sec)

        profile.writer_write_sec = 2
        self.assertAlmostEqual(profile.raw_avi_write_mb_per_sec or 0, 5)

    def test_progress_log_includes_timing_averages_only_when_supplied(self) -> None:
        events: list[dict] = []
        profile = _RenderTimingProfile(
            decode_sec=0.2,
            identity_diagnostics_sec=0.1,
            draw_labels_sec=0.1,
            draw_minimap_sec=0.2,
            draw_hud_sec=0.1,
            writer_write_sec=1.0,
        )
        emitter = _ProgressEmitter(events.append)
        emitter.emit(
            "render_frames",
            processed_frames=2,
            total_frames=10,
            timing_profile=profile,
            force=True,
        )
        emitter.emit("validate_output", processed_frames=2, total_frames=2, force=True)

        with tempfile.TemporaryDirectory(), patch(
            "app.services.identity_reviewed_output_jobs.logger.info"
        ) as info:
            path = Path("/tmp/match")
            _log_render({}, path, events[0])
            timed_line = str(info.call_args.args[0])
            self.assertIn("decode_avg_ms=100.0", timed_line)
            self.assertIn("overlay_avg_ms=250.0", timed_line)
            self.assertIn("writer_avg_ms=500.0", timed_line)

            _log_render({}, path, events[1])
            untimed_line = str(info.call_args.args[0])
            self.assertNotIn("decode_avg_ms", untimed_line)
            self.assertNotIn("writer_avg_ms", untimed_line)

    def test_profile_summary_log_is_compact(self) -> None:
        profile = _RenderTimingProfile(
            decode_sec=1,
            identity_diagnostics_sec=2,
            draw_labels_sec=3,
            draw_minimap_sec=4,
            draw_hud_sec=5,
            writer_write_sec=6,
            render_frames_wall_sec=22,
            raw_avi_bytes=10 * 1024 * 1024,
            encode_mp4_sec=2,
            hash_source_sec=0.2,
            hash_output_sec=0.1,
            manifest_build_sec=0.01,
            manifest_write_sec=0.02,
            total_wall_sec=25,
        )
        event = {
            "stage": "performance_profile_summary",
            "frames": 100,
            "fps": 25,
            "resolution": [1920, 1080],
            "performance_profile": profile.as_dict(),
        }

        with patch("app.services.identity_reviewed_output_jobs.logger.info") as info:
            _log_render({"match_id": "match-1", "job_key": "abcdefghijk"}, Path("."), event)

        lines = [_formatted_log_line(call) for call in info.call_args_list]
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(line.startswith("[reviewed-render-profile]") for line in lines))
        self.assertIn("resolution=1920x1080", lines[0])
        self.assertIn("render_other=1.000s", lines[1])
        self.assertIn("raw_write_throughput=1.67MB/s", lines[2])
        self.assertIn("encode_fps=50.00", lines[3])

    def test_encode_stage_profile_log_is_emitted(self) -> None:
        event = {
            "stage": "performance_profile_stage",
            "profile_stage": "encode_mp4",
            "elapsed_sec": 12.345,
        }

        with patch("app.services.identity_reviewed_output_jobs.logger.info") as info:
            _log_render({"match_id": "match-1"}, Path("."), event)

        self.assertIn(
            "stage=encode_mp4 elapsed=12.345s",
            _formatted_log_line(info.call_args),
        )

    def test_render_persists_profile_without_changing_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "video.mp4"
            source.write_bytes(b"source")
            fourcc_calls: list[tuple[str, ...]] = []
            fake_cv2 = _fake_cv2(fourcc_calls)

            def fake_encode(raw: Path, output: Path, **_kwargs: object) -> None:
                self.assertTrue(raw.exists())
                output.write_bytes(b"encoded")

            rows = [
                {
                    "identity_status": "confirmed",
                    "canonical_player_id": "player-1",
                    "stable_anonymous_slot_id": "A1",
                    "display_label": "A1",
                    "bbox_xyxy": [1, 2, 3, 4],
                }
            ]
            with patch.dict(sys.modules, {"cv2": fake_cv2}), patch(
                "app.services.identity_reviewed_video._positions_by_frame",
                return_value={0: rows},
            ), patch(
                "app.services.identity_reviewed_video._load_optional",
                return_value={},
            ), patch(
                "app.services.identity_reviewed_video._draw_rows"
            ), patch(
                "app.services.identity_reviewed_video._hud"
            ), patch(
                "app.services.identity_reviewed_video._encode",
                side_effect=fake_encode,
            ):
                manifest = render_reviewed_video(
                    root,
                    {"semantic_digest": "snapshot"},
                    {"video_filename": source.name},
                    include_minimap=False,
                    include_ball=False,
                )

            persisted = json.loads(
                (root / "reviewed_video_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["performance_profile"], persisted["performance_profile"])
            self.assertEqual(manifest["renderer_version"], RENDERER_VERSION)
            self.assertEqual(manifest["schema_version"], "1.4.0")
            self.assertEqual(fourcc_calls, [("M", "J", "P", "G")])
            self.assertEqual(
                manifest["semantic_checks"],
                {
                    "frames_with_player_labels": 1,
                    "confirmed_labels_rendered": 1,
                    "fallback_labels_rendered": 0,
                    "minimap_frames_rendered": 0,
                    "ball_frames_rendered": 0,
                    "duplicate_stable_labels_rendered": 0,
                    "duplicate_canonical_players_rendered": 0,
                    "max_simultaneous_stable_labels": 1,
                },
            )
            self.assertGreater(manifest["performance_profile"]["raw_avi_bytes"], 0)
            self.assertGreater(manifest["performance_profile"]["manifest_write_sec"], 0)
            self.assertGreaterEqual(
                manifest["performance_profile"]["total_wall_sec"],
                manifest["performance_profile"]["manifest_write_sec"],
            )
            self.assertFalse((root / "reviewed_video.raw.avi").exists())

    def test_encode_keeps_current_ffmpeg_path(self) -> None:
        process = Mock()
        process.stdout = iter(["frame=1\n", "progress=end\n"])
        process.stderr = StringIO("")
        process.wait.return_value = 0

        with patch(
            "app.services.identity_reviewed_video.shutil.which",
            return_value="/usr/bin/ffmpeg",
        ), patch(
            "app.services.identity_reviewed_video.subprocess.Popen",
            return_value=process,
        ) as popen:
            _encode(Path("raw.avi"), Path("output.mp4"), total_frames=1)

        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-progress") + 1], "pipe:1")
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-preset") + 1], "veryfast")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("format=yuv420p", command)


def _formatted_log_line(call: object) -> str:
    args = call.args
    return str(args[0]) % tuple(args[1:]) if len(args) > 1 else str(args[0])


def _fake_cv2(fourcc_calls: list[tuple[str, ...]]) -> SimpleNamespace:
    class Capture:
        def __init__(self, _path: str) -> None:
            self.frame_available = True

        def isOpened(self) -> bool:
            return True

        def get(self, property_id: int) -> float:
            return {1: 25.0, 2: 80, 3: 60, 4: 1}[property_id]

        def read(self) -> tuple[bool, object | None]:
            if self.frame_available:
                self.frame_available = False
                return True, object()
            return False, None

        def release(self) -> None:
            pass

    class Writer:
        def __init__(
            self,
            path: str,
            _fourcc: str,
            _fps: float,
            _resolution: tuple[int, int],
        ) -> None:
            self.path = Path(path)

        def isOpened(self) -> bool:
            return True

        def write(self, _frame: object) -> None:
            self.path.write_bytes(b"raw-mjpeg")

        def release(self) -> None:
            pass

    def fourcc(*value: str) -> str:
        fourcc_calls.append(value)
        return "".join(value)

    return SimpleNamespace(
        CAP_PROP_FPS=1,
        CAP_PROP_FRAME_WIDTH=2,
        CAP_PROP_FRAME_HEIGHT=3,
        CAP_PROP_FRAME_COUNT=4,
        VideoCapture=Capture,
        VideoWriter=Writer,
        VideoWriter_fourcc=fourcc,
    )


if __name__ == "__main__":
    unittest.main()
