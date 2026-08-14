from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_reviewed_action_gate import (
    DeferredReviewActionError,
    validate_deferred_review_action,
)
from app.services.identity_reviewed_progress import PROGRESS_SCHEMA_VERSION


class DeferredReviewedActionGateTests(unittest.TestCase):
    def test_coverage_priority_unit_from_v2_baseline_is_actionable(self) -> None:
        with _workspace() as root:
            coverage = {
                **_whole("coverage-subject"),
                "priority": "coverage",
                "current_resolution_status": "pending_coverage_review",
            }
            _baseline(root, [coverage])
            progress = json.loads(
                (root / "reviewed_identity_progress.json").read_text(encoding="utf-8")
            )
            progress["schema_version"] = PROGRESS_SCHEMA_VERSION
            _write(root / "reviewed_identity_progress.json", progress)

            result = validate_deferred_review_action(
                root,
                {"id": "m1"},
                {
                    "candidate_subject_id": "coverage-subject",
                    "action": "assign_team",
                    "team_label": "A",
                },
            )

            self.assertEqual(result["review_unit"]["priority"], "coverage")

    def test_high_priority_whole_subject_is_actionable(self) -> None:
        with _workspace() as root:
            _baseline(root, [_whole("s1")])
            result = validate_deferred_review_action(
                root,
                {"id": "m1"},
                {"candidate_subject_id": "s1", "action": "unresolved"},
            )
            self.assertFalse(result["idempotent_replay"])
            self.assertEqual(
                result["detected_team_labels_by_subject"],
                {"s1": set()},
            )

    def test_high_priority_segment_requires_exact_target_and_subject(self) -> None:
        with _workspace() as root:
            _baseline(root, [_segment("s1", "target-1")])
            result = validate_deferred_review_action(
                root,
                {"id": "m1"},
                {
                    "candidate_subject_id": "s1",
                    "review_target_id": "target-1",
                    "source_ownership_digest": "owner-1",
                    "action": "assign_team",
                    "team_label": "A",
                },
            )
            self.assertEqual(
                result["review_unit"]["review_target_id"],
                "target-1",
            )

            for payload in (
                {
                    "candidate_subject_id": "s1",
                    "review_target_id": "target-wrong",
                    "action": "unresolved",
                },
                {
                    "candidate_subject_id": "s2",
                    "review_target_id": "target-1",
                    "action": "unresolved",
                },
            ):
                with self.subTest(payload=payload), self.assertRaisesRegex(
                    DeferredReviewActionError,
                    "aktualnej kolejce",
                ):
                    validate_deferred_review_action(root, {"id": "m1"}, payload)

    def test_absent_and_optional_units_are_rejected(self) -> None:
        optional = {
            **_whole("optional"),
            "priority": "optional",
            "current_resolution_status": "pending_optional",
        }
        with _workspace() as root:
            _baseline(root, [_whole("s1"), optional])
            for subject_id in ("missing", "optional"):
                with self.subTest(subject_id=subject_id), self.assertRaises(
                    DeferredReviewActionError
                ) as raised:
                    validate_deferred_review_action(
                        root,
                        {"id": "m1"},
                        {"candidate_subject_id": subject_id, "action": "unresolved"},
                    )
                self.assertEqual(raised.exception.code, "review_unit_not_actionable")

    def test_missing_malformed_or_clearly_stale_queue_fails_closed(self) -> None:
        with _workspace() as root:
            for setup in ("missing", "malformed", "stale"):
                with self.subTest(setup=setup):
                    for path in root.iterdir():
                        path.unlink()
                    if setup == "malformed":
                        (root / "reviewed_identity_progress.json").write_text(
                            "[]",
                            encoding="utf-8",
                        )
                    elif setup == "stale":
                        _baseline(root, [_whole("s1")])
                        _write(
                            root / "reviewed_identity_report.json",
                            {"snapshot_digest": "different"},
                        )
                    with self.assertRaises(DeferredReviewActionError) as raised:
                        validate_deferred_review_action(
                            root,
                            {"id": "m1"},
                            {"candidate_subject_id": "s1", "action": "unresolved"},
                        )
                    self.assertEqual(raised.exception.code, "review_queue_stale")

    def test_whole_subject_without_exact_team_context_fails_closed(self) -> None:
        with _workspace() as root:
            _baseline(root, [_whole("s1")])
            progress = json.loads(
                (root / "reviewed_identity_progress.json").read_text(
                    encoding="utf-8"
                )
            )
            progress.pop("deferred_correction_context")
            _write(root / "reviewed_identity_progress.json", progress)

            with self.assertRaises(DeferredReviewActionError) as raised:
                validate_deferred_review_action(
                    root,
                    {"id": "m1"},
                    {"candidate_subject_id": "s1", "action": "unresolved"},
                )

            self.assertEqual(raised.exception.code, "review_queue_stale")

    def test_malformed_materialized_team_labels_fail_closed(self) -> None:
        for labels in (["B", "A"], ["A", "A"], ["U"]):
            with self.subTest(labels=labels), _workspace() as root:
                _baseline(root, [_whole("s1")])
                progress = json.loads(
                    (root / "reviewed_identity_progress.json").read_text(
                        encoding="utf-8"
                    )
                )
                progress["deferred_correction_context"]["subjects"][0][
                    "detected_team_labels"
                ] = labels
                _write(root / "reviewed_identity_progress.json", progress)

                with self.assertRaises(DeferredReviewActionError) as raised:
                    validate_deferred_review_action(
                        root,
                        {"id": "m1"},
                        {"candidate_subject_id": "s1", "action": "unresolved"},
                    )

                self.assertEqual(raised.exception.code, "review_queue_stale")

    def test_dirty_marker_does_not_block_later_units_from_same_batch(self) -> None:
        with _workspace() as root:
            _baseline(root, [_whole("s1"), _whole("s2"), _whole("s3")])
            validate_deferred_review_action(
                root,
                {"id": "m1"},
                {"candidate_subject_id": "s1", "action": "unresolved"},
            )
            _write(
                root / "reviewed_identity_slot_assignments.json",
                {
                    "decisions": [
                        {"candidate_subject_id": "s1", "action": "unresolved"}
                    ]
                },
            )
            _write(
                root / "reviewed_identity_recompute_required.json",
                {"status": "required"},
            )
            for subject_id in ("s2", "s3"):
                result = validate_deferred_review_action(
                    root,
                    {"id": "m1"},
                    {"candidate_subject_id": subject_id, "action": "unresolved"},
                )
                self.assertFalse(result["idempotent_replay"])

    def test_exact_replay_is_idempotent_but_conflicting_replay_is_rejected(self) -> None:
        with _workspace() as root:
            _baseline(root, [_whole("s1")])
            _write(
                root / "reviewed_identity_slot_assignments.json",
                {
                    "decisions": [
                        {
                            "candidate_subject_id": "s1",
                            "action": "assign_team",
                            "team_label": "A",
                            "comment": "ignored",
                        }
                    ]
                },
            )
            replay = validate_deferred_review_action(
                root,
                {"id": "m1"},
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_team",
                    "team_label": "A",
                },
            )
            self.assertTrue(replay["idempotent_replay"])
            with self.assertRaises(DeferredReviewActionError) as raised:
                validate_deferred_review_action(
                    root,
                    {"id": "m1"},
                    {"candidate_subject_id": "s1", "action": "unresolved"},
                )
            self.assertEqual(raised.exception.code, "review_unit_already_decided")


