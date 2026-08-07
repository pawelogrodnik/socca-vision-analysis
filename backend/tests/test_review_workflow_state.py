from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.review_workflow_state import (
    WorkflowActionError,
    assert_workflow_action_allowed,
    derive_review_workflow_state,
    get_review_workflow_state,
    _issue_evidence,
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
            ("ready", evidence(), "ready", "ready_to_finalize", ["finalize_identity"]),
            ("queued", evidence(render={"status": "queued"}), "processing", "rendering_review_video", []),
            ("running", evidence(render={"status": "running"}), "processing", "rendering_review_video", []),
            ("failed", evidence(render={"status": "failed"}), "error", "rendering_review_video", ["retry_render"]),
            ("recompute", evidence(recompute_failed=True), "error", "initial_audit", ["retry_review_recompute"]),
            ("stale-render", evidence(freshness={"reviewed_identity_current": True, "reviewed_stats_current": True, "reviewed_output_current": False, "qa_approval_current": True}, render={"status": "completed"}), "ready", "ready_to_finalize", ["finalize_identity"]),
            ("qa", evidence(freshness={"reviewed_identity_current": True, "reviewed_stats_current": True, "reviewed_output_current": True, "qa_approval_current": False}, render={"status": "completed"}), "action_required", "video_qa", ["review_video", "approve_video_qa", "correct_video_identity"]),
            ("complete", evidence(freshness={"reviewed_identity_current": True, "reviewed_stats_current": True, "reviewed_output_current": True, "qa_approval_current": True}, render={"status": "completed"}), "complete", "complete", []),
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

    def test_frame_resolved_technical_conflict_is_not_an_exception_but_a_real_gap_is(self) -> None:
        progress = {"summary": {"structural_blockers": 1, "important_decisions_remaining": 0}}
        resolved = _issue_evidence({"summary": {"conflicted": 0, "blocked": 0}}, progress)
        unresolved = _issue_evidence({"summary": {"conflicted": 1, "blocked": 0}}, progress)
        self.assertEqual(resolved["blocking"], 0)
        self.assertEqual(unresolved["blocking"], 1)

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


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
