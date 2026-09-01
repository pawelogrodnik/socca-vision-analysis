from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.review_workflow_orchestrator import (
    ReviewWorkflowRecomputeError,
    _not_materialized_team_attribution_sources,
    _technical_retry_sources_from_current_durable_progress,
    after_video_qa_correction,
    finalize_review_for_qa,
    refresh_review_after_identity_mutation,
    retry_review_recompute,
)
from app.services.review_workflow_state import WorkflowActionError
from app.services.review_workflow_state import build_compact_review_workflow_state
from app.services.identity_reviewed_recompute_state import (
    mark_reviewed_identity_recompute_required,
)
from app.services.identity_initial_audit_store import (
    write_identity_json_atomic as write_identity_json_atomic_original,
)
from app.services.identity_reviewed_progress import PROGRESS_SCHEMA_VERSION


def ready_state() -> dict:
    return {"issues": {"blocking": 0}, "allowed_actions": ["finalize_identity"], "phase": "ready_to_finalize"}


def _focused_terminal_progress_and_workflow() -> tuple[dict, dict]:
    progress = {
        "summary": {"important_decisions_remaining": 0},
        "mixed_players": {"summary": {"unresolved": 0}},
        "coverage_residuals": {
            "B": {
                "non_actionable_required_team_uncertainty_cases": [
                    {
                        "candidate_subject_id": "cross-team-b",
                        "scope_kind": "whole_subject",
                        "source_ownership_digest": "whole-source-digest",
                        "team_attribution_evidence_source_digest": "evidence-digest",
                        "team_attribution_evidence_status": "team_attribution_evidence_not_materialized",
                    }
                ]
            }
        },
        "_internal_review_units": [
            {
                "candidate_subject_id": "cross-team-b",
                "scope_kind": "whole_subject",
                "source_ownership_digest": "whole-source-digest",
                "team_attribution_evidence_source_digest": "evidence-digest",
                "source_team_label": "B",
                "detected_pairs": [("track-b", 10), ("track-b", 11)],
            }
        ],
    }
    workflow = {
        "issues": {
            "blocking": 0,
            "normal_blocking": 0,
            "mixed_blocking": 0,
            "coverage_readiness_blocked": True,
        },
        "allowed_actions": [],
        "phase": "exceptions",
        "status": "error",
    }
    return progress, workflow


