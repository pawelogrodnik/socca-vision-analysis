from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.main import get_artifact

from app.services.identity_reviewed_segments import (
    SegmentTargetError,
    build_segment_review_document,
    load_segment_decisions,
    save_segment_decision,
    segment_observation_assignments,
)
from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.identity_reviewed_effective_observation import (
    effective_reviewed_observation,
)


class ReviewedIdentitySegmentTests(unittest.TestCase):
    def test_generated_segment_crop_is_available_through_match_artifact_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            match_path = Path(directory)
            digest = "a" * 64
            relative = Path("reviewed_identity_segments") / digest / "01_f001218.jpg"
            artifact = match_path / relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"jpeg")

            with patch("app.main.match_dir", return_value=match_path):
                response = get_artifact("match", str(relative))

            self.assertIsInstance(response, FileResponse)
            self.assertEqual(response.media_type, "image/jpeg")

    def test_segment_crop_route_rejects_non_digest_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            match_path = Path(directory)
            relative = Path("reviewed_identity_segments") / "not-a-digest" / "crop.jpg"
            artifact = match_path / relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"jpeg")

            with patch("app.main.match_dir", return_value=match_path):
                with self.assertRaises(HTTPException) as raised:
                    get_artifact("match", str(relative))

            self.assertEqual(raised.exception.status_code, 404)

    def test_segment_overrides_exact_anchor_but_not_safety_demotion(self) -> None:
        effective = effective_reviewed_observation(
            {"tracklet_id": "t1", "identity_status": "unresolved"},
            {"tracklet_id": "t1", "frame": 5},
            {("t1", 5): {"identity_status": "confirmed", "player_name": "Old"}},
            {("t1", 5): {"identity_status": "conflicted", "player_name": None}},
            {("t1", 5): {"identity_status": "unresolved"}},
            {("t1", 5): {"identity_status": "confirmed", "player_name": "Pawel"}},
        )
        self.assertEqual(effective["identity_status"], "conflicted")
        self.assertIsNone(effective["player_name"])

    def test_mixed_tracklet_creates_exact_non_overlapping_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            review = build_segment_review_document(root, match)

            self.assertEqual(review["summary"]["mixed_tracklets"], 1)
            self.assertEqual(review["summary"]["targets_total"], 2)
            targets = {row["stable_slot_id"]: row for row in review["targets"]}
            self.assertEqual(targets["A03"]["frame_ranges"], [[1, 2], [5, 5]])
            self.assertEqual(targets["A05"]["frame_ranges"], [[3, 4]])
            self.assertEqual(
                targets["A03"]["legacy_suggestion"]["player_id"],
                "p1",
            )
            self.assertIsNone(targets["A05"]["legacy_suggestion"])

    def test_segment_decisions_do_not_bleed_and_stale_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            review = build_segment_review_document(root, match)
            targets = {row["stable_slot_id"]: row for row in review["targets"]}

            with self.assertRaisesRegex(SegmentTargetError, "review_target_stale"):
                save_segment_decision(
                    root,
                    match,
                    {
                        "review_target_id": targets["A03"]["review_target_id"],
                        "source_ownership_digest": "stale",
                        "action": "assign_roster_player",
                        "player_id": "p1",
                    },
                )

            for slot_id, action, extra in (
                ("A03", "assign_roster_player", {"player_id": "p1"}),
                ("A05", "assign_team", {"team_label": "B"}),
            ):
                target = targets[slot_id]
                save_segment_decision(
                    root,
                    match,
                    {
                        "review_target_id": target["review_target_id"],
                        "source_ownership_digest": target["source_ownership_digest"],
                        "action": action,
                        **extra,
                    },
                )

            refreshed = build_segment_review_document(root, match)
            rows = segment_observation_assignments(
                refreshed,
                load_segment_decisions(root),
                {
                    "p1": {
                        "name": "Pawel",
                        "number": 92,
                        "team_label": "A",
                    }
                },
            )
            by_frame = {int(row["frame"]): row for row in rows}
            self.assertEqual(set(by_frame), {1, 2, 3, 4, 5})
            self.assertEqual(by_frame[1]["canonical_player_id"], "p1")
            self.assertEqual(by_frame[5]["player_name"], "Pawel")
            self.assertIsNone(by_frame[3]["canonical_player_id"])
            self.assertEqual(by_frame[3]["team_label"], "B")
            self.assertEqual(by_frame[3]["display_label"], "B?")

    def test_progress_counts_targets_instead_of_unsafe_whole_subject_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            review = build_segment_review_document(root, match)
            progress = build_reviewed_identity_progress(root, match)
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 2)
            self.assertEqual(
                {row["review_target_id"] for row in progress["next_cases"]},
                {row["review_target_id"] for row in review["targets"]},
            )

            target = next(
                row for row in review["targets"] if row["stable_slot_id"] == "A03"
            )
            save_segment_decision(
                root,
                match,
                {
                    "review_target_id": target["review_target_id"],
                    "source_ownership_digest": target["source_ownership_digest"],
                    "action": "assign_roster_player",
                    "player_id": "p1",
                },
            )
            build_segment_review_document(root, match)
            after = build_reviewed_identity_progress(root, match)
            self.assertEqual(after["summary"]["important_decisions_remaining"], 1)


def _fixture(root: Path) -> dict:
    match = {
        "id": "m1",
        "teams": [
            {
                "team_label": "A",
                "players": [{"id": "p1", "name": "Pawel", "number": 92}],
            },
            {"team_label": "B", "players": []},
        ],
    }
    positions = [
        {
            "frame": frame,
            "time_sec": frame / 10,
            "status": "detected",
            "bbox_xyxy": [10, 10, 20, 30],
        }
        for frame in range(1, 7)
    ]
    _write(
        root / "tracklets.json",
        {"tracklets": [{"tracklet_id": "t1", "team_label": "A", "positions_m": positions}]},
    )
    _write(
        root / "identity_candidate_shadow.json",
        {"subjects": [{"candidate_subject_id": "s1", "tracklet_ids": ["t1"]}]},
    )
    _write(
        root / "global_identity.json",
        {
            "slots": [
                {
                    "stable_player_id": "A03",
                    "team_label": "A",
                    "tracklet_ids": ["t1"],
                    "positions_m": [
                        {"tracklet_id": "t1", "frame": frame, "status": "detected"}
                        for frame in (1, 2, 5)
                    ],
                },
                {
                    "stable_player_id": "A05",
                    "team_label": "A",
                    "tracklet_ids": ["t1"],
                    "positions_m": [
                        {"tracklet_id": "t1", "frame": frame, "status": "detected"}
                        for frame in (3, 4)
                    ],
                },
            ]
        },
    )
    _write(
        root / "identity_roster_subject_review_shadow.json",
        {
            "cards": [
                {
                    "candidate_subject_id": "s1",
                    "visual_evidence": {"anchor_crops": []},
                }
            ]
        },
    )
    _write(
        root / "identity_roster_subject_review_decisions_shadow.json",
        {
            "decisions": [
                {
                    "candidate_subject_id": "s1",
                    "decision": "assign_roster_player",
                    "player_id": "p1",
                }
            ]
        },
    )
    return match


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