def _baseline(root: Path, cases: list[dict]) -> None:
    whole_subject_ids = sorted(
        {
            str(case["candidate_subject_id"])
            for case in cases
            if case.get("review_target_id") is None
        }
    )
    _write(
        root / "reviewed_identity_progress.json",
        {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "status": "ready",
            "match_id": "m1",
            "source_snapshot_digest": "snapshot-1",
            "next_cases": cases,
            "deferred_correction_context": {
                "schema_version": "1.0.0",
                "status": "unavailable",
                "detected_team_evidence_status": "ready",
                "subjects": [
                    {
                        "candidate_subject_id": subject_id,
                        "source_team_label": "U",
                        "detected_team_labels": [],
                        "detected_frames": [],
                    }
                    for subject_id in whole_subject_ids
                ],
            },
        },
    )
    _write(
        root / "reviewed_identity_report.json",
        {"snapshot_digest": "snapshot-1"},
    )


def _whole(subject_id: str) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "review_target_id": None,
        "scope_kind": None,
        "priority": "high",
        "current_resolution_status": "pending_high_priority",
    }


def _segment(subject_id: str, target_id: str) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "review_target_id": target_id,
        "scope_kind": "canonical_segment",
        "priority": "high",
        "current_resolution_status": "pending_high_priority",
        "source_ownership_digest": "owner-1",
    }


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class _workspace:
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()

    def __enter__(self) -> Path:
        return Path(self._temporary.__enter__())

    def __exit__(self, *args: object) -> None:
        self._temporary.__exit__(*args)
