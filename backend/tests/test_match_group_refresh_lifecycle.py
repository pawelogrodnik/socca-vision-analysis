from __future__ import annotations

"""Lifecycle/concurrency hardening regressions for Issue #93 (PR #94).

Covers the refresh correctness, failure/rollback, concurrency, reader
coherence, video/external-branch, Key Moments transition, side-effect and
three-member atomicity matrix that the refresh-to-latest flow must prove.
"""

import ast
import copy
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app.services.match_group_refresh as match_group_refresh
from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_group_aggregation import (
    build_match_group_report_candidate,
    generate_match_group_report,
    get_coherent_match_group_report,
    get_match_group_report,
)
from app.services.match_group_external_video import save_match_group_external_video
from app.services.match_group_refresh import preview_match_group_refresh, refresh_match_group_to_latest
from app.services.match_group_video import (
    COMBINED_VIDEO_FILENAME,
    MatchGroupVideoError,
    delete_match_group_when_video_idle,
    generate_match_group_video,
    get_match_group_video_status,
)
from app.services.match_groups import (
    MatchGroupError,
    create_match_group,
    get_match_group,
    update_match_group_and_generate_report,
)
from app.services.published_video import PUBLISHED_VIDEO_ARTIFACT, PUBLISHED_VIDEO_DESCRIPTOR_FILENAME
from test_match_group_aggregation import _metadata, _set_source_momentum, _write_source


YOUTUBE_URL = "https://www.youtube.com/watch?v=AbCdEfGhI_1"


class RefreshLifecycleTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

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

    def _group(self, root: Path, members: tuple[str, ...] = ("published-one", "published-two")) -> dict[str, object]:
        physical = {"published-one": "physical-one", "published-two": "physical-two", "published-three": "physical-three"}
        for published_id in members:
            _write_source(root, published_id, physical[published_id])
        group = create_match_group(member_published_ids=list(members), metadata=_metadata())
        generate_match_group_report(str(group["group_id"]))
        return group

    def _group_dir(self, root: Path, group_id: str) -> Path:
        return root / "groups" / group_id

    def _snapshot_group_bytes(self, root: Path, group_id: str) -> dict[str, bytes]:
        directory = self._group_dir(root, group_id)
        return {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}

    def _snapshot_physical_bytes(self, root: Path) -> dict[str, bytes]:
        rows: dict[str, bytes] = {}
        for path in sorted((root / "published").rglob("*")):
            if path.is_file():
                rows[str(path.relative_to(root))] = path.read_bytes()
        return rows

    def _rewrite_source_docs(self, root: Path, published_id: str, *, aggregate_mut=None, public_mut=None) -> None:
        directory = root / "published" / published_id
        public = json.loads((directory / "public_report.json").read_text(encoding="utf-8"))
        aggregate = json.loads((directory / "aggregate_inputs.json").read_text(encoding="utf-8"))
        if public_mut is not None:
            public_mut(public)
        if aggregate_mut is not None:
            aggregate_mut(aggregate)
        aggregate["source"]["public_report_semantic_digest"] = canonical_json_sha256(public)
        digest_document = copy.deepcopy(aggregate)
        digest_document["source"].pop("aggregation_input_semantic_digest", None)
        aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(digest_document)
        (directory / "public_report.json").write_text(json.dumps(public, sort_keys=True), encoding="utf-8")
        (directory / "aggregate_inputs.json").write_text(json.dumps(aggregate, sort_keys=True), encoding="utf-8")

    def _write_video_files(self, root: Path, published_id: str, payload: bytes, *, duration: float = 10) -> None:
        directory = root / "published" / published_id
        public = json.loads((directory / "public_report.json").read_text(encoding="utf-8"))
        public_digest = canonical_json_sha256(public)
        (directory / PUBLISHED_VIDEO_ARTIFACT).write_bytes(payload)
        from app.services.published_video import sha256_file

        descriptor = {
            "schema_version": "1.0.0",
            "status": "available",
            "artifact": PUBLISHED_VIDEO_ARTIFACT,
            "semantic_digest": sha256_file(directory / PUBLISHED_VIDEO_ARTIFACT),
            "duration_sec": duration,
            "width": 1280,
            "height": 720,
            "fps": 25.0,
            "codec": "h264",
            "pix_fmt": "yuv420p",
            "source_public_report_semantic_digest": public_digest,
        }
        descriptor["descriptor_semantic_digest"] = canonical_json_sha256(descriptor)
        (directory / PUBLISHED_VIDEO_DESCRIPTOR_FILENAME).write_text(
            json.dumps(descriptor, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _video_group(self, root: Path) -> dict[str, object]:
        """Two video-capable sources grouped and rendered with mocked ffmpeg."""

        self._group(root)
        group_id = self._group_id(root)
        self._write_video_files(root, "published-one", b"video-one")
        self._write_video_files(root, "published-two", b"video-two")
        self._render_video(group_id)
        self.assertEqual(get_match_group_video_status(group_id)["status"], "ready")
        return get_match_group(group_id)

    def _group_id(self, root: Path) -> str:
        groups = [path.name for path in (root / "groups").iterdir() if path.is_dir()]
        self.assertEqual(len(groups), 1)
        return groups[0]

    def _render_video(self, group_id: str) -> None:
        def concat(paths, output, *, copy_streams: bool) -> None:
            output.write_bytes(b"combined")

        def probe(path: Path) -> dict[str, object]:
            duration = 20.0 if path.name == COMBINED_VIDEO_FILENAME else 10.0
            return {"codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720, "fps": 25.0, "duration_sec": duration, "audio": False}

        with patch("app.services.match_group_video._concat", side_effect=concat), patch(
            "app.services.match_group_video._probe", side_effect=probe
        ):
            generate_match_group_video(group_id)

    @staticmethod
    def _capture(errors: list[BaseException], action) -> None:
        try:
            action()
        except BaseException as error:  # noqa: BLE001 - collected for assertions
            errors.append(error)

    # ------------------------------------------------------------------
    # all-or-nothing candidate validation matrix
    # ------------------------------------------------------------------

    def test_blocked_members_leave_every_derived_byte_untouched(self) -> None:
        tamper_cases = {
            "source_directory_missing": lambda root, pid: shutil.rmtree(root / "published" / pid),
            "aggregate_inputs_missing": lambda root, pid: (root / "published" / pid / "aggregate_inputs.json").unlink(),
            "public_report_missing": lambda root, pid: (root / "published" / pid / "public_report.json").unlink(),
            "aggregate_inputs_invalid_json": lambda root, pid: (root / "published" / pid / "aggregate_inputs.json").write_text(
                "{not json", encoding="utf-8"
            ),
            "aggregate_digest_tampered": lambda root, pid: (
                root / "published" / pid / "aggregate_inputs.json"
            ).write_text(
                json.dumps({
                    **json.loads((root / "published" / pid / "aggregate_inputs.json").read_text(encoding="utf-8")),
                    "players": [{"player_id": "ghost", "team_id": "team-corgi"}],
                }),
                encoding="utf-8",
            ),
            "public_report_digest_tampered": lambda root, pid: (
                root / "published" / pid / "public_report.json"
            ).write_text(
                json.dumps({**json.loads((root / "published" / pid / "public_report.json").read_text(encoding="utf-8")), "extra": 1}),
                encoding="utf-8",
            ),
            "published_id_mismatch": lambda root, pid: self._rewrite_source_docs(
                root, pid, aggregate_mut=lambda doc: doc["source"].update({"published_id": "other-publication"})
            ),
            "source_match_identity_changed": lambda root, pid: _write_source(root, pid, "different-physical-source"),
            "unsupported_aggregate_schema": lambda root, pid: self._rewrite_source_docs(
                root, pid, aggregate_mut=lambda doc: doc.update({"schema_version": "9.9.9"})
            ),
            "unsupported_aggregation_policy": lambda root, pid: self._rewrite_source_docs(
                root, pid, aggregate_mut=lambda doc: doc.update({"aggregation_policy_version": "9.9.9"})
            ),
            "unsupported_public_report_schema": lambda root, pid: self._rewrite_source_docs(
                root, pid, public_mut=lambda doc: doc.update({"schema_version": "9.9.9"})
            ),
            "unsupported_public_report_type": lambda root, pid: self._rewrite_source_docs(
                root, pid, public_mut=lambda doc: doc.update({"report_type": "confidential_report"})
            ),
            "duplicate_physical_source": lambda root, pid: _write_source(root, pid, "physical-two"),
            "incompatible_team_ids": lambda root, pid: self._rewrite_source_docs(
                root,
                pid,
                aggregate_mut=lambda doc: doc.update({"teams": [{"team_id": "team-x"}, {"team_id": "team-y"}]}),
            ),
            "player_team_mismatch": lambda root, pid: self._rewrite_source_docs(
                root,
                pid,
                aggregate_mut=lambda doc: doc.update({
                    "players": [
                        {"player_id": "player-one", "team_id": "team-verisk", "movement": {"total_distance_m": 1}},
                    ]
                }),
            ),
        }
        for name, tamper in tamper_cases.items():
            with self.subTest(case=name), self._store() as root:
                group = self._group(root)
                group_id = str(group["group_id"])
                # A valid republish of the untouched member must not mask the broken one.
                _write_source(root, "published-two", "physical-two", player_distance=333)
                tamper(root, "published-one")
                before_group = self._snapshot_group_bytes(root, group_id)
                before_physical = self._snapshot_physical_bytes(root)

                preview = preview_match_group_refresh(group_id)
                self.assertEqual(preview["status"], "blocked", name)
                self.assertTrue(preview["blocking_reasons"], name)
                with self.assertRaises(MatchGroupError, msg=name):
                    refresh_match_group_to_latest(group_id)

                self.assertEqual(self._snapshot_group_bytes(root, group_id), before_group, name)
                self.assertEqual(self._snapshot_physical_bytes(root), before_physical, name)

    def test_tampered_persisted_manifest_blocks_refresh_without_mutation(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            manifest_path = self._group_dir(root, group_id) / "manifest.json"
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["metadata"]["title"] = "tampered-operator-title"
            manifest_path.write_text(json.dumps(document), encoding="utf-8")
            before = self._snapshot_group_bytes(root, group_id)

            preview = preview_match_group_refresh(group_id)
            self.assertEqual(preview["status"], "blocked")
            with self.assertRaises(MatchGroupError):
                refresh_match_group_to_latest(group_id)
            self.assertEqual(self._snapshot_group_bytes(root, group_id), before)

    # ------------------------------------------------------------------
    # failure/rollback matrix around the storage boundary
    # ------------------------------------------------------------------

    def test_report_candidate_build_failure_preserves_old_bytes(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            before = self._snapshot_group_bytes(root, group_id)
            with patch(
                "app.services.match_group_refresh.build_match_group_report_candidate",
                side_effect=MatchGroupError("aggregate_generation_blocked", "candidate exploded"),
            ):
                with self.assertRaises(MatchGroupError):
                    refresh_match_group_to_latest(group_id)
            self.assertEqual(self._snapshot_group_bytes(root, group_id), before)

    def test_staging_failure_changes_no_durable_state(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            before = self._snapshot_group_bytes(root, group_id)
            with patch(
                "app.services.match_group_refresh._stage_json", side_effect=OSError("no space left on device")
            ):
                with self.assertRaises(OSError):
                    refresh_match_group_to_latest(group_id)
            self.assertEqual(self._snapshot_group_bytes(root, group_id), before)

    def test_first_durable_replace_failure_keeps_old_coherent_pair(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            before = self._snapshot_group_bytes(root, group_id)
            real_replace = match_group_refresh.os.replace
            calls = 0

            def fail_first_replace(source: object, target: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("simulated first replace failure")
                real_replace(source, target)  # type: ignore[arg-type]

            with patch("app.services.match_group_refresh.os.replace", side_effect=fail_first_replace):
                with self.assertRaises(OSError):
                    refresh_match_group_to_latest(group_id)
            self.assertEqual(calls, 1)
            self.assertEqual(self._snapshot_group_bytes(root, group_id), before)
            # The interrupted commit recovers transparently on the next read.
            self.assertEqual(get_match_group(group_id)["group_id"], group_id)
            self.assertEqual(refresh_match_group_to_latest(group_id)["status"], "refreshed")

    def test_group_deleted_during_commit_stays_deleted(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()
            real_replace = match_group_refresh.os.replace

            def delete_between_replaces(source: object, target: object) -> None:
                real_replace(source, target)  # type: ignore[arg-type]
                if str(target).endswith("manifest.json") and not entered.is_set():
                    entered.set()
                    self.assertTrue(release.wait(timeout=5))
                    shutil.rmtree(self._group_dir(root, group_id), ignore_errors=True)

            worker_errors: list[BaseException] = []
            with patch("app.services.match_group_refresh.os.replace", side_effect=delete_between_replaces):
                worker = threading.Thread(
                    target=lambda: self._capture(worker_errors, lambda: refresh_match_group_to_latest(group_id))
                )
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                release.set()
                worker.join(timeout=5)
            self.assertTrue(worker_errors)
            self.assertFalse(self._group_dir(root, group_id).exists())
            with self.assertRaises(KeyError):
                refresh_match_group_to_latest(group_id)
            self.assertFalse(self._group_dir(root, group_id).exists())

    # ------------------------------------------------------------------
    # concurrency matrix
    # ------------------------------------------------------------------

    def test_concurrent_refreshes_produce_one_coherent_result(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()
            original_commit = match_group_refresh._commit_pair

            def pause_commit(*args: object, **kwargs: object) -> None:
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                original_commit(*args, **kwargs)  # type: ignore[arg-type]

            outcomes: list[object] = []
            with patch("app.services.match_group_refresh._commit_pair", side_effect=pause_commit):
                first = threading.Thread(
                    target=lambda: self._capture(outcomes, lambda: outcomes.append(refresh_match_group_to_latest(group_id)))
                )
                first.start()
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaises(MatchGroupVideoError) as failure:
                    refresh_match_group_to_latest(group_id)
                self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                release.set()
                first.join(timeout=5)
            worker_errors = [item for item in outcomes if isinstance(item, BaseException)]
            self.assertFalse(worker_errors)
            # A second refresh after the first commit is a deterministic no-op.
            self.assertEqual(refresh_match_group_to_latest(group_id)["status"], "current")
            snapshot = get_coherent_match_group_report(group_id)
            self.assertEqual(
                [row["published_id"] for row in snapshot["report"]["sources"]],
                [row["published_id"] for row in snapshot["manifest"]["members"]],
            )
            self.assertEqual(snapshot["validation"]["status"], "compatible")

    def test_regenerate_during_refresh_window_cannot_emit_old_report(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            old_manifest_bytes = (self._group_dir(root, group_id) / "manifest.json").read_bytes()
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()
            original_commit = match_group_refresh._commit_pair

            def pause_commit(*args: object, **kwargs: object) -> None:
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                original_commit(*args, **kwargs)  # type: ignore[arg-type]

            worker_errors: list[BaseException] = []
            with patch("app.services.match_group_refresh._commit_pair", side_effect=pause_commit):
                worker = threading.Thread(
                    target=lambda: self._capture(worker_errors, lambda: refresh_match_group_to_latest(group_id))
                )
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                # Regeneration holds no lock here; it must conflict, never queue behind a stale snapshot.
                with self.assertRaises(MatchGroupVideoError) as failure:
                    generate_match_group_report(group_id)
                self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker_errors)
            self.assertNotEqual((self._group_dir(root, group_id) / "manifest.json").read_bytes(), old_manifest_bytes)
            snapshot = get_coherent_match_group_report(group_id)
            self.assertEqual(
                [row["published_id"] for row in snapshot["report"]["sources"]],
                [row["published_id"] for row in snapshot["manifest"]["members"]],
            )

    def test_regenerate_holding_lock_blocks_refresh_until_old_report_commits(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            old_report_bytes = (self._group_dir(root, group_id) / "public_report.json").read_bytes()
            entered = threading.Event()
            release = threading.Event()
            import app.services.match_group_aggregation as aggregation

            original_write = aggregation._write_existing_group_report

            def pause_write(path: Path, value) -> None:
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                original_write(path, value)

            worker_errors: list[BaseException] = []
            with patch("app.services.match_group_aggregation._write_existing_group_report", side_effect=pause_write):
                worker = threading.Thread(
                    target=lambda: self._capture(worker_errors, lambda: generate_match_group_report(group_id))
                )
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaises(MatchGroupVideoError) as failure:
                    refresh_match_group_to_latest(group_id)
                self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker_errors)
            # Regeneration rebuilt the report from the still-pinned manifest.
            self.assertEqual((self._group_dir(root, group_id) / "public_report.json").read_bytes(), old_report_bytes)
            snapshot = get_coherent_match_group_report(group_id)
            self.assertEqual(snapshot["validation"]["status"], "compatible")

    def test_failed_update_never_rolls_back_over_a_completed_refresh(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()

            def pause_report(manifest: dict[str, object]) -> dict[str, object]:
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                return build_match_group_report_candidate(manifest)  # type: ignore[arg-type]

            worker_errors: list[BaseException] = []
            worker = threading.Thread(
                target=lambda: self._capture(
                    worker_errors,
                    lambda: update_match_group_and_generate_report(
                        group_id,
                        member_published_ids=["published-one", "published-two"],
                        metadata={**_metadata(), "title": "operator update"},
                        generate_report=pause_report,
                    ),
                )
            )
            worker.start()
            self.assertTrue(entered.wait(timeout=5))
            # The whole update transaction owns the group: refresh must wait/conflict, not interleave.
            with self.assertRaises(MatchGroupVideoError) as failure:
                refresh_match_group_to_latest(group_id)
            self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
            release.set()
            worker.join(timeout=5)
            self.assertFalse(worker_errors)
            updated = get_match_group(group_id)
            self.assertEqual(updated["metadata"]["title"], "operator update")
            old_pin = next(row for row in group["members"] if row["published_id"] == "published-one")[
                "aggregation_input_semantic_digest"
            ]
            new_pin = next(row for row in updated["members"] if row["published_id"] == "published-one")[
                "aggregation_input_semantic_digest"
            ]
            self.assertNotEqual(new_pin, old_pin)
            # The update committed the refreshed pins; a later refresh is a clean no-op.
            self.assertEqual(refresh_match_group_to_latest(group_id)["status"], "current")

    def test_update_during_refresh_window_conflicts_and_refresh_pins_win(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()
            original_commit = match_group_refresh._commit_pair

            def pause_commit(*args: object, **kwargs: object) -> None:
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                original_commit(*args, **kwargs)  # type: ignore[arg-type]

            worker_errors: list[BaseException] = []
            with patch("app.services.match_group_refresh._commit_pair", side_effect=pause_commit):
                worker = threading.Thread(
                    target=lambda: self._capture(worker_errors, lambda: refresh_match_group_to_latest(group_id))
                )
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaises(MatchGroupVideoError) as failure:
                    update_match_group_and_generate_report(
                        group_id,
                        member_published_ids=["published-one", "published-two"],
                        metadata={**_metadata(), "title": "late operator update"},
                        generate_report=build_match_group_report_candidate,
                    )
                self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker_errors)
            self.assertEqual(get_match_group(group_id)["metadata"]["title"], "Logical match")

    def test_delete_during_refresh_conflicts_then_deletes_cleanly(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()
            original_commit = match_group_refresh._commit_pair

            def pause_commit(*args: object, **kwargs: object) -> None:
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                original_commit(*args, **kwargs)  # type: ignore[arg-type]

            worker_errors: list[BaseException] = []
            with patch("app.services.match_group_refresh._commit_pair", side_effect=pause_commit):
                worker = threading.Thread(
                    target=lambda: self._capture(worker_errors, lambda: refresh_match_group_to_latest(group_id))
                )
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaises(MatchGroupVideoError) as failure:
                    delete_match_group_when_video_idle(group_id)
                self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker_errors)
            delete_match_group_when_video_idle(group_id)
            self.assertFalse(self._group_dir(root, group_id).exists())
            with self.assertRaises(KeyError):
                refresh_match_group_to_latest(group_id)

    def test_report_regeneration_after_delete_cannot_resurrect_group(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            delete_match_group_when_video_idle(group_id)
            with self.assertRaises(KeyError):
                generate_match_group_report(group_id)
            self.assertFalse(self._group_dir(root, group_id).exists())

    def test_video_generation_during_refresh_conflicts_without_starting_work(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()
            original_commit = match_group_refresh._commit_pair

            def pause_commit(*args: object, **kwargs: object) -> None:
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                original_commit(*args, **kwargs)  # type: ignore[arg-type]

            worker_errors: list[BaseException] = []
            with patch("app.services.match_group_refresh._commit_pair", side_effect=pause_commit):
                worker = threading.Thread(
                    target=lambda: self._capture(worker_errors, lambda: refresh_match_group_to_latest(group_id))
                )
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                with patch("app.services.match_group_video.threading.Thread") as worker_thread:
                    with self.assertRaises(MatchGroupVideoError) as failure:
                        generate_match_group_video(group_id)
                self.assertEqual(failure.exception.code, "match_group_maintenance_in_progress")
                worker_thread.assert_not_called()
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker_errors)

    # ------------------------------------------------------------------
    # reader coherence
    # ------------------------------------------------------------------

    def test_concurrent_reader_never_observes_a_mixed_pair(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            old_manifest = get_match_group(group_id)
            old_report = get_match_group_report(group_id)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            entered = threading.Event()
            release = threading.Event()
            real_replace = match_group_refresh.os.replace
            calls = 0

            def pause_between_replaces(source: object, target: object) -> None:
                nonlocal calls
                real_replace(source, target)  # type: ignore[arg-type]
                if Path(str(target)).name in {"manifest.json", "public_report.json"}:
                    calls += 1
                    if calls == 1:
                        entered.set()
                        self.assertTrue(release.wait(timeout=5))

            worker_errors: list[BaseException] = []
            with patch("app.services.match_group_refresh.os.replace", side_effect=pause_between_replaces):
                worker = threading.Thread(
                    target=lambda: self._capture(worker_errors, lambda: refresh_match_group_to_latest(group_id))
                )
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                # The commit sits exactly between the two file replacements:
                # NEW manifest.json is durable while public_report.json is OLD.
                try:
                    snapshot = get_coherent_match_group_report(group_id)
                except MatchGroupError as error:
                    self.assertIn(
                        error.code,
                        {"aggregate_report_lineage_mismatch", "match_group_maintenance_in_progress"},
                    )
                else:
                    self._assert_fully_old_or_fully_new(snapshot, old_manifest, old_report)
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker_errors)
            self.assertEqual(calls, 2)
            snapshot = get_coherent_match_group_report(group_id)
            self.assertNotEqual(
                snapshot["manifest"]["aggregate_semantic_digest"], old_manifest["aggregate_semantic_digest"]
            )

    def _assert_fully_old_or_fully_new(self, snapshot, old_manifest, old_report) -> None:
        manifest_digest = str(snapshot["manifest"].get("aggregate_semantic_digest") or "")
        report_digest = str(snapshot["report"].get("aggregate_semantic_digest") or "")
        old_manifest_digest = str(old_manifest.get("aggregate_semantic_digest") or "")
        old_report_digest = str(old_report.get("aggregate_semantic_digest") or "")
        variants = {(old_manifest_digest, old_report_digest)}
        self.assertIn((manifest_digest, report_digest), variants)

    def test_tampered_report_or_lineage_fails_closed_on_read(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            report_path = self._group_dir(root, group_id) / "public_report.json"

            document = json.loads(report_path.read_text(encoding="utf-8"))
            document["timing"]["timeline_span_sec"] = 12345.0
            report_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(MatchGroupError) as failure:
                get_coherent_match_group_report(group_id)
            self.assertEqual(failure.exception.code, "aggregate_report_invalid")

            snapshot_group = self._group_dir(root, group_id)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            refresh_match_group_to_latest(group_id)
            document = json.loads((snapshot_group / "public_report.json").read_text(encoding="utf-8"))
            document["sources"] = list(reversed(document["sources"]))
            digest_document = copy.deepcopy(document)
            digest_document.pop("aggregate_semantic_digest", None)
            document["aggregate_semantic_digest"] = canonical_json_sha256(digest_document)
            (snapshot_group / "public_report.json").write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(MatchGroupError) as failure:
                get_coherent_match_group_report(group_id)
            self.assertEqual(failure.exception.code, "aggregate_report_lineage_mismatch")

    # ------------------------------------------------------------------
    # combined-video and external-video branches
    # ------------------------------------------------------------------

    def test_report_only_refresh_keeps_combined_video_current(self) -> None:
        with self._store() as root:
            group = self._video_group(root)
            group_id = str(group["group_id"])
            generation_dir = self._group_dir(root, group_id) / "video-generations"
            before_files = {path.name: path.read_bytes() for path in sorted(generation_dir.rglob("*")) if path.is_file()}
            pointer_before = (self._group_dir(root, group_id) / "current_video_generation.json").read_bytes()

            _write_source(root, "published-one", "physical-one", player_distance=333)
            with patch("app.services.match_group_video._run_ffmpeg") as ffmpeg:
                result = refresh_match_group_to_latest(group_id)

            self.assertEqual(result["status"], "refreshed")
            ffmpeg.assert_not_called()
            self.assertEqual(result["video"]["status"], "ready")
            self.assertEqual(
                (self._group_dir(root, group_id) / "current_video_generation.json").read_bytes(), pointer_before
            )
            self.assertEqual(
                {path.name: path.read_bytes() for path in sorted(generation_dir.rglob("*")) if path.is_file()},
                before_files,
            )

    def test_video_input_change_marks_combined_video_stale_without_deleting_it(self) -> None:
        with self._store() as root:
            group = self._video_group(root)
            group_id = str(group["group_id"])
            output_before = next((self._group_dir(root, group_id) / "video-generations").rglob("combined_match_video.mp4")).read_bytes()

            # Report-relevant pin change plus a new proven published video generation.
            _write_source(root, "published-one", "physical-one", player_distance=333)
            self._write_video_files(root, "published-one", b"video-one-republished")
            with patch("app.services.match_group_video._run_ffmpeg") as ffmpeg:
                result = refresh_match_group_to_latest(group_id)

            self.assertEqual(result["status"], "refreshed")
            ffmpeg.assert_not_called()
            self.assertEqual(result["video"]["status"], "stale")
            output_after = next((self._group_dir(root, group_id) / "video-generations").rglob("combined_match_video.mp4")).read_bytes()
            self.assertEqual(output_after, output_before)

    def test_external_video_bytes_never_change_but_status_follows_video_digest(self) -> None:
        with self._store() as root:
            group = self._video_group(root)
            group_id = str(group["group_id"])
            save_match_group_external_video(group_id, YOUTUBE_URL)
            external_before = (self._group_dir(root, group_id) / "external_video.json").read_bytes()

            # Report-only refresh: linked video digest unchanged -> current.
            _write_source(root, "published-one", "physical-one", player_distance=333)
            result = refresh_match_group_to_latest(group_id)
            self.assertEqual(result["status"], "refreshed")
            self.assertEqual((self._group_dir(root, group_id) / "external_video.json").read_bytes(), external_before)
            self.assertEqual(result["external_video"]["status"], "current")

            # Video-input refresh: linked digest changed -> stale, same bytes.
            _write_source(root, "published-one", "physical-one", player_distance=444)
            self._write_video_files(root, "published-one", b"video-one-republished")
            result = refresh_match_group_to_latest(group_id)
            self.assertEqual(result["status"], "refreshed")
            self.assertEqual((self._group_dir(root, group_id) / "external_video.json").read_bytes(), external_before)
            self.assertEqual(result["external_video"]["status"], "stale")

    # ------------------------------------------------------------------
    # #92 Key Moments transition
    # ------------------------------------------------------------------

    def test_refresh_regenerates_key_moments_from_richer_sources(self) -> None:
        with self._store() as root:
            _write_source(
                root, "published-one", "physical-one", duration=600, possession_status="not_available", momentum_status="not_available"
            )
            _write_source(
                root, "published-two", "physical-two", duration=300, possession_status="not_available", momentum_status="not_available"
            )
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            generate_match_group_report(group_id)
            before = get_match_group_report(group_id)
            self.assertEqual(before["key_moments"]["moments"], [])

            preview = preview_match_group_refresh(group_id)
            self.assertEqual(preview["status"], "current")

            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            _set_source_momentum(
                root,
                "published-two",
                {
                    "start_time_sec": 120,
                    "end_time_sec": 125,
                    "team_values_by_team_id": {"team-corgi": 0.82, "team-verisk": 0.18},
                    "dominant_team_id": "team-corgi",
                    "intensity": 0.82,
                    "confidence": 1.0,
                },
                fake_key_moment=False,
            )
            result = refresh_match_group_to_latest(group_id)

            self.assertEqual(result["status"], "refreshed")
            refreshed = get_match_group(group_id)
            self.assertEqual(refreshed["group_id"], group_id)
            self.assertEqual(
                [row["published_id"] for row in refreshed["members"]], ["published-one", "published-two"]
            )
            self.assertEqual(
                [row["source_match_id"] for row in refreshed["members"]], ["physical-one", "physical-two"]
            )
            report = get_coherent_match_group_report(group_id)["report"]
            moments = report["key_moments"]["moments"]
            self.assertTrue(moments)
            self.assertIn(722.5, [moment["time_sec"] for moment in moments])
            # Refresh contains no Key Moments special case: the moments emerge
            # from the normal aggregate builder only.
            source = Path(match_group_refresh.__file__).read_text(encoding="utf-8")
            self.assertNotIn("key_moment", source.lower())
            self.assertNotIn("key moment", source.lower())

    # ------------------------------------------------------------------
    # forbidden side effects
    # ------------------------------------------------------------------

    def test_refresh_touches_no_video_render_review_or_physical_bytes(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_id = str(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)
            before_physical = self._snapshot_physical_bytes(root)
            with (
                patch("app.services.match_group_video._run_ffmpeg") as ffmpeg,
                patch("app.services.match_group_video._concat") as concat,
                patch("app.services.match_group_video._normalize") as normalize,
                patch("app.services.match_group_video.threading.Thread") as worker_thread,
                patch("subprocess.run") as subprocess_run,
            ):
                result = refresh_match_group_to_latest(group_id)
            self.assertEqual(result["status"], "refreshed")
            ffmpeg.assert_not_called()
            concat.assert_not_called()
            normalize.assert_not_called()
            worker_thread.assert_not_called()
            subprocess_run.assert_not_called()
            self.assertEqual(self._snapshot_physical_bytes(root), before_physical)

    def test_refresh_module_has_no_heavy_lifecycle_imports(self) -> None:
        tree = ast.parse(Path(match_group_refresh.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = ("review", "yolo", "track", "analysis", "youtube", "ffmpeg", "upload", "identity")
        hits = [name for name in imported if any(token in name for token in forbidden)]
        self.assertEqual(hits, [])

    # ------------------------------------------------------------------
    # three-member atomic refresh
    # ------------------------------------------------------------------

    def test_three_member_refresh_updates_two_pins_atomically(self) -> None:
        with self._store() as root:
            group = self._group(root, ("published-one", "published-two", "published-three"))
            group_id = str(group["group_id"])
            old_report = get_match_group_report(group_id)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            _write_source(root, "published-three", "physical-three", player_distance=444)

            preview = preview_match_group_refresh(group_id)
            self.assertEqual(preview["status"], "refreshable")

            result = refresh_match_group_to_latest(group_id)
            self.assertEqual(result["status"], "refreshed")
            refreshed = get_match_group(group_id)
            self.assertEqual(
                [row["published_id"] for row in refreshed["members"]],
                ["published-one", "published-two", "published-three"],
            )
            self.assertEqual(refreshed["group_id"], group_id)
            self.assertEqual(refreshed["metadata"], group["metadata"])
            report = get_coherent_match_group_report(group_id)["report"]
            self.assertEqual(
                [row["published_id"] for row in report["sources"]],
                ["published-one", "published-two", "published-three"],
            )
            self.assertEqual(report["timing"]["analyzed_duration_sec"], 30.0)
            offsets = [(row["logical_start_sec"], row["logical_end_sec"]) for row in refreshed["members"]]
            self.assertEqual(offsets, [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)])
            self.assertNotEqual(report["aggregate_semantic_digest"], old_report["aggregate_semantic_digest"])

    def test_three_member_refresh_with_one_invalid_member_changes_nothing(self) -> None:
        with self._store() as root:
            group = self._group(root, ("published-one", "published-two", "published-three"))
            group_id = str(group["group_id"])
            before = self._snapshot_group_bytes(root, group_id)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            _write_source(root, "published-two", "physical-two", player_distance=444)
            _write_source(root, "published-three", "different-physical-source")

            preview = preview_match_group_refresh(group_id)
            self.assertEqual(preview["status"], "blocked")
            with self.assertRaises(MatchGroupError):
                refresh_match_group_to_latest(group_id)
            self.assertEqual(self._snapshot_group_bytes(root, group_id), before)


if __name__ == "__main__":
    unittest.main()
