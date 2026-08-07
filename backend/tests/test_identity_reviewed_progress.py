from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_reviewed_progress import build_reviewed_identity_progress


class ReviewedIdentityProgressTests(unittest.TestCase):
    def test_progress_uses_candidate_subjects_and_real_detected_positions(self) -> None:
        with _workspace() as root:
            _fixture(root)
            before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
            progress = build_reviewed_identity_progress(root, _match())
            self.assertEqual(progress["summary"]["review_units_total"], 6)
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 1)
            self.assertEqual(progress["summary"]["optional_cases_remaining"], 1)
            self.assertEqual(progress["summary"]["ignored_low_impact"], 1)
            self.assertEqual(progress["summary"]["structural_blockers"], 3)
            long = _unit(progress, "long")
            self.assertEqual(long["tracklet_count"], 2)
            self.assertEqual(long["detected_observation_count"], 120)
            self.assertEqual(long["detected_frame_count"], 60)
            self.assertEqual(long["current_resolution_status"], "pending_high_priority")
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()})

    def test_operator_decision_marks_one_whole_subject_complete(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _write(root / "reviewed_identity_slot_assignments.json", {
                "decisions": [{"candidate_subject_id": "long", "action": "assign_team", "team_label": "B"}],
                "reviewed_slots": [],
            })
            progress = build_reviewed_identity_progress(root, _match())
            long = _unit(progress, "long")
            self.assertEqual(long["current_resolution_status"], "reviewed_by_operator")
            self.assertEqual(progress["summary"]["completed_by_operator"], 1)
            self.assertEqual(progress["observations"]["operator_reviewed_observations"], 120)
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)


def _fixture(root: Path) -> None:
    tracklets = [
        _tracklet("l1", "U", range(1, 61)),
        _tracklet("l2", "U", range(1, 61)),
        _tracklet("optional", "A", range(70, 85)),
        _tracklet("noise", "A", [100]),
        _tracklet("mixed-a", "A", [110]),
        _tracklet("mixed-b", "B", [111]),
        _tracklet("ambiguous", "A", [120]),
    ]
    tracklets[0]["positions_m"].append({"frame": 61, "status": "predicted", "source": "predicted"})
    _write(root / "tracklets.json", {"tracklets": tracklets})
    _write(root / "identity_candidate_shadow.json", {"subjects": [
        {"candidate_subject_id": "long", "tracklet_ids": ["l1", "l2"]},
        {"candidate_subject_id": "optional", "tracklet_ids": ["optional"]},
        {"candidate_subject_id": "noise", "tracklet_ids": ["noise"]},
        {"candidate_subject_id": "mixed", "tracklet_ids": ["mixed-a", "mixed-b"]},
        {"candidate_subject_id": "ambiguous", "tracklet_ids": ["ambiguous"]},
        {"candidate_subject_id": "ambiguous-other", "tracklet_ids": ["ambiguous"]},
    ]})


def _tracklet(tracklet_id: str, team: str, frames: range | list[int]) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team,
        "positions_m": [{"frame": frame, "status": "detected", "source": "detected"} for frame in frames],
    }


def _match() -> dict:
    return {"id": "progress", "teams": []}


def _unit(progress: dict, subject_id: str) -> dict:
    return next(row for row in progress["review_units"] if row["candidate_subject_id"] == subject_id)


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class _workspace:
    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        return Path(self.temporary.name)

    def __exit__(self, *args: object) -> None:
        self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
