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
from app.services.identity_reviewed_corrections import (
    persist_reviewed_identity_correction,
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
            self.assertEqual(review["summary"]["targets_total"], 3)
            targets = review["targets"]
            a03_targets = [row for row in targets if row["stable_slot_id"] == "A03"]
            a05_target = next(row for row in targets if row["stable_slot_id"] == "A05")
            self.assertEqual(
                [row["frame_ranges"] for row in a03_targets],
                [[[1, 2]], [[5, 6]]],
            )
            self.assertEqual(a05_target["frame_ranges"], [[3, 4]])
            self.assertTrue(all(len(row["frame_ranges"]) == 1 for row in targets))
            self.assertEqual(len({row["review_target_id"] for row in a03_targets}), 2)
            self.assertNotEqual(
                a03_targets[0]["source_ownership_digest"],
                a03_targets[1]["source_ownership_digest"],
            )
            self.assertEqual(
                a03_targets[0]["legacy_suggestion"]["player_id"],
                "p1",
            )
            self.assertIsNone(a03_targets[1]["legacy_suggestion"])
            self.assertIsNone(a05_target["legacy_suggestion"])

    def test_segment_decisions_do_not_bleed_and_stale_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            review = build_segment_review_document(root, match)
            a03_first = next(
                row
                for row in review["targets"]
                if row["stable_slot_id"] == "A03" and row["frame_start"] == 1
            )
            a03_second = next(
                row
                for row in review["targets"]
                if row["stable_slot_id"] == "A03" and row["frame_start"] == 5
            )
            a05 = next(
                row for row in review["targets"] if row["stable_slot_id"] == "A05"
            )

            with self.assertRaisesRegex(SegmentTargetError, "review_target_stale"):
                save_segment_decision(
                    root,
                    match,
                    {
                        "review_target_id": a03_first["review_target_id"],
                        "source_ownership_digest": "stale",
                        "action": "assign_roster_player",
                        "player_id": "p1",
                    },
                )

            for target, action, extra in (
                (a03_first, "assign_roster_player", {"player_id": "p1"}),
                (a05, "assign_team", {"team_label": "B"}),
            ):
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
            refreshed_a03_second = next(
                row
                for row in refreshed["targets"]
                if row["stable_slot_id"] == "A03" and row["frame_start"] == 5
            )
            self.assertEqual(
                refreshed_a03_second["source_ownership_digest"],
                a03_second["source_ownership_digest"],
            )
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
            self.assertEqual(set(by_frame), {1, 2, 3, 4})
            self.assertEqual(by_frame[1]["canonical_player_id"], "p1")
            self.assertNotIn(5, by_frame)
            self.assertNotIn(6, by_frame)
            self.assertIsNone(by_frame[3]["canonical_player_id"])
            self.assertEqual(by_frame[3]["team_label"], "B")
            self.assertEqual(by_frame[3]["display_label"], "B?")
            self.assertNotEqual(
                a03_first["review_target_id"], a03_second["review_target_id"]
            )

    def test_deferred_segment_save_uses_materialized_target_without_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            review = build_segment_review_document(root, match)
            target = review["targets"][0]
            with patch(
                "app.services.identity_reviewed_segments.build_segment_review_document",
                side_effect=AssertionError("segment review must not rebuild per click"),
            ):
                result = persist_reviewed_identity_correction(
                    root,
                    match,
                    {
                        "candidate_subject_id": target["candidate_subject_id"],
                        "review_target_id": target["review_target_id"],
                        "source_ownership_digest": target["source_ownership_digest"],
                        "action": "unresolved",
                    },
                )
            self.assertTrue(result["recompute_deferred"])
            self.assertEqual(
                load_segment_decisions(root)["decisions"][0]["review_target_id"],
                target["review_target_id"],
            )

    def test_deferred_segment_save_rejects_stale_ownership_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            target = build_segment_review_document(root, match)["targets"][0]
            with self.assertRaisesRegex(SegmentTargetError, "review_target_stale"):
                persist_reviewed_identity_correction(
                    root,
                    match,
                    {
                        "candidate_subject_id": target["candidate_subject_id"],
                        "review_target_id": target["review_target_id"],
                        "source_ownership_digest": "stale",
                        "action": "unresolved",
                    },
                )
            self.assertEqual(load_segment_decisions(root)["decisions"], [])

    def test_pre_split_decision_is_preserved_as_orphan_and_never_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            build_segment_review_document(root, match)
            old_target_id = "review-segment:v1:pre-split-aggregate"
            _write(
                root / "reviewed_identity_segment_decisions.json",
                {
                    "schema_version": "1.0.0",
                    "mode": "reviewed_identity_segment_decisions",
                    "decisions": [
                        {
                            "review_target_id": old_target_id,
                            "candidate_subject_id": "s1",
                            "tracklet_ids": ["t1"],
                            "stable_slot_id": "A03",
                            "source_ownership_digest": "old-aggregate-digest",
                            "action": "assign_roster_player",
                            "player_id": "p1",
                            "team_label": "A",
                        }
                    ],
                },
            )

            refreshed = build_segment_review_document(root, match)
            stored = load_segment_decisions(root)
            assignments = segment_observation_assignments(
                refreshed,
                stored,
                {
                    "p1": {
                        "name": "Pawel",
                        "number": 92,
                        "team_label": "A",
                    }
                },
            )

            self.assertEqual(assignments, [])
            self.assertEqual(refreshed["summary"]["targets_reviewed"], 0)
            self.assertEqual(refreshed["summary"]["stale_decisions"], 1)
            self.assertEqual(
                refreshed["summary"]["orphaned_decisions_requiring_review"], 1
            )
            self.assertEqual(stored["decisions"][0]["review_target_id"], old_target_id)

    def test_progress_counts_targets_instead_of_unsafe_whole_subject_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            review = build_segment_review_document(root, match)
            progress = build_reviewed_identity_progress(root, match)
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 3)
            self.assertEqual(
                {row["review_target_id"] for row in progress["next_cases"]},
                {row["review_target_id"] for row in review["targets"]},
            )

            target = next(
                row
                for row in review["targets"]
                if row["stable_slot_id"] == "A03" and row["frame_start"] == 1
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
            self.assertEqual(after["summary"]["important_decisions_remaining"], 2)

    def test_pending_segment_without_crop_is_optional_but_saved_decision_stays_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root, include_bbox=False)
            review = build_segment_review_document(root, match)
            progress = build_reviewed_identity_progress(root, match)

            self.assertTrue(review["targets"])
            self.assertTrue(
                all(
                    not (target["visual_evidence"]["anchor_crops"])
                    for target in review["targets"]
                )
            )
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(progress["summary"]["optional_cases_remaining"], 3)
            self.assertEqual(progress["next_cases"], [])
            self.assertTrue(
                all(
                    unit["current_resolution_status"] == "pending_optional"
                    and "mixed_tracklet_segment_without_visual_evidence"
                    in unit["reason_codes"]
                    for unit in progress["review_units"]
                )
            )

            target = review["targets"][0]
            save_segment_decision(
                root,
                match,
                {
                    "review_target_id": target["review_target_id"],
                    "source_ownership_digest": target["source_ownership_digest"],
                    "action": "unresolved",
                },
            )
            build_segment_review_document(root, match)
            reviewed = build_reviewed_identity_progress(root, match)
            saved_unit = next(
                unit
                for unit in reviewed["review_units"]
                if unit["review_target_id"] == target["review_target_id"]
            )
            self.assertEqual(saved_unit["current_resolution_status"], "reviewed_by_operator")

    def test_pending_segment_with_one_crop_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root)
            build_segment_review_document(root, match)
            progress = build_reviewed_identity_progress(root, match)

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 3)
            self.assertEqual(len(progress["next_cases"]), 3)
            self.assertTrue(
                all(
                    unit["current_resolution_status"] == "pending_high_priority"
                    for unit in progress["review_units"]
                )
            )

    def test_only_conservative_whole_subject_actions_suppress_mixed_review(self) -> None:
        for action, expected_targets in (
            ("unresolved", 0),
            ("team_unknown", 0),
            ("assign_team", 3),
            ("referee", 3),
            ("false_detection", 3),
        ):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                match = _fixture(root)
                _write(
                    root / "reviewed_identity_slot_assignments.json",
                    {
                        "decisions": [
                            {
                                "candidate_subject_id": "s1",
                                "action": action,
                                "team_label": "B" if action == "assign_team" else None,
                            }
                        ]
                    },
                )

                review = build_segment_review_document(root, match)

                self.assertEqual(review["summary"]["targets_total"], expected_targets)


def _fixture(root: Path, *, include_bbox: bool = True) -> dict:
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
            **({"bbox_xyxy": [10, 10, 20, 30]} if include_bbox else {}),
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
                        for frame in (1, 2, 5, 6)
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
