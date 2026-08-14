from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.review_workflow_state import (
    WorkflowActionError,
    assert_workflow_action_allowed,
    derive_review_workflow_state,
    get_review_workflow_state,
    _issue_evidence,
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


class ReviewWorkflowStateTests(unittest.TestCase):
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

    def test_optional_team_audit_remains_available_without_blocking_finalize(self) -> None:
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
        }
        issues = _issue_evidence({"summary": {}}, progress)

        state = derive_review_workflow_state(evidence(issues=issues))

        self.assertEqual(state["phase"], "ready_to_finalize")
        self.assertEqual(state["issues"]["optional_audit"], 180)
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
        self.assertEqual(state["status"], "action_required")
        self.assertEqual(state["issues"]["blocking"], 0)
        self.assertEqual(state["issues"]["actionable_blocking"], 0)
        self.assertTrue(state["issues"]["coverage_readiness_blocked"])
        self.assertTrue(state["issues"]["overall_identity_blocked"])
        self.assertEqual(state["allowed_actions"], [])
        self.assertIsNone(state["required_action"])
        self.assertEqual(
            state["blockers"][0]["code"],
            "identity_coverage_unresolved_without_reviewable_evidence",
        )
        self.assertFalse(state["blockers"][0]["user_actionable"])
        exceptions = next(row for row in state["steps"] if row["id"] == "exceptions")
        self.assertEqual(exceptions["remaining"], 0)

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
