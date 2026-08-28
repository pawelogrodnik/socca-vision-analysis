from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.review_workflow_orchestrator import (
    ReviewWorkflowRecomputeError,
    after_video_qa_correction,
    finalize_review_for_qa,
    refresh_review_after_identity_mutation,
    retry_review_recompute,
)
from app.services.review_workflow_state import WorkflowActionError
from app.services.identity_reviewed_recompute_state import (
    mark_reviewed_identity_recompute_required,
)
from app.services.identity_initial_audit_store import (
    write_identity_json_atomic as write_identity_json_atomic_original,
)


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
    def test_retry_commits_and_warms_the_generation_before_returning_workflow(self) -> None:
        retryable = {
            "allowed_actions": ["retry_review_recompute"],
            "issues": {"normal_blocking": 0, "mixed_blocking": 0},
        }
        refreshed = {"workflow": {"issues": {"normal_blocking": 0}}}
        with patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value=retryable,
        ), patch(
            "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
            return_value=refreshed,
        ) as refresh:
            result = retry_review_recompute(Path("/tmp/match"), {"id": "m1"})

        self.assertEqual(result, refreshed)
        refresh.assert_called_once_with(
            Path("/tmp/match"),
            {"id": "m1"},
            source="retry",
            leave_hot_state_warm=True,
        )

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
            "summary": {"important_decisions_remaining": 1},
            "mixed_players": {"summary": {"unresolved": 0}},
            "coverage_residuals": {},
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
            return_value={"summary": {}},
        ) as materialize:
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
