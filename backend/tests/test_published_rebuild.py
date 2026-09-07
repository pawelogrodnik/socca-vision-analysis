from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


def write_rebuildable_match_fixture(match_dir: Path, *, match_id: str, title: str) -> None:
    """A publishable local match with two stable teams for rebuild tests."""

    from test_match_package import write_json, write_ready_match_fixture

    write_ready_match_fixture(match_dir)
    write_json(
        match_dir / "match.json",
        {
            "id": match_id,
            "title": title,
            "status": "reviewed",
            "format": "7v7",
            "video_filename": "video.mp4",
            "video": {"fps": 25, "frame_count": 250, "duration_sec": 10, "width": 1280, "height": 720},
            "teams": [
                {
                    "id": "team-a",
                    "name": "Team A",
                    "players": [{"id": "p-a1", "name": "Player A1", "role": "player", "is_guest": False}],
                },
                {
                    "id": "team-b",
                    "name": "Team B",
                    "players": [{"id": "p-b1", "name": "Player B1", "role": "player", "is_guest": False}],
                },
            ],
        },
    )


def write_rebuildable_reviewed_fixture(match_dir: Path) -> None:
    """Reviewed-identity variant so publication includes aggregate inputs."""

    from test_match_package import write_json, write_reviewed_identity_fixture

    for name in ("player_identity_assignments.json", "resolved_player_stats.json"):
        (match_dir / name).unlink(missing_ok=True)
    write_reviewed_identity_fixture(match_dir)
    write_json(
        match_dir / "team_config.json",
        {
            "schema_version": "0.1.0",
            "teams": [
                {"team_label": "A", "team_id": "team-a"},
                {"team_label": "B", "team_id": "team-b"},
            ],
        },
    )
    write_json(
        match_dir / "reviewed_player_stats.json",
        {
            "source_snapshot_digest": "reviewed-digest",
            "players": [
                {
                    "player_id": "p-a1",
                    "player_name": "Player A1",
                    "team_label": "A",
                    "detected_time_sec": 8.0,
                    "total_distance_m": 25.0,
                    "detected_frames": 200,
                },
                {
                    "player_id": "p-b1",
                    "player_name": "Player B1",
                    "team_label": "B",
                    "detected_time_sec": 7.0,
                    "total_distance_m": 20.0,
                    "detected_frames": 175,
                },
            ],
        },
    )


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is required for rebuild endpoint tests")
class PublishedRebuildTests(unittest.TestCase):
    def test_legacy_migration_gate_requires_exact_complete_published_review(self) -> None:
        from app.main import _assert_physical_rebuild_workflow
        from fastapi import HTTPException

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "video.mp4").write_bytes(b"source")
            source_digest = hashlib.sha256(b"source").hexdigest()
            legacy_video = {"duration_sec": 10.0, "source_fingerprint": {"sha256": source_digest}}
            (path / "match.json").write_text(json.dumps({"video": legacy_video}), encoding="utf-8")
            publication = {"package": {"match": {"video": legacy_video}}}
            failure = HTTPException(status_code=409, detail={"code": "review_not_completed"})
            with patch("app.main._assert_publish_workflow", side_effect=failure), patch(
                "app.main.reviewed_identity_package_status", return_value={"ready": True, "digest": "published-digest"}
            ):
                with patch("app.main.get_reviewed_identity_status", return_value={"status": "complete_reviewed", "semantic_digest": "published-digest"}):
                    _assert_physical_rebuild_workflow(path, publication)
                with patch("app.main.get_reviewed_identity_status", return_value={"status": "partial_reviewed", "semantic_digest": "published-digest"}):
                    _assert_physical_rebuild_workflow(path, publication)
                for snapshot in (
                    {"status": "partial_reviewed", "semantic_digest": "different-digest"},
                    {"status": "blocked", "semantic_digest": "published-digest"},
                    {"status": "stale", "semantic_digest": "published-digest"},
                    {"status": "complete_reviewed", "semantic_digest": "different-digest"},
                ):
                    with patch("app.main.get_reviewed_identity_status", return_value=snapshot):
                        with self.assertRaises(HTTPException):
                            _assert_physical_rebuild_workflow(path, publication)

                (path / "video.mp4").write_bytes(b"changed source")
                with patch("app.main.get_reviewed_identity_status", return_value={"status": "complete_reviewed", "semantic_digest": "published-digest"}):
                    with self.assertRaises(HTTPException):
                        _assert_physical_rebuild_workflow(path, publication)
                (path / "video.mp4").write_bytes(b"source")

            canonical = {"video": {"timebase_schema_version": "1.0.0", "source_fingerprint": {"sha256": source_digest}}}
            (path / "match.json").write_text(json.dumps(canonical), encoding="utf-8")
            with patch("app.main._assert_publish_workflow", side_effect=failure), patch(
                "app.main.reviewed_identity_package_status", return_value={"ready": True, "digest": "published-digest"}
            ), patch("app.main.get_reviewed_identity_status", return_value={"status": "complete_reviewed", "semantic_digest": "published-digest"}):
                with self.assertRaises(HTTPException):
                    _assert_physical_rebuild_workflow(path, publication)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
    def test_rebuild_migrates_real_cfr_video_and_preserves_reviewed_identity(self) -> None:
        from app.main import api_rebuild_published_match, publish_local_match, read_match_meta
        from app.services.identity_reviewed_snapshot import (
            IDENTITY_SOURCE_SEMANTICS_LEGACY,
            _source_digest,
            _source_documents,
            finalize_reviewed_identity,
            get_reviewed_identity_status,
        )
        from app.services.identity_reviewed_stats import build_reviewed_stats

        with self._store() as root:
            match_dir = self._local_match(root, "match-1", title="Original", reviewed=True)
            source = match_dir / "video.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25", "-frames:v", "50", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                check=True,
            )
            # This is intentionally historical nominal metadata: the endpoint
            # must inspect the source rather than trust its frame count.
            meta = read_match_meta(match_dir)
            meta["video"] = {
                "fps": 25, "frame_count": 250, "duration_sec": 10, "width": 320, "height": 180,
                "source_fingerprint": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
            }
            (match_dir / "match.json").write_text(json.dumps(meta), encoding="utf-8")
            snapshot = finalize_reviewed_identity(match_dir, meta)
            build_reviewed_stats(match_dir, snapshot, meta, {})
            output_manifest_path = match_dir / "reviewed_output_manifest.json"
            output_manifest = json.loads(output_manifest_path.read_text(encoding="utf-8"))
            output_manifest["reviewed_identity"]["digest"] = snapshot["semantic_digest"]
            output_manifest["stats"]["source_snapshot_digest"] = snapshot["semantic_digest"]
            output_manifest_path.write_text(json.dumps(output_manifest), encoding="utf-8")
            self.assertNotEqual(get_reviewed_identity_status(match_dir)["status"], "stale")
            published = publish_local_match("match-1", replace=False)
            # Persist a genuine pre-#103 descriptor for the now-published
            # historical match: exact old semantics, no version marker.
            legacy_meta = read_match_meta(match_dir)
            snapshot["source"].pop("identity_source_semantics_version", None)
            snapshot["source"]["semantic_input_digest"] = _source_digest(
                _source_documents(match_dir), legacy_meta, semantics_version=IDENTITY_SOURCE_SEMANTICS_LEGACY
            )
            (match_dir / "reviewed_identity_snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")

            # Existing historical publication: the contemporary workflow
            # projection is incomplete, but its authoritative Reviewed
            # Identity is current. Migration may prove timebase without
            # rerunning human Review or identity finalization.
            from fastapi import HTTPException
            with patch(
                "app.main._assert_publish_workflow",
                side_effect=HTTPException(status_code=409, detail="review_not_completed"),
            ):
                rebuilt = api_rebuild_published_match(published["id"])
            migrated = read_match_meta(match_dir)
            timing = json.loads((match_dir / "reviewed_player_stats.json").read_text(encoding="utf-8"))["video_timing"]
            identity_status = get_reviewed_identity_status(match_dir)["status"]
            migrated_snapshot = json.loads((match_dir / "reviewed_identity_snapshot.json").read_text(encoding="utf-8"))
            # The migration has refreshed the derived review-progress digest,
            # so future rebuild authorization takes the normal workflow path
            # rather than requiring the historical-publication fallback.
            from app.main import _assert_publish_workflow
            _assert_publish_workflow(match_dir)

        self.assertEqual(rebuilt["id"], "published-match-1")
        self.assertEqual(migrated["video"]["timebase_schema_version"], "1.0.0")
        self.assertEqual(migrated["video"]["source"], "decoded_cfr_timebase")
        self.assertEqual(migrated["video"]["frame_count"], 50)
        self.assertAlmostEqual(migrated["video"]["duration_sec"], 2.0, places=3)
        self.assertEqual(timing["source"], "decoded_cfr_timebase")
        self.assertNotEqual(identity_status, "stale")
        self.assertEqual(migrated_snapshot["source"]["identity_source_semantics_version"], "timebase-insensitive-v2")
    def test_rebuild_updates_publication_preserving_stable_identity(self) -> None:
        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original title")
            published = publish_local_match("match-1", replace=False)
            self.assertEqual(published["id"], "published-match-1")
            before_summary = (root / "published" / "published-match-1" / "summary.json").read_bytes()
            before_report = (root / "published" / "published-match-1" / "public_report.json").read_bytes()

            self._set_local_title(root, "match-1", "Rebuilt title")
            rebuilt = api_rebuild_published_match("published-match-1")

            self.assertEqual(rebuilt["id"], "published-match-1")
            self.assertEqual(rebuilt["source_match_id"], "match-1")
            self.assertEqual(rebuilt["title"], "Rebuilt title")
            self.assertEqual(rebuilt["public_report"]["match"]["title"], "Rebuilt title")
            self.assertNotEqual(
                (root / "published" / "published-match-1" / "public_report.json").read_bytes(), before_report
            )
            self.assertNotEqual(
                (root / "published" / "published-match-1" / "summary.json").read_bytes(), before_summary
            )
            self.assertEqual(
                [path.name for path in (root / "published").iterdir() if path.is_dir()],
                ["published-match-1"],
            )
            # Publication remains fully readable.
            from app.services.json_publish_store import get_published_match

            fetched = get_published_match("published-match-1")
            self.assertEqual(fetched["title"], "Rebuilt title")

    def test_rebuild_rolls_back_restart_candidates_when_publication_replace_fails(self) -> None:
        from fastapi import HTTPException
        from app.main import api_rebuild_published_match, publish_local_match
        from app.services.ball_event_rebuild import PACKAGE_PUBLISH_REBUILD_OUTPUT_FILENAMES, rebuild_ball_event_artifacts
        from app.services.publish import PublishError

        with self._store() as root:
            match_dir = self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            old_publication = self._publication_bytes(root, "published-match-1")
            restart_path = match_dir / "restart_candidates.json"
            restart_path.write_bytes(b'{"candidates":[{"candidate_id":"old","setup_start_frame":3,"release_frame":8,"boundary_line":"touchline"}]}')
            (match_dir / "contact_candidates.json").write_text(json.dumps({"candidates": [{
                "candidate_id": "contact-a", "stable_player_id": "A01", "stable_subject_id": "A01", "team_label": "A",
                "start_frame": 0, "end_frame": 2, "start_time_sec": 0.0, "end_time_sec": 0.1,
                "start_ball_position_m": [10.0, 30.0], "end_ball_position_m": [10.0, 29.0], "mean_confidence": 0.9,
            }]}), encoding="utf-8")
            (match_dir / "possession_candidates.json").write_text(json.dumps({
                "parameters": {"pitch_width_m": 30.0, "pitch_length_m": 47.4},
                "frames": [{"frame": 0, "time_sec": 0.0, "status": "controlled", "team_label": "A", "ball_position_m": [10.0, 30.0], "confidence": 0.9}],
            }), encoding="utf-8")
            (match_dir / "possession_segments.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
            (match_dir / "match_phase_config.json").write_text(json.dumps({"periods": [{
                "period_id": "full", "start_time_sec": 0.0, "end_time_sec": 10.0,
                "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
            }], "summary": {"needs_review": False}}), encoding="utf-8")
            before_local = {
                name: (match_dir / name).read_bytes() if (match_dir / name).exists() else None
                for name in (
                    "match.json", "reviewed_player_stats.json", "reviewed_player_timeline.json",
                    "reviewed_player_heatmaps.json", "reviewed_stats_readiness.json", "reviewed_identity_snapshot.json",
                    "reviewed_output_manifest.json", "reviewed_video_manifest.json", "reviewed_video_job.json",
                    *PACKAGE_PUBLISH_REBUILD_OUTPUT_FILENAMES,
                )
            }
            package = json.loads((root / "published" / "published-match-1" / "package.json").read_text(encoding="utf-8"))
            normalized_restart_key = False

            def write_new_derived_files(_path: Path) -> dict:
                nonlocal normalized_restart_key
                rebuild_ball_event_artifacts(match_dir, trigger="package_publish")
                current_restart = json.loads(restart_path.read_text(encoding="utf-8"))
                normalized_restart_key = str(current_restart["candidates"][0].get("candidate_key") or "").startswith("restart:v1:")
                return package

            with patch("app.main.resolve_match_video_path", side_effect=FileNotFoundError), patch(
                "app.main.build_match_package", side_effect=write_new_derived_files
            ), patch("app.main.import_match_package", side_effect=PublishError("forced publication failure")):
                with self.assertRaises(HTTPException) as failure:
                    api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 400)
            self.assertTrue(normalized_restart_key)
            for name, previous in before_local.items():
                target = match_dir / name
                self.assertEqual(target.read_bytes() if target.exists() else None, previous)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), old_publication)

    def test_rebuild_rejects_source_identity_mismatch_without_mutation(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            before = self._publication_bytes(root, "published-match-1")

            # Local directory now claims a different physical source.
            self._set_local_match_id(root, "match-1", "match-2")
            with self.assertRaises(HTTPException) as failure:
                api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), before)
            self.assertFalse((root / "published" / "published-match-2").exists())

    def test_rebuild_unknown_published_id_creates_nothing(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match

        with self._store() as root:
            with self.assertRaises(HTTPException) as failure:
                api_rebuild_published_match("published-missing")

            self.assertEqual(failure.exception.status_code, 404)
            self.assertFalse((root / "published" / "published-missing").exists())

    def test_rebuild_missing_local_source_keeps_publication_intact(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            before = self._publication_bytes(root, "published-match-1")

            import shutil

            shutil.rmtree(root / "matches" / "match-1")
            with self.assertRaises(HTTPException) as failure:
                api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 404)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), before)

    def test_rebuild_unpublishable_source_keeps_old_snapshot_usable(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            before = self._publication_bytes(root, "published-match-1")

            (root / "matches" / "match-1" / "resolved_player_stats.json").unlink()
            with self.assertRaises(HTTPException) as failure:
                api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), before)

    def test_rebuild_enforces_publish_workflow_before_mutation(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            before = self._publication_bytes(root, "published-match-1")

            with patch(
                "app.main._assert_publish_workflow",
                side_effect=HTTPException(status_code=409, detail="review_not_completed"),
            ):
                with self.assertRaises(HTTPException) as failure:
                    api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), before)

    def test_rebuild_invokes_no_analysis_review_or_logical_side_effects(self) -> None:
        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            with (
                patch("app.main.run_match_analysis_and_update_meta") as analyze,
                patch(
                    "app.services.identity_reviewed_snapshot.finalize_reviewed_identity"
                ) as finalize_identity,
                patch(
                    "app.services.match_group_refresh.refresh_match_group_to_latest"
                ) as logical_refresh,
                patch(
                    "app.services.match_group_video.generate_match_group_video"
                ) as combined_video,
            ):
                rebuilt = api_rebuild_published_match("published-match-1")

            self.assertEqual(rebuilt["id"], "published-match-1")
            analyze.assert_not_called()
            finalize_identity.assert_not_called()
            logical_refresh.assert_not_called()
            combined_video.assert_not_called()

    def test_rebuild_makes_logical_group_refreshable_without_touching_it(self) -> None:
        from app.main import api_rebuild_published_match, publish_local_match
        from app.services.match_group_aggregation import generate_match_group_report
        from app.services.match_group_refresh import preview_match_group_refresh
        from app.services.match_groups import create_match_group, get_match_group

        with self._store() as root:
            self._local_match(root, "match-a", title="Half one", reviewed=True)
            self._local_match(root, "match-b", title="Half two", reviewed=True)
            publish_local_match("match-a", replace=False)
            publish_local_match("match-b", replace=False)

            group = create_match_group(
                member_published_ids=["published-match-a", "published-match-b"],
                metadata={"title": "Full match"},
            )
            group_id = str(group["group_id"])
            generate_match_group_report(group_id)
            self.assertEqual(preview_match_group_refresh(group_id)["status"], "current")
            group_manifest_before = (root / "groups" / group_id / "manifest.json").read_bytes()
            group_report_before = (root / "groups" / group_id / "public_report.json").read_bytes()

            # A meaningful local change, republished through the new endpoint.
            self._set_player_distance(root, "match-a", "p-a1", 99.0)
            rebuilt = api_rebuild_published_match("published-match-a")
            self.assertEqual(rebuilt["id"], "published-match-a")

            # The logical group was not refreshed automatically.
            self.assertEqual((root / "groups" / group_id / "manifest.json").read_bytes(), group_manifest_before)
            self.assertEqual((root / "groups" / group_id / "public_report.json").read_bytes(), group_report_before)
            self.assertEqual(get_match_group(group_id)["group_id"], group_id)

            preview = preview_match_group_refresh(group_id)
            self.assertEqual(preview["status"], "refreshable")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _local_match(self, root: Path, match_id: str, *, title: str, reviewed: bool = False) -> Path:
        match_dir = root / "matches" / match_id
        match_dir.mkdir(parents=True, exist_ok=True)
        write_rebuildable_match_fixture(match_dir, match_id=match_id, title=title)
        if reviewed:
            write_rebuildable_reviewed_fixture(match_dir)
        return match_dir

    @staticmethod
    def _set_local_title(root: Path, match_id: str, title: str) -> None:
        meta_path = root / "matches" / match_id / "match.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["title"] = title
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @staticmethod
    def _set_local_match_id(root: Path, match_id: str, new_id: str) -> None:
        meta_path = root / "matches" / match_id / "match.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["id"] = new_id
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @staticmethod
    def _set_player_distance(root: Path, match_id: str, player_id: str, distance: float) -> None:
        stats_path = root / "matches" / match_id / "reviewed_player_stats.json"
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        for player in stats["players"]:
            if player["player_id"] == player_id:
                player["total_distance_m"] = distance
        stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    @staticmethod
    def _publication_bytes(root: Path, published_id: str) -> dict[str, bytes]:
        directory = root / "published" / published_id
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    def _store(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "matches").mkdir(parents=True, exist_ok=True)
        patches = (
            patch("app.main.MATCHES_DIR", root / "matches"),
            patch("app.main._assert_publish_workflow", return_value=None),
            patch("app.services.json_publish_store.MATCHES_DIR", root / "matches"),
            patch("app.services.json_publish_store.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.public_match_report.CLIENT_PUBLIC_MATCHES_DIR", root / "mirror"),
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


if __name__ == "__main__":
    unittest.main()
