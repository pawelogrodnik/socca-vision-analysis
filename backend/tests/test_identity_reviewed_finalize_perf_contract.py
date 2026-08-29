from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_coverage import paginate_progress
from app.services.identity_reviewed_hot_state import (
    FILENAME,
    hot_progress,
    last_hot_state_build_phases,
    load_existing_fresh_hot_state,
    load_or_rebuild_review_hot_state,
    rebuild_review_hot_state,
)
from app.services.identity_reviewed_progress import (
    PROGRESS_SCHEMA_VERSION,
    required_queue_descriptor,
    required_queue_source_keys,
)
from app.services.identity_reviewed_video import (
    DIGEST_CACHE_FILENAME,
    reviewed_source_video_digest,
)
from app.services.review_workflow_orchestrator import (
    public_finalized_identity,
    refresh_review_after_identity_mutation,
)


EXACT_OWNERSHIP_KEYS = (
    "detected_pairs",
    "detected_pair_runs",
    "owned_observations",
    "owned_observation_runs",
    "_potential_named_observation_pairs",
    "_potential_named_observation_runs",
)

LARGE_UNIT_BASE = {
    "candidate_subject_id": "subject-large",
    "review_target_id": None,
    "scope_kind": "whole_subject",
    "source_team_label": "A",
    "effective_team_label": "A",
    "coverage_team_label": "A",
    "tracklet_ids": ["t-1"],
    "tracklet_count": 1,
    "frame_start": 0,
    "frame_end": 2400,
    "detected_frame_count": 2001,
    "detected_observation_count": 2001,
    "detected_time_sec": 83.375,
    "current_resolution_status": "pending_high_priority",
    "priority": "high",
    "reason_codes": ["unresolved_named_slot"],
    "operator_actionable": True,
    "non_actionable_reason": None,
    "source_ownership_digest": "digest-1",
    "visual_evidence": {"kind": "identity_continuity", "anchor_crops": [{"artifact": "crop.jpg"}]},
}


def _match() -> dict:
    return {"id": "perf-contract", "teams": [{"team_label": "A", "players": [{"id": "p1", "name": "Player"}]}]}


def _large_unit() -> dict:
    unit = dict(LARGE_UNIT_BASE)
    unit["detected_pairs"] = [[("t-1", frame)] for frame in range(2001)]
    unit["detected_pair_runs"] = {"t-1": [[0, 2000]]}
    unit["owned_observations"] = [
        {"tracklet_id": "t-1", "frame": frame, "x": 0.1, "y": 0.2}
        for frame in range(2001)
    ]
    unit["owned_observation_runs"] = {"t-1": [[0, 2000]]}
    unit["_potential_named_observation_pairs"] = [("t-1", frame) for frame in range(2001)]
    unit["_potential_named_observation_runs"] = {"t-1": [[0, 2000]]}
    return unit


class _HotStateWorkspaceTestCase(unittest.TestCase):
    def _workspace(self) -> tempfile.TemporaryDirectory:
        raise NotImplementedError


