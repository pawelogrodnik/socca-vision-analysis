from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import finalize_match_reviewed_identity_corrections
from app.services.review_workflow_orchestrator import finalize_review_for_qa
from app.services.review_workflow_state import WorkflowActionError


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _match() -> dict:
    return {
        "id": "m1",
        "status": "analyzed",
        "fps": 1,
        "teams": [
            {"team_label": "A", "players": [{"id": "player-a", "name": "Patryk"}]},
            {"team_label": "B", "players": [{"id": "player-b", "name": "Verisk"}]},
        ],
    }


def _fixture(root: Path) -> dict:
    match = _match()
    _write(root / "match.json", match)
    _write(
        root / "identity_candidate_shadow.json",
        {"subjects": [{"candidate_subject_id": "subject-1", "tracklet_ids": ["t1"]}]},
    )
    _write(root / "tracklets.json", {"tracklets": [{
        "tracklet_id": "t1",
        "team_label": "A",
        "positions_m": [
            {
                "frame": frame,
                "time_sec": float(frame),
                "x_m": float(frame),
                "y_m": 1.0,
                "detected": True,
                "play_area_status": "inside_play",
                "bbox_xyxy": [10, 10, 20, 30],
            }
            for frame in range(1, 10)
        ],
    }]})
    _write(root / "identity_roster_subject_review_shadow.json", {"cards": [{
        "candidate_subject_id": "subject-1",
        "review_status": "blocked_conflict",
        "requires_operator_review": True,
        "reason_codes": ["parallel_roster_candidate_conflict"],
        "visual_evidence": {"anchor_crops": [
            {"anchor_crop_id": f"c{frame}", "artifact": f"c{frame}.jpg", "frame": frame, "time_sec": float(frame), "tracklet_id": "t1"}
            for frame in (1, 3, 5, 7, 9)
        ]},
    }]})
    _write(root / "global_identity.json", {"slots": []})
    _write(root / "reviewed_identity_snapshot.json", {
        "semantic_digest": "snapshot-1",
        "tracklet_assignments": [],
    })
    return match


class FinalizeEndpointContractTests(unittest.TestCase):
    def test_corrections_finalize_endpoint_returns_compact_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            match = _fixture(root)
            with patch("app.main.match_dir", return_value=root), patch(
                "app.main.read_match_meta", return_value=match
            ):
                response = finalize_match_reviewed_identity_corrections("m1")
        self.assertNotIn("canonical_observation_assignments", response["reviewed_identity"])
        self.assertNotIn("tracklet_assignments", response["reviewed_identity"])
        self.assertNotIn("_internal_review_units", response["review_progress"])

    def test_finalize_still_rejects_when_blockers_remain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.review_workflow_orchestrator.build_cheap_finalize_preflight_state",
            return_value={"issues": {"blocking": 0}, "allowed_actions": ["finalize_identity"], "phase": "ready_to_finalize"},
        ), patch(
            "app.services.review_workflow_orchestrator.finalize_reviewed_identity",
            return_value={"semantic_digest": "fresh"},
        ), patch(
            "app.services.review_workflow_orchestrator.build_reviewed_identity_progress",
            return_value={"summary": {}},
        ), patch(
            "app.services.review_workflow_orchestrator.get_review_workflow_state",
            return_value={
                "issues": {"blocking": 2, "overall_identity_blocked": True},
                "allowed_actions": [],
                "phase": "exceptions",
            },
        ):
            with self.assertRaises(WorkflowActionError):
                finalize_review_for_qa(Path(tmp), {"id": "m1"})


if __name__ == "__main__":
    unittest.main()
