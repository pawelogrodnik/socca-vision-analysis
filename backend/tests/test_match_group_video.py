from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_group_video import (
    COMBINED_VIDEO_FILENAME,
    VIDEO_DURATION_TOLERANCE_SEC,
    VIDEO_MANIFEST_FILENAME,
    generate_match_group_video,
    get_match_group_video_status,
)
from app.services.match_groups import create_match_group, delete_match_group, update_match_group
from app.services.published_video import PUBLISHED_VIDEO_ARTIFACT, PUBLISHED_VIDEO_DESCRIPTOR_FILENAME, sha256_file


class MatchGroupVideoTests(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("ORLIK_RUN_FFMPEG_INTEGRATION") == "1" and shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "set ORLIK_RUN_FFMPEG_INTEGRATION=1 with ffmpeg and ffprobe available",
    )
    def test_ffmpeg_combines_small_final_reviewed_videos(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=1, payload=b"")
            self._source(root, "published-b", "b", duration=1, payload=b"")
            for published_id, color in (("published-a", "blue"), ("published-b", "red")):
                video = root / "published" / published_id / PUBLISHED_VIDEO_ARTIFACT
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d=1", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                descriptor_path = video.with_name(PUBLISHED_VIDEO_DESCRIPTOR_FILENAME)
                descriptor = self._load(descriptor_path)
                descriptor["semantic_digest"] = sha256_file(video)
                descriptor["width"] = 320
                descriptor["height"] = 180
                descriptor["descriptor_semantic_digest"] = canonical_json_sha256({
                    key: value for key, value in descriptor.items() if key != "descriptor_semantic_digest"
                })
                self._write(descriptor_path, descriptor)

            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            status = generate_match_group_video(group["group_id"])

            self.assertEqual(status["status"], "ready")
            manifest = self._load(root / "groups" / group["group_id"] / VIDEO_MANIFEST_FILENAME)
            self.assertEqual(manifest["observability"]["generation_mode"], "stream_copy")
            self.assertAlmostEqual(manifest["output"]["duration_sec"], 2.0, delta=VIDEO_DURATION_TOLERANCE_SEC)

    def test_generation_uses_persisted_member_order_and_closes_logical_timeline(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=20, payload=b"B")
            group = create_match_group(member_published_ids=["published-b", "published-a"], metadata={"title": "Ordered"})
            calls: list[list[Path]] = []

            def concat(paths: list[Path], output: Path, *, copy_streams: bool) -> None:
                calls.append(paths)
                output.write_bytes(b"combined")

            def probe(path: Path) -> dict[str, object]:
                duration = 30.0 if path.name == COMBINED_VIDEO_FILENAME else (20.0 if path.read_bytes() == b"B" else 10.0)
                return {"codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720, "fps": 25.0, "duration_sec": duration, "audio": False}

            with patch("app.services.match_group_video._concat", side_effect=concat), patch("app.services.match_group_video._probe", side_effect=probe):
                status = generate_match_group_video(group["group_id"])

            self.assertEqual(status["status"], "ready")
            self.assertEqual([path.read_bytes() for path in calls[0]], [b"B", b"A"])
            manifest = self._load(root / "groups" / group["group_id"] / VIDEO_MANIFEST_FILENAME)
            self.assertEqual([(row["logical_start_sec"], row["logical_end_sec"]) for row in manifest["members"]], [(0.0, 20.0), (20.0, 30.0)])
            self.assertEqual(manifest["output"]["duration_sec"], 30.0)
            self.assertEqual((root / "published" / "published-a" / PUBLISHED_VIDEO_ARTIFACT).read_bytes(), b"A")

    def test_missing_or_changed_source_video_fails_closed_and_marks_previous_video_stale(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})

            def concat(paths: list[Path], output: Path, *, copy_streams: bool) -> None:
                output.write_bytes(b"combined")

            def probe(path: Path) -> dict[str, object]:
                duration = 20.0 if path.name == COMBINED_VIDEO_FILENAME else 10.0
                return {"codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720, "fps": 25.0, "duration_sec": duration, "audio": False}

            with patch("app.services.match_group_video._concat", side_effect=concat), patch("app.services.match_group_video._probe", side_effect=probe):
                generate_match_group_video(group["group_id"])
            output = root / "groups" / group["group_id"] / COMBINED_VIDEO_FILENAME
            before = output.read_bytes()
            (root / "published" / "published-b" / PUBLISHED_VIDEO_ARTIFACT).write_bytes(b"changed")
            self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "stale")
            self.assertEqual(output.read_bytes(), before)

    def test_member_order_change_stales_video_but_metadata_change_does_not(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={"title": "A"})
            def concat(paths: list[Path], output: Path, *, copy_streams: bool) -> None: output.write_bytes(b"combined")
            def probe(path: Path) -> dict[str, object]: return {"codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720, "fps": 25.0, "duration_sec": 20.0 if path.name == COMBINED_VIDEO_FILENAME else 10.0, "audio": False}
            with patch("app.services.match_group_video._concat", side_effect=concat), patch("app.services.match_group_video._probe", side_effect=probe):
                generate_match_group_video(group["group_id"])
            update_match_group(group["group_id"], member_published_ids=["published-a", "published-b"], metadata={"title": "Renamed"})
            self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "ready")
            update_match_group(group["group_id"], member_published_ids=["published-b", "published-a"], metadata={"title": "Renamed"})
            self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "stale")

    def test_failed_regeneration_keeps_the_previous_coherent_video(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})

            def concat(paths: list[Path], output: Path, *, copy_streams: bool) -> None:
                output.write_bytes(b"coherent")

            def probe(path: Path) -> dict[str, object]:
                return {"codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720, "fps": 25.0, "duration_sec": 20.0 if path.name == COMBINED_VIDEO_FILENAME else 10.0, "audio": False}

            with patch("app.services.match_group_video._concat", side_effect=concat), patch("app.services.match_group_video._probe", side_effect=probe):
                generate_match_group_video(group["group_id"])
            output = root / "groups" / group["group_id"] / COMBINED_VIDEO_FILENAME
            before = output.read_bytes()
            with (
                patch("app.services.match_group_video._concat", side_effect=RuntimeError("ffmpeg failed")),
                patch("app.services.match_group_video._probe", side_effect=probe),
                self.assertRaises(RuntimeError),
            ):
                generate_match_group_video(group["group_id"])

            self.assertEqual(output.read_bytes(), before)
            self.assertTrue(self._load(root / "groups" / group["group_id"] / "video_status.json")["previous_coherent_video"])

    def test_deleting_a_group_removes_only_its_derived_video(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            group_video = root / "groups" / group["group_id"] / COMBINED_VIDEO_FILENAME
            group_video.write_bytes(b"derived")

            delete_match_group(group["group_id"])

            self.assertFalse(group_video.exists())
            self.assertEqual((root / "published" / "published-a" / PUBLISHED_VIDEO_ARTIFACT).read_bytes(), b"A")
            self.assertEqual((root / "published" / "published-b" / PUBLISHED_VIDEO_ARTIFACT).read_bytes(), b"B")

    def _source(self, root: Path, published_id: str, source_match_id: str, *, duration: float, payload: bytes) -> None:
        directory = root / "published" / published_id
        directory.mkdir(parents=True)
        public = {"schema_version": "0.1.0", "report_type": "public_match_report", "id": published_id, "source_match_id": source_match_id}
        public_digest = canonical_json_sha256(public)
        aggregate = {
            "schema_version": "1.0.0", "aggregation_policy_version": "1.0.0",
            "source": {
                "source_match_id": source_match_id,
                "published_id": published_id,
                "public_report_semantic_digest": public_digest,
                "reviewed_identity_digest": f"reviewed-{source_match_id}",
            },
            "timing": {"analyzed_duration_sec": duration, "timeline_span_sec": duration, "mapping": "ordered_sequential_source_durations"},
            "teams": [{"team_id": "a"}, {"team_id": "b"}], "players": [], "identity_coverage": {}, "ball": {}, "timelines": {}, "spatial": {}, "metric_readiness": {},
        }
        aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(aggregate)
        self._write(directory / "public_report.json", public)
        self._write(directory / "aggregate_inputs.json", aggregate)
        video = directory / PUBLISHED_VIDEO_ARTIFACT
        video.write_bytes(payload)
        descriptor = {"schema_version": "1.0.0", "status": "available", "artifact": PUBLISHED_VIDEO_ARTIFACT, "semantic_digest": sha256_file(video), "duration_sec": duration, "width": 1280, "height": 720, "fps": 25.0, "codec": "h264", "pix_fmt": "yuv420p", "source_public_report_semantic_digest": public_digest}
        descriptor["descriptor_semantic_digest"] = canonical_json_sha256(descriptor)
        self._write(directory / PUBLISHED_VIDEO_DESCRIPTOR_FILENAME, descriptor)

    def _store(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        patches = [
            patch("app.services.match_groups.PUBLISHED_MATCHES_DIR", root / "published"), patch("app.services.match_groups.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_video.PUBLISHED_MATCHES_DIR", root / "published"), patch("app.services.match_group_video.MATCH_GROUPS_DIR", root / "groups"),
        ]
        class Context:
            def __enter__(self) -> Path:
                for item in patches: item.__enter__()
                return root
            def __exit__(self, *args: object) -> None:
                for item in reversed(patches): item.__exit__(*args)
                temporary.cleanup()
        return Context()

    @staticmethod
    def _write(path: Path, document: dict[str, object]) -> None:
        path.write_text(json.dumps(document), encoding="utf-8")

    @staticmethod
    def _load(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))