class ReviewProgressPayloadContractTests(unittest.TestCase):
    """Public queue pages never expose server-only exact ownership (§45E)."""

    def test_public_page_strips_exact_ownership_from_large_case(self) -> None:
        progress = {
            "next_cases": [_large_unit()],
            "_internal_review_units": [_large_unit()],
            "_projection_inputs": {"pair_index_runs": {}},
            "summary": {"important_decisions_remaining": 1},
            "mixed_players": {
                "schema_version": "1.0.0",
                "mode": "queue",
                "match_id": "m1",
                "summary": {"total": 1, "unresolved": 1, "resolved": 0, "complex_unresolved": 0},
                "assignment_options": {"roster": [], "slots": []},
                "cases": [{
                    "case_id": "case-1",
                    "candidate_subject_id": "subject-mixed",
                    "original_issue": "mixed_players",
                    "mixed_hint": "cross_team",
                    "resolution_status": "unresolved",
                    "observation_count": 42,
                    "source_subject_digest": "digest",
                    "source_tracklet_ids": ["t-9"],
                    "frame_start": 5,
                    "frame_end": 90,
                    "temporal_evidence": {"status": "ready", "anchor_crops": [{"artifact": "a.jpg"}]},
                    "source": {"owned_observations": [{"tracklet_id": "t-9", "frame": 5}]},
                }],
            },
        }
        page = paginate_progress(progress, offset=0, limit=20, team_label=None, queue="required")

        self.assertEqual(len(page["next_cases"]), 1)
        case = page["next_cases"][0]
        for key in EXACT_OWNERSHIP_KEYS:
            self.assertNotIn(key, case)
        # Frontend navigation contract stays intact.
        self.assertEqual(case["candidate_subject_id"], "subject-large")
        self.assertEqual(case["detected_observation_count"], 2001)
        self.assertEqual(case["tracklet_ids"], ["t-1"])
        self.assertEqual(case["filter_team_label"], "A")
        self.assertNotIn("_internal_review_units", page)
        self.assertNotIn("_projection_inputs", page)
        mixed_case = page["mixed_players"]["cases"][0]
        self.assertNotIn("temporal_evidence", mixed_case)
        self.assertNotIn("source_subject_digest", mixed_case)
        self.assertNotIn("source_tracklet_ids", mixed_case)
        self.assertTrue(mixed_case["has_exact_source"])

    def test_pagination_semantics_unchanged_for_queues_teams_and_offsets(self) -> None:
        cases = [
            {
                **LARGE_UNIT_BASE,
                "candidate_subject_id": f"subject-{index}",
                "effective_team_label": "B" if index % 2 else "A",
                "coverage_team_label": "B" if index % 2 else "A",
            }
            for index in range(25)
        ]
        progress = {"next_cases": cases}

        required = paginate_progress(progress, offset=0, limit=20, queue="required")
        self.assertEqual(required["pagination"], {
            "offset": 0, "limit": 20, "returned": 20,
            "total_remaining": 25, "global_total_remaining": 25, "has_more": True,
        })
        offset_page = paginate_progress(progress, offset=20, limit=20, queue="required")
        self.assertEqual(offset_page["pagination"]["returned"], 5)
        self.assertFalse(offset_page["pagination"]["has_more"])
        team_b = paginate_progress(progress, offset=0, limit=20, team_label="B", queue="required")
        self.assertEqual(team_b["filters"]["counts"]["B"], 12)
        self.assertTrue(all(row["filter_team_label"] == "B" for row in team_b["next_cases"]))
        with self.assertRaises(ValueError):
            paginate_progress(progress, queue="everything")


