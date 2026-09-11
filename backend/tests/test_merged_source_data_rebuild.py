from __future__ import annotations

import ast
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import app.services.merged_source_data_rebuild as rebuild
from app.services.review_workflow_store import approval_is_current, current_approval_fingerprint
from app.services.identity_review_scope import identity_review_scope_digest


class MergedSourceDataRebuildTests(unittest.TestCase):
    def _source(self, root: Path, *, job_snapshot: str = "identity-old") -> tuple[dict[str, object], Path]:
        match_path = root / "matches" / "source-one"
        match_path.mkdir(parents=True)
        (match_path / "match.json").write_text(json.dumps({"id": "source-one"}), encoding="utf-8")
        (match_path / "reviewed_video_job.json").write_text(
            json.dumps({
                "status": "completed",
                "source_snapshot_digest": job_snapshot,
                "source_review_scope_digest": identity_review_scope_digest({"id": "source-one"}),
                "source_video_digest": "raw-video",
            }),
            encoding="utf-8",
        )
        (match_path / "reviewed_identity_progress.json").write_text(
            json.dumps({"status": "ready", "source_snapshot_digest": "identity-new", "summary": {}, "coverage_readiness": {"allows_finalize": True}}),
            encoding="utf-8",
        )
        member = {"published_id": "published-source-one", "source_match_id": "source-one"}
        return member, match_path

    def _preflight_patches(self, root: Path, *, job_snapshot: str = "identity-old"):
        member, _ = self._source(root, job_snapshot=job_snapshot)
        return member, (
            patch.object(rebuild, "MATCHES_DIR", root / "matches"),
            patch.object(rebuild, "get_published_match", return_value={
                "source_kind": "physical",
                "source_match_id": "source-one",
                "package": {"reviewed_player_stats": {"source_snapshot_digest": "identity-old"}},
            }),
            patch.object(rebuild, "get_reviewed_identity_status", return_value={"status": "partial_reviewed", "semantic_digest": "identity-new"}),
            patch.object(rebuild, "reviewed_source_video_path", return_value=root / "raw-video.mp4"),
            patch.object(rebuild, "sha256_file", return_value="raw-video"),
            patch.object(rebuild, "reviewed_stats_artifact_is_current", return_value=False),
        )

    def test_newer_identity_keeps_video_historical_but_allows_stats_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            member, patches = self._preflight_patches(Path(temporary), job_snapshot="identity-old")
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                result = rebuild._preflight_source(member)
        self.assertEqual(result["classification"], "safe_stats_only")
        self.assertEqual(result["derived_data_status"], "stale")
        self.assertEqual(result["video_disposition"], "historical_preserved")
        self.assertEqual(result["qa_disposition"], "historical_preserved")

    def test_matching_identity_preserves_current_visual_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            member, patches = self._preflight_patches(Path(temporary), job_snapshot="identity-new")
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                result = rebuild._preflight_source(member)
        self.assertEqual(result["classification"], "safe_stats_only")
        self.assertEqual(result["video_disposition"], "current_preserved")
        self.assertEqual(result["qa_disposition"], "current_preserved")

    def test_changed_review_scope_keeps_video_and_qa_historical_with_same_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            member, patches = self._preflight_patches(Path(temporary), job_snapshot="identity-new")
            job_path = Path(temporary) / "matches" / "source-one" / "reviewed_video_job.json"
            job = json.loads(job_path.read_text(encoding="utf-8"))
            job["source_review_scope_digest"] = "scope-before-change"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                result = rebuild._preflight_source(member)
        self.assertEqual(result["classification"], "safe_stats_only")
        self.assertEqual(result["video_disposition"], "historical_preserved")
        self.assertEqual(result["qa_disposition"], "historical_preserved")

    def test_unproven_raw_video_binding_blocks_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            member, patches = self._preflight_patches(Path(temporary), job_snapshot="identity-new")
            with patches[0], patches[1], patches[2], patches[3], patch.object(rebuild, "sha256_file", return_value="different-raw-video"), patches[5]:
                result = rebuild._preflight_source(member)
        self.assertEqual(result["classification"], "blocked")
        self.assertEqual(result["blocking_code"], "source_video_binding_unproven")

    def test_mismatched_published_source_identity_blocks_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            member, patches = self._preflight_patches(Path(temporary), job_snapshot="identity-new")
            with patches[0], patch.object(rebuild, "get_published_match", return_value={
                "source_kind": "physical", "source_match_id": "another-source", "package": {},
            }), patches[2], patches[3], patches[4], patches[5]:
                result = rebuild._preflight_source(member)
        self.assertEqual(result["classification"], "blocked")
        self.assertEqual(result["blocking_code"], "physical_publication_binding_unproven")

    def test_known_curated_physical_projection_without_sidecar_blocks_source_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            member, patches = self._preflight_patches(Path(temporary), job_snapshot="identity-new")
            published = {
                "source_kind": "physical", "source_match_id": "source-one", "package": {},
                "public_report": {"key_moments": {"policy_version": "editorial-curated:v1", "moments": [{"moment_id": "manual-1"}]}},
            }
            with patches[0], patch.object(rebuild, "get_published_match", return_value=published), patch(
                "app.services.key_moment_editor.EDITORIAL_DIRECTORY", Path(temporary) / "editorial"
            ):
                result = rebuild._preflight_source(member)
        self.assertEqual(result["classification"], "blocked")
        self.assertEqual(result["blocking_code"], "key_moment_editorial_recovery_required")

    def test_missing_review_progress_blocks_before_stats_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            member, patches = self._preflight_patches(Path(temporary), job_snapshot="identity-new")
            progress_path = Path(temporary) / "matches" / "source-one" / "reviewed_identity_progress.json"
            progress_path.unlink()
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                result = rebuild._preflight_source(member)
        self.assertEqual(result["classification"], "blocked")
        self.assertEqual(result["blocking_code"], "review_progress_missing")

    def test_active_reviewed_render_blocks_before_any_stats_or_publication_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            member, patches = self._preflight_patches(Path(temporary), job_snapshot="identity-new")
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch.object(
                rebuild, "reviewed_output_status_read_only", return_value={"status": "running"}
            ):
                source = rebuild._preflight_source(member)
        self.assertEqual(source["classification"], "blocked")
        self.assertEqual(source["blocking_code"], "reviewed_render_in_progress")
        with patch.object(rebuild, "_group_id_for_merged", return_value="match-group-one"), patch.object(
            rebuild, "_preflight", return_value={"status": "blocked", "sources": [source], "blocking_reasons": [{"code": "reviewed_render_in_progress"}]}
        ), patch.object(rebuild, "_rebuild_one_source") as rebuild_one, patch.object(
            rebuild, "import_match_package"
        ) as publish, patch.object(rebuild, "_refresh_merged_match_to_latest_locked") as refresh:
            result = rebuild.run_merged_source_data_rebuild(
                "published-merged-00000000-0000-4000-8000-000000000001",
                package_builder=lambda _path: {},
            )
        self.assertEqual(result["status"], "blocked")
        rebuild_one.assert_not_called()
        publish.assert_not_called()
        refresh.assert_not_called()

    def test_writer_reservation_race_blocks_before_stats_mutation(self) -> None:
        source = {
            "published_id": "published-source-one",
            "source_match_id": "source-one",
            "classification": "safe_stats_only",
        }
        with patch.object(rebuild, "reserve_reviewed_output_idle", side_effect=rebuild.ReviewedOutputBusyError("busy")), patch.object(
            rebuild, "build_reviewed_stats"
        ) as build_stats, patch.object(rebuild, "import_match_package") as publish:
            with self.assertRaisesRegex(rebuild.SourceDataRebuildError, "Reviewed render"):
                rebuild._rebuild_one_source(source, lambda _path: {}, progress=lambda _phase: None)
        build_stats.assert_not_called()
        publish.assert_not_called()

    def test_data_provenance_refresh_never_rebinds_visual_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            match_path = Path(temporary)
            original_video = {
                "status": "completed",
                "digest": "video-bytes",
                "source_snapshot_digest": "identity-old",
            }
            (match_path / "reviewed_output_manifest.json").write_text(json.dumps({
                "reviewed_identity": {"status": "fresh", "digest": "identity-old"},
                "stats": {"status": "completed", "source_snapshot_digest": "identity-old"},
                "video": original_video,
            }), encoding="utf-8")
            documents = {
                "reviewed_player_stats.json": {"players": [{"player_id": "one"}]},
                "reviewed_stats_readiness.json": {"status": "completed"},
            }
            rebuild._refresh_data_provenance(
                match_path,
                {"semantic_digest": "identity-new"},
                {"id": "source-one"},
                documents,
            )
            updated = json.loads((match_path / "reviewed_output_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["reviewed_identity"]["digest"], "identity-new")
        self.assertEqual(updated["stats"]["source_snapshot_digest"], "identity-new")
        self.assertEqual(updated["video"], original_video)
        self.assertEqual(updated["review_video_generation"]["status"], "historical")
        self.assertEqual(updated["review_video_generation"]["source_identity_digest"], "identity-old")

    def test_scope_change_marks_visual_generation_historical_without_touching_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            match_path = Path(temporary)
            original_video = {
                "status": "completed",
                "digest": "video-bytes",
                "source_snapshot_digest": "identity-same",
                "source_review_scope_digest": "scope-before-change",
            }
            (match_path / "reviewed_output_manifest.json").write_text(json.dumps({
                "reviewed_identity": {"status": "fresh", "digest": "identity-same"},
                "stats": {"status": "completed", "source_snapshot_digest": "identity-same"},
                "video": original_video,
            }), encoding="utf-8")
            (match_path / "reviewed_video_job.json").write_text(json.dumps({
                "source_review_scope_digest": "scope-before-change"}), encoding="utf-8")
            documents = {
                "reviewed_player_stats.json": {"players": [{"player_id": "one"}]},
                "reviewed_stats_readiness.json": {"status": "completed"},
            }
            rebuild._refresh_data_provenance(
                match_path,
                {"semantic_digest": "identity-same"},
                {"id": "source-one", "identity_review_scope": {"teams": {"A": "complete_roster", "B": "team_stats_only"}}},
                documents,
            )
            updated = json.loads((match_path / "reviewed_output_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["video"], original_video)
        self.assertEqual(updated["review_video_generation"]["status"], "historical")
        old_fingerprints = current_approval_fingerprint(
            "identity-same", {"version": "old"}, {"video_digest": "video-bytes"}, {"video": original_video}
        )
        approval = {
            key: value
            for key, value in old_fingerprints.items()
            if key not in {"reviewed_output_data_maintenance", "reviewed_visual_generation_status"}
        }
        refreshed = current_approval_fingerprint(
            "identity-same", {"version": "new"}, {"video_digest": "video-bytes"}, updated
        )
        self.assertFalse(approval_is_current(approval, refreshed))

    def test_stats_only_change_keeps_qa_current_only_when_visual_identity_still_matches(self) -> None:
        job = {"video_digest": "video-bytes"}
        old_manifest = {"video": {"source_snapshot_digest": "identity-same"}}
        old_fingerprints = current_approval_fingerprint(
            "identity-same", {"version": "old"}, job, old_manifest
        )
        approval = {key: value for key, value in old_fingerprints.items() if key != "reviewed_output_data_maintenance"}
        refreshed_manifest = {
            "video": {"source_snapshot_digest": "identity-same"},
            "data_generation": {"maintenance": "stats_only"},
            "review_video_generation": {"status": "current"},
        }
        refreshed = current_approval_fingerprint(
            "identity-same", {"version": "new"}, job, refreshed_manifest
        )
        self.assertTrue(approval_is_current(approval, refreshed))
        historical = current_approval_fingerprint(
            "identity-new", {"version": "new"}, job, refreshed_manifest
        )
        self.assertFalse(approval_is_current(approval, historical))

    def test_multiple_sources_refresh_merged_only_after_each_source_finishes(self) -> None:
        job = {
            "job_id": "job-one",
            "group_id": "match-group-one",
            "merged_published_match_id": "published-merged-00000000-0000-4000-8000-000000000001",
            "status": "queued",
            "sources": [],
        }
        sources = [
            {"published_id": "published-one", "source_match_id": "one", "classification": "safe_stats_only"},
            {"published_id": "published-two", "source_match_id": "two", "classification": "already_current"},
        ]
        writes: list[dict[str, object]] = []
        with patch.object(rebuild, "reserve_match_group_video_idle", return_value=nullcontext()), patch.object(
            rebuild, "_preflight", return_value={"status": "ready", "sources": sources}
        ), patch.object(rebuild, "_rebuild_one_source", side_effect=[
            {**sources[0], "result": "rebuilt"}, {**sources[1], "result": "already_current"},
        ]) as rebuild_one, patch.object(rebuild, "_refresh_merged_match_to_latest_locked", return_value={"status": "refreshed"}) as refresh, patch.object(
            rebuild, "_write_job", side_effect=lambda _group, value: writes.append(dict(value))
        ):
            result = rebuild._run_job(job, lambda _path: {})
        self.assertEqual(result["status"], "completed")
        self.assertEqual([row["result"] for row in result["sources"]], ["rebuilt", "already_current"])
        self.assertEqual(rebuild_one.call_count, 2)
        refresh.assert_called_once_with("match-group-one")
        self.assertEqual(result["merged"]["status"], "refreshed")
        self.assertTrue(any(row.get("progress", {}).get("phase") == "refreshing_merged" for row in writes))

    def test_blocked_preflight_does_not_rebuild_or_refresh(self) -> None:
        with patch.object(rebuild, "_group_id_for_merged", return_value="match-group-one"), patch.object(
            rebuild, "_preflight", return_value={"status": "blocked", "sources": [], "blocking_reasons": [{"code": "blocked"}]}
        ), patch.object(rebuild, "_refresh_merged_match_to_latest_locked") as refresh:
            result = rebuild.run_merged_source_data_rebuild("published-merged-00000000-0000-4000-8000-000000000001", package_builder=lambda _path: {})
        self.assertEqual(result["status"], "blocked")
        refresh.assert_not_called()

    def test_source_failure_keeps_previous_merged_publication_readable_and_does_not_refresh(self) -> None:
        job = {
            "job_id": "job-one",
            "group_id": "match-group-one",
            "merged_published_match_id": "published-merged-00000000-0000-4000-8000-000000000001",
            "status": "queued",
            "sources": [],
        }
        source = {"published_id": "published-one", "source_match_id": "one", "classification": "safe_stats_only"}
        with patch.object(rebuild, "reserve_match_group_video_idle", return_value=nullcontext()), patch.object(
            rebuild, "_preflight", return_value={"status": "ready", "sources": [source]}
        ), patch.object(rebuild, "_rebuild_one_source", side_effect=ValueError("package staging failed")), patch.object(
            rebuild, "_refresh_merged_match_to_latest_locked"
        ) as refresh, patch.object(rebuild, "_write_job"):
            result = rebuild._run_job(job, lambda _path: {})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure"]["code"], "source_rebuild_failed")
        self.assertEqual(result["failure"]["published_id"], "published-one")
        refresh.assert_not_called()

    def test_orphaned_durable_job_is_reported_failed_not_running_forever(self) -> None:
        job = {
            "job_id": "orphaned",
            "status": "running",
            "group_id": "match-group-one",
            "merged_published_match_id": "published-merged-00000000-0000-4000-8000-000000000001",
            "worker_pid": -1,
            "progress": {"phase": "building_stats"},
        }
        with patch.object(rebuild, "_group_id_for_merged", return_value="match-group-one"), patch.object(
            rebuild, "_load", return_value=job
        ), patch.object(rebuild, "_write_job") as write:
            result = rebuild.get_merged_source_data_rebuild_status(job["merged_published_match_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure"]["code"], "job_worker_unavailable")
        write.assert_called_once()

    def test_stats_only_service_has_no_render_or_cv_entrypoint(self) -> None:
        source = Path(rebuild.__file__).read_text(encoding="utf-8")
        names = {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}
        self.assertFalse({"render_reviewed_video", "generate_reviewed_output", "analyze_match", "analyze_match_ball_yolo"} & names)
        self.assertNotIn("reviewed_source_video_digest", source)
