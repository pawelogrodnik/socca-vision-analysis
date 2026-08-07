from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is required for workflow API tests")
class ReviewWorkflowApiTests(unittest.TestCase):
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
        self.assertEqual(raised.exception.detail["code"], "review_not_completed")

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

    def test_initial_audit_identity_update_is_gated_and_recomputed(self) -> None:
        from app.main import update_initial_identity_audit_seeds

        initial_state = {
            "phase": "initial_audit",
            "allowed_actions": ["identify_players"],
            "blockers": [],
        }
        refreshed = {
            "workflow": {"phase": "exceptions"},
            "snapshot": {"semantic_digest": "new"},
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
            "app.main.refresh_review_after_identity_mutation", return_value=refreshed
        ) as refresh:
            response = update_initial_identity_audit_seeds(
                "m1",
                {"updates": [{"observation_key": "one", "action": "skip"}]},
            )
        save.assert_called_once()
        rebuild.assert_called_once()
        refresh.assert_called_once()
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