class ReviewProgressColdWarmContractTests(unittest.TestCase):
    def _progress(self) -> dict:
        unit = {
            **LARGE_UNIT_BASE,
            "detected_observation_count": 2,
            "detected_pairs": [["t-1", 10], ["t-1", 11]],
        }
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "status": "ready",
            "source_snapshot_digest": "snapshot",
            "next_cases": [dict(unit)],
            "optional_audit_cases": [],
            "summary": {"important_decisions_remaining": 1},
            "_internal_review_units": [unit],
            "_projection_inputs": {
                "match_id": "perf-contract",
                "coverage": {},
                "technical_diagnostics": {},
                "pair_index_runs": {
                    "t-1": [[10, 11, {"identity_status": "unresolved", "team_label": "A", "canonical_player_id": None}]],
                },
                "observed_pair_runs": {"t-1": [[10, 11]]},
                "mixed_players": {},
                "deferred_correction_context": {},
            },
        }

    def test_warm_and_cold_projections_keep_exact_required_sources_identical(self) -> None:
        progress = self._progress()
        second = {
            **LARGE_UNIT_BASE,
            "candidate_subject_id": "subject-segment",
            "review_target_id": "segment-1",
            "scope_kind": "canonical_segment",
            "continuity_group_id": "continuity-1",
            "source_ownership_digest": "digest-2",
            "team_attribution_evidence_source_digest": "evidence-2",
            "detected_pairs": [["t-2", 20]],
        }
        progress["next_cases"] = [progress["next_cases"][0], second]
        progress["summary"] = {"important_decisions_remaining": 2}
        progress["required_queue"] = required_queue_descriptor(progress)
        progress["coverage_readiness"] = {
            "status": "incomplete",
            "allows_finalize": False,
        }
        progress["mixed_players"] = {"summary": {"unresolved": 1, "total": 1, "resolved": 0}}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            side_effect=lambda *args, **kwargs: self._progress_with_required_sources(),
        ):
            root = Path(tmp)
            match = _match()
            warm = rebuild_review_hot_state(root, match, prebuilt_progress=progress)
            warm_progress = hot_progress(warm)

            # Force the same canonical inputs through the cold path rather
            # than merely reading the warm cache written above.
            (root / FILENAME).unlink()
            cold = load_or_rebuild_review_hot_state(root, match)
            cold_progress = hot_progress(cold)

        expected_sources = required_queue_source_keys(progress)
        self.assertEqual(required_queue_source_keys(warm_progress), expected_sources)
        self.assertEqual(required_queue_source_keys(cold_progress), expected_sources)
        self.assertEqual(warm_progress["required_queue"], progress["required_queue"])
        self.assertEqual(cold_progress["required_queue"], progress["required_queue"])
        self.assertEqual(
            paginate_progress(warm_progress, queue="required")["pagination"]["global_total_remaining"],
            len(expected_sources),
        )
        self.assertEqual(warm_progress["mixed_players"]["summary"], cold_progress["mixed_players"]["summary"])
        self.assertEqual(warm_progress["coverage_readiness"], cold_progress["coverage_readiness"])

    def _progress_with_required_sources(self) -> dict:
        progress = self._progress()
        second = {
            **LARGE_UNIT_BASE,
            "candidate_subject_id": "subject-segment",
            "review_target_id": "segment-1",
            "scope_kind": "canonical_segment",
            "continuity_group_id": "continuity-1",
            "source_ownership_digest": "digest-2",
            "team_attribution_evidence_source_digest": "evidence-2",
            "detected_pairs": [["t-2", 20]],
        }
        progress["next_cases"] = [progress["next_cases"][0], second]
        progress["summary"] = {"important_decisions_remaining": 2}
        progress["required_queue"] = required_queue_descriptor(progress)
        progress["coverage_readiness"] = {
            "status": "incomplete",
            "allows_finalize": False,
        }
        progress["mixed_players"] = {"summary": {"unresolved": 1, "total": 1, "resolved": 0}}
        return progress

    def test_cold_build_once_then_warm_hits_without_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            return_value=self._progress(),
        ) as build:
            root = Path(tmp)
            match = _match()

            cold = load_or_rebuild_review_hot_state(root, match)
            self.assertEqual(build.call_count, 1)

            warm_probe = load_existing_fresh_hot_state(root, match)
            self.assertIsNotNone(warm_probe)
            self.assertEqual(build.call_count, 1)

            again = load_or_rebuild_review_hot_state(root, match)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(
                again["progress"]["summary"],
                cold["progress"]["summary"],
            )

    def test_deleted_cache_recovers_with_exactly_one_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            side_effect=lambda *args, **kwargs: self._progress(),
        ) as build:
            root = Path(tmp)
            match = _match()
            load_or_rebuild_review_hot_state(root, match)
            self.assertEqual(build.call_count, 1)
            (root / FILENAME).unlink()
            self.assertIsNone(load_existing_fresh_hot_state(root, match))
            load_or_rebuild_review_hot_state(root, match)
            self.assertEqual(build.call_count, 2)
            self.assertIsNotNone(load_existing_fresh_hot_state(root, match))
            self.assertEqual(build.call_count, 2)

    def test_structural_invalidation_rebuilds_exactly_once_then_warm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            side_effect=lambda *args, **kwargs: self._progress(),
        ) as build:
            root = Path(tmp)
            match = _match()
            load_or_rebuild_review_hot_state(root, match)
            self.assertIsNotNone(load_existing_fresh_hot_state(root, match))

            # Structural mutations invalidate via the snapshot fingerprint.
            snapshot = root / "reviewed_identity_snapshot.json"
            snapshot.write_text(json.dumps({"semantic_digest": "after-split"}), encoding="utf-8")

            self.assertIsNone(load_existing_fresh_hot_state(root, match))
            load_or_rebuild_review_hot_state(root, match)
            self.assertEqual(build.call_count, 2)
            self.assertIsNotNone(load_existing_fresh_hot_state(root, match))
            self.assertEqual(build.call_count, 2)

    def test_prebuilt_progress_is_reused_without_second_canonical_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
        ) as build, patch(
            "app.services.identity_reviewed_hot_state.build_materialized_reviewed_slot_registry",
            return_value={},
        ), patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_slot_registry",
            return_value={},
        ):
            progress = self._progress()
            rebuild_review_hot_state(Path(tmp), _match(), prebuilt_progress=progress)
            build.assert_not_called()

    def test_hot_state_rebuild_exposes_materialization_subphases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_hot_state.build_materialized_reviewed_slot_registry",
            return_value={},
        ), patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_slot_registry",
            return_value={},
        ):
            rebuild_review_hot_state(Path(tmp), _match(), prebuilt_progress=self._progress())

        phases = last_hot_state_build_phases()
        self.assertTrue({
            "materialized_slot_registry_ms",
            "canonical_segment_registry_ms",
            "exact_whole_subject_digest_attachment_ms",
            "legacy_context_attachment_ms",
            "correction_temporal_evidence_attachment_ms",
            "historical_repair_materialization_ms",
            "temporal_split_context_attachment_ms",
            "lookup_source_index_build_ms",
            "freshness_calculation_ms",
            "durable_encoding_ms",
            "hot_state_json_write_ms",
            "total_ms",
        }.issubset(phases))

    def test_structural_reproject_warms_the_immediate_progress_read_without_another_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            side_effect=lambda *args, **kwargs: self._progress(),
        ) as recovery_build, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "after-split"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            side_effect=lambda *args, **kwargs: self._progress(),
        ) as reproject_build, patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value={"issues": {"blocking": 1}, "phase": "review"},
        ):
            root = Path(tmp)
            match = _match()

            load_or_rebuild_review_hot_state(root, match)
            self.assertEqual(recovery_build.call_count, 1)
            self.assertIsNotNone(load_existing_fresh_hot_state(root, match))

            # A structural save changes canonical topology and invalidates the
            # old generation before the explicit Review reproject.
            (root / "reviewed_identity_snapshot.json").write_text(
                json.dumps({"semantic_digest": "after-split"}),
                encoding="utf-8",
            )
            self.assertIsNone(load_existing_fresh_hot_state(root, match))

            refresh_review_after_identity_mutation(
                root,
                match,
                source="mixed_players_reproject",
                operator_evidence=False,
                leave_hot_state_warm=True,
            )
            self.assertEqual(reproject_build.call_count, 1)
            self.assertEqual(recovery_build.call_count, 1)

            # The real immediate GET review-progress consumes the warm
            # generation and performs zero additional canonical builds.
            from fastapi import Response
            from app.main import get_match_reviewed_identity_progress

            with patch("app.main.match_dir", return_value=root), patch(
                "app.main.read_match_meta", return_value=match
            ):
                response = get_match_reviewed_identity_progress(
                    match["id"],
                    Response(),
                    offset=0,
                    limit=20,
                    queue="required",
                )
            self.assertEqual(response["summary"]["important_decisions_remaining"], 1)
            self.assertEqual(response["server_timing"]["review_hot_state_source"], "warm_hit")
            self.assertEqual(reproject_build.call_count, 1)
            self.assertEqual(recovery_build.call_count, 1)


