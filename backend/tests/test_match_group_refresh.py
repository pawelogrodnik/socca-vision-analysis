from __future__ import annotations

import tempfile
import unittest
import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import app.services.match_group_refresh as match_group_refresh
from app.services.match_group_aggregation import generate_match_group_report, get_match_group_report
from app.services.match_group_pair_transaction import (
    PAIR_TRANSACTION_FILENAME,
    PREVIOUS_MANIFEST_FILENAME,
    PREVIOUS_REPORT_FILENAME,
    prepare_pair_recovery,
)
from app.services.match_group_refresh import preview_match_group_refresh, refresh_match_group_to_latest
from app.services.match_group_video import MatchGroupVideoError, delete_match_group_when_video_idle, submit_match_group_video_generation
from app.services.match_groups import MatchGroupError, create_match_group, get_match_group, update_match_group, update_match_group_and_generate_report
from test_match_group_aggregation import _metadata, _write_source


class MatchGroupRefreshTests(unittest.TestCase):
    def test_refresh_advances_same_stable_publications_and_rebuilds_report(self) -> None:
        with self._store() as root:
            group = self._group(root)
            previous = get_match_group(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)

            preview = preview_match_group_refresh(group["group_id"])
            self.assertEqual(preview["status"], "refreshable")
            self.assertEqual([row["published_id"] for row in preview["members"]], ["published-one", "published-two"])
            result = refresh_match_group_to_latest(group["group_id"])

            self.assertEqual(result["status"], "refreshed")
            refreshed = get_match_group(group["group_id"])
            self.assertEqual([row["published_id"] for row in refreshed["members"]], ["published-one", "published-two"])
            self.assertEqual([row["source_match_id"] for row in refreshed["members"]], ["physical-one", "physical-two"])
            self.assertNotEqual(
                previous["members"][0]["aggregation_input_semantic_digest"],
                refreshed["members"][0]["aggregation_input_semantic_digest"],
            )
            self.assertEqual(result["validation"]["status"], "compatible")
            self.assertEqual(result["video"]["status"], "unavailable_source_video")
            self.assertEqual(result["external_video"]["status"], "not_configured")

    def test_current_refresh_is_a_byte_preserving_no_op(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_dir = root / "groups" / group["group_id"]
            before = {name: (group_dir / name).read_bytes() for name in ("manifest.json", "public_report.json")}

            self.assertEqual(preview_match_group_refresh(group["group_id"])["status"], "current")
            result = refresh_match_group_to_latest(group["group_id"])

            self.assertEqual(result["status"], "current")
            self.assertEqual(before, {name: (group_dir / name).read_bytes() for name in before})

    def test_refresh_blocks_stable_publication_that_changed_physical_identity(self) -> None:
        with self._store() as root:
            group = self._group(root)
            _write_source(root, "published-one", "different-physical-source")

            preview = preview_match_group_refresh(group["group_id"])

            self.assertEqual(preview["status"], "blocked")
            self.assertEqual(preview["blocking_reasons"][0]["code"], "source_match_identity_changed")

    def test_refresh_blocks_a_tampered_persisted_manifest_without_rewriting_it(self) -> None:
        with self._store() as root:
            group = self._group(root)
            manifest_path = root / "groups" / str(group["group_id"]) / "manifest.json"
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["metadata"]["title"] = "tampered"
            manifest_path.write_text(json.dumps(document), encoding="utf-8")

            preview = preview_match_group_refresh(str(group["group_id"]))

            self.assertEqual(preview["status"], "blocked")
            self.assertEqual(preview["blocking_reasons"][0]["code"], "refresh_manifest_invalid")

    def test_refresh_never_commits_a_mixed_source_generation(self) -> None:
        with self._store() as root:
            group = self._group(root)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            original = match_group_refresh._build_refresh_candidate
            calls = 0

            def drift_after_report(current: dict[str, object]) -> dict[str, object]:
                nonlocal calls
                calls += 1
                result = original(current)  # type: ignore[arg-type]
                if calls == 2:
                    _write_source(root, "published-one", "physical-one", player_distance=444)
                return result

            with patch("app.services.match_group_refresh._build_refresh_candidate", side_effect=drift_after_report):
                with self.assertRaises(MatchGroupError) as failure:
                    refresh_match_group_to_latest(group["group_id"])
            self.assertEqual(failure.exception.code, "source_generation_changed_during_refresh")

    def test_refresh_never_overwrites_a_concurrent_manifest_metadata_change(self) -> None:
        with self._store() as root:
            group = self._group(root)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            original = match_group_refresh._build_refresh_candidate
            calls = 0

            def update_after_candidate(current: dict[str, object]) -> dict[str, object]:
                nonlocal calls
                calls += 1
                result = original(current)  # type: ignore[arg-type]
                if calls == 2:
                    update_match_group(
                        str(group["group_id"]),
                        member_published_ids=["published-one", "published-two"],
                        metadata={**_metadata(), "title": "operator changed title"},
                    )
                return result

            with patch("app.services.match_group_refresh._build_refresh_candidate", side_effect=update_after_candidate):
                with self.assertRaises(MatchGroupError) as failure:
                    refresh_match_group_to_latest(str(group["group_id"]))
            self.assertEqual(failure.exception.code, "source_generation_changed_during_refresh")
            self.assertEqual(get_match_group(str(group["group_id"]))["metadata"]["title"], "operator changed title")

    def test_pair_commit_rolls_back_when_second_replace_fails(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_dir = root / "groups" / group["group_id"]
            before = {name: (group_dir / name).read_bytes() for name in ("manifest.json", "public_report.json")}
            real_replace = match_group_refresh.os.replace
            replaces = 0

            def fail_second_replace(source: object, target: object) -> None:
                nonlocal replaces
                replaces += 1
                if replaces == 2:
                    raise OSError("simulated second replace failure")
                real_replace(source, target)

            with patch("app.services.match_group_refresh.os.replace", side_effect=fail_second_replace):
                with self.assertRaises(OSError):
                    match_group_refresh._commit_pair(group["group_id"], get_match_group(group["group_id"]), {"report": "replacement"})
            self.assertEqual(before, {name: (group_dir / name).read_bytes() for name in before})

    def test_refresh_is_rejected_while_video_generation_owns_group(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_dir = root / "groups" / group["group_id"]
            (group_dir / "video_job.json").write_text('{"status":"generating","job_key":"live"}', encoding="utf-8")
            (group_dir / "video_job.lock").write_text('{"pid":999999,"job_key":"live"}', encoding="utf-8")

            with patch("app.services.match_group_video._lock_owner_alive", return_value=True):
                with self.assertRaises(MatchGroupVideoError) as failure:
                    refresh_match_group_to_latest(group["group_id"])

            self.assertEqual(failure.exception.code, "video_generation_in_progress")

    def test_commit_refuses_missing_group_without_recreating_it(self) -> None:
        with self._store() as root:
            with self.assertRaises(KeyError):
                match_group_refresh._commit_pair("match-group-00000000-0000-0000-0000-000000000000", {}, {})
            self.assertFalse((root / "groups" / "match-group-00000000-0000-0000-0000-000000000000").exists())

    def test_report_regeneration_owns_group_until_report_commit(self) -> None:
        with self._store() as root:
            group = self._group(root)
            entered = threading.Event()
            release = threading.Event()
            original_write = __import__("app.services.match_group_aggregation", fromlist=["_write_existing_group_report"])._write_existing_group_report
            errors: list[BaseException] = []

            def pause_report_write(path: Path, value: dict[str, object]) -> None:
                _write_source(root, "published-one", "physical-one", player_distance=333)
                entered.set()
                self.assertTrue(release.wait(timeout=2))
                original_write(path, value)

            with patch("app.services.match_group_aggregation._write_existing_group_report", side_effect=pause_report_write):
                worker = threading.Thread(target=lambda: self._capture(errors, lambda: generate_match_group_report(str(group["group_id"]))))
                worker.start()
                self.assertTrue(entered.wait(timeout=2))
                with self.assertRaises(MatchGroupVideoError) as failure:
                    refresh_match_group_to_latest(str(group["group_id"]))
                self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                release.set()
                worker.join(timeout=2)
            self.assertFalse(errors)

    def test_refresh_owns_group_against_update_refresh_and_delete(self) -> None:
        with self._store() as root:
            group = self._group(root)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()
            original_commit = match_group_refresh._commit_pair
            errors: list[BaseException] = []

            def pause_commit(*args: object, **kwargs: object) -> None:
                entered.set()
                self.assertTrue(release.wait(timeout=2))
                original_commit(*args, **kwargs)  # type: ignore[arg-type]

            with patch("app.services.match_group_refresh._commit_pair", side_effect=pause_commit):
                worker = threading.Thread(target=lambda: self._capture(errors, lambda: refresh_match_group_to_latest(str(group["group_id"]))))
                worker.start()
                self.assertTrue(entered.wait(timeout=2))
                for operation in (
                    lambda: refresh_match_group_to_latest(str(group["group_id"])),
                    lambda: update_match_group_and_generate_report(
                        str(group["group_id"]),
                        member_published_ids=["published-one", "published-two"],
                        metadata={**_metadata(), "title": "operator update"},
                        build_report_candidate=lambda manifest: {"group_id": manifest["group_id"]},
                    ),
                    lambda: delete_match_group_when_video_idle(str(group["group_id"])),
                ):
                    with self.assertRaises(MatchGroupVideoError) as failure:
                        operation()
                    self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                release.set()
                worker.join(timeout=2)
            self.assertFalse(errors)
            self.assertTrue((root / "groups" / str(group["group_id"])).is_dir())

    def test_delete_first_means_later_refresh_is_not_found(self) -> None:
        with self._store() as root:
            group = self._group(root)
            delete_match_group_when_video_idle(str(group["group_id"]))
            with self.assertRaises(KeyError):
                refresh_match_group_to_latest(str(group["group_id"]))

    def test_video_request_during_refresh_returns_maintenance_conflict_without_job(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_dir = root / "groups" / str(group["group_id"])
            from app.services.match_group_video import reserve_match_group_video_idle

            with reserve_match_group_video_idle(str(group["group_id"]), operation="refresh"):
                with patch("app.services.match_group_video.threading.Thread") as worker:
                    with self.assertRaises(MatchGroupVideoError) as failure:
                        submit_match_group_video_generation(str(group["group_id"]))
                self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                worker.assert_not_called()
                self.assertFalse((group_dir / "video_job.json").exists())

    def test_interrupted_pair_commit_recovers_before_a_fresh_authoritative_read(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            group_dir = root / "groups" / group_id
            previous_manifest = (group_dir / "manifest.json").read_bytes()
            previous_report = (group_dir / "public_report.json").read_bytes()
            _write_source(root, "published-one", "physical-one", player_distance=333)
            replacement = match_group_refresh._build_refresh_candidate(get_match_group(group_id))
            replacement_report = match_group_refresh.build_match_group_report_candidate(replacement)
            prepare_pair_recovery(group_dir, previous_manifest=previous_manifest, previous_report=previous_report)
            (group_dir / "manifest.json").write_text(json.dumps(replacement), encoding="utf-8")

            recovered = get_match_group(group_id)

            self.assertEqual(recovered, json.loads(previous_manifest))
            self.assertEqual(get_match_group_report(group_id), json.loads(previous_report))
            for name in (PAIR_TRANSACTION_FILENAME, PREVIOUS_MANIFEST_FILENAME, PREVIOUS_REPORT_FILENAME):
                self.assertFalse((group_dir / name).exists())
            self.assertNotEqual(replacement_report["aggregate_semantic_digest"], json.loads(previous_report)["aggregate_semantic_digest"])
            self.assertEqual(refresh_match_group_to_latest(group_id)["status"], "refreshed")

    def test_reader_fails_closed_while_a_live_owner_has_replaced_only_the_manifest(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            group_dir = root / "groups" / group_id
            previous_manifest = (group_dir / "manifest.json").read_bytes()
            previous_report = (group_dir / "public_report.json").read_bytes()
            _write_source(root, "published-one", "physical-one", player_distance=333)
            replacement = match_group_refresh._build_refresh_candidate(get_match_group(group_id))
            prepare_pair_recovery(group_dir, previous_manifest=previous_manifest, previous_report=previous_report)
            (group_dir / "manifest.json").write_text(json.dumps(replacement), encoding="utf-8")
            (group_dir / "video_job.lock").write_text(json.dumps({"pid": os.getpid(), "job_key": "refresh-live"}), encoding="utf-8")

            with self.assertRaises(MatchGroupError) as failure:
                get_match_group(group_id)

            self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
            (group_dir / "video_job.lock").unlink()
            self.assertEqual(get_match_group(group_id), json.loads(previous_manifest))

    def test_refresh_does_not_rewrite_external_video_or_start_video_generation(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_dir = root / "groups" / str(group["group_id"])
            external = group_dir / "external_video.json"
            external.write_bytes(b'{"legacy":"external-video-bytes"}\n')
            before = external.read_bytes()
            _write_source(root, "published-one", "physical-one", player_distance=333)

            with patch("app.services.match_group_video._run_ffmpeg") as ffmpeg:
                result = refresh_match_group_to_latest(str(group["group_id"]))

            self.assertEqual(result["status"], "refreshed")
            self.assertEqual(external.read_bytes(), before)
            ffmpeg.assert_not_called()

    @staticmethod
    def _capture(errors: list[BaseException], action: object) -> None:
        try:
            action()  # type: ignore[operator]
        except BaseException as error:
            errors.append(error)

    def _group(self, root: Path) -> dict[str, object]:
        _write_source(root, "published-one", "physical-one")
        _write_source(root, "published-two", "physical-two")
        group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
        generate_match_group_report(str(group["group_id"]))
        return group

    def _store(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        patches = (
            patch("app.services.match_groups.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_groups.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_aggregation.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_group_aggregation.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_refresh.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_video.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_group_video.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_external_video.MATCH_GROUPS_DIR", root / "groups"),
        )

        class StoreContext:
            def __enter__(self) -> Path:
                for item in patches:
                    item.__enter__()
                return root

            def __exit__(self, *args: object) -> None:
                for item in reversed(patches):
                    item.__exit__(*args)
                temporary.cleanup()

        return StoreContext()
