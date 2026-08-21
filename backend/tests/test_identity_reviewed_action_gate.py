from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_action_gate import (
    DeferredReviewActionError,
    validate_deferred_review_action,
)
from app.services.identity_reviewed_action_scope import (
    ReviewedIdentityActionScopeError,
)
from app.services.identity_reviewed_progress import PROGRESS_SCHEMA_VERSION
from app.services.identity_review_scope import identity_review_scope_digest


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

    def test_material_continuity_group_is_authorized_without_synthetic_subject_context(self) -> None:
        with _workspace() as root:
            material = _material("continuity:A12:5737-7132")
            _baseline(root, [material])

            result = validate_deferred_review_action(
                root,
                {"id": "m1"},
                {
                    "candidate_subject_id": material["candidate_subject_id"],
                    "action": "assign_roster_player",
                    "player_id": "mateusz",
                },
            )

            self.assertEqual(result["review_unit"]["scope_kind"], "material_continuity")
            self.assertEqual(result["detected_team_labels_by_subject"], None)

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

    def test_optional_team_audit_whole_subject_actions_are_actionable(self) -> None:
        actions = (
            {"action": "assign_team", "team_label": "B"},
            {"action": "assign_roster_player", "player_id": "team-a-player"},
            {"action": "referee"},
            {"action": "false_detection"},
            {"action": "mixed_players", "mixed_hint": "unknown"},
            {"action": "unresolved"},
        )
        for action in actions:
            with self.subTest(action=action), _workspace() as root:
                match_doc = _scoped_match()
                _baseline(root, [], [_optional("optional-b")], match_doc)
                result = validate_deferred_review_action(
                    root,
                    match_doc,
                    {"candidate_subject_id": "optional-b", **action},
                )
                self.assertEqual(result["review_unit"]["priority"], "optional")
                self.assertEqual(
                    result["review_unit"]["current_resolution_status"],
                    "optional_team_audit",
                )

    def test_baseline_present_optional_save_does_not_rebuild_current_progress(self) -> None:
        with _workspace() as root:
            match_doc = _scoped_match()
            _baseline(root, [], [_optional("optional")], match_doc)
            with patch(
                "app.services.identity_reviewed_action_gate.build_reviewed_identity_progress",
                side_effect=AssertionError("unexpected dynamic rebuild"),
            ):
                result = validate_deferred_review_action(
                    root,
                    match_doc,
                    {"candidate_subject_id": "optional", "action": "unresolved"},
                )
            self.assertEqual(result["authorization_source"], "batch_baseline")

    def test_hot_state_authorizes_exact_whole_subject_without_progress_rebuild(self) -> None:
        with _workspace() as root:
            whole = {**_whole("hot-subject"), "source_ownership_digest": "owner-hot"}
            _baseline(root, [whole])
            baseline = json.loads(
                (root / "reviewed_identity_progress.json").read_text(encoding="utf-8")
            )
            hot_state = {
                "state_version": 7,
                "progress": baseline,
                "internal_review_units": [whole],
                "unit_lookup": {"hot-subject\u001f": 0},
            }
            with patch(
                "app.services.identity_reviewed_action_gate.load_existing_fresh_hot_state",
                return_value=hot_state,
            ), patch(
                "app.services.identity_reviewed_action_gate.build_reviewed_identity_progress",
                side_effect=AssertionError("hot save must not rebuild progress"),
            ):
                result = validate_deferred_review_action(
                    root,
                    {"id": "m1"},
                    {
                        "candidate_subject_id": "hot-subject",
                        "source_ownership_digest": "owner-hot",
                        "review_state_version": 7,
                        "action": "unresolved",
                    },
                )
            self.assertEqual(result["authorization_source"], "batch_baseline")
            self.assertTrue(result["review_unit"]["_hot_state_authorized"])

    def test_hot_state_rejects_stale_source_before_persistence(self) -> None:
        with _workspace() as root:
            whole = {**_whole("hot-subject"), "source_ownership_digest": "owner-hot"}
            _baseline(root, [whole])
            baseline = json.loads(
                (root / "reviewed_identity_progress.json").read_text(encoding="utf-8")
            )
            with patch(
                "app.services.identity_reviewed_action_gate.load_existing_fresh_hot_state",
                return_value={
                    "state_version": 7,
                    "progress": baseline,
                    "internal_review_units": [whole],
                    "unit_lookup": {"hot-subject\u001f": 0},
                },
            ), self.assertRaises(DeferredReviewActionError) as raised:
                validate_deferred_review_action(
                    root,
                    {"id": "m1"},
                    {
                        "candidate_subject_id": "hot-subject",
                        "source_ownership_digest": "wrong-owner",
                        "review_state_version": 7,
                        "action": "unresolved",
                    },
                )
            self.assertEqual(raised.exception.code, "review_target_stale")

    def test_versioned_browser_context_cannot_fall_back_after_structural_invalidation(self) -> None:
        with _workspace() as root:
            _baseline(root, [_whole("structural-parent")])
            with patch(
                "app.services.identity_reviewed_action_gate.load_existing_fresh_hot_state",
                return_value=None,
            ), self.assertRaises(DeferredReviewActionError) as raised:
                validate_deferred_review_action(
                    root,
                    {"id": "m1"},
                    {
                        "candidate_subject_id": "structural-parent",
                        "review_state_version": 4,
                        "action": "assign_roster_player",
                        "player_id": "team-a-player",
                    },
                )
            self.assertEqual(raised.exception.code, "review_state_stale")

    def test_deferred_team_attribution_unit_keeps_the_unified_action_vocabulary(self) -> None:
        allowed = (
            {"action": "assign_team", "team_label": "A"},
            {"action": "assign_team", "team_label": "B"},
            {"action": "referee"},
            {"action": "false_detection"},
            {"action": "team_unknown"},
            {"action": "unresolved"},
            {"action": "assign_roster_player", "player_id": "team-a-player"},
            {"action": "assign_existing_slot", "stable_slot_id": "A01"},
            {"action": "create_new_stable_player", "team_label": "A"},
            {"action": "mixed_players", "mixed_hint": "unknown"},
        )
        for payload in allowed:
            with self.subTest(allowed=payload), _workspace() as root:
                _baseline(root, [_team_attribution("team-u")])
                result = validate_deferred_review_action(
                    root,
                    {"id": "m1"},
                    {"candidate_subject_id": "team-u", **payload},
                )
                self.assertEqual(result["review_unit"]["visual_evidence"]["kind"], "team_attribution")
    def test_deferred_team_attribution_assign_team_requires_canonical_a_or_b(self) -> None:
        with _workspace() as root:
            _baseline(root, [_team_attribution("team-u")])
            with self.assertRaises(ReviewedIdentityActionScopeError) as raised:
                validate_deferred_review_action(
                    root,
                    {"id": "m1"},
                    {
                        "candidate_subject_id": "team-u",
                        "action": "assign_team",
                        "team_label": "Corgi",
                    },
                )
            self.assertEqual(raised.exception.code, "reviewed_identity_team_label_invalid")

    def test_absent_and_malformed_optional_units_are_rejected(self) -> None:
        malformed = (
            {**_optional("wrong-priority"), "priority": "coverage"},
            {
                **_optional("wrong-status"),
                "current_resolution_status": "pending_optional",
            },
            {**_optional("not-actionable"), "operator_actionable": False},
        )
        with _workspace() as root:
            match_doc = _scoped_match()
            _baseline(root, [_whole("s1")], list(malformed), match_doc)
            for subject_id in (
                "missing",
                "wrong-priority",
                "wrong-status",
                "not-actionable",
            ):
                with self.subTest(subject_id=subject_id), self.assertRaises(
                    DeferredReviewActionError
                ) as raised:
                    validate_deferred_review_action(
                        root,
                        match_doc,
                        {"candidate_subject_id": subject_id, "action": "unresolved"},
                    )
                self.assertEqual(raised.exception.code, "review_unit_not_actionable")

    def test_scope_change_makes_optional_batch_stale(self) -> None:
        with _workspace() as root:
            complete = _scoped_match(b_scope="complete_roster")
            current = _scoped_match(b_scope="team_stats_only")
            _baseline(root, [], [_optional("optional-b")], complete)

            with self.assertRaises(DeferredReviewActionError) as raised:
                validate_deferred_review_action(
                    root,
                    current,
                    {"candidate_subject_id": "optional-b", "action": "unresolved"},
                )

            self.assertEqual(raised.exception.code, "review_queue_stale")

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

    def test_dirty_marker_does_not_block_later_optional_unit_from_same_batch(self) -> None:
        with _workspace() as root:
            match_doc = _scoped_match()
            _baseline(
                root,
                [],
                [_optional("optional-1"), _optional("optional-2")],
                match_doc,
            )
            validate_deferred_review_action(
                root,
                match_doc,
                {
                    "candidate_subject_id": "optional-1",
                    "action": "assign_team",
                    "team_label": "B",
                },
            )
            _write(
                root / "reviewed_identity_slot_assignments.json",
                {
                    "decisions": [
                        {
                            "candidate_subject_id": "optional-1",
                            "action": "assign_team",
                            "team_label": "B",
                        }
                    ]
                },
            )
            _write(
                root / "reviewed_identity_recompute_required.json",
                {"status": "required"},
            )

            result = validate_deferred_review_action(
                root,
                match_doc,
                {
                    "candidate_subject_id": "optional-2",
                    "action": "assign_team",
                    "team_label": "B",
                },
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

    def test_dynamic_optional_rerank_authorizes_exact_new_case_and_replays_safely(self) -> None:
        """A deferred broad disposition may expose a narrower MAX unit."""
        with _workspace() as root:
            match_doc = _scoped_match()
            broad = _optional("broad")
            narrow = _optional("narrow")
            narrow["current_resolution_status"] = "pending_optional_max_audit"
            _baseline(root, [], [broad], match_doc)
            _write(
                root / "reviewed_identity_slot_assignments.json",
                {"decisions": [{"candidate_subject_id": "broad", "action": "unresolved"}]},
            )
            _write(root / "reviewed_identity_recompute_required.json", {"status": "required"})
            fresh = _fresh_dynamic_progress(match_doc, [narrow])

            with patch(
                "app.services.identity_reviewed_action_gate.build_reviewed_identity_progress",
                return_value=fresh,
            ):
                first = validate_deferred_review_action(
                    root,
                    match_doc,
                    {
                        "candidate_subject_id": "narrow",
                        "action": "assign_roster_player",
                        "player_id": "team-a-player",
                    },
                )

            self.assertEqual(first["authorization_source"], "dynamic_optional")
            self.assertFalse(first["idempotent_replay"])

            _write(
                root / "reviewed_identity_slot_assignments.json",
                {
                    "decisions": [
                        {"candidate_subject_id": "broad", "action": "unresolved"},
                        {
                            "candidate_subject_id": "narrow",
                            "action": "assign_roster_player",
                            "player_id": "team-a-player",
                        },
                    ]
                },
            )
            completed = _fresh_dynamic_progress(match_doc, [])
            completed["_internal_review_units"] = [narrow]
            completed["deferred_correction_context"]["subjects"] = [
                {
                    "candidate_subject_id": "narrow",
                    "source_team_label": "A",
                    "detected_team_labels": ["A"],
                    "detected_frames": [],
                }
            ]
            with patch(
                "app.services.identity_reviewed_action_gate.build_reviewed_identity_progress",
                return_value=completed,
            ):
                replay = validate_deferred_review_action(
                    root,
                    match_doc,
                    {
                        "candidate_subject_id": "narrow",
                        "action": "assign_roster_player",
                        "player_id": "team-a-player",
                    },
                )
                self.assertTrue(replay["idempotent_replay"])
                with self.assertRaises(DeferredReviewActionError) as raised:
                    validate_deferred_review_action(
                        root,
                        match_doc,
                        {"candidate_subject_id": "narrow", "action": "unresolved"},
                    )
            self.assertEqual(raised.exception.code, "review_unit_already_decided")

    def test_dynamic_optional_replay_rejects_stale_segment_ownership(self) -> None:
        with _workspace() as root:
            match_doc = _scoped_match()
            _baseline(root, [], [_optional("broad")], match_doc)
            _write(root / "reviewed_identity_recompute_required.json", {"status": "required"})
            target = _segment("narrow", "target-1")
            target.update({
                "priority": "optional",
                "current_resolution_status": "pending_optional_max_audit",
            })
            _write(
                root / "reviewed_identity_segment_decisions.json",
                {
                    "decisions": [
                        {
                            "review_target_id": "target-1",
                            "action": "assign_roster_player",
                            "player_id": "team-a-player",
                            "source_ownership_digest": "stale-owner",
                        }
                    ]
                },
            )
            fresh = _fresh_dynamic_progress(match_doc, [])
            fresh["_internal_review_units"] = [target]
            with patch(
                "app.services.identity_reviewed_action_gate.build_reviewed_identity_progress",
                return_value=fresh,
            ), self.assertRaises(DeferredReviewActionError) as raised:
                validate_deferred_review_action(
                    root,
                    match_doc,
                    {
                        "candidate_subject_id": "narrow",
                        "review_target_id": "target-1",
                        "source_ownership_digest": "owner-1",
                        "action": "assign_roster_player",
                        "player_id": "team-a-player",
                    },
                )
            self.assertEqual(raised.exception.code, "review_target_stale")

    def test_dynamic_optional_replay_rejects_reverse_conflicting_decision(self) -> None:
        """A dynamically exposed MAX unit cannot be changed after "Nie wiem"."""
        with _workspace() as root:
            match_doc = _scoped_match()
            broad = _optional("broad")
            narrow = _optional("narrow")
            narrow["current_resolution_status"] = "pending_optional_max_audit"
            _baseline(root, [], [broad], match_doc)
            _write(
                root / "reviewed_identity_slot_assignments.json",
                {"decisions": [{"candidate_subject_id": "broad", "action": "unresolved"}]},
            )
            _write(root / "reviewed_identity_recompute_required.json", {"status": "required"})
            fresh = _fresh_dynamic_progress(match_doc, [narrow])

            with patch(
                "app.services.identity_reviewed_action_gate.build_reviewed_identity_progress",
                return_value=fresh,
            ):
                first = validate_deferred_review_action(
                    root,
                    match_doc,
                    {"candidate_subject_id": "narrow", "action": "unresolved"},
                )
            self.assertEqual(first["authorization_source"], "dynamic_optional")

            _write(
                root / "reviewed_identity_slot_assignments.json",
                {
                    "decisions": [
                        {"candidate_subject_id": "broad", "action": "unresolved"},
                        {"candidate_subject_id": "narrow", "action": "unresolved"},
                    ]
                },
            )
            completed = _fresh_dynamic_progress(match_doc, [])
            completed["_internal_review_units"] = [narrow]
            completed["deferred_correction_context"]["subjects"] = [
                {
                    "candidate_subject_id": "narrow",
                    "source_team_label": "A",
                    "detected_team_labels": ["A"],
                    "detected_frames": [],
                }
            ]
            with patch(
                "app.services.identity_reviewed_action_gate.build_reviewed_identity_progress",
                return_value=completed,
            ), self.assertRaises(DeferredReviewActionError) as raised:
                validate_deferred_review_action(
                    root,
                    match_doc,
                    {
                        "candidate_subject_id": "narrow",
                        "action": "assign_roster_player",
                        "player_id": "team-a-player",
                    },
                )
            self.assertEqual(raised.exception.code, "review_unit_already_decided")


def _baseline(
    root: Path,
    cases: list[dict],
    optional_cases: list[dict] | None = None,
    match_doc: dict | None = None,
) -> None:
    optional_cases = optional_cases or []
    all_cases = [*cases, *optional_cases]
    whole_subject_ids = sorted(
        {
            str(case["candidate_subject_id"])
            for case in all_cases
            if case.get("review_target_id") is None
        }
    )
    progress = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "status": "ready",
        "match_id": "m1",
        "source_snapshot_digest": "snapshot-1",
        "next_cases": cases,
        "optional_audit_cases": optional_cases,
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
    }
    if match_doc is not None:
        progress["source_review_scope_digest"] = identity_review_scope_digest(match_doc)
    _write(
        root / "reviewed_identity_progress.json",
        progress,
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


def _team_attribution(subject_id: str) -> dict:
    return {
        **_whole(subject_id),
        "visual_evidence": {"kind": "team_attribution", "anchor_crops": [{}, {}, {}]},
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


def _optional(subject_id: str) -> dict:
    return {
        **_whole(subject_id),
        "priority": "optional",
        "current_resolution_status": "optional_team_audit",
        "operator_actionable": True,
    }


def _fresh_dynamic_progress(match_doc: dict, optional_cases: list[dict]) -> dict:
    subjects = sorted(
        {
            str(case["candidate_subject_id"])
            for case in optional_cases
            if case.get("review_target_id") is None
        }
    )
    return {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "status": "ready",
        "match_id": "m1",
        "source_snapshot_digest": "snapshot-1",
        "source_review_scope_digest": identity_review_scope_digest(match_doc),
        "optional_audit_cases": optional_cases,
        "_internal_review_units": list(optional_cases),
        "deferred_correction_context": {
            "schema_version": "1.0.0",
            "status": "unavailable",
            "detected_team_evidence_status": "ready",
            "subjects": [
                {
                    "candidate_subject_id": subject_id,
                    "source_team_label": "A",
                    "detected_team_labels": ["A"],
                    "detected_frames": [],
                }
                for subject_id in subjects
            ],
        },
    }


def _material(subject_id: str) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "review_target_id": None,
        "scope_kind": "material_continuity",
        "priority": "continuity",
        "current_resolution_status": "pending_material_continuity_review",
        "operator_actionable": True,
        "effective_team_label": "A",
        "continuity_subject_ids": ["fragment-1", "fragment-2", "fragment-3", "fragment-4"],
        "visual_evidence": {"kind": "identity_continuity", "anchor_crops": [{}, {}]},
    }


def _scoped_match(b_scope: str = "team_stats_only") -> dict:
    return {
        "id": "m1",
        "identity_review_scope": {
            "teams": {"A": "complete_roster", "B": b_scope},
        },
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
