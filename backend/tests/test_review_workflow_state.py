from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.review_workflow_state import (
    WorkflowActionError,
    assert_workflow_action_allowed,
    build_compact_review_workflow_state,
    derive_review_workflow_state,
    get_review_workflow_state,
    _current_cached_progress,
    _issue_evidence,
)
from app.services.identity_reviewed_coverage import COVERAGE_POLICY_VERSION
from app.services.identity_reviewed_progress import (
    PROGRESS_SCHEMA_VERSION,
    required_queue_descriptor,
)
from app.services.identity_seeded_review_reduction import (
    build_initial_audit_completion_evidence,
)
from app.services.identity_reviewed_output_jobs import reviewed_output_status_read_only
from app.services.review_workflow_store import (
    approval_is_current,
    current_approval_fingerprint,
    save_video_qa_approval,
)


def evidence(**overrides: object) -> dict:
    value = {
        "match_id": "m1",
        "analysis_completed": True,
        "initial_audit": {"complete": True, "completed": 8, "total": 8, "remaining": 0},
        "issues": {"blocking": 0, "important": 0, "optional": 0},
        "freshness": {
            "reviewed_identity_current": False,
            "reviewed_stats_current": False,
            "reviewed_output_current": False,
            "qa_approval_current": False,
        },
        "render": {"status": "missing"},
    }
    value.update(overrides)
    return value


def _public_workflow_semantics(state: dict) -> dict:
    return {
        key: state.get(key)
        for key in (
            "available", "phase", "status", "current_step_id", "review_complete",
            "can_enter_report", "can_publish", "required_action", "issues",
            "mandatory_operator_review_complete", "data_quality_ready_for_output",
            "optional_max_available",
            "allowed_actions", "blockers", "render", "freshness",
        )
    } | {
        "steps": [
            {
                key: step.get(key)
                for key in (
                    "id", "status", "completed", "total", "remaining",
                    "locked_reason_code",
                )
            }
            for step in state.get("steps") or []
        ],
    }


