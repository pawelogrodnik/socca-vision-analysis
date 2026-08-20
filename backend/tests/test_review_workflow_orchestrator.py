from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.review_workflow_orchestrator import (
    ReviewWorkflowRecomputeError,
    after_video_qa_correction,
    finalize_review_for_qa,
    refresh_review_after_identity_mutation,
)
from app.services.review_workflow_state import WorkflowActionError
from app.services.identity_reviewed_recompute_state import (
    mark_reviewed_identity_recompute_required,
)


def ready_state() -> dict:
    return {"issues": {"blocking": 0}, "allowed_actions": ["finalize_identity"], "phase": "ready_to_finalize"}


class ReviewWorkflowOrchestratorTests(unittest.TestCase):
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
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=ready_state(),
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
