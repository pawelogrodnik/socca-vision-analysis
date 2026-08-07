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


if __name__ == "__main__":
    unittest.main()
