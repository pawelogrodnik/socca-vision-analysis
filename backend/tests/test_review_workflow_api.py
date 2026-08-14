from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is required for workflow API tests")
class ReviewWorkflowApiTests(unittest.TestCase):
    def test_scope_change_rebuilds_only_progress_and_preserves_identity_decisions(self) -> None:
        from app.main import update_match_metadata
        from app.models import MatchMetadataPayload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            match = {
                "id": "m1",
                "title": "Match",
                "format": "7v7",
                "status": "analyzed",
                "teams": [
                    {"id": "a", "name": "Corgi", "players": [{"id": "pa", "name": "Paweł"}]},
                    {"id": "b", "name": "Verisk", "players": [{"id": "pb", "name": "Opponent"}]},
                ],
                "identity_review_scope": {
                    "teams": {"A": "complete_roster", "B": "complete_roster"}
                },
            }
            (root / "match.json").write_text(json.dumps(match), encoding="utf-8")
            (root / "reviewed_identity_snapshot.json").write_text(
                json.dumps({"semantic_digest": "snapshot"}), encoding="utf-8"
            )
            decisions = root / "reviewed_slot_assignments.json"
            decisions.write_text(
                json.dumps({"decisions": [{"candidate_subject_id": "subject", "action": "assign_team"}]}),
                encoding="utf-8",
            )
            team_stats = root / "team_stats.json"
            team_stats.write_text(
                json.dumps({"teams": [{"team_label": "B", "observations": 1200}]}),
                encoding="utf-8",
            )
            before_decisions = decisions.read_bytes()
            before_team_stats = team_stats.read_bytes()
            payload = MatchMetadataPayload(
                title="Match",
                format="7v7",
                status="analyzed",
                teams=match["teams"],
                identity_review_scope={
                    "teams": {"A": "complete_roster", "B": "team_stats_only"}
                },
            )
            progress = {
                "schema_version": "2.2.0",
                "source_snapshot_digest": "snapshot",
                "next_cases": [],
            }

            with patch("app.main.match_dir", return_value=root), patch(
                "app.main.build_reviewed_identity_progress", return_value=progress
            ) as build_progress, patch("app.main.refresh_review_after_identity_mutation") as identity_rebuild:
                updated = update_match_metadata("m1", payload)

            self.assertEqual(updated["identity_review_scope"]["teams"]["B"], "team_stats_only")
            self.assertEqual(decisions.read_bytes(), before_decisions)
            self.assertEqual(team_stats.read_bytes(), before_team_stats)
            build_progress.assert_called_once()
            identity_rebuild.assert_not_called()

    def test_publish_gate_rejects_direct_backend_bypass(self) -> None:
        from fastapi import HTTPException
        from app.main import _assert_publish_workflow

        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch(
            "app.main.get_review_workflow_state",
            return_value={"review_complete": False, "phase": "video_qa"},
        ):
            with self.assertRaises(HTTPException) as raised:
                _assert_publish_workflow(Path(tmp))
        self.assertEqual(raised.exception.status_code, 409)

    def test_publish_gate_allows_current_qa_approval(self) -> None:
        from app.main import _assert_publish_workflow

        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch(
            "app.main.get_review_workflow_state",
            return_value={"review_complete": True, "phase": "complete"},
        ):
            _assert_publish_workflow(Path(tmp))

    def test_legacy_finalize_only_refreshes_identity_and_progress(self) -> None:
        from app.main import finalize_match_reviewed_identity

        refreshed = {
            "snapshot": {"status": "partial_reviewed", "semantic_digest": "identity"},
            "workflow": {"phase": "ready_to_finalize"},
        }
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch("app.main.refresh_review_after_identity_mutation", return_value=refreshed) as refresh, patch(
            "app.main.finalize_review_for_qa"
        ) as expensive:
            response = finalize_match_reviewed_identity("m1")
        self.assertEqual(response["semantic_digest"], "identity")
        self.assertEqual(response["workflow"]["phase"], "ready_to_finalize")
        self.assertEqual(refresh.call_args.kwargs["source"], "legacy_reviewed_identity_finalize")
        expensive.assert_not_called()

    def test_approved_complete_state_correction_uses_video_qa_orchestration(self) -> None:
        from app.main import post_match_reviewed_identity_correction

        refreshed = {
            "workflow": {"phase": "rendering_review_video"},
            "snapshot": {"semantic_digest": "new"},
            "render_job": {"status": "queued", "job_key": "new-render"},
        }
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch(
            "app.main.get_review_workflow_state",
            return_value={"phase": "complete", "allowed_actions": ["correct_video_identity"]},
        ), patch(
            "app.main.save_reviewed_identity_correction", return_value={"saved": True}
        ), patch("app.main.after_video_qa_correction", return_value=refreshed) as after, patch(
            "app.main.refresh_review_after_identity_mutation"
        ) as lightweight:
            response = post_match_reviewed_identity_correction("m1", {"candidate_subject_id": "subject-1", "action": "unresolved"})
        self.assertTrue(response["saved"])
        self.assertEqual(response["workflow"]["phase"], "rendering_review_video")
        self.assertEqual(response["render_job"]["job_key"], "new-render")
        after.assert_called_once()
        lightweight.assert_not_called()

    def test_exception_correction_does_not_rebuild_seeded_candidate_evidence(self) -> None:
        from app.main import post_match_reviewed_identity_correction

        refreshed = {
            "snapshot": {"status": "partial_reviewed", "semantic_digest": "identity"},
            "workflow": {"phase": "exceptions"},
        }
        state = {"phase": "exceptions", "allowed_actions": ["review_identity_issue"]}
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch("app.main.get_review_workflow_state", return_value=state), patch(
            "app.main.save_reviewed_identity_correction", return_value={"saved": True}
        ), patch(
            "app.main.refresh_review_after_identity_mutation", return_value=refreshed
        ) as refresh:
            response = post_match_reviewed_identity_correction(
                "m1",
                {"candidate_subject_id": "subject-1", "action": "team_unknown"},
            )

        self.assertTrue(response["saved"])
        self.assertEqual(response["workflow"]["phase"], "exceptions")
        self.assertFalse(refresh.call_args.kwargs["rebuild_seeded_candidates"])

    def test_deferred_exception_correction_only_persists(self) -> None:
        from app.main import post_match_reviewed_identity_correction

        persisted = {
            "saved_decision": {"candidate_subject_id": "subject-1"},
            "effective_action": "unresolved",
            "allocated_stable_slot_id": None,
            "semantic_decision_digest": "decision",
            "recompute_deferred": True,
            "persistence": {
                "status": "saved",
                "downstream_recompute_triggered": False,
            },
        }
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch(
            "app.main.validate_deferred_review_action",
            return_value={
                "idempotent_replay": False,
                "detected_team_labels_by_subject": {"subject-1": {"A", "B"}},
            },
        ) as gate, patch(
            "app.main.persist_reviewed_identity_correction",
            return_value=persisted,
        ) as persist, patch(
            "app.main.get_review_workflow_state"
        ) as workflow_state, patch(
            "app.main.save_reviewed_identity_correction"
        ) as legacy_save, patch(
            "app.main.refresh_review_after_identity_mutation"
        ) as refresh, patch(
            "app.main.after_video_qa_correction"
        ) as video_qa, patch(
            "app.main.build_reviewed_identity_progress"
        ) as progress_build, patch(
            "app.main.finalize_reviewed_identity"
        ) as finalize_snapshot, patch(
            "app.main.rebuild_identity_seeded_candidate_assignments"
        ) as seeded_rebuild:
            response = post_match_reviewed_identity_correction(
                "m1",
                {
                    "candidate_subject_id": "subject-1",
                    "action": "unresolved",
                    "defer_recompute": True,
                    "detected_team_labels": ["A"],
                },
            )

        self.assertTrue(response["recompute_deferred"])
        gate.assert_called_once()
        persist.assert_called_once()
        self.assertEqual(
            persist.call_args.kwargs["trusted_materialized_detected_team_labels"],
            {"subject-1": {"A", "B"}},
        )
        workflow_state.assert_not_called()
        legacy_save.assert_not_called()
        refresh.assert_not_called()
        video_qa.assert_not_called()
        progress_build.assert_not_called()
        finalize_snapshot.assert_not_called()
        seeded_rebuild.assert_not_called()

    def test_deferred_gate_failure_returns_actionable_conflict(self) -> None:
        from fastapi import HTTPException
        from app.main import post_match_reviewed_identity_correction
        from app.services.identity_reviewed_action_gate import (
            DeferredReviewActionError,
        )

        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch(
            "app.main.validate_deferred_review_action",
            side_effect=DeferredReviewActionError(
                "review_queue_stale",
                "Odśwież Review.",
            ),
        ), patch(
            "app.main.persist_reviewed_identity_correction"
        ) as persist:
            with self.assertRaises(HTTPException) as raised:
                post_match_reviewed_identity_correction(
                    "m1",
                    {
                        "candidate_subject_id": "subject-1",
                        "action": "unresolved",
                        "defer_recompute": True,
                    },
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "review_queue_stale")
        persist.assert_not_called()

    def test_finalize_deferred_corrections_refreshes_once_without_seeded_rebuild(self) -> None:
        from app.main import finalize_match_reviewed_identity_corrections

        refreshed = {
            "snapshot": {"semantic_digest": "identity"},
            "review_progress": {"summary": {}},
            "workflow": {"phase": "ready_to_finalize"},
            "performance": {"total_ms": 10.0},
        }
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch(
            "app.main.refresh_review_after_identity_mutation",
            return_value=refreshed,
        ) as refresh:
            response = finalize_match_reviewed_identity_corrections("m1")

        self.assertEqual(response["workflow"]["phase"], "ready_to_finalize")
        refresh.assert_called_once()
        self.assertFalse(refresh.call_args.kwargs["rebuild_seeded_candidates"])

    def test_initial_audit_frame_save_is_gated_without_recompute(self) -> None:
        from app.main import update_initial_identity_audit_seeds

        initial_state = {
            "phase": "initial_audit",
            "allowed_actions": ["identify_players"],
            "blockers": [],
        }
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch("app.main.match_video_path", return_value=Path("/tmp/m1/video.mp4")), patch(
            "app.main.get_review_workflow_state", return_value=initial_state
        ), patch(
            "app.main.prepare_initial_identity_audit"
        ), patch(
            "app.main.save_initial_identity_audit_seeds",
            return_value={"decisions": [{"observation_key": "one"}]},
        ) as save, patch(
            "app.main.benchmark_context_for_workspace", return_value=None
        ), patch(
            "app.main.rebuild_seeded_identity_after_operator_audit",
            return_value={"status": "fresh"},
        ) as rebuild, patch(
            "app.main.refresh_review_after_identity_mutation"
        ) as refresh:
            response = update_initial_identity_audit_seeds(
                "m1",
                {"updates": [{"observation_key": "one", "action": "skip"}]},
            )
        save.assert_called_once()
        rebuild.assert_not_called()
        refresh.assert_not_called()
        self.assertEqual(response["workflow"]["phase"], "initial_audit")

    def test_initial_audit_finish_recomputes_once_after_final_save(self) -> None:
        from app.main import update_initial_identity_audit_seeds

        refreshed = {
            "workflow": {"phase": "exceptions"},
            "snapshot": {"semantic_digest": "new"},
        }
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch("app.main.match_video_path", return_value=Path("/tmp/m1/video.mp4")), patch(
            "app.main.get_review_workflow_state", return_value={
                "phase": "initial_audit",
                "allowed_actions": ["identify_players"],
                "blockers": [],
            }
        ), patch(
            "app.main.prepare_initial_identity_audit"
        ), patch(
            "app.main.save_initial_identity_audit_seeds",
            return_value={"decisions": [{"observation_key": "one"}]},
        ) as save, patch(
            "app.main.benchmark_context_for_workspace", return_value=None
        ), patch(
            "app.main.rebuild_seeded_identity_after_operator_audit",
            return_value={"status": "fresh"},
        ) as rebuild, patch(
            "app.main.refresh_review_after_identity_mutation", return_value=refreshed
        ) as refresh:
            response = update_initial_identity_audit_seeds(
                "m1",
                {
                    "updates": [],
                    "telemetry_events": [{"event_type": "session_finished"}],
                    "finalize": True,
                },
            )

        save.assert_called_once()
        rebuild.assert_called_once()
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.kwargs["source"], "initial_audit_finish")
        self.assertEqual(response["workflow"]["phase"], "exceptions")

    def test_retried_initial_audit_finish_does_not_recompute(self) -> None:
        from app.main import update_initial_identity_audit_seeds

        completed_state = {
            "phase": "exceptions",
            "allowed_actions": ["review_identity_issue"],
            "blockers": [],
        }
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch(
            "app.main.match_video_path", return_value=Path("/tmp/m1/video.mp4")
        ), patch(
            "app.main.get_review_workflow_state", return_value=completed_state
        ), patch(
            "app.main.prepare_initial_identity_audit"
        ), patch(
            "app.main.save_initial_identity_audit_seeds",
            return_value={"decisions": []},
        ) as save, patch(
            "app.main.rebuild_seeded_identity_after_operator_audit"
        ) as rebuild, patch(
            "app.main.refresh_review_after_identity_mutation"
        ) as refresh:
            response = update_initial_identity_audit_seeds(
                "m1",
                {
                    "updates": [],
                    "telemetry_events": [{"event_type": "session_finished"}],
                    "finalize": True,
                },
            )

        save.assert_called_once()
        rebuild.assert_not_called()
        refresh.assert_not_called()
        self.assertEqual(response["workflow"]["phase"], "exceptions")

    def test_initial_audit_telemetry_after_completion_skips_recompute(self) -> None:
        from app.main import update_initial_identity_audit_seeds

        completed_state = {
            "phase": "exceptions",
            "allowed_actions": ["review_identity_issue"],
            "blockers": [],
        }
        for event_type in ("session_finished", "frame_shown"):
            with self.subTest(event_type=event_type), patch(
                "app.main.match_dir", return_value=Path("/tmp/m1")
            ), patch(
                "app.main.read_match_meta", return_value={"id": "m1"}
            ), patch("app.main.match_video_path", return_value=Path("/tmp/m1/video.mp4")), patch(
                "app.main.get_review_workflow_state", return_value=completed_state
            ) as state, patch(
                "app.main.prepare_initial_identity_audit"
            ), patch(
                "app.main.save_initial_identity_audit_seeds", return_value={"decisions": []}
            ) as save, patch(
                "app.main.rebuild_seeded_identity_after_operator_audit"
            ) as rebuild, patch(
                "app.main.refresh_review_after_identity_mutation"
            ) as refresh:
                response = update_initial_identity_audit_seeds(
                    "m1",
                    {"updates": [], "telemetry_events": [{"event_type": event_type}]},
                )
            save.assert_called_once()
            self.assertEqual(state.call_count, 1)
            self.assertEqual(response["workflow"]["phase"], "exceptions")
            rebuild.assert_not_called()
            refresh.assert_not_called()

    def test_late_initial_audit_identity_update_remains_rejected(self) -> None:
        from fastapi import HTTPException
        from app.main import update_initial_identity_audit_seeds

        completed_state = {
            "phase": "exceptions",
            "allowed_actions": [],
            "blockers": [{"code": "initial_audit_incomplete"}],
        }
        with patch("app.main.match_dir", return_value=Path("/tmp/m1")), patch(
            "app.main.read_match_meta", return_value={"id": "m1"}
        ), patch("app.main.get_review_workflow_state", return_value=completed_state), patch(
            "app.main.save_initial_identity_audit_seeds"
        ) as save:
            with self.assertRaises(HTTPException) as raised:
                update_initial_identity_audit_seeds(
                    "m1",
                    {"updates": [{"observation_key": "late", "action": "skip"}]},
                )
        self.assertEqual(raised.exception.status_code, 409)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