class ReviewWorkflowOrchestratorTests(unittest.TestCase):
    def test_terminal_technical_retry_includes_companion_not_materialized_sources(self) -> None:
        """One terminal Retry cannot repair only a subset of exact sources."""
        durable_progress = {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "source_snapshot_digest": "current",
            "coverage_residuals": {
                "U": {
                    "non_actionable_required_team_uncertainty_cases": [
                        {
                            "candidate_subject_id": "technical-source",
                            "scope_kind": "whole_subject",
                            "team_attribution_evidence_source_digest": "technical-digest",
                            "team_attribution_evidence_status": "source_video_unavailable",
                        },
                        {
                            "candidate_subject_id": "not-materialized-source",
                            "scope_kind": "whole_subject",
                            "team_attribution_evidence_source_digest": "pending-digest",
                            "team_attribution_evidence_status": "team_attribution_evidence_not_materialized",
                        },
                    ]
                }
            },
        }
        resolved = [{"candidate_subject_id": "technical-source"}, {"candidate_subject_id": "not-materialized-source"}]
        with patch(
            "app.services.review_workflow_orchestrator.load_json_object",
            return_value=durable_progress,
        ), patch(
            "app.services.review_workflow_orchestrator.resolve_current_team_attribution_sources",
            return_value=resolved,
        ) as resolve_sources:
            result = _technical_retry_sources_from_current_durable_progress(
                Path("/tmp/match"),
                {"semantic_digest": "current"},
            )

        self.assertEqual(result, resolved)
        descriptors = resolve_sources.call_args.args[1]
        self.assertEqual(
            [row["candidate_subject_id"] for row in descriptors],
            ["technical-source", "not-materialized-source"],
        )

    def test_pending_recompute_rebuilds_the_generation_instead_of_reusing_stale_progress(self) -> None:
        pending = {
            "allowed_actions": ["retry_review_recompute"],
            "freshness": {
                "review_progress_current": False,
                "review_progress_reason": "review_progress_recompute_required",
                "reviewed_identity_current": True,
            },
            "issues": {"normal_blocking": 0, "mixed_blocking": 0},
        }
        refreshed = {"workflow": {"issues": {"normal_blocking": 0}}}
        with patch(
            "app.services.review_workflow_orchestrator.build_compact_review_workflow_state",
            return_value=pending,
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value=refreshed,
        ) as refresh:
            result = retry_review_recompute(Path("/tmp/match"), {"id": "m1"})

        self.assertEqual(result["workflow"], refreshed["workflow"])
        refresh.assert_called_once_with(
            Path("/tmp/match"),
            {"id": "m1"},
            source="retry",
            operator_evidence=True,
            leave_hot_state_warm=True,
            reuse_current_snapshot=False,
            retry_technical_team_attribution_evidence=False,
        )

    def test_retry_commits_and_warms_the_generation_before_returning_workflow(self) -> None:
        retryable = {
            "allowed_actions": ["retry_review_recompute"],
            "issues": {"normal_blocking": 0, "mixed_blocking": 0},
        }
        refreshed = {"workflow": {"issues": {"normal_blocking": 0}}}
        with patch(
            "app.services.review_workflow_orchestrator.build_compact_review_workflow_state",
            return_value=retryable,
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value=refreshed,
        ) as refresh:
            result = retry_review_recompute(Path("/tmp/match"), {"id": "m1"})

        self.assertEqual(result["workflow"], refreshed["workflow"])
        self.assertIn("retry_preflight_ms", result["performance"])
        self.assertIn("retry_refresh_ms", result["performance"])
        self.assertIn("endpoint_total_ms", result["performance"])
        refresh.assert_called_once_with(
            Path("/tmp/match"),
            {"id": "m1"},
            source="retry",
            operator_evidence=True,
            leave_hot_state_warm=True,
            reuse_current_snapshot=False,
            retry_technical_team_attribution_evidence=False,
        )

    def test_policy_stale_retry_skips_global_evidence_and_keeps_warm_commit(self) -> None:
        retryable = {
            "allowed_actions": ["retry_review_recompute"],
            "freshness": {
                "review_progress_reason": "review_progress_policy_stale",
                "reviewed_identity_current": True,
            },
            "issues": {"normal_blocking": 0, "mixed_blocking": 0},
        }
        refreshed = {"workflow": {"issues": {"normal_blocking": 0}}}
        with patch(
            "app.services.review_workflow_orchestrator.build_compact_review_workflow_state",
            return_value=retryable,
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value=refreshed,
        ) as refresh:
            result = retry_review_recompute(Path("/tmp/match"), {"id": "m1"})

        self.assertEqual(result["workflow"], refreshed["workflow"])
        refresh.assert_called_once_with(
            Path("/tmp/match"),
            {"id": "m1"},
            source="retry",
            operator_evidence=False,
            leave_hot_state_warm=True,
            reuse_current_snapshot=True,
            retry_technical_team_attribution_evidence=False,
        )

    def test_technical_evidence_retry_rechecks_authoritative_source_after_repair(self) -> None:
        retryable = {
            "allowed_actions": ["retry_review_recompute"],
            "freshness": {
                "review_progress_reason": None,
                "review_progress_current": True,
                "reviewed_identity_current": True,
            },
            "issues": {
                "normal_blocking": 0,
                "mixed_blocking": 0,
                "team_attribution_evidence_technical_failure": True,
            },
        }
        repaired_result = {
            "workflow": {
                "issues": {"normal_blocking": 1, "mixed_blocking": 0},
                "allowed_actions": ["review_identity_issue"],
            }
        }
        with patch(
            "app.services.review_workflow_orchestrator.build_compact_review_workflow_state",
            return_value=retryable,
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value=repaired_result,
        ) as refresh:
            result = retry_review_recompute(Path("/tmp/match"), {"id": "m1"})

        self.assertEqual(result["workflow"], repaired_result["workflow"])
        refresh.assert_called_once_with(
            Path("/tmp/match"),
            {"id": "m1"},
            source="retry",
            operator_evidence=False,
            leave_hot_state_warm=True,
            reuse_current_snapshot=True,
            retry_technical_team_attribution_evidence=True,
        )

    def test_lifecycle_migration_reprojects_old_recovery_incomplete_sources(self) -> None:
        """A pre-#82 terminal progress retry rematerializes exact evidence once."""
        migration_state = {
            "allowed_actions": ["retry_review_recompute"],
            "freshness": {
                "review_progress_reason": (
                    "review_progress_team_attribution_evidence_lifecycle_stale"
                ),
                "reviewed_identity_current": True,
            },
            "issues": {"normal_blocking": 0, "mixed_blocking": 0},
        }
        refreshed = {
            "workflow": {
                "issues": {"normal_blocking": 1, "mixed_blocking": 0},
                "allowed_actions": ["review_identity_issue"],
            }
        }
        with patch(
            "app.services.review_workflow_orchestrator.build_compact_review_workflow_state",
            return_value=migration_state,
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value=refreshed,
        ) as refresh:
            result = retry_review_recompute(Path("/tmp/match"), {"id": "m1"})

        self.assertEqual(result["workflow"], refreshed["workflow"])
        refresh.assert_called_once_with(
            Path("/tmp/match"),
            {"id": "m1"},
            source="retry",
            operator_evidence=False,
            leave_hot_state_warm=True,
            reuse_current_snapshot=True,
            retry_technical_team_attribution_evidence=True,
        )

    def test_technical_retry_selects_current_exact_sources_but_normal_refresh_does_not(self) -> None:
        source_digest = "exact-evidence-digest"
        workflow = {
            "issues": {
                "coverage_readiness_blocked": True,
                "normal_blocking": 0,
                "mixed_blocking": 0,
            }
        }
        progress = {
            "coverage_residuals": {
                "U": {
                    "non_actionable_required_team_uncertainty_cases": [{
                        "candidate_subject_id": "team-u-source",
                        "scope_kind": "whole_subject",
                        "team_attribution_evidence_source_digest": source_digest,
                        "team_attribution_evidence_status": "team_attribution_evidence_recovery_incomplete",
                    }]
                }
            },
            "_internal_review_units": [{
                "candidate_subject_id": "team-u-source",
                "scope_kind": "whole_subject",
                "source_team_label": "U",
                "team_attribution_evidence_source_digest": source_digest,
                "detected_pairs": [("track-current", 12), ("track-current", 13)],
            }],
        }

        self.assertEqual(_not_materialized_team_attribution_sources(workflow, progress), [])
        self.assertEqual(
            _not_materialized_team_attribution_sources(
                workflow,
                progress,
                include_technical_failures=True,
            ),
            [{
                "candidate_subject_id": "team-u-source",
                "scope_kind": "whole_subject",
                "review_target_id": None,
                "continuity_group_id": None,
                "source_team_label": "U",
                "source_ownership_digest": source_digest,
                "detected_pairs": [("track-current", 12), ("track-current", 13)],
            }],
        )

    def test_evidence_only_technical_retry_falls_back_to_full_progress_without_safe_durable_source(self) -> None:
        source_digest = "exact-evidence-digest"
        second_source_digest = "second-exact-evidence-digest"
        source = {
            "candidate_subject_id": "team-u-source",
            "scope_kind": "whole_subject",
            "review_target_id": None,
            "continuity_group_id": None,
            "source_team_label": "U",
            "source_ownership_digest": source_digest,
            "detected_pairs": [("track-current", 12), ("track-current", 13)],
        }
        second_source = {
            "candidate_subject_id": "team-u-source-two",
            "scope_kind": "whole_subject",
            "review_target_id": None,
            "continuity_group_id": None,
            "source_team_label": "U",
            "source_ownership_digest": second_source_digest,
            "detected_pairs": [("track-current-two", 31), ("track-current-two", 32)],
        }
        technical_state = {
            "allowed_actions": ["retry_review_recompute"],
            "freshness": {"review_progress_current": True, "reviewed_identity_current": True},
            "issues": {
                "coverage_readiness_blocked": True,
                "normal_blocking": 0,
                "mixed_blocking": 0,
                "team_attribution_evidence_technical_failure": True,
            },
        }
        initial_progress = {
            "summary": {},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_residuals": {
                "U": {
                    "non_actionable_required_team_uncertainty_cases": [
                        {
                            "candidate_subject_id": "team-u-source",
                            "scope_kind": "whole_subject",
                            "team_attribution_evidence_source_digest": source_digest,
                            "team_attribution_evidence_status": "team_attribution_evidence_recovery_incomplete",
                        },
                        {
                            "candidate_subject_id": "team-u-source-two",
                            "scope_kind": "whole_subject",
                            "team_attribution_evidence_source_digest": second_source_digest,
                            "team_attribution_evidence_status": "team_attribution_evidence_recovery_incomplete",
                        },
                    ]
                }
            },
            "_internal_review_units": [
                {
                    "candidate_subject_id": "team-u-source",
                    "scope_kind": "whole_subject",
                    "source_team_label": "U",
                    "team_attribution_evidence_source_digest": source_digest,
                    "detected_pairs": source["detected_pairs"],
                },
                {
                    "candidate_subject_id": "team-u-source-two",
                    "scope_kind": "whole_subject",
                    "source_team_label": "U",
                    "team_attribution_evidence_source_digest": second_source_digest,
                    "detected_pairs": second_source["detected_pairs"],
                },
            ],
        }
        repaired_progress = {
            "summary": {},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_residuals": {
                "U": {"non_actionable_required_team_uncertainty_cases": []}
            },
            "coverage_readiness": {"allows_finalize": True, "status": "accepted_within_tolerance"},
            "_internal_review_units": [],
        }
        accepted = {
            "issues": {"blocking": 0, "normal_blocking": 0, "mixed_blocking": 0},
            "mandatory_operator_review_complete": True,
            "data_quality_ready_for_output": True,
            "allowed_actions": ["finalize_identity"],
        }
        evidence_document = {
            "cases": [
                {
                    "candidate_subject_id": source["candidate_subject_id"],
                    "scope_kind": source["scope_kind"],
                    "source_ownership_digest": source_digest,
                    "status": "no_team_attribution_evidence",
                },
                {
                    "candidate_subject_id": second_source["candidate_subject_id"],
                    "scope_kind": second_source["scope_kind"],
                    "source_ownership_digest": second_source_digest,
                    "status": "no_team_attribution_evidence",
                },
            ]
        }
        durable_technical_progress = {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "source_snapshot_digest": "current",
            "coverage_residuals": initial_progress["coverage_residuals"],
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.build_compact_review_workflow_state",
            return_value=technical_state,
        ), patch(
            "app.services.review_workflow_orchestrator.get_reviewed_identity_status",
            return_value={"status": "partial_reviewed", "semantic_digest": "current"},
        ), patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
        ) as finalize, patch(
            "app.services.review_workflow_orchestrator.load_json_object",
            return_value=durable_technical_progress,
        ), patch(
            "app.services.review_workflow_orchestrator.resolve_current_team_attribution_sources",
            return_value=None,
        ) as resolve_sources, patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            side_effect=[initial_progress, repaired_progress],
        ) as progress, patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            side_effect=[technical_state, accepted],
        ), patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value=evidence_document,
        ) as materialize, patch(
            "app.services.identity_reviewed_hot_state.rebuild_review_hot_state",
        ):
            result = retry_review_recompute(Path(tmp), {"id": "m1"})

        finalize.assert_not_called()
        resolve_sources.assert_called_once()
        self.assertEqual(progress.call_count, 2)
        materialize.assert_called_once_with(
            Path(tmp),
            focused_sources=[source, second_source],
        )
        self.assertTrue(result["workflow"]["mandatory_operator_review_complete"])
        self.assertTrue(result["workflow"]["data_quality_ready_for_output"])

    def test_evidence_only_technical_retry_uses_current_durable_sources_before_one_rebuild(self) -> None:
        source = {
            "candidate_subject_id": "team-u-source",
            "scope_kind": "whole_subject",
            "review_target_id": None,
            "continuity_group_id": None,
            "source_team_label": "U",
            "source_ownership_digest": "exact-evidence-digest",
            "detected_pairs": [("track-current", 12), ("track-current", 13)],
        }
        second_source = {
            "candidate_subject_id": "team-u-source-two",
            "scope_kind": "whole_subject",
            "review_target_id": None,
            "continuity_group_id": None,
            "source_team_label": "U",
            "source_ownership_digest": "second-exact-evidence-digest",
            "detected_pairs": [("track-current-two", 31), ("track-current-two", 32)],
        }
        technical_state = {
            "allowed_actions": ["retry_review_recompute"],
            "freshness": {"review_progress_current": True, "reviewed_identity_current": True},
            "issues": {
                "coverage_readiness_blocked": True,
                "normal_blocking": 0,
                "mixed_blocking": 0,
                "team_attribution_evidence_technical_failure": True,
            },
        }
        required_progress = {
            "summary": {},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_residuals": {"U": {"non_actionable_required_team_uncertainty_cases": []}},
            "next_cases": [{"candidate_subject_id": source["candidate_subject_id"]}, {
                "candidate_subject_id": second_source["candidate_subject_id"],
            }],
            "_internal_review_units": [source, second_source],
        }
        required_workflow = {
            "issues": {"blocking": 2, "normal_blocking": 2, "mixed_blocking": 0},
            "mandatory_operator_review_complete": False,
            "data_quality_ready_for_output": False,
            "allowed_actions": ["review_identity_issue"],
        }
        evidence_document = {
            "cases": [
                {
                    "candidate_subject_id": row["candidate_subject_id"],
                    "scope_kind": "whole_subject",
                    "source_ownership_digest": row["source_ownership_digest"],
                    "status": "ready_for_team_attribution",
                }
                for row in [source, second_source]
            ]
        }
        durable_progress = {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "source_snapshot_digest": "current",
            "coverage_residuals": {
                "U": {
                    "non_actionable_required_team_uncertainty_cases": [
                        {
                            "candidate_subject_id": row["candidate_subject_id"],
                            "scope_kind": "whole_subject",
                            "team_attribution_evidence_source_digest": row["source_ownership_digest"],
                            "team_attribution_evidence_status": "team_attribution_evidence_recovery_incomplete",
                        }
                        for row in [source, second_source]
                    ]
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            match_path = Path(tmp)
            (match_path / "reviewed_identity_progress.json").write_text(
                json.dumps(durable_progress), encoding="utf-8"
            )
            with patch(
                "app.services.review_workflow_orchestrator.build_compact_review_workflow_state",
                return_value=technical_state,
            ), patch(
                "app.services.review_workflow_orchestrator.get_reviewed_identity_status",
                return_value={"status": "partial_reviewed", "semantic_digest": "current"},
            ), patch(
                "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            ) as finalize, patch(
                "app.services.review_workflow_orchestrator.resolve_current_team_attribution_sources",
                return_value=[source, second_source],
            ) as resolve_sources, patch(
                "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
                return_value=required_progress,
            ) as progress, patch(
                "app.services.review_workflow_orchestrator.get_review_workflow_state",
                return_value=required_workflow,
            ), patch(
                "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
                return_value=evidence_document,
            ) as materialize, patch(
                "app.services.identity_reviewed_hot_state.rebuild_review_hot_state",
            ):
                result = retry_review_recompute(match_path, {"id": "m1"})

        finalize.assert_not_called()
        resolve_sources.assert_called_once()
        progress.assert_called_once_with(match_path, {"id": "m1"}, include_internal_units=True)
        materialize.assert_called_once_with(
            match_path,
            focused_sources=[source, second_source],
        )
        self.assertEqual(result["workflow"]["issues"]["normal_blocking"], 2)
        self.assertFalse(result["workflow"]["mandatory_operator_review_complete"])

    def test_focused_existing_normal_evidence_is_actionable_and_never_rewritten(self) -> None:
        """Focused recovery may discover an existing exact Required crop."""
        initial_progress, blocked = _focused_terminal_progress_and_workflow()
        required_progress = {
            "summary": {"important_decisions_remaining": 1},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_residuals": {"B": {"non_actionable_required_team_uncertainty_cases": []}},
            "next_cases": [{"candidate_subject_id": "cross-team-b"}],
            "_internal_review_units": [],
        }
        required_workflow = {
            "issues": {
                "blocking": 1,
                "normal_blocking": 1,
                "mixed_blocking": 0,
                "coverage_readiness_blocked": False,
            },
            "allowed_actions": ["review_identity_issue"],
            "phase": "exceptions",
            "status": "action_required",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            side_effect=[initial_progress, required_progress],
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            side_effect=[blocked, required_workflow],
        ), patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={
                "cases": [{
                    "candidate_subject_id": "cross-team-b",
                    "scope_kind": "whole_subject",
                    "source_ownership_digest": "evidence-digest",
                    "status": "focused_source_already_actionable",
                }]
            },
        ), patch(
            "app.services.review_workflow_orchestrator.mark_team_attribution_evidence_technical_failure",
        ) as mark_technical:
            result = refresh_review_after_identity_mutation(
                Path(tmp), {"id": "m1"}, source="retry", operator_evidence=False
            )

        mark_technical.assert_not_called()
        self.assertEqual(result["workflow"], required_workflow)
        self.assertEqual(result["performance"]["team_attribution_remediation"], {
            "pre_retry_exact_source_count": 1,
            "requested_focused_sources": 1,
            "selector_unresolved_sources": 0,
            "materialized_actionable": 1,
            "terminal_unavailable": 0,
            "technical_failure": 0,
            "post_retry_remediable_not_established": 0,
        })

    def test_retry_reports_orchestration_timings_in_the_returned_performance(self) -> None:
        retryable = {
            "allowed_actions": ["retry_review_recompute"],
            "issues": {"normal_blocking": 0, "mixed_blocking": 0},
        }
        refreshed = {
            "workflow": {"phase": "exceptions"},
            "performance": {"total_ms": 7.0},
        }
        with patch(
            "app.services.review_workflow_orchestrator.build_compact_review_workflow_state",
            return_value=retryable,
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value=refreshed,
        ), patch(
            "app.services.review_workflow_orchestrator.time.perf_counter",
            side_effect=[10.0, 11.0, 13.0, 14.0, 19.0, 21.0],
        ):
            result = retry_review_recompute(Path("/tmp/match"), {"id": "m1"})

        self.assertEqual(result["performance"], {
            "total_ms": 7.0,
            "retry_preflight_ms": 2000.0,
            "retry_refresh_ms": 5000.0,
            "endpoint_total_ms": 11000.0,
        })
        self.assertEqual(refreshed["performance"], {"total_ms": 7.0})

    def test_current_snapshot_reuse_skips_finalize_and_returns_durable_snapshot(self) -> None:
        current = {"status": "partial_reviewed", "semantic_digest": "current"}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.get_reviewed_identity_status",
            return_value=current,
        ), patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
        ) as finalize, patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}, "coverage_residuals": {}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=ready_state(),
        ):
            result = refresh_review_after_identity_mutation(
                Path(tmp), {"id": "m1"}, source="retry", operator_evidence=False,
                reuse_current_snapshot=True,
            )

        finalize.assert_not_called()
        self.assertIs(result["snapshot"], current)
        self.assertTrue(result["performance"]["snapshot_reused"])

    def test_stale_snapshot_reuse_falls_back_to_canonical_finalize(self) -> None:
        stale = {"status": "stale", "semantic_digest": "old"}
        canonical = {"status": "partial_reviewed", "semantic_digest": "fresh"}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.get_reviewed_identity_status",
            return_value=stale,
        ), patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value=canonical,
        ) as finalize, patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}, "coverage_residuals": {}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=ready_state(),
        ):
            result = refresh_review_after_identity_mutation(
                Path(tmp), {"id": "m1"}, source="retry", operator_evidence=False,
                reuse_current_snapshot=True,
            )

        finalize.assert_called_once()
        self.assertIs(result["snapshot"], canonical)
        self.assertFalse(result["performance"]["snapshot_reused"])

    def test_missing_snapshot_reuse_falls_back_to_canonical_finalize(self) -> None:
        missing = {"status": "missing"}
        canonical = {"status": "partial_reviewed", "semantic_digest": "fresh"}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.get_reviewed_identity_status",
            return_value=missing,
        ), patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value=canonical,
        ) as finalize, patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}, "coverage_residuals": {}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=ready_state(),
        ):
            result = refresh_review_after_identity_mutation(
                Path(tmp), {"id": "m1"}, source="retry", operator_evidence=False,
                reuse_current_snapshot=True,
            )

        finalize.assert_called_once()
        self.assertIs(result["snapshot"], canonical)
        self.assertFalse(result["performance"]["snapshot_reused"])

    def test_same_exact_source_generic_after_recovery_is_reprojected_as_technical_failure(self) -> None:
        initial_progress, blocked = _focused_terminal_progress_and_workflow()
        still_generic = {
            **initial_progress,
            "_internal_review_units": list(initial_progress["_internal_review_units"]),
        }
        technical = {
            **initial_progress,
            "coverage_residuals": {
                "B": {
                    "non_actionable_required_team_uncertainty_cases": [{
                        **initial_progress["coverage_residuals"]["B"]["non_actionable_required_team_uncertainty_cases"][0],
                        "team_attribution_evidence_status": "team_attribution_evidence_recovery_incomplete",
                    }]
                }
            },
        }
        technical_workflow = {
            "issues": {
                "blocking": 0,
                "normal_blocking": 0,
                "mixed_blocking": 0,
                "coverage_readiness_blocked": True,
                "team_attribution_evidence_technical_failure": True,
            },
            "allowed_actions": [],
            "phase": "exceptions",
            "status": "error",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            side_effect=[initial_progress, still_generic, technical],
        ) as progress, patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            side_effect=[blocked, technical_workflow],
        ), patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            # The builder produced a final result for the exact source, but
            # the immediately rebuilt projection still reports it as generic.
            # This models a post-reproject evidence reattachment failure.
            return_value={
                "cases": [{
                    "candidate_subject_id": "cross-team-b",
                    "scope_kind": "whole_subject",
                    "source_ownership_digest": "evidence-digest",
                    "status": "no_team_attribution_evidence",
                }]
            },
        ), patch(
            "app.services.review_workflow_orchestrator.mark_team_attribution_evidence_technical_failure",
        ) as mark_technical:
            result = refresh_review_after_identity_mutation(
                Path(tmp), {"id": "m1"}, source="retry", operator_evidence=False
            )

        # The guard performs one additional authoritative projection *only*
        # after proving the same exact source survived as generic
        # ``not_materialized``.  The returned workflow therefore comes from
        # the durable technical status, never from the stale generic pass.
        self.assertEqual(progress.call_count, 3)
        self.assertEqual(result["workflow"], technical_workflow)
        mark_technical.assert_called_once()
        self.assertEqual(
            mark_technical.call_args.kwargs["status"],
            "team_attribution_evidence_recovery_incomplete",
        )
        self.assertEqual(
            result["performance"]["team_attribution_remediation"],
            {
                "pre_retry_exact_source_count": 1,
                "requested_focused_sources": 1,
                "selector_unresolved_sources": 0,
                "materialized_actionable": 0,
                "terminal_unavailable": 1,
                "technical_failure": 1,
                "post_retry_remediable_not_established": 0,
            },
        )

    def test_ultimate_convergence_failure_persists_controlled_failure_envelope(self) -> None:
        """The next workflow read must not expose the older generic retry."""
        initial_progress, blocked = _focused_terminal_progress_and_workflow()
        still_generic = {
            **initial_progress,
            "_internal_review_units": list(initial_progress["_internal_review_units"]),
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            side_effect=[initial_progress, still_generic, still_generic],
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=blocked,
        ), patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={"cases": [{
                "candidate_subject_id": "cross-team-b",
                "scope_kind": "whole_subject",
                "source_ownership_digest": "evidence-digest",
                "status": "no_team_attribution_evidence",
            }]},
        ), patch(
            "app.services.review_workflow_orchestrator.mark_team_attribution_evidence_technical_failure",
        ):
            root = Path(tmp)
            with self.assertRaises(ReviewWorkflowRecomputeError):
                refresh_review_after_identity_mutation(
                    root, {"id": "m1"}, source="retry", operator_evidence=False
                )

            failure = json.loads(
                (root / "review_workflow_recompute_failure.json").read_text()
            )

            self.assertEqual(failure["code"], "review_recompute_failed")
            self.assertEqual(failure["source"], "retry")
            self.assertIn("did not converge", failure["error"])
            with patch(
                "app.services.review_workflow_state._analysis_completed", return_value=True
            ), patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value={"complete": True},
            ):
                independent_read = build_compact_review_workflow_state(
                    root, {"id": "m1"}
                )
            self.assertEqual(
                independent_read["blockers"][0]["code"], "review_recompute_failed"
            )

    def test_selector_disagreement_becomes_durable_technical_failure_not_generic_retry(self) -> None:
        """Coverage may not authorize a source that focused recovery cannot own."""
        initial_progress, blocked = _focused_terminal_progress_and_workflow()
        # The current canonical unit has changed ownership. It must not be
        # force-materialized through the old residual descriptor.
        initial_progress["_internal_review_units"][0][
            "team_attribution_evidence_source_digest"
        ] = "current-digest"
        technical_progress = {
            **initial_progress,
            "coverage_residuals": {
                "B": {
                    "non_actionable_required_team_uncertainty_cases": [{
                        **initial_progress["coverage_residuals"]["B"][
                            "non_actionable_required_team_uncertainty_cases"
                        ][0],
                        "team_attribution_evidence_status": (
                            "team_attribution_evidence_recovery_incomplete"
                        ),
                    }]
                }
            },
        }
        technical_workflow = {
            "issues": {
                "blocking": 0,
                "normal_blocking": 0,
                "mixed_blocking": 0,
                "coverage_readiness_blocked": True,
                "team_attribution_evidence_technical_failure": True,
            },
            "allowed_actions": ["retry_review_recompute"],
            "phase": "exceptions",
            "status": "error",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            side_effect=[initial_progress, technical_progress],
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            side_effect=[blocked, technical_workflow],
        ), patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
        ) as materialize, patch(
            "app.services.review_workflow_orchestrator.mark_team_attribution_evidence_technical_failure",
        ) as mark_technical:
            result = refresh_review_after_identity_mutation(
                Path(tmp), {"id": "m1"}, source="retry", operator_evidence=False
            )

        materialize.assert_not_called()
        mark_technical.assert_called_once()
        self.assertEqual(
            mark_technical.call_args.kwargs["status"],
            "team_attribution_evidence_recovery_incomplete",
        )
        self.assertEqual(result["workflow"], technical_workflow)
        self.assertEqual(
            result["performance"]["team_attribution_remediation"][
                "selector_unresolved_sources"
            ],
            1,
        )

    def test_remediation_diagnostics_accumulate_selector_and_materializer_outcomes(self) -> None:
        """A selector fault must not overwrite a successful focused outcome."""
        initial_progress, blocked = _focused_terminal_progress_and_workflow()
        selector_only = {
            "candidate_subject_id": "selector-only",
            "scope_kind": "whole_subject",
            "team_attribution_evidence_source_digest": "selector-digest",
            "team_attribution_evidence_status": (
                "team_attribution_evidence_not_materialized"
            ),
        }
        initial_progress["coverage_residuals"]["B"][
            "non_actionable_required_team_uncertainty_cases"
        ].append(selector_only)
        terminal_progress = {
            **initial_progress,
            "coverage_residuals": {
                "B": {
                    "non_actionable_required_team_uncertainty_cases": [
                        {
                            **initial_progress["coverage_residuals"]["B"][
                                "non_actionable_required_team_uncertainty_cases"
                            ][0],
                            "team_attribution_evidence_status": (
                                "no_team_attribution_evidence"
                            ),
                        },
                        {
                            **selector_only,
                            "team_attribution_evidence_status": (
                                "team_attribution_evidence_recovery_incomplete"
                            ),
                        },
                    ]
                }
            },
        }
        terminal_workflow = {
            **blocked,
            "issues": {
                **blocked["issues"],
                "team_attribution_evidence_technical_failure": True,
            },
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            side_effect=[initial_progress, terminal_progress],
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            side_effect=[blocked, terminal_workflow],
        ), patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={"cases": [{
                "candidate_subject_id": "cross-team-b",
                "scope_kind": "whole_subject",
                "source_ownership_digest": "evidence-digest",
                "status": "ready_for_team_attribution",
            }]},
        ), patch(
            "app.services.review_workflow_orchestrator.mark_team_attribution_evidence_technical_failure",
        ) as mark_technical:
            result = refresh_review_after_identity_mutation(
                Path(tmp), {"id": "m1"}, source="retry", operator_evidence=False
            )

        mark_technical.assert_called_once()
        diagnostics = result["performance"]["team_attribution_remediation"]
        self.assertEqual(diagnostics["selector_unresolved_sources"], 1)
        self.assertEqual(diagnostics["materialized_actionable"], 1)
        self.assertEqual(diagnostics["technical_failure"], 1)

    def test_lightweight_identity_refresh_does_not_build_stats_or_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.rebuild_identity_seeded_candidate_assignments"
        ) as rebuild_seeded, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ) as finalize, patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}},
        ) as progress, patch(
            "app.services.review_workflow_orchestrator.render_segment_review_evidence",
            return_value=set(),
        ) as segment_evidence, patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={"summary": {}},
        ) as team_evidence, patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=ready_state(),
        ), patch("app.services.review_workflow_orchestrator.build_reviewed_stats") as stats, patch(
            "app.services.review_workflow_orchestrator.generate_reviewed_output"
        ) as render:
            result = refresh_review_after_identity_mutation(Path(tmp), {"id": "m1"}, source="initial_audit_decision")
            self.assertEqual(result["workflow"]["phase"], "ready_to_finalize")
            rebuild_seeded.assert_not_called()
            finalize.assert_called_once()
            progress.assert_called_once()
            segment_evidence.assert_called_once()
            team_evidence.assert_called_once()
            stats.assert_not_called()
            render.assert_not_called()

    def test_failed_finalize_keeps_dirty_marker_and_retry_clears_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            mark_reviewed_identity_recompute_required(
                path,
                semantic_decision_digest="decision",
            )
            with patch(
                "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
                side_effect=RuntimeError("temporary"),
            ):
                with self.assertRaises(ReviewWorkflowRecomputeError):
                    refresh_review_after_identity_mutation(
                        path,
                        {"id": "m1"},
                        source="review_exception_finish",
                    )
            self.assertTrue(
                (path / "reviewed_identity_recompute_required.json").exists()
            )

            with patch(
                "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
                return_value={"semantic_digest": "identity"},
            ), patch(
                "app.services.review_workflow_orchestrator.render_segment_review_evidence",
                return_value=set(),
            ), patch(
                "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
                return_value={"summary": {}},
            ), patch(
                "app.services.review_workflow_orchestrator.get_review_workflow_state",
                return_value=ready_state(),
            ):
                result = refresh_review_after_identity_mutation(
                    path,
                    {"id": "m1"},
                    source="review_exception_finish",
                )
            self.assertEqual(result["workflow"]["phase"], "ready_to_finalize")
            self.assertFalse(
                (path / "reviewed_identity_recompute_required.json").exists()
            )

    def test_exception_refresh_rebuilds_only_seeded_candidate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.rebuild_identity_seeded_candidate_assignments",
            return_value={"summary": {}},
        ) as rebuild_seeded, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=ready_state(),
        ), patch("app.services.review_workflow_orchestrator.build_reviewed_stats") as stats, patch(
            "app.services.review_workflow_orchestrator.generate_reviewed_output"
        ) as render:
            refresh_review_after_identity_mutation(
                Path(tmp),
                {"id": "m1"},
                source="review_exception_decision",
                rebuild_seeded_candidates=True,
            )

            rebuild_seeded.assert_called_once_with(Path(tmp), {"id": "m1"})
            stats.assert_not_called()
            render.assert_not_called()

    def test_fast_reproject_materializes_only_terminal_not_materialized_team_evidence(self) -> None:
        initial_progress = {
            "summary": {"important_decisions_remaining": 0},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_residuals": {
                "B": {
                    "non_actionable_required_team_uncertainty_cases": [
                        {
                            "candidate_subject_id": "cross-team-b",
                            "scope_kind": "whole_subject",
                            "source_ownership_digest": "whole-source-digest",
                            "team_attribution_evidence_source_digest": "evidence-digest",
                            "team_attribution_evidence_status": "team_attribution_evidence_not_materialized",
                        }
                    ]
                }
            },
            "_internal_review_units": [
                {
                    "candidate_subject_id": "cross-team-b",
                    "scope_kind": "whole_subject",
                    "source_ownership_digest": "whole-source-digest",
                    "team_attribution_evidence_source_digest": "evidence-digest",
                    "source_team_label": "B",
                    "detected_pairs": [("track-b", 10), ("track-b", 11)],
                }
            ],
        }
        recovered_progress = {
            "next_cases": [{
                "candidate_subject_id": "cross-team-b",
                "scope_kind": "whole_subject",
                "source_ownership_digest": "whole-source-digest",
                "team_attribution_evidence_source_digest": "evidence-digest",
            }],
            "summary": {"important_decisions_remaining": 1},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_residuals": {},
            "_internal_review_units": [{
                "candidate_subject_id": "cross-team-b",
                "scope_kind": "whole_subject",
                "source_ownership_digest": "whole-source-digest",
                "team_attribution_evidence_source_digest": "evidence-digest",
            }],
        }
        blocked = {
            "issues": {
                "blocking": 0,
                "normal_blocking": 0,
                "mixed_blocking": 0,
                "coverage_readiness_blocked": True,
            },
            "allowed_actions": [],
            "phase": "exceptions",
            "status": "action_required",
        }
        actionable = {
            "issues": {"blocking": 1, "normal_blocking": 1, "mixed_blocking": 0},
            "allowed_actions": ["review_identity_issue"],
            "phase": "exceptions",
            "status": "action_required",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "identity"},
        ) as finalize, patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            side_effect=[initial_progress, recovered_progress],
        ) as progress, patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            side_effect=[blocked, actionable],
        ) as workflow, patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={"cases": [{
                "candidate_subject_id": "cross-team-b",
                "scope_kind": "whole_subject",
                "source_ownership_digest": "evidence-digest",
                "status": "ready_for_team_attribution",
                "rendered_anchor_crops": [{"artifact": "evidence.jpg"}] * 3,
            }]},
        ) as materialize, patch(
            "app.services.review_workflow_orchestrator.mark_team_attribution_evidence_technical_failure",
        ) as mark_technical:
            result = refresh_review_after_identity_mutation(
                Path(tmp),
                {"id": "m1"},
                source="mixed_players_reproject",
                operator_evidence=False,
                leave_hot_state_warm=False,
            )

        finalize.assert_called_once()
        self.assertEqual(progress.call_count, 2)
        self.assertEqual(workflow.call_count, 2)
        materialize.assert_called_once_with(
            Path(tmp),
            focused_sources=[
                {
                    "candidate_subject_id": "cross-team-b",
                    "scope_kind": "whole_subject",
                    "review_target_id": None,
                    "continuity_group_id": None,
                    "source_team_label": "B",
                    "source_ownership_digest": "evidence-digest",
                    "detected_pairs": [("track-b", 10), ("track-b", 11)],
                }
            ],
        )
        self.assertEqual(result["workflow"], actionable)
        mark_technical.assert_not_called()
        self.assertIn("focused_team_attribution_evidence_ms", result["performance"])
        self.assertIn("focused_progress_rebuild_ms", result["performance"])

    def test_focused_progress_failure_writes_controlled_recompute_failure(self) -> None:
        initial_progress, blocked = _focused_terminal_progress_and_workflow()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            mark_reviewed_identity_recompute_required(path, semantic_decision_digest="dirty")
            with patch(
                "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
                return_value={"semantic_digest": "identity"},
            ), patch(
                "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
                side_effect=[initial_progress, RuntimeError("focused progress failed")],
            ), patch(
                "app.services.review_workflow_orchestrator.get_review_workflow_state",
                return_value=blocked,
            ), patch(
                "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
                return_value={"summary": {}},
            ):
                with self.assertRaises(ReviewWorkflowRecomputeError):
                    refresh_review_after_identity_mutation(
                        path,
                        {"id": "m1"},
                        source="mixed_players_reproject",
                        operator_evidence=False,
                    )

            self.assertTrue((path / "reviewed_identity_recompute_required.json").exists())
            failure = json.loads((path / "review_workflow_recompute_failure.json").read_text())
            self.assertEqual(failure["source"], "mixed_players_reproject")
            self.assertIn("focused progress failed", failure["error"])

    def test_durable_progress_write_failure_uses_controlled_recompute_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            mark_reviewed_identity_recompute_required(path, semantic_decision_digest="dirty")

            def fail_progress_write(target: Path, value: dict, **kwargs: object) -> None:
                if Path(target).name == "reviewed_identity_progress.json":
                    raise RuntimeError("durable progress failed")
                write_identity_json_atomic_original(target, value, **kwargs)

            with patch(
                "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
                return_value={"semantic_digest": "identity"},
            ), patch(
                "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
                return_value={"summary": {}, "coverage_residuals": {}},
            ), patch(
                "app.services.review_workflow_orchestrator.get_review_workflow_state",
                return_value=ready_state(),
            ), patch(
                "app.services.review_workflow_orchestrator.write_identity_json_atomic",
                side_effect=fail_progress_write,
            ):
                with self.assertRaises(ReviewWorkflowRecomputeError):
                    refresh_review_after_identity_mutation(
                        path,
                        {"id": "m1"},
                        source="mixed_players_reproject",
                        operator_evidence=False,
                    )

            self.assertTrue((path / "reviewed_identity_recompute_required.json").exists())
            failure = json.loads((path / "review_workflow_recompute_failure.json").read_text())
            self.assertIn("durable progress failed", failure["error"])

    def test_hot_state_failure_uses_controlled_recompute_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            mark_reviewed_identity_recompute_required(path, semantic_decision_digest="dirty")
            with patch(
                "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
                return_value={"semantic_digest": "identity"},
            ), patch(
                "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
                return_value={"summary": {}, "coverage_residuals": {}},
            ), patch(
                "app.services.review_workflow_orchestrator.get_review_workflow_state",
                return_value=ready_state(),
            ), patch(
                "app.services.identity_reviewed_hot_state.rebuild_review_hot_state",
                side_effect=RuntimeError("hot state failed"),
            ):
                with self.assertRaises(ReviewWorkflowRecomputeError):
                    refresh_review_after_identity_mutation(
                        path,
                        {"id": "m1"},
                        source="mixed_players_reproject",
                        operator_evidence=False,
                        leave_hot_state_warm=True,
                    )

            self.assertTrue((path / "reviewed_identity_recompute_required.json").exists())
            failure = json.loads((path / "review_workflow_recompute_failure.json").read_text())
            self.assertIn("hot state failed", failure["error"])

    def test_finalize_builds_stats_then_queues_one_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.build_cheap_finalize_preflight_state",
            return_value=ready_state(),
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            side_effect=[ready_state(), ready_state(), {**ready_state(), "phase": "rendering_review_video"}],
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value={"workflow": ready_state(), "snapshot": {"semantic_digest": "identity"}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_reviewed_identity_status",
            return_value={"semantic_digest": "identity"},
        ), patch("app.services.review_workflow_orchestrator.build_reviewed_stats") as stats, patch(
            "app.services.review_workflow_orchestrator.generate_reviewed_output",
            return_value={"status": "queued"},
        ) as render:
            result = finalize_review_for_qa(Path(tmp), {"id": "m1"})
            self.assertEqual(result["render_job"]["status"], "queued")
            stats.assert_called_once()
            self.assertTrue(render.call_args.kwargs["stats_already_current"])

    def test_finalize_rejects_non_actionable_coverage_blocker_after_refresh(self) -> None:
        blocked = {
            "issues": {
                "blocking": 0,
                "coverage_readiness_blocked": True,
                "overall_identity_blocked": True,
            },
            "allowed_actions": [],
            "phase": "exceptions",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.build_cheap_finalize_preflight_state",
            return_value=ready_state(),
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=blocked,
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value={"workflow": blocked, "snapshot": {}},
        ), patch("app.services.review_workflow_orchestrator.build_reviewed_stats") as stats, patch(
            "app.services.review_workflow_orchestrator.generate_reviewed_output"
        ) as render:
            with self.assertRaises(WorkflowActionError) as raised:
                finalize_review_for_qa(Path(tmp), {"id": "m1"})

            self.assertEqual(
                raised.exception.code,
                "identity_coverage_unresolved_without_reviewable_evidence",
            )
            stats.assert_not_called()
            render.assert_not_called()

    def test_finalize_regenerates_operator_evidence_when_recompute_discovers_blocker(self) -> None:
        blocked = {
            "issues": {
                "blocking": 3,
                "overall_identity_blocked": True,
                "coverage_readiness_blocked": False,
            },
            "allowed_actions": [],
            "phase": "exceptions",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.build_cheap_finalize_preflight_state",
            return_value=ready_state(),
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value={"workflow": blocked, "snapshot": {}},
        ), patch(
            "app.services.review_workflow_orchestrator.render_segment_review_evidence",
            return_value=set(),
        ) as segment_evidence, patch(
            "app.services.review_workflow_orchestrator.materialize_team_attribution_evidence",
            return_value={"summary": {}},
        ) as team_evidence, patch("app.services.review_workflow_orchestrator.build_reviewed_stats") as stats, patch(
            "app.services.review_workflow_orchestrator.generate_reviewed_output"
        ) as render:
            with self.assertRaises(WorkflowActionError):
                finalize_review_for_qa(Path(tmp), {"id": "m1"})
            segment_evidence.assert_called_once()
            team_evidence.assert_called_once()
            stats.assert_not_called()
            render.assert_not_called()

    def test_video_qa_correction_with_blocker_does_not_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value={"workflow": {"issues": {"blocking": 1}, "phase": "exceptions"}, "snapshot": {}},
        ), patch("app.services.review_workflow_orchestrator.generate_reviewed_output") as render:
            result = after_video_qa_correction(Path(tmp), {"id": "m1"})
            self.assertEqual(result["workflow"]["phase"], "exceptions")
            render.assert_not_called()

    def test_video_qa_correction_with_non_actionable_coverage_blocker_does_not_render(self) -> None:
        workflow = {
            "issues": {
                "blocking": 0,
                "coverage_readiness_blocked": True,
                "overall_identity_blocked": True,
            },
            "phase": "exceptions",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value={"workflow": workflow, "snapshot": {}},
        ), patch("app.services.review_workflow_orchestrator.build_reviewed_stats") as stats, patch(
            "app.services.review_workflow_orchestrator.generate_reviewed_output"
        ) as render:
            result = after_video_qa_correction(Path(tmp), {"id": "m1"})
            self.assertEqual(result["workflow"]["phase"], "exceptions")
            stats.assert_not_called()
            render.assert_not_called()

    def test_video_qa_correction_without_blocker_rebuilds_once_and_returns_to_qa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value={"workflow": {"issues": {"blocking": 0}, "phase": "ready_to_finalize"}, "snapshot": {"semantic_digest": "new"}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_reviewed_identity_status",
            return_value={"semantic_digest": "new"},
        ), patch("app.services.review_workflow_orchestrator.build_reviewed_stats") as stats, patch(
            "app.services.review_workflow_orchestrator.generate_reviewed_output",
            return_value={"status": "queued"},
        ) as render, patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value={"phase": "rendering_review_video", "review_complete": False},
        ):
            result = after_video_qa_correction(Path(tmp), {"id": "m1"})
            self.assertEqual(result["workflow"]["phase"], "rendering_review_video")
            self.assertFalse(result["workflow"]["review_complete"])
            stats.assert_called_once()
            render.assert_called_once()


if __name__ == "__main__":
    unittest.main()
