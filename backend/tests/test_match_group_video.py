from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.services.match_group_video as match_group_video
from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_group_video import (
    COMBINED_VIDEO_FILENAME,
    CURRENT_GENERATION_FILENAME,
    VIDEO_DURATION_TOLERANCE_SEC,
    VIDEO_JOB_FILENAME,
    VIDEO_LOCK_FILENAME,
    VIDEO_MANIFEST_FILENAME,
    MatchGroupVideoError,
    _acquire_lock,
    _active_job_keys,
    _active_token,
    _group_dir,
    _write as write_video_document,
    combined_video_path,
    generate_match_group_video,
    get_match_group_video_status,
    submit_match_group_video_generation,
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
            manifest = get_match_group_video_status(group["group_id"])["manifest"]
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
            manifest = get_match_group_video_status(group["group_id"])["manifest"]
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
            output = combined_video_path(group["group_id"])
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
            output = combined_video_path(group["group_id"])
            before = output.read_bytes()
            with (
                patch("app.services.match_group_video._concat", side_effect=RuntimeError("ffmpeg failed")),
                patch("app.services.match_group_video._probe", side_effect=probe),
                self.assertRaises(RuntimeError),
            ):
                generate_match_group_video(group["group_id"])

            self.assertEqual(combined_video_path(group["group_id"]).read_bytes(), before)
            status = get_match_group_video_status(group["group_id"])
            self.assertEqual(status["status"], "ready")
            self.assertEqual(status["last_attempt"]["status"], "failed")

    def test_deleting_a_group_removes_only_its_derived_video(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            group_video = root / "groups" / group["group_id"] / "video-generations" / "derived" / COMBINED_VIDEO_FILENAME
            group_video.parent.mkdir(parents=True)
            group_video.write_bytes(b"derived")

            delete_match_group(group["group_id"])

            self.assertFalse(group_video.exists())
            self.assertEqual((root / "published" / "published-a" / PUBLISHED_VIDEO_ARTIFACT).read_bytes(), b"A")
            self.assertEqual((root / "published" / "published-b" / PUBLISHED_VIDEO_ARTIFACT).read_bytes(), b"B")

    def test_dead_persisted_generating_owner_becomes_interrupted_and_can_retry(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            group_dir = root / "groups" / group["group_id"]
            self._write(group_dir / VIDEO_JOB_FILENAME, {"status": "generating", "job_key": "dead-job", "owner_pid": 999999})
            self._write(group_dir / VIDEO_LOCK_FILENAME, {"pid": 999999, "job_key": "dead-job"})

            status = get_match_group_video_status(group["group_id"])

            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["reason"], "video_generation_interrupted")
            with patch("app.services.match_group_video._background_generate") as run:
                retry = submit_match_group_video_generation(group["group_id"])
            self.assertEqual(retry["status"], "generating")
            run.assert_called_once()
            _active_job_keys.clear()

    def test_live_durable_owner_prevents_a_second_background_submission(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            with patch("app.services.match_group_video._background_generate") as run:
                first = submit_match_group_video_generation(group["group_id"])
                second = submit_match_group_video_generation(group["group_id"])
            self.assertEqual(first["status"], "generating")
            self.assertEqual(second["status"], "generating")
            run.assert_called_once()
            _active_job_keys.clear()

    def test_persisted_lock_allows_only_one_service_owner(self) -> None:
        with self._store() as root:
            lock = root / "groups" / "group" / VIDEO_LOCK_FILENAME
            owner = {"pid": os.getpid(), "job_key": "worker-one"}
            _active_job_keys.add(_active_token(lock.parent, "worker-one"))
            self.assertTrue(_acquire_lock(lock, owner))
            self.assertFalse(_acquire_lock(lock, {"pid": os.getpid(), "job_key": "worker-two"}))
            _active_job_keys.clear()

    def test_pointer_switch_failure_keeps_the_old_coherent_generation(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            self._generate_with_fake_media(group["group_id"], b"old")
            old_video = combined_video_path(group["group_id"])
            old_bytes = old_video.read_bytes()
            old_manifest = get_match_group_video_status(group["group_id"])["manifest"]
            original_write = write_video_document

            def fail_pointer(path: Path, document: dict[str, object]) -> None:
                if path.name == CURRENT_GENERATION_FILENAME:
                    raise OSError("injected pointer switch failure")
                original_write(path, document)

            with patch("app.services.match_group_video._write", side_effect=fail_pointer), self.assertRaises(OSError):
                self._generate_with_fake_media(group["group_id"], b"new")
            status = get_match_group_video_status(group["group_id"])
            self.assertEqual(status["status"], "ready")
            self.assertEqual(combined_video_path(group["group_id"]).read_bytes(), old_bytes)
            self.assertEqual(status["manifest"]["output"]["semantic_digest"], old_manifest["output"]["semantic_digest"])

    def test_first_generation_pointer_failure_serves_no_partial_video_and_can_retry(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            original_write = write_video_document

            def fail_pointer(path: Path, document: dict[str, object]) -> None:
                if path.name == CURRENT_GENERATION_FILENAME:
                    raise OSError("injected pointer switch failure")
                original_write(path, document)

            with patch("app.services.match_group_video._write", side_effect=fail_pointer), self.assertRaises(OSError):
                self._generate_with_fake_media(group["group_id"], b"new")
            self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "failed")
            with self.assertRaises(FileNotFoundError):
                combined_video_path(group["group_id"])
            self._generate_with_fake_media(group["group_id"], b"retry")
            self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "ready")

    def test_post_pointer_job_cleanup_failure_keeps_new_current_generation(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            self._generate_with_fake_media(group["group_id"], b"old")
            old_video = combined_video_path(group["group_id"])
            original_unlink = Path.unlink

            def fail_job_cleanup(path: Path, *, missing_ok: bool = False) -> None:
                if path.name == VIDEO_JOB_FILENAME:
                    raise OSError("injected post-commit cleanup failure")
                original_unlink(path, missing_ok=missing_ok)

            with patch.object(Path, "unlink", new=fail_job_cleanup):
                self._generate_with_fake_media(group["group_id"], b"new")

            current_video = combined_video_path(group["group_id"])
            status = get_match_group_video_status(group["group_id"])
            self.assertNotEqual(current_video, old_video)
            self.assertEqual(current_video.read_bytes(), b"new")
            self.assertEqual(status["status"], "ready")
            self.assertEqual(status["manifest"]["output"]["semantic_digest"], sha256_file(current_video))
            self.assertEqual(status["last_attempt"]["status"], "completed")

    def test_first_generation_post_pointer_job_cleanup_failure_remains_coherent(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            original_unlink = Path.unlink

            def fail_job_cleanup(path: Path, *, missing_ok: bool = False) -> None:
                if path.name == VIDEO_JOB_FILENAME:
                    raise OSError("injected post-commit cleanup failure")
                original_unlink(path, missing_ok=missing_ok)

            with patch.object(Path, "unlink", new=fail_job_cleanup):
                self._generate_with_fake_media(group["group_id"], b"first")

            current_video = combined_video_path(group["group_id"])
            status = get_match_group_video_status(group["group_id"])
            self.assertEqual(current_video.read_bytes(), b"first")
            self.assertEqual(status["status"], "ready")
            self.assertEqual(status["manifest"]["output"]["semantic_digest"], sha256_file(current_video))

    def test_repeated_unchanged_status_reads_do_not_rehash_full_media(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            self._generate_with_fake_media(group["group_id"], b"combined")

            with (
                patch("app.services.match_group_video.sha256_file", side_effect=AssertionError("status must not hash combined media")),
                patch("app.services.published_video.sha256_file", side_effect=AssertionError("status must not hash source media")),
            ):
                self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "ready")
                self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "ready")

    def test_source_fingerprint_change_fails_closed_then_generation_rehashes(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            self._generate_with_fake_media(group["group_id"], b"combined")
            (root / "published" / "published-b" / PUBLISHED_VIDEO_ARTIFACT).write_bytes(b"changed-source")

            self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "stale")
            with self.assertRaises(MatchGroupVideoError) as failure:
                self._generate_with_fake_media(group["group_id"], b"not-used")
            self.assertEqual(failure.exception.code, "unavailable_source_video")

    def test_normalization_work_files_are_removed_before_generation_is_committed(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})

            def normalize(path: Path, work_dir: Path, index: int, probes: list[dict[str, object]]) -> Path:
                normalized = work_dir / f"normalized-{index}.mp4"
                normalized.write_bytes(path.read_bytes())
                return normalized

            def concat(paths: list[Path], output: Path, *, copy_streams: bool) -> None:
                (output.parent / ".work" / "concat.txt").write_text("temporary", encoding="utf-8")
                output.write_bytes(b"combined")

            def probe(path: Path) -> dict[str, object]:
                if path.name == COMBINED_VIDEO_FILENAME:
                    return {"codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720, "fps": 25.0, "duration_sec": 20.0, "audio": False}
                width = 1280 if path.parent.name == "published-a" else 640
                return {"codec": "h264", "pix_fmt": "yuv420p", "width": width, "height": 720, "fps": 25.0, "duration_sec": 10.0, "audio": False}

            with (
                patch("app.services.match_group_video._stream_copy_compatible", return_value=False),
                patch("app.services.match_group_video._normalize", side_effect=normalize),
                patch("app.services.match_group_video._concat", side_effect=concat),
                patch("app.services.match_group_video._probe", side_effect=probe),
            ):
                generate_match_group_video(group["group_id"])

            generation_dir = combined_video_path(group["group_id"]).parent
            self.assertEqual(sorted(path.name for path in generation_dir.iterdir()), [COMBINED_VIDEO_FILENAME, VIDEO_MANIFEST_FILENAME])

    def test_successful_regeneration_retains_only_the_new_current_generation(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            self._generate_with_fake_media(group["group_id"], b"old")
            old_generation = combined_video_path(group["group_id"]).parent

            self._generate_with_fake_media(group["group_id"], b"new")

            current = combined_video_path(group["group_id"])
            generations_root = current.parent.parent
            self.assertEqual(current.read_bytes(), b"new")
            self.assertFalse(old_generation.exists())
            self.assertEqual([path.name for path in generations_root.iterdir() if path.is_dir()], [current.parent.name])

    def test_superseded_generation_cleanup_failure_cannot_rollback_new_current_video(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            self._generate_with_fake_media(group["group_id"], b"old")
            old_generation = combined_video_path(group["group_id"]).parent
            original_remove = shutil.rmtree

            def fail_old_generation(path: str | Path, *args: object, **kwargs: object) -> None:
                if Path(path) == old_generation:
                    raise OSError("injected superseded cleanup failure")
                original_remove(path, *args, **kwargs)

            with patch("app.services.match_group_video.shutil.rmtree", side_effect=fail_old_generation):
                self._generate_with_fake_media(group["group_id"], b"new")

            current = combined_video_path(group["group_id"])
            status = get_match_group_video_status(group["group_id"])
            self.assertEqual(current.read_bytes(), b"new")
            self.assertEqual(status["status"], "ready")
            self.assertTrue(old_generation.exists())
            self.assertIn("superseded generation cleanup", status["last_attempt"]["cleanup_warning"])

    def test_source_change_during_render_keeps_previous_generation_current(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            self._generate_with_fake_media(group["group_id"], b"old")
            old_video = combined_video_path(group["group_id"])
            original_validate = match_group_video._validate_pre_commit

            def mutate_source_then_validate(*args: object) -> None:
                (root / "published" / "published-b" / PUBLISHED_VIDEO_ARTIFACT).write_bytes(b"source-changed-during-render")
                original_validate(*args)

            with patch("app.services.match_group_video._validate_pre_commit", side_effect=mutate_source_then_validate), self.assertRaises(MatchGroupVideoError) as failure:
                self._generate_with_fake_media(group["group_id"], b"candidate")

            self.assertEqual(failure.exception.code, "source_video_generation_changed")
            self.assertEqual(combined_video_path(group["group_id"]), old_video)
            self.assertEqual(old_video.read_bytes(), b"old")
            self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "stale")

    def test_group_order_change_during_render_keeps_previous_generation_current(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={})
            self._generate_with_fake_media(group["group_id"], b"old")
            old_video = combined_video_path(group["group_id"])
            original_validate = match_group_video._validate_pre_commit

            def reorder_then_validate(*args: object) -> None:
                update_match_group(group["group_id"], member_published_ids=["published-b", "published-a"], metadata={})
                original_validate(*args)

            with patch("app.services.match_group_video._validate_pre_commit", side_effect=reorder_then_validate), self.assertRaises(MatchGroupVideoError) as failure:
                self._generate_with_fake_media(group["group_id"], b"candidate")

            self.assertEqual(failure.exception.code, "match_group_changed_during_generation")
            self.assertEqual(combined_video_path(group["group_id"]), old_video)
            self.assertEqual(old_video.read_bytes(), b"old")

    def test_metadata_only_change_during_render_allows_candidate_commit(self) -> None:
        with self._store() as root:
            self._source(root, "published-a", "a", duration=10, payload=b"A")
            self._source(root, "published-b", "b", duration=10, payload=b"B")
            group = create_match_group(member_published_ids=["published-a", "published-b"], metadata={"title": "Old title"})
            original_validate = match_group_video._validate_pre_commit

            def rename_then_validate(*args: object) -> None:
                update_match_group(group["group_id"], member_published_ids=["published-a", "published-b"], metadata={"title": "New title"})
                original_validate(*args)

            with patch("app.services.match_group_video._validate_pre_commit", side_effect=rename_then_validate):
                self._generate_with_fake_media(group["group_id"], b"candidate")

            self.assertEqual(combined_video_path(group["group_id"]).read_bytes(), b"candidate")
            self.assertEqual(get_match_group_video_status(group["group_id"])["status"], "ready")

    def _generate_with_fake_media(self, group_id: str, payload: bytes) -> None:
        def concat(paths: list[Path], output: Path, *, copy_streams: bool) -> None:
            output.write_bytes(payload)

        def probe(path: Path) -> dict[str, object]:
            return {"codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720, "fps": 25.0, "duration_sec": 20.0 if path.name == COMBINED_VIDEO_FILENAME else 10.0, "audio": False}

        with patch("app.services.match_group_video._concat", side_effect=concat), patch("app.services.match_group_video._probe", side_effect=probe):
            generate_match_group_video(group_id)

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
