from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.identity_reviewed_segment_coalescing import (
    coalesced_conflict_episodes,
    exact_frame_ranges,
    max_segment_review_gap_frames,
)
from app.services.identity_reviewed_segments import (
    _review_fps,
    build_segment_review_document,
    load_segment_decisions,
    save_segment_decision,
    segment_observation_assignments,
)
from app.services.review_workflow_state import _issue_evidence


class ReviewedIdentitySegmentCoalescingTests(unittest.TestCase):
    def test_match_metadata_fps_is_fixture_fallback_and_controls_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root, a03_frames=[100, 106], fps=20.0)

            review = build_segment_review_document(root, match)

            self.assertEqual(_review_fps(root, match), 20.0)
            self.assertEqual(review["target_policy"]["max_unowned_gap_frames"], 5)
            self.assertEqual(
                len([row for row in review["targets"] if row["stable_slot_id"] == "A03"]),
                1,
            )

    def test_real_video_fps_wins_over_stale_match_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_video(root / "video.avi", fps=40.0)
            match = {"video": {"fps": 30.0}}

            fps = _review_fps(root, match)

            self.assertAlmostEqual(fps, 40.0, places=1)
            self.assertEqual(max_segment_review_gap_frames(fps), 10)

    def test_same_frame_gap_uses_time_semantics_at_different_fps(self) -> None:
        group = ("subject", "tracklet", "A03", "A")
        claims = _claims("tracklet", "A03", "A", [100, 108])

        lower_fps = coalesced_conflict_episodes(
            {group: claims},
            claims,
            fps=20.0,
        )
        higher_fps = coalesced_conflict_episodes(
            {group: claims},
            claims,
            fps=40.0,
        )

        self.assertEqual(lower_fps[group], [[100], [108]])
        self.assertEqual(higher_fps[group], [[100, 108]])

    def test_invalid_container_fps_uses_valid_match_fallback_then_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "app.services.identity_reviewed_segments.read_match_video_metadata",
                return_value={"fps": 0},
            ):
                self.assertEqual(
                    _review_fps(root, {"video": {"fps": 0}, "fps": 24}),
                    24.0,
                )
                self.assertEqual(
                    _review_fps(root, {"video": {"fps": "invalid"}, "fps": 0}),
                    30.0,
                )

    def test_time_policy_thresholds_are_fps_aware(self) -> None:
        self.assertEqual(max_segment_review_gap_frames(20.0), 5)
        self.assertEqual(max_segment_review_gap_frames(30.0), 7)
        self.assertEqual(max_segment_review_gap_frames(40.0), 10)
        self.assertEqual(max_segment_review_gap_frames(60.0), 15)

    def test_same_conflict_coalesces_across_gap_within_policy(self) -> None:
        group = ("subject", "tracklet", "A03", "A")
        episodes = coalesced_conflict_episodes(
            {group: _claims("tracklet", "A03", "A", [100, 101, 102, 104, 105])},
            _claims("tracklet", "A03", "A", [100, 101, 102, 104, 105]),
            fps=10.0,
        )

        self.assertEqual(episodes[group], [[100, 101, 102, 104, 105]])
        self.assertEqual(
            exact_frame_ranges(episodes[group][0]),
            [[100, 102], [104, 105]],
        )

    def test_gap_at_threshold_coalesces_and_larger_gap_does_not(self) -> None:
        group = ("subject", "tracklet", "A03", "A")
        threshold = max_segment_review_gap_frames(10.0)
        self.assertEqual(threshold, 2)
        at_threshold = [100, 100 + threshold + 1]
        over_threshold = [100, 100 + threshold + 2]

        coalesced = coalesced_conflict_episodes(
            {group: _claims("tracklet", "A03", "A", at_threshold)},
            _claims("tracklet", "A03", "A", at_threshold),
            fps=10.0,
        )
        separate = coalesced_conflict_episodes(
            {group: _claims("tracklet", "A03", "A", over_threshold)},
            _claims("tracklet", "A03", "A", over_threshold),
            fps=10.0,
        )

        self.assertEqual(coalesced[group], [at_threshold])
        self.assertEqual(separate[group], [[100], [104]])

    def test_real_owner_transition_prevents_coalescing(self) -> None:
        a03 = ("subject", "tracklet", "A03", "A")
        a05 = ("subject", "tracklet", "A05", "A")
        ownership = [
            *_claims("tracklet", "A03", "A", [100, 102]),
            *_claims("tracklet", "A05", "A", [101]),
        ]

        episodes = coalesced_conflict_episodes(
            {
                a03: _claims("tracklet", "A03", "A", [100, 102]),
                a05: _claims("tracklet", "A05", "A", [101]),
            },
            ownership,
            fps=10.0,
        )

        self.assertEqual(episodes[a03], [[100], [102]])
        self.assertEqual(episodes[a05], [[101]])

    def test_semantic_group_boundaries_are_never_combined(self) -> None:
        groups = {
            ("subject-1", "tracklet-1", "A03", "A"): _claims(
                "tracklet-1", "A03", "A", [100]
            ),
            ("subject-2", "tracklet-1", "A03", "A"): _claims(
                "tracklet-1", "A03", "A", [102]
            ),
            ("subject-1", "tracklet-2", "A03", "A"): _claims(
                "tracklet-2", "A03", "A", [102]
            ),
            ("subject-1", "tracklet-1", "B03", "B"): _claims(
                "tracklet-1", "B03", "B", [103]
            ),
        }
        ownership = [claim for claims in groups.values() for claim in claims]

        episodes = coalesced_conflict_episodes(groups, ownership, fps=10.0)

        self.assertEqual(set(episodes), set(groups))
        self.assertTrue(all(len(value) == 1 for value in episodes.values()))
        self.assertTrue(all(len(value[0]) == 1 for value in episodes.values()))

    def test_builder_preserves_sparse_ownership_and_gap_frame_decision_safety(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root, a03_frames=[100, 101, 102, 104, 105])
            review = build_segment_review_document(root, match)
            target = _target(review, "A03")

            self.assertEqual(target["frame_start"], 100)
            self.assertEqual(target["frame_end"], 105)
            self.assertEqual(target["owned_frames"], [100, 101, 102, 104, 105])
            self.assertEqual(target["frame_ranges"], [[100, 102], [104, 105]])
            self.assertTrue(target["review_target_id"].startswith("review-segment:v2:"))
            self.assertTrue(target["visual_evidence"]["anchor_crops"])
            evidence_frames = [
                crop["frame"]
                for crop in target["visual_evidence"]["anchor_crops"]
            ]
            self.assertEqual(evidence_frames[0], target["owned_frames"][0])
            self.assertEqual(evidence_frames[-1], target["owned_frames"][-1])
            self.assertTrue(
                all(
                    crop["frame"] in target["owned_frames"]
                    for crop in target["visual_evidence"]["anchor_crops"]
                )
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
            refreshed = build_segment_review_document(root, match)
            assignments = segment_observation_assignments(
                refreshed,
                load_segment_decisions(root),
                {"p1": {"name": "Pawel", "number": 92, "team_label": "A"}},
            )

            self.assertEqual(
                {row["frame"] for row in assignments if row["tracklet_id"] == "t1"},
                {100, 101, 102, 104, 105},
            )
            self.assertNotIn(103, {row["frame"] for row in assignments})

    def test_target_identity_is_deterministic_and_tied_to_exact_sparse_set(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first_root = Path(first_dir)
            second_root = Path(second_dir)
            first_match = _fixture(first_root, a03_frames=[100, 101, 104])
            second_match = _fixture(second_root, a03_frames=[100, 102, 104])

            first = _target(build_segment_review_document(first_root, first_match), "A03")
            repeated = _target(build_segment_review_document(first_root, first_match), "A03")
            second = _target(build_segment_review_document(second_root, second_match), "A03")

            self.assertEqual(first["review_target_id"], repeated["review_target_id"])
            self.assertEqual(
                first["source_ownership_digest"],
                repeated["source_ownership_digest"],
            )
            self.assertEqual((first["frame_start"], first["frame_end"]), (100, 104))
            self.assertEqual((second["frame_start"], second["frame_end"]), (100, 104))
            self.assertNotEqual(first["review_target_id"], second["review_target_id"])
            self.assertNotEqual(
                first["source_ownership_digest"],
                second["source_ownership_digest"],
            )

    def test_old_v1_decision_is_orphaned_and_never_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root, a03_frames=[100, 101, 103])
            target = _target(build_segment_review_document(root, match), "A03")
            _write(
                root / "reviewed_identity_segment_decisions.json",
                {
                    "schema_version": "1.0.0",
                    "decisions": [
                        {
                            "review_target_id": "review-segment:v1:old-contiguous-target",
                            "candidate_subject_id": "s1",
                            "tracklet_ids": ["t1"],
                            "stable_slot_id": "A03",
                            "source_ownership_digest": target[
                                "source_ownership_digest"
                            ],
                            "action": "assign_roster_player",
                            "player_id": "p1",
                            "team_label": "A",
                        }
                    ],
                },
            )

            refreshed = build_segment_review_document(root, match)
            current = _target(refreshed, "A03")
            assignments = segment_observation_assignments(
                refreshed,
                load_segment_decisions(root),
                {"p1": {"name": "Pawel", "team_label": "A"}},
            )

            self.assertEqual(current["decision_status"], "pending")
            self.assertEqual(assignments, [])
            self.assertEqual(
                refreshed["summary"]["orphaned_decisions_requiring_review"],
                1,
            )

    def test_micro_run_regression_becomes_one_meaningful_episode(self) -> None:
        frames = [
            *range(100, 131),
            132,
            *range(135, 138),
            140,
            *range(143, 146),
            148,
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root, a03_frames=frames, fps=30.0)

            review = build_segment_review_document(root, match)
            a03_targets = [
                row for row in review["targets"] if row["stable_slot_id"] == "A03"
            ]

            self.assertEqual(len(a03_targets), 1)
            self.assertEqual(
                a03_targets[0]["frame_ranges"],
                [[100, 130], [132, 132], [135, 137], [140, 140], [143, 145], [148, 148]],
            )
            self.assertEqual(a03_targets[0]["owned_frames"], frames)

    def test_progress_and_workflow_blockers_use_coalesced_target_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = _fixture(root, a03_frames=[100, 101, 103, 104])
            review = build_segment_review_document(root, match)
            progress = build_reviewed_identity_progress(root, match)
            pending_targets = sum(
                row["decision_status"] == "pending" for row in review["targets"]
            )
            issues = _issue_evidence(
                {"summary": {"conflicted": 0, "blocked": 0}},
                progress,
            )

            self.assertEqual(
                progress["summary"]["important_decisions_remaining"],
                pending_targets,
            )
            self.assertEqual(issues["blocking"], pending_targets)


def _fixture(
    root: Path,
    *,
    a03_frames: list[int],
    fps: float = 10.0,
) -> dict:
    match = {
        "id": "m1",
        "video": {"fps": fps},
        "teams": [
            {
                "team_label": "A",
                "players": [{"id": "p1", "name": "Pawel", "number": 92}],
            },
            {"team_label": "B", "players": []},
        ],
    }
    all_frames = sorted({*a03_frames, 300})
    _write(
        root / "tracklets.json",
        {
            "tracklets": [
                {
                    "tracklet_id": "t1",
                    "team_label": "A",
                    "positions_m": [
                        {
                            "frame": frame,
                            "time_sec": frame / fps,
                            "status": "detected",
                            "bbox_xyxy": [10, 10, 20, 30],
                        }
                        for frame in all_frames
                    ],
                }
            ]
        },
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
                        for frame in a03_frames
                    ],
                },
                {
                    "stable_player_id": "A05",
                    "team_label": "A",
                    "tracklet_ids": ["t1"],
                    "positions_m": [
                        {"tracklet_id": "t1", "frame": 300, "status": "detected"}
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
                    "requires_operator_review": True,
                    "reason_codes": ["parallel_roster_candidate_conflict"],
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


def _claims(
    tracklet_id: str,
    slot_id: str,
    team_label: str,
    frames: list[int],
) -> list[dict]:
    return [
        {
            "tracklet_id": tracklet_id,
            "frame": frame,
            "stable_slot_id": slot_id,
            "team_label": team_label,
        }
        for frame in frames
    ]


def _target(review: dict, slot_id: str) -> dict:
    return next(
        row for row in review["targets"] if row["stable_slot_id"] == slot_id
    )


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_video(path: Path, *, fps: float) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        (32, 24),
    )
    if not writer.isOpened():
        raise RuntimeError("Could not create test video")
    frame = np.zeros((24, 32, 3), dtype=np.uint8)
    for _ in range(3):
        writer.write(frame)
    writer.release()


if __name__ == "__main__":
    unittest.main()