class FinalizeResponseContractTests(unittest.TestCase):
    def test_authoritative_snapshot_builder_runs_exactly_once_per_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ) as finalize, patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}, "next_cases": [], "optional_audit_cases": []},
        ) as progress, patch(
            "app.services.review_workflow_orchestrator.render_segment_review_evidence",
            return_value=set(),
        ), patch(
            "app.services.review_workflow_orchestrator.render_mixed_review_evidence",
            return_value=set(),
        ), patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={"summary": {}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value={"issues": {"blocking": 0}, "phase": "ready_to_finalize"},
        ):
            refreshed = refresh_review_after_identity_mutation(Path(tmp), _match(), source="finalize_check")
            finalize.assert_called_once()
            progress.assert_called_once()
            self.assertEqual(refreshed["snapshot"]["semantic_digest"], "identity")

    def test_operator_evidence_gating_skips_rendering_on_finalize_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}, "next_cases": [], "optional_audit_cases": []},
        ), patch(
            "app.services.review_workflow_orchestrator.render_segment_review_evidence",
            return_value=set(),
        ) as segment_evidence, patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={"summary": {}},
        ) as team_evidence, patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value={"issues": {"blocking": 0}, "phase": "ready_to_finalize"},
        ):
            refresh_review_after_identity_mutation(
                Path(tmp),
                _match(),
                source="finalize_check",
                operator_evidence=False,
            )
            segment_evidence.assert_not_called()
            team_evidence.assert_not_called()

    def test_finalize_refresh_leaves_hot_state_warm_with_prebuilt_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={
                "summary": {},
                "next_cases": [],
                "optional_audit_cases": [],
                "_internal_review_units": [_large_unit()],
                "_projection_inputs": {},
            },
        ) as progress, patch(
            "app.services.identity_reviewed_hot_state.rebuild_review_hot_state",
            return_value={},
        ) as hot_rebuild, patch(
            "app.services.review_workflow_orchestrator.render_segment_review_evidence",
            return_value=set(),
        ), patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={"summary": {}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value={"issues": {"blocking": 0}, "phase": "ready_to_finalize"},
        ):
            refresh_review_after_identity_mutation(
                Path(tmp),
                _match(),
                source="finalize_check",
                leave_hot_state_warm=True,
            )
            progress.assert_called_once()
            hot_rebuild.assert_called_once()
            self.assertIsNotNone(hot_rebuild.call_args.kwargs.get("prebuilt_progress"))

    def test_reproject_performance_contains_snapshot_and_hot_state_subphases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.last_snapshot_build_phases",
            return_value={"segment_review_ms": 12.3, "total_ms": 45.6},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}, "next_cases": [], "optional_audit_cases": []},
        ), patch(
            "app.services.identity_reviewed_hot_state.rebuild_review_hot_state",
            return_value={},
        ), patch(
            "app.services.identity_reviewed_hot_state.last_hot_state_build_phases",
            return_value={"durable_encoding_ms": 7.8, "total_ms": 9.0},
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value={"issues": {"blocking": 0}, "phase": "ready_to_finalize"},
        ):
            refreshed = refresh_review_after_identity_mutation(
                Path(tmp),
                _match(),
                source="mixed_players_reproject",
                operator_evidence=False,
                leave_hot_state_warm=True,
            )

        self.assertEqual(
            refreshed["performance"]["finalize_phases"]["segment_review_ms"],
            12.3,
        )
        self.assertEqual(
            refreshed["performance"]["hot_state_warm_write_phases"]["durable_encoding_ms"],
            7.8,
        )
        self.assertEqual(refreshed["performance"]["finalize_segment_review_ms"], 12.3)
        self.assertEqual(refreshed["performance"]["hot_durable_encoding_ms"], 7.8)

    def test_public_finalized_identity_drops_large_internal_arrays(self) -> None:
        snapshot = {
            "status": "complete_reviewed",
            "semantic_digest": "digest-1",
            "summary": {"entities": 2},
            "identity_coverage": {"named_observation_coverage": 0.9},
            "coverage_readiness": {"allows_finalize": True},
            "source": {"analysis_run_id": "run-1"},
            "entities": [{"player_id": f"p{index}"} for index in range(1000)],
            "canonical_observation_assignments": [
                {"observation": index} for index in range(100_000)
            ],
            "segment_observation_assignments": [{"observation": index} for index in range(100_000)],
            "observation_demotions": [{"row": index} for index in range(10_000)],
            "observation_overrides": [{"row": index} for index in range(10_000)],
        }
        public = public_finalized_identity(snapshot)
        for key in (
            "canonical_observation_assignments",
            "segment_observation_assignments",
            "observation_demotions",
            "observation_overrides",
            "entities",
        ):
            self.assertNotIn(key, public)
        self.assertEqual(public["status"], "complete_reviewed")
        self.assertEqual(public["semantic_digest"], "digest-1")
        self.assertEqual(public["entities_total"], 1000)
        serialized = json.dumps(public)
        self.assertLess(len(serialized.encode("utf-8")), 4096)


