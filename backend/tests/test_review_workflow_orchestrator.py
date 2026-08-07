from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.review_workflow_orchestrator import (
    after_video_qa_correction,
    finalize_review_for_qa,
    refresh_review_after_identity_mutation,
)


def ready_state() -> dict:
    return {"issues": {"blocking": 0}, "allowed_actions": ["finalize_identity"], "phase": "ready_to_finalize"}


class ReviewWorkflowOrchestratorTests(unittest.TestCase):
    def test_lightweight_identity_refresh_does_not_build_stats_or_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
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
            result = refresh_review_after_identity_mutation(Path(tmp), {"id": "m1"}, source="initial_audit_decision")
            self.assertEqual(result["workflow"]["phase"], "ready_to_finalize")
            stats.assert_not_called()
            render.assert_not_called()

    def test_finalize_builds_stats_then_queues_one_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
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

    def test_video_qa_correction_with_blocker_does_not_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value={"workflow": {"issues": {"blocking": 1}, "phase": "exceptions"}, "snapshot": {}},
        ), patch("app.services.review_workflow_orchestrator.generate_reviewed_output") as render:
            result = after_video_qa_correction(Path(tmp), {"id": "m1"})
            self.assertEqual(result["workflow"]["phase"], "exceptions")
            render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