class ReviewWorkflowStateTests(unittest.TestCase):
    def test_compact_workflow_matches_authoritative_lifecycle_semantics(self) -> None:
        complete = {"prepared": True, "complete": True, "completed": 8, "total": 8, "remaining": 0}
        base_progress = {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "policy": {"version": COVERAGE_POLICY_VERSION},
            "source_snapshot_digest": "snapshot",
            "summary": {"important_decisions_remaining": 0},
            "mixed_players": {"summary": {"unresolved": 0, "total": 0, "resolved": 0}},
        }
        cases = [
            ("required", {**base_progress, "summary": {"important_decisions_remaining": 3}}, {"status": "missing"}, False, False),
            ("required_and_mixed", {**base_progress, "summary": {"important_decisions_remaining": 3}, "mixed_players": {"summary": {"unresolved": 2, "total": 2, "resolved": 0}}}, {"status": "missing"}, False, True),
            ("mixed_only", {**base_progress, "mixed_players": {"summary": {"unresolved": 2, "total": 2, "resolved": 0}}}, {"status": "missing"}, False, True),
            ("ready", base_progress, {"status": "missing"}, False, False),
            ("render_queued", base_progress, {"status": "queued"}, False, False),
            ("render_running", base_progress, {"status": "running"}, False, False),
            ("render_failed", base_progress, {"status": "failed"}, False, False),
            ("video_qa", base_progress, {"status": "completed", "source_snapshot_digest": "snapshot"}, False, False),
            ("complete", base_progress, {"status": "completed", "source_snapshot_digest": "snapshot"}, False, False),
            ("recompute_failure", base_progress, {"status": "missing"}, True, False),
            (
                "technical_evidence_retry",
                {
                    **base_progress,
                    "next_cases": [],
                    "coverage_readiness": {"allows_finalize": False},
                    "coverage_residuals": {"U": {
                        "non_actionable_required_team_uncertainty_cases": [{
                            "candidate_subject_id": "u",
                            "team_attribution_evidence_status": "team_attribution_evidence_recovery_incomplete",
                        }]
                    }},
                },
                {"status": "missing"},
                False,
                False,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            match = {"id": "m1", "status": "analyzed"}
            for name, progress, job, recompute_failed, mixed_allowed in cases:
                with self.subTest(name=name), patch(
                    "app.services.review_workflow_state._analysis_completed", return_value=True
                ), patch(
                    "app.services.review_workflow_state.load_initial_audit_completion_evidence", return_value=complete
                ), patch(
                    "app.services.review_workflow_state.get_reviewed_identity_status",
                    side_effect=AssertionError(
                        "compact workflow must not load the heavyweight snapshot"
                    ),
                ), patch(
                    "app.services.review_workflow_state._current_cached_progress",
                    return_value=(progress, None),
                ), patch(
                    "app.services.review_workflow_state._current_cached_progress_for_snapshot_digest",
                    return_value=(progress, None),
                ), patch(
                    "app.services.review_workflow_state.canonical_generation_maybe_current", return_value=True
                ), patch(
                    "app.services.review_workflow_state.review_scope_dependency_matches", return_value=True
                ), patch(
                    "app.services.review_workflow_state.reviewed_output_status_read_only", return_value=job
                ), patch(
                    "app.services.review_workflow_state.load_video_qa_approval", return_value={}
                ), patch(
                    "app.services.review_workflow_state.approval_is_current", return_value=name == "complete"
                ), patch(
                    "app.services.review_workflow_state.load_json_object",
                    side_effect=lambda path: {
                        "reviewed_identity_report.json": {"snapshot_digest": "snapshot", "source_file_fingerprints": {}},
                        "reviewed_player_stats.json": {"source_snapshot_digest": "snapshot"},
                        "reviewed_output_manifest.json": {"stale": False},
                        "review_workflow_recompute_failure.json": (
                            {"code": "failed"} if recompute_failed else None
                        ),
                    }.get(Path(path).name),
                ):
                    authoritative = get_review_workflow_state(
                        root,
                        match,
                        snapshot={"status": "current", "semantic_digest": "snapshot"},
                        progress=progress,
                        completion_evidence=complete,
                    )
                    compact = build_compact_review_workflow_state(root, match)

                self.assertEqual(
                    _public_workflow_semantics(compact),
                    _public_workflow_semantics(authoritative),
                )
                for state in (authoritative, compact):
                    if name == "technical_evidence_retry":
                        self.assertIn("retry_review_recompute", state["allowed_actions"])
                        self.assertTrue(state["issues"]["team_attribution_evidence_technical_failure"])
                    if mixed_allowed:
                        assert_workflow_action_allowed(state, "review_mixed_players")
                    else:
                        with self.assertRaises(WorkflowActionError):
                            assert_workflow_action_allowed(state, "review_mixed_players")

    def test_compact_workflow_pre_report_states_match_authoritative_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            match = {"id": "m1", "status": "analyzed"}
            audit_incomplete = {"prepared": True, "complete": False, "completed": 1, "total": 3, "remaining": 2}
            with patch("app.services.review_workflow_state._analysis_completed", return_value=True), patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence", return_value=audit_incomplete
            ), patch(
                "app.services.review_workflow_state.get_reviewed_identity_status",
                side_effect=AssertionError("compact workflow must not load the heavyweight snapshot"),
            ):
                authoritative = get_review_workflow_state(root, match, snapshot={"status": "missing"}, completion_evidence=audit_incomplete)
                compact = build_compact_review_workflow_state(root, match)
            self.assertEqual(_public_workflow_semantics(compact), _public_workflow_semantics(authoritative))
            self.assertEqual(compact["phase"], "initial_audit")
            self.assertIn("identify_players", compact["allowed_actions"])
            with self.assertRaises(WorkflowActionError):
                assert_workflow_action_allowed(compact, "review_mixed_players")

            complete = {"prepared": True, "complete": True, "completed": 3, "total": 3, "remaining": 0}
            with patch("app.services.review_workflow_state._analysis_completed", return_value=True), patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence", return_value=complete
            ), patch(
                "app.services.review_workflow_state.get_reviewed_identity_status",
                side_effect=AssertionError("compact workflow must not load the heavyweight snapshot"),
            ):
                authoritative = get_review_workflow_state(root, match, snapshot={"status": "missing"}, completion_evidence=complete)
                compact = build_compact_review_workflow_state(root, match)
            self.assertEqual(_public_workflow_semantics(compact), _public_workflow_semantics(authoritative))
            self.assertEqual(compact["phase"], "exceptions")
            self.assertNotIn("finalize_identity", compact["allowed_actions"])
            with self.assertRaises(WorkflowActionError):
                assert_workflow_action_allowed(compact, "review_mixed_players")

            with patch("app.services.review_workflow_state._analysis_completed", return_value=False), patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence", return_value=audit_incomplete
            ), patch(
                "app.services.review_workflow_state.get_reviewed_identity_status",
                side_effect=AssertionError("compact workflow must not load the heavyweight snapshot"),
            ):
                authoritative = get_review_workflow_state(root, match, snapshot={"status": "missing"}, completion_evidence=audit_incomplete)
                compact = build_compact_review_workflow_state(root, match)
            self.assertEqual(_public_workflow_semantics(compact), _public_workflow_semantics(authoritative))
            self.assertFalse(compact["available"])
    def test_transition_table(self) -> None:
        cases = [
            ("analysis", evidence(analysis_completed=False), "unavailable", "initial_audit", []),
            ("audit", evidence(initial_audit={"complete": False, "completed": 2, "total": 8, "remaining": 6}), "action_required", "initial_audit", ["identify_players"]),
            ("exceptions", evidence(issues={"blocking": 2, "important": 2}), "action_required", "exceptions", ["review_identity_issue"]),
            ("ready", evidence(), "ready", "ready_to_finalize", ["finalize_identity", "review_identity_issue"]),
            ("queued", evidence(render={"status": "queued"}), "processing", "rendering_review_video", []),
            ("running", evidence(render={"status": "running"}), "processing", "rendering_review_video", []),
            ("failed", evidence(render={"status": "failed"}), "error", "rendering_review_video", ["retry_render"]),
            ("recompute", evidence(recompute_failed=True), "error", "initial_audit", ["retry_review_recompute"]),
            ("stale-render", evidence(freshness={"reviewed_identity_current": True, "reviewed_stats_current": True, "reviewed_output_current": False, "qa_approval_current": True}, render={"status": "completed"}), "ready", "ready_to_finalize", ["finalize_identity", "review_identity_issue"]),
            ("qa", evidence(freshness={"reviewed_identity_current": True, "reviewed_stats_current": True, "reviewed_output_current": True, "qa_approval_current": False}, render={"status": "completed"}), "action_required", "video_qa", ["review_video", "approve_video_qa", "correct_video_identity"]),
            ("complete", evidence(freshness={"reviewed_identity_current": True, "reviewed_stats_current": True, "reviewed_output_current": True, "qa_approval_current": True}, render={"status": "completed"}), "complete", "complete", ["review_video", "correct_video_identity"]),
        ]
        for name, raw, status, phase, actions in cases:
            with self.subTest(name=name):
                state = derive_review_workflow_state(raw)
                self.assertEqual(state["status"], status)
                self.assertEqual(state["phase"], phase)
                self.assertEqual(state["allowed_actions"], actions)

    def test_future_action_is_rejected_with_machine_readable_state(self) -> None:
        state = derive_review_workflow_state(evidence(issues={"blocking": 1}))
        with self.assertRaises(WorkflowActionError) as raised:
            assert_workflow_action_allowed(state, "finalize_identity")
        self.assertEqual(raised.exception.code, "identity_issues_remaining")
        self.assertEqual(raised.exception.state["phase"], "exceptions")

    def test_required_and_blocking_mixed_are_parallel_review_actions(self) -> None:
        state = derive_review_workflow_state(evidence(issues={
            "blocking": 34,
            "normal_blocking": 6,
            "mixed_blocking": 28,
            "mixed_total": 28,
            "mixed_resolved": 0,
            "important": 34,
        }))

        self.assertEqual(state["phase"], "exceptions")
        self.assertEqual(
            state["allowed_actions"],
            ["review_identity_issue", "review_mixed_players"],
        )
        steps = {row["id"]: row for row in state["steps"]}
        self.assertEqual(steps["exceptions"]["status"], "current")
        self.assertEqual(steps["mixed_players"]["status"], "current")
        self.assertNotIn("finalize_identity", state["allowed_actions"])

    def test_technical_structural_diagnostics_do_not_become_blockers(self) -> None:
        state = derive_review_workflow_state(evidence(issues={"blocking": 0, "important": 0, "optional": 0}))
        self.assertEqual(state["phase"], "ready_to_finalize")

    def test_snapshot_only_conflicts_do_not_create_an_empty_exception_queue(self) -> None:
        progress = {"summary": {"structural_blockers": 1, "important_decisions_remaining": 0}}
        resolved = _issue_evidence({"summary": {"conflicted": 0, "blocked": 0}}, progress)
        snapshot_only_conflict = _issue_evidence(
            {"summary": {"conflicted": 1, "blocked": 0}},
            progress,
        )
        self.assertEqual(resolved["blocking"], 0)
        self.assertEqual(snapshot_only_conflict["blocking"], 0)

    def test_optional_and_safe_anonymous_subjects_do_not_block_finalize(self) -> None:
        progress = {"summary": {
            "important_decisions_remaining": 0,
            "optional_cases_remaining": 100,
            "safe_anonymous_units": 24,
        }}
        issues = _issue_evidence({"summary": {"conflicted": 0, "blocked": 0}}, progress)
        state = derive_review_workflow_state(evidence(issues=issues))
        self.assertEqual(issues["blocking"], 0)
        self.assertEqual(state["phase"], "ready_to_finalize")

    def test_optional_max_audit_remains_available_without_blocking_finalize(self) -> None:
        progress = {
            "summary": {
                "important_decisions_remaining": 0,
                "optional_audit_cases_remaining": 180,
            },
            "coverage_readiness": {
                "status": "ready_with_review",
                "allows_finalize": True,
                "blockers": [],
            },
            "optional_audit": {
                "status": "available",
                "blocking": False,
                "remaining_cases": 180,
                "safe_max_named_coverage": 0.987,
            },
        }
        issues = _issue_evidence({"summary": {}}, progress)

        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertEqual(state["phase"], "ready_to_finalize")
        self.assertEqual(state["issues"]["optional_audit"], 180)
        self.assertEqual(state["issues"]["optional_audit_summary"]["status"], "available")
        self.assertIn("finalize_identity", state["allowed_actions"])
        self.assertIn("review_identity_issue", state["allowed_actions"])

    def test_zero_evidence_conflict_diagnostic_does_not_block_finalize(self) -> None:
        progress = {"summary": {
            "important_decisions_remaining": 0,
            "optional_cases_remaining": 1,
        }}
        issues = _issue_evidence({"summary": {"conflicted": 1, "blocked": 1}}, progress)
        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertEqual(issues["blocking"], 0)
        self.assertEqual(issues["optional"], 1)
        self.assertEqual(state["phase"], "ready_to_finalize")

    def test_true_conflict_blocks_and_resolved_progress_unblocks_workflow(self) -> None:
        conflict = _issue_evidence(
            {"summary": {"conflicted": 0, "blocked": 0}},
            {"summary": {"important_decisions_remaining": 1}},
        )
        resolved = _issue_evidence(
            {"summary": {"conflicted": 0, "blocked": 0}},
            {"summary": {"important_decisions_remaining": 0}},
        )
        self.assertEqual(derive_review_workflow_state(evidence(issues=conflict))["phase"], "exceptions")
        self.assertEqual(derive_review_workflow_state(evidence(issues=resolved))["phase"], "ready_to_finalize")

    def test_actionable_coverage_debt_keeps_existing_exception_queue(self) -> None:
        issues = _issue_evidence({}, {
            "summary": {
                "important_decisions_remaining": 3,
                "semantic_decisions_remaining": 0,
                "coverage_decisions_remaining": 3,
            },
            "coverage_readiness": {
                "status": "incomplete",
                "allows_finalize": False,
                "blockers": [{"code": "significant_named_coverage_debt"}],
            },
        })

        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertEqual(state["phase"], "exceptions")
        self.assertEqual(state["issues"]["blocking"], 3)
        self.assertTrue(state["issues"]["overall_identity_blocked"])
        self.assertEqual(state["allowed_actions"], ["review_identity_issue"])
        self.assertNotIn("finalize_identity", state["allowed_actions"])

    def test_not_materialized_remediation_never_becomes_a_fake_required_case(self) -> None:
        # Regression for the real retry sequence: a stale summary previously
        # leaked two remediation sources into the workflow badge while the
        # exact Required queue was empty and could not open either source.
        progress = {
            "next_cases": [],
            "summary": {
                "important_decisions_remaining": 2,
                "semantic_decisions_remaining": 0,
                "coverage_decisions_remaining": 0,
            },
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_readiness": {
                "status": "incomplete",
                "allows_finalize": False,
                "blockers": [{"code": "team_attribution_evidence_not_materialized"}],
            },
            "coverage_residuals": {
                "A": {
                    "non_actionable_required_team_uncertainty_cases": [{
                        "candidate_subject_id": "shadow-a",
                        "team_attribution_evidence_status": "team_attribution_evidence_not_materialized",
                    }],
                },
                "B": {
                    "non_actionable_required_team_uncertainty_cases": [{
                        "candidate_subject_id": "shadow-b",
                        "team_attribution_evidence_status": "team_attribution_evidence_not_materialized",
                    }],
                },
            },
        }

        issues = _issue_evidence({}, progress)
        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertEqual(issues["normal_blocking"], 0)
        self.assertEqual(issues["required_queue"]["count"], 0)
        self.assertEqual(state["issues"]["blocking"], 0)
        self.assertTrue(state["issues"]["coverage_readiness_blocked"])
        self.assertTrue(state["issues"]["team_attribution_evidence_not_materialized"])
        self.assertEqual(state["allowed_actions"], ["retry_review_recompute"])
        self.assertEqual(state["required_action"]["type"], "retry_review_recompute")

    def test_technical_team_evidence_failure_fails_closed_with_post_repair_retry(self) -> None:
        issues = {
            "blocking": 0,
            "normal_blocking": 0,
            "mixed_blocking": 0,
            "coverage_readiness_blocked": True,
            "team_attribution_evidence_not_materialized": False,
            "team_attribution_evidence_technical_failure": True,
            "coverage_readiness": {
                "status": "incomplete",
                "allows_finalize": False,
                "blockers": [{"code": "team_attribution_evidence_technical_failure"}],
            },
        }
        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertEqual(state["status"], "error")
        self.assertEqual(state["allowed_actions"], ["retry_review_recompute"])
        self.assertEqual(state["required_action"]["type"], "coverage_evidence_technical_failure")
        self.assertTrue(state["issues"]["team_attribution_evidence_technical_failure"])
        self.assertFalse(state["mandatory_operator_review_complete"])
        self.assertFalse(state["data_quality_ready_for_output"])
        self.assertNotIn("finalize_identity", state["allowed_actions"])

    def test_workflow_carries_the_exact_required_queue_descriptor(self) -> None:
        progress = {
            "next_cases": [
                {
                    "candidate_subject_id": "subject-a",
                    "scope_kind": "whole_subject",
                    "review_target_id": None,
                    "continuity_group_id": None,
                    "source_ownership_digest": "source-a",
                },
                {
                    "candidate_subject_id": "subject-b",
                    "scope_kind": "canonical_segment",
                    "review_target_id": "segment-b",
                    "continuity_group_id": "continuity-b",
                    "source_ownership_digest": "source-b",
                },
            ],
            # Deliberately wrong: workflow must read the exact queue, not this
            # counter, and its digest must change when the source set changes
            # even if the count stays at two.
            "summary": {"important_decisions_remaining": 99},
            "mixed_players": {"summary": {"unresolved": 0}},
        }
        expected = required_queue_descriptor(progress)
        issues = _issue_evidence({}, progress)
        workflow = derive_review_workflow_state(evidence(issues=issues))

        changed = {
            **progress,
            "next_cases": [
                progress["next_cases"][0],
                {**progress["next_cases"][1], "source_ownership_digest": "source-b-new"},
            ],
        }

        self.assertEqual(issues["normal_blocking"], 2)
        self.assertEqual(workflow["issues"]["required_queue"], expected)
        self.assertNotEqual(
            workflow["issues"]["required_queue"]["source_keys_digest"],
            required_queue_descriptor(changed)["source_keys_digest"],
        )

    def test_non_actionable_coverage_debt_blocks_without_fake_case(self) -> None:
        issues = _issue_evidence({}, {
            "summary": {
                "important_decisions_remaining": 0,
                "semantic_decisions_remaining": 0,
                "coverage_decisions_remaining": 0,
            },
            "coverage_readiness": {
                "status": "incomplete",
                "allows_finalize": False,
                "blockers": [{"code": "coverage_evidence_unavailable"}],
            },
            "mixed_players": {"summary": {"unresolved": 0}},
        })

        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertEqual(state["phase"], "exceptions")
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["issues"]["blocking"], 0)
        self.assertEqual(state["issues"]["actionable_blocking"], 0)
        self.assertTrue(state["issues"]["coverage_readiness_blocked"])
        self.assertTrue(state["issues"]["overall_identity_blocked"])
        self.assertEqual(state["allowed_actions"], [])
        self.assertEqual(
            state["required_action"],
            {"type": "coverage_evidence_unavailable", "step_id": "exceptions"},
        )
        self.assertEqual(
            state["blockers"][0]["code"],
            "identity_coverage_unresolved_without_reviewable_evidence",
        )
        self.assertFalse(state["blockers"][0]["user_actionable"])
        exceptions = next(row for row in state["steps"] if row["id"] == "exceptions")
        self.assertEqual(exceptions["remaining"], 0)

    def test_bounded_unavailable_team_residual_allows_finalize_after_mandatory_review(self) -> None:
        issues = _issue_evidence({}, {
            "summary": {"important_decisions_remaining": 0},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_readiness": {
                "status": "ready_with_review",
                "allows_finalize": True,
                "blockers": [],
                "team_attribution_residual": {
                    "status": "accepted_within_tolerance",
                    "observations": 193,
                    "residual_budget_observations": 6_400,
                },
            },
            "optional_audit": {"status": "available", "remaining_cases": 7},
        })

        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertTrue(state["mandatory_operator_review_complete"])
        self.assertTrue(state["data_quality_ready_for_output"])
        self.assertTrue(state["optional_max_available"])
        self.assertEqual(state["phase"], "ready_to_finalize")
        self.assertIn("finalize_identity", state["allowed_actions"])

    def test_data_quality_residual_is_distinct_from_remaining_operator_work(self) -> None:
        issues = _issue_evidence({}, {
            "summary": {"important_decisions_remaining": 0},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_readiness": {
                "status": "incomplete",
                "allows_finalize": False,
                "blockers": [{
                    "code": "team_attribution_residual_exceeds_tolerance",
                    "observations": 193,
                    "residual_budget_observations": 10,
                }],
            },
        })

        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertTrue(state["mandatory_operator_review_complete"])
        self.assertFalse(state["data_quality_ready_for_output"])
        self.assertEqual(state["issues"]["blocking"], 0)
        self.assertEqual(state["allowed_actions"], [])
        self.assertEqual(state["required_action"]["type"], "coverage_evidence_unavailable")

    def test_stale_progress_with_cached_coverage_block_is_not_terminal_completion(self) -> None:
        issues = _issue_evidence({}, {
            "summary": {"important_decisions_remaining": 0},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_readiness": {
                "status": "incomplete",
                "allows_finalize": False,
                "blockers": [{"code": "team_attribution_residual_exceeds_tolerance"}],
            },
        })

        state = derive_review_workflow_state(evidence(
            issues=issues,
            freshness={
                "reviewed_identity_current": False,
                "reviewed_stats_current": False,
                "reviewed_output_current": False,
                "qa_approval_current": False,
                "review_progress_current": False,
                "review_progress_reason": "review_progress_stale",
            },
        ))

        self.assertEqual(state["status"], "error")
        self.assertEqual(state["blockers"][0]["code"], "review_progress_stale")
        self.assertFalse(state["mandatory_operator_review_complete"])
        self.assertFalse(state["data_quality_ready_for_output"])
        self.assertEqual(state["allowed_actions"], ["retry_review_recompute"])

    def test_unmaterialized_team_evidence_keeps_a_bounded_remediation_action(self) -> None:
        issues = _issue_evidence({}, {
            "summary": {"important_decisions_remaining": 0},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_readiness": {
                "status": "incomplete",
                "allows_finalize": False,
                "blockers": [{"code": "team_attribution_evidence_not_materialized"}],
            },
            "coverage_residuals": {
                "U": {"non_actionable_required_team_uncertainty_cases": [{
                    "team_attribution_evidence_status": "team_attribution_evidence_not_materialized",
                }]},
            },
        })

        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertTrue(state["mandatory_operator_review_complete"])
        self.assertFalse(state["data_quality_ready_for_output"])
        self.assertEqual(state["allowed_actions"], ["retry_review_recompute"])
        self.assertEqual(state["required_action"]["type"], "retry_review_recompute")

    def test_empty_queue_with_ready_or_ready_with_review_coverage_can_finalize(self) -> None:
        for readiness_status in ("ready", "ready_with_review"):
            with self.subTest(readiness_status=readiness_status):
                issues = _issue_evidence({}, {
                    "summary": {"important_decisions_remaining": 0},
                    "coverage_readiness": {
                        "status": readiness_status,
                        "allows_finalize": True,
                        "blockers": [],
                    },
                })
                state = derive_review_workflow_state(evidence(issues=issues))
                self.assertEqual(state["phase"], "ready_to_finalize")
                self.assertIn("finalize_identity", state["allowed_actions"])

    def test_approval_requires_exact_current_fingerprints(self) -> None:
        snapshot = {"semantic_digest": "identity"}
        stats = {"source_snapshot_digest": "identity", "players": []}
        job = {"video_digest": "video"}
        manifest = {"video": {"digest": "video"}}
        fingerprints = current_approval_fingerprint(snapshot, stats, job, manifest)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            approval = save_video_qa_approval(root, match_id="m1", fingerprints=fingerprints)
            self.assertTrue(approval_is_current(approval, fingerprints))
            self.assertFalse(approval_is_current(approval, {**fingerprints, "reviewed_output_fingerprint": "new-video"}))

    def test_get_state_is_read_only_and_uses_cached_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "match.json", {"id": "m1", "status": "analyzed"})
            write_json(root / "analysis_report.json", {"status": "completed"})
            write_json(root / "reviewed_identity_snapshot.json", {"status": "partial_reviewed", "semantic_digest": "identity", "summary": {"conflicted": 0, "blocked": 0}, "source": {"semantic_input_digest": "x", "algorithm_version": "wrong"}})
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            state = get_review_workflow_state(root, {"id": "m1", "status": "analyzed"})
            after = {path.name: path.read_bytes() for path in root.iterdir()}
            self.assertEqual(before, after)
            self.assertEqual(state["phase"], "initial_audit")

    def test_cached_progress_with_an_old_coverage_policy_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            progress = {
                "schema_version": "2.3.0",
                "source_snapshot_digest": "identity",
                "policy": {"version": "coverage-driven-review:v3-per-team-scope"},
            }
            write_json(root / "reviewed_identity_progress.json", progress)

            current, reason = _current_cached_progress(
                root,
                {"semantic_digest": "identity"},
                {"teams": []},
            )

            self.assertIsNone(current)
            self.assertEqual(reason, "review_progress_policy_stale")

    def test_get_state_does_not_recover_or_write_an_interrupted_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "analysis_report.json", {"status": "completed"})
            write_json(root / "reviewed_video_job.json", {"status": "running", "job_key": "old", "source_snapshot_digest": "identity"})
            before = (root / "reviewed_video_job.json").read_bytes()
            state = reviewed_output_status_read_only(root, {"semantic_digest": "identity"})
            self.assertEqual((root / "reviewed_video_job.json").read_bytes(), before)
            self.assertEqual(state["status"], "failed")

    def test_initial_audit_requires_reducer_cases_not_one_click_per_frame(self) -> None:
        selection = {"selected_frames": [{"frame": frame, "visible_detections": [{}, {}]} for frame in range(1, 6)]}
        required = [f"observation-{index}" for index in range(10)]
        decisions = [
            {"observation_key": f"observation-{index}", "action": "assign_roster_player"}
            for index in range(5)
        ]
        audit = build_initial_audit_completion_evidence(
            selection,
            decisions,
            reducer_evidence={"status": "fresh", "required_observation_keys": required, "safe_to_stop": False},
        )
        state = derive_review_workflow_state(evidence(initial_audit=audit))
        self.assertFalse(audit["complete"])
        self.assertEqual(audit["remaining"], 5)
        self.assertEqual(state["phase"], "initial_audit")
        self.assertIn("identify_players", state["allowed_actions"])

    def test_initial_audit_completes_when_every_required_case_has_disposition(self) -> None:
        audit = build_initial_audit_completion_evidence(
            {"selected_frames": [{"frame": 1, "visible_detections": [{}]}]},
            [
                {"observation_key": "one", "action": "skip"},
                {"observation_key": "two", "action": "team_a_unknown"},
            ],
            reducer_evidence={"status": "fresh", "required_observation_keys": ["one", "two"], "safe_to_stop": False},
        )
        self.assertTrue(audit["complete"])
        self.assertEqual(audit["completed"], 2)

    def test_initial_audit_reducer_safe_stop_completes_before_all_cases_are_clicked(self) -> None:
        audit = build_initial_audit_completion_evidence(
            {"selected_frames": [{"frame": 1, "visible_detections": [{}, {}]}]},
            [{"observation_key": "one", "action": "skip"}],
            reducer_evidence={"status": "fresh", "required_observation_keys": ["one", "two"], "safe_to_stop": True},
        )
        self.assertTrue(audit["complete"])
        self.assertTrue(audit["safe_to_stop"])

    def test_initial_audit_completion_evidence_missing_or_stale_fails_closed(self) -> None:
        selection = {"selected_frames": [{"frame": 1, "visible_detections": [{}]}]}
        audit = build_initial_audit_completion_evidence(selection, [], reducer_evidence=None)
        stale = build_initial_audit_completion_evidence(selection, [], reducer_evidence={"status": "stale"})
        self.assertFalse(audit["complete"])
        self.assertFalse(stale["complete"])

    def test_missing_or_stale_progress_blocks_finalization_after_initial_audit(self) -> None:
        for reason in ("review_progress_missing", "review_progress_stale"):
            with self.subTest(reason=reason):
                state = derive_review_workflow_state(evidence(freshness={
                    "reviewed_identity_current": True,
                    "reviewed_stats_current": False,
                    "reviewed_output_current": False,
                    "qa_approval_current": False,
                    "review_progress_current": False,
                    "review_progress_reason": reason,
                }))
                self.assertEqual(state["status"], "error")
                self.assertEqual(state["blockers"][0]["code"], reason)
                self.assertEqual(state["allowed_actions"], ["retry_review_recompute"])
                self.assertNotIn("finalize_identity", state["allowed_actions"])

    def test_fresh_progress_distinguishes_zero_issues_from_issues(self) -> None:
        fresh = {"review_progress_current": True, "reviewed_identity_current": False, "reviewed_stats_current": False, "reviewed_output_current": False, "qa_approval_current": False}
        self.assertEqual(
            derive_review_workflow_state(evidence(freshness=fresh))["phase"],
            "ready_to_finalize",
        )
        self.assertEqual(
            derive_review_workflow_state(evidence(freshness=fresh, issues={"blocking": 1}))["phase"],
            "exceptions",
        )

    def test_get_state_treats_progress_digest_mismatch_as_stale_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_state.load_initial_audit_completion_evidence",
            return_value={"prepared": True, "complete": True, "completed": 2, "total": 2, "remaining": 0},
        ):
            root = Path(tmp)
            write_json(root / "analysis_report.json", {"status": "completed"})
            write_json(root / "reviewed_identity_snapshot.json", {
                "status": "partial_reviewed",
                "semantic_digest": "identity-new",
                "summary": {"conflicted": 0, "blocked": 0},
            })
            write_json(root / "reviewed_identity_progress.json", {
                "source_snapshot_digest": "identity-old",
                "summary": {"important_decisions_remaining": 0},
            })
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            state = get_review_workflow_state(root, {"id": "m1", "status": "analyzed"})
            after = {path.name: path.read_bytes() for path in root.iterdir()}
            self.assertEqual(before, after)
            self.assertEqual(state["phase"], "exceptions")
            self.assertEqual(state["blockers"][0]["code"], "review_progress_stale")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