class SourceVideoDigestCacheTests(unittest.TestCase):
    def _video(self, root: Path, payload: bytes = b"fake-video-bytes") -> Path:
        video = root / "video.mp4"
        video.write_bytes(payload)
        return video

    def test_same_source_reuses_sha_across_calls_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = self._video(root)
            match = {"id": "m1", "video_filename": "video.mp4"}
            with patch(
                "app.services.identity_reviewed_video._sha",
                return_value="a" * 64,
            ) as sha:
                first = reviewed_source_video_digest(root, match)
                second = reviewed_source_video_digest(root, match)
                # A restart keeps no process state; the durable cache answers.
                third = reviewed_source_video_digest(root, match)
            self.assertEqual((first, second, third), ("a" * 64,) * 3)
            self.assertEqual(sha.call_count, 1)
            cached = json.loads((root / DIGEST_CACHE_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(cached["sha256"], "a" * 64)
            self.assertEqual(cached["fingerprint"]["path"], str(video.resolve()))
            self.assertEqual(cached["fingerprint"]["size_bytes"], len(b"fake-video-bytes"))

    def test_changed_fingerprint_invalidates_and_recomputes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = self._video(root)
            match = {"id": "m1", "video_filename": "video.mp4"}
            with patch(
                "app.services.identity_reviewed_video._sha",
                side_effect=["a" * 64, "b" * 64],
            ) as sha:
                self.assertEqual(reviewed_source_video_digest(root, match), "a" * 64)
                video.write_bytes(b"changed-content-with-different-length!!")
                self.assertEqual(reviewed_source_video_digest(root, match), "b" * 64)
            self.assertEqual(sha.call_count, 2)

    def test_corrupt_cache_file_falls_back_to_recompute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._video(root)
            (root / DIGEST_CACHE_FILENAME).write_text("{not json", encoding="utf-8")
            with patch(
                "app.services.identity_reviewed_video._sha",
                return_value="c" * 64,
            ) as sha:
                digest = reviewed_source_video_digest(root, {"id": "m1", "video_filename": "video.mp4"})
            self.assertEqual(digest, "c" * 64)
            self.assertEqual(sha.call_count, 1)


if __name__ == "__main__":
    unittest.main()
