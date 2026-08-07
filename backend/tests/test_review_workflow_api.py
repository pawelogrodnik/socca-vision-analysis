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


if __name__ == "__main__":
    unittest.main()
