from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_corrections import (
    persist_reviewed_identity_correction,
    reviewed_correction_context,
    save_reviewed_identity_correction,
)
from app.services.identity_reviewed_action_gate import validate_deferred_review_action
from app.services.identity_reviewed_action_scope import (
    ReviewedIdentityActionScopeError,
)
from app.services.identity_reviewed_snapshot import (
    finalize_reviewed_identity,
    get_reviewed_identity_status,
    reviewed_assignment_at,
)
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_reviewed_correction_context import (
    load_required as load_reviewed_required,
    reviewed_decisions_semantic_digest,
)
from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.review_workflow_state import get_review_workflow_state


class ReviewedIdentityCorrectionTests(unittest.TestCase):
    def test_structural_and_action_validation_are_order_independent(self) -> None:
        with _workspace() as root:
            _fixture(root)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "Unknown candidate_subject_id"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "missing", "action": "unresolved"},
                )
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", "action": "guess"},
                )
            candidates = _load(root / "identity_candidate_shadow.json")
            candidates["subjects"].append(
                {"candidate_subject_id": "another", "tracklet_ids": ["t1"]}
            )
            _write(root / "identity_candidate_shadow.json", candidates)
            with self.assertRaisesRegex(ValueError, "Ambiguous candidate subject"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", "action": "unresolved"},
                )
            self.assertEqual(_decision_files(root), before)

        with _workspace() as root:
            _fixture(root)
            candidates = _load(root / "identity_candidate_shadow.json")
            candidates["subjects"].append(
                {"candidate_subject_id": "s1", "tracklet_ids": ["t3"]}
            )
            _write(root / "identity_candidate_shadow.json", candidates)
            tracklets = _load(root / "tracklets.json")
            tracklets["tracklets"].append(
                {
                    "tracklet_id": "t3",
                    "team_label": "B",
                    "positions_m": [{"frame": 30, "status": "detected"}],
                }
            )
            _write(root / "tracklets.json", tracklets)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "Mixed-team"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", "action": "unresolved"},
                )
            self.assertEqual(_decision_files(root), before)

    def test_roster_assignment_is_whole_subject_and_comment_is_nonsemantic(self) -> None:
        with _workspace() as root:
            _fixture(root)
            baseline = finalize_reviewed_identity(root, _match())
            first = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p1",
                },
            )
            self.assertTrue(first["snapshot"]["stale"])
            self.assertEqual(get_reviewed_identity_status(root)["status"], "stale")
            same = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p1",
                    "comment": "non-semantic note",
                },
            )
            self.assertEqual(
                first["semantic_decision_digest"],
                same["semantic_decision_digest"],
            )
            result = finalize_reviewed_identity(root, _match())
            self.assertNotEqual(baseline["semantic_digest"], result["semantic_digest"])
            rows = [
                row
                for row in result["tracklet_assignments"]
                if row["candidate_subject_id"] == "s1"
            ]
            self.assertEqual({row["identity_status"] for row in rows}, {"confirmed"})
            self.assertEqual({row["canonical_player_id"] for row in rows}, {"p1"})
            self.assertEqual(result["summary"]["confirmed_detected_observations"], 4)
            with patch(
                "app.services.identity_reviewed_stats.read_match_video_metadata",
                return_value={
                    "fps": 10.0,
                    "frame_count": 100,
                    "duration_sec": 10.0,
                    "source": "fixture",
                    "filename": "fixture.mp4",
                },
            ):
                stats = build_reviewed_stats(root, result, _match())
            player = stats["reviewed_player_stats.json"]["players"][0]
            self.assertEqual(player["confirmed_detected_observations"], 4)
            self.assertGreater(player["observed_distance_m"], 0)
            self.assertEqual(player["heatmap_samples"], 4)

    def test_material_continuity_assignment_is_limited_to_exact_member_subjects(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_material_continuity_members(root)
            unit = {
                "candidate_subject_id": "continuity:A12:100-139",
                "continuity_group_id": "continuity:A12:100-139",
                "continuity_subject_ids": ["mc1", "mc2", "mc3", "mc4"],
                "scope_kind": "material_continuity",
                "effective_team_label": "A",
                "priority": "continuity",
                "current_resolution_status": "pending_material_continuity_review",
            }

            saved = persist_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": unit["candidate_subject_id"],
                    "action": "assign_roster_player",
                    "player_id": "p1",
                },
                authorized_review_unit=unit,
            )
            snapshot = finalize_reviewed_identity(root, _match())
            decisions = _load(root / "reviewed_identity_slot_assignments.json")["decisions"]

            self.assertEqual(saved["saved_decision"]["continuity_subject_ids"], unit["continuity_subject_ids"])
            member_decisions = [
                row for row in decisions if row["candidate_subject_id"] in set(unit["continuity_subject_ids"])
            ]
            self.assertEqual(len(member_decisions), 4)
            self.assertEqual({row["player_id"] for row in member_decisions}, {"p1"})
            self.assertEqual({row["stable_slot_id"] for row in member_decisions}, {None})
            assigned_subjects = {
                row["candidate_subject_id"]
                for row in snapshot["tracklet_assignments"]
                if row.get("canonical_player_id") == "p1"
            }
            self.assertEqual(assigned_subjects, set(unit["continuity_subject_ids"]))
            self.assertNotIn("s1", assigned_subjects)

    def test_roster_assignment_persists_safe_canonical_slot_binding(self) -> None:
        with _workspace() as root:
            _fixture(root)
            global_identity = _load(root / "global_identity.json")
            next(
                row
                for row in global_identity["slots"]
                if row["stable_player_id"] == "A03"
            )["tracklet_ids"] = ["t1", "t1b"]
            _write(root / "global_identity.json", global_identity)
            save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p1",
                },
            )
            decision = next(
                row
                for row in _load(
                    root / "reviewed_identity_slot_assignments.json"
                )["decisions"]
                if row["candidate_subject_id"] == "s1"
            )
            self.assertEqual(decision["stable_slot_id"], "A03")

    def test_named_roster_assignment_corrects_wrong_detected_team(self) -> None:
        with _workspace() as root:
            _fixture(root)
            immutable_before = _immutable_identity_files(root)
            saved = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p2",
                },
            )
            decision = saved["saved_decision"]
            self.assertEqual(decision["team_label"], "B")
            self.assertEqual(decision["source_team_label"], "A")
            self.assertTrue(decision["team_correction"])
            self.assertIsNone(decision["stable_slot_id"])
            self.assertEqual(_immutable_identity_files(root), immutable_before)

            result = finalize_reviewed_identity(root, _match())
            rows = [
                row
                for row in result["tracklet_assignments"]
                if row["candidate_subject_id"] == "s1"
            ]
            self.assertEqual({row["team_label"] for row in rows}, {"B"})
            self.assertEqual({row["canonical_player_id"] for row in rows}, {"p2"})
            self.assertEqual({row["identity_status"] for row in rows}, {"confirmed"})
            self.assertEqual(
                result["frame_uniqueness_diagnostics"][
                    "duplicate_canonical_player_claim_groups"
                ],
                0,
            )

    def test_unknown_team_assignment_is_reviewed_only_and_deterministic(self) -> None:
        with _workspace() as root:
            _fixture(root)
            baseline = finalize_reviewed_identity(root, _match())
            immutable_before = _immutable_identity_files(root)
            first = save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "assign_team", "team_label": "B"},
            )
            same = save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "assign_team", "team_label": "B", "comment": "non-semantic"},
            )
            self.assertTrue(first["snapshot"]["stale"])
            self.assertEqual(first["semantic_decision_digest"], same["semantic_decision_digest"])
            self.assertEqual(first["review_progress"]["summary"]["completed_by_operator"], 1)
            self.assertEqual(first["decision_impact"]["affected_tracklets"], 1)
            self.assertEqual(first["decision_impact"]["affected_detected_observations"], 2)
            self.assertEqual(same["decision_impact"]["operator_reviewed_observations_delta"], 0)
            self.assertEqual(get_reviewed_identity_status(root)["status"], "stale")
            saved = first["saved_decision"]
            self.assertEqual(saved["action"], "assign_team")
            self.assertEqual(saved["team_label"], "B")
            self.assertIsNone(saved["stable_slot_id"])
            self.assertEqual(_immutable_identity_files(root), immutable_before)
            result = finalize_reviewed_identity(root, _match())
            row = _subject_row(result, "su")
            self.assertEqual(row["team_label"], "B")
            self.assertEqual(row["fallback_label"], "B?")
            self.assertIsNone(row["stable_anonymous_slot_id"])
            self.assertIsNone(row["canonical_player_id"])
            self.assertEqual(row["identity_status"], "unresolved")
            self.assertEqual(row["identity_source"], "operator_team_assignment")
            self.assertFalse(row["eligible_for_player_stats"])
            slot_document = _load(root / "reviewed_identity_slot_assignments.json")
            self.assertEqual(slot_document["reviewed_slots"], [])
            self.assertNotIn("U01", str(slot_document))
            self.assertEqual(
                baseline["fragmentation_diagnostics"]["reviewed_slot_registry_entries"],
                result["fragmentation_diagnostics"]["reviewed_slot_registry_entries"],
            )
            self.assertEqual(result["fragmentation_diagnostics"]["automatic_permanent_allocations"], 0)

    def test_unknown_subject_can_use_team_b_slot_or_roster_without_cross_team_escape(self) -> None:
        with _workspace() as root:
            _fixture(root)
            saved = save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "assign_existing_slot", "stable_slot_id": "B03"},
            )
            self.assertEqual(saved["saved_decision"]["team_label"], "B")
            result = finalize_reviewed_identity(root, _match())
            row = _subject_row(result, "su")
            self.assertEqual(row["team_label"], "B")
            self.assertEqual(row["stable_anonymous_slot_id"], "B03")
            self.assertEqual(_load(root / "reviewed_identity_slot_assignments.json")["reviewed_slots"], [])
            self.assertEqual(result["frame_uniqueness_diagnostics"]["duplicate_stable_slot_claim_groups"], 0)
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_identity_correction(
                    root, _match(), {"candidate_subject_id": "s1", "action": "assign_existing_slot", "stable_slot_id": "B03"}
                )
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_identity_correction(
                    root, _match(), {"candidate_subject_id": "s2", "action": "assign_existing_slot", "stable_slot_id": "A03"}
                )
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_identity_correction(
                    root, _match(), {"candidate_subject_id": "s1", "action": "assign_team", "team_label": "B"}
                )

        with _workspace() as root:
            _fixture(root)
            save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "assign_roster_player", "player_id": "p2"},
            )
            result = finalize_reviewed_identity(root, _match())
            row = _subject_row(result, "su")
            self.assertEqual(row["team_label"], "B")
            self.assertEqual(row["canonical_player_id"], "p2")
            self.assertEqual(row["identity_status"], "confirmed")
            self.assertEqual(result["frame_uniqueness_diagnostics"]["duplicate_canonical_player_claim_groups"], 0)
            corrected = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p2",
                },
            )
            self.assertTrue(corrected["saved_decision"]["team_correction"])

    def test_unknown_team_new_player_uses_bounded_target_team_slot(self) -> None:
        with _workspace() as root:
            _fixture(root)
            first = save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "create_new_stable_player", "team_label": "B"},
            )
            self.assertEqual(first["allocated_stable_slot_id"], "B04")
            result = finalize_reviewed_identity(root, _match())
            row = _subject_row(result, "su")
            self.assertEqual(row["team_label"], "B")
            self.assertEqual(row["stable_anonymous_slot_id"], "B04")

    def test_context_exposes_both_rosters_but_existing_slots_stay_team_filtered(self) -> None:
        with _workspace() as root:
            _fixture(root)
            context = reviewed_correction_context(root, _match(), "s1")
            self.assertEqual(
                [row["player_id"] for row in context["roster_options"]],
                ["p1", "p2"],
            )
            self.assertEqual(
                [row["stable_slot_id"] for row in context["slot_options"]],
                ["A01", "A02", "A03"],
            )
            saved = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_existing_slot",
                    "stable_slot_id": "A03",
                },
            )
            self.assertEqual(saved["saved_decision"]["stable_slot_id"], "A03")
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s2",
                        "action": "assign_existing_slot",
                        "stable_slot_id": "A03",
                    },
                )

    def test_new_player_allocates_persistent_bounded_slot_and_checks_active_cap(self) -> None:
        with _workspace() as root:
            _fixture(root)
            first = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                },
            )
            second = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                },
            )
            self.assertEqual(first["allocated_stable_slot_id"], "A04")
            self.assertEqual(second["allocated_stable_slot_id"], "A04")

        with _workspace() as root:
            _fixture(root, active_team_a=7)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "Eighth simultaneous"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s1",
                        "action": "create_new_stable_player",
                        "team_label": "A",
                    },
                )
            self.assertEqual(_decision_files(root), before)

        with _workspace() as root:
            _fixture(root)
            global_identity = _load(root / "global_identity.json")
            global_identity["slots"] = [
                {
                    "stable_player_id": f"A{number:02d}",
                    "team_label": "A",
                    "tracklet_ids": [],
                }
                for number in range(1, 15)
            ]
            _write(root / "global_identity.json", global_identity)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "bounded pool exhausted"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s1",
                        "action": "create_new_stable_player",
                        "team_label": "A",
                    },
                )
            self.assertEqual(_decision_files(root), before)

    def test_special_actions_respect_overlay_stats_and_unknown_fallback(self) -> None:
        with _workspace() as root:
            _fixture(root)
            for subject, action in (("s1", "team_unknown"), ("s2", "referee")):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": subject, "action": action},
                )
            result = finalize_reviewed_identity(root, _match())
            by_subject = {
                row["candidate_subject_id"]: row
                for row in result["tracklet_assignments"]
            }
            self.assertEqual(by_subject["s1"]["fallback_label"], "U?")
            self.assertIsNone(by_subject["s1"]["stable_anonymous_slot_id"])
            self.assertEqual(by_subject["s2"]["display_label"], "Sędzia")
            at = reviewed_assignment_at(result, _tracklets(root), 2.0, 10.0)
            self.assertEqual(at[0]["identity_status"], "referee")

            save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "s2", "action": "false_detection"},
            )
            result = finalize_reviewed_identity(root, _match())
            self.assertEqual(reviewed_assignment_at(result, _tracklets(root), 2.0, 10.0), [])

        with _workspace() as root:
            _fixture(root)
            global_identity = _load(root / "global_identity.json")
            next(row for row in global_identity["slots"] if row["stable_player_id"] == "A03")["tracklet_ids"] = ["t1", "t1b"]
            _write(root / "global_identity.json", global_identity)
            save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "s1", "action": "unresolved"},
            )
            result = finalize_reviewed_identity(root, _match())
            row = next(
                item
                for item in result["tracklet_assignments"]
                if item["candidate_subject_id"] == "s1"
            )
            self.assertEqual(row["identity_status"], "unresolved")
            self.assertEqual(row["stable_anonymous_slot_id"], "A03")
            self.assertIsNone(row["canonical_player_id"])

    def test_deferred_roster_correction_persists_without_authoritative_reads(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _enable_materialized_candidate_context(root)
            with patch(
                "app.services.identity_reviewed_corrections.build_reviewed_identity_progress",
                side_effect=AssertionError("progress must be deferred"),
            ), patch(
                "app.services.identity_reviewed_corrections.get_reviewed_identity_status",
                side_effect=AssertionError("snapshot status must be deferred"),
            ):
                result = persist_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s1",
                        "action": "assign_roster_player",
                        "player_id": "p1",
                    },
                )

            self.assertTrue(result["recompute_deferred"])
            self.assertEqual(result["persistence"]["status"], "saved")
            self.assertEqual(result["saved_decision"]["player_id"], "p1")
            self.assertTrue((root / "reviewed_identity_recompute_required.json").exists())
            self.assertFalse((root / "reviewed_identity_snapshot.json").exists())

    def test_optional_gate_context_persists_cross_team_roster_correction(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _enable_materialized_candidate_context(root)
            progress = build_reviewed_identity_progress(root, _match())
            optional = next(
                row
                for row in progress["review_units"]
                if row["candidate_subject_id"] == "s2"
            )
            optional.update(
                priority="optional",
                current_resolution_status="optional_team_audit",
                operator_actionable=True,
            )
            progress.update(
                status="ready",
                source_snapshot_digest="snapshot-1",
                next_cases=[
                    row
                    for row in progress["next_cases"]
                    if row["candidate_subject_id"] != "s2"
                ],
                optional_audit_cases=[optional],
            )
            _write(root / "reviewed_identity_progress.json", progress)
            _write(
                root / "reviewed_identity_report.json",
                {"snapshot_digest": "snapshot-1"},
            )
            payload = {
                "candidate_subject_id": "s2",
                "action": "assign_roster_player",
                "player_id": "p1",
                "defer_recompute": True,
            }

            gate = validate_deferred_review_action(root, _match(), payload)
            result = persist_reviewed_identity_correction(
                root,
                _match(),
                payload,
                trusted_materialized_detected_team_labels=gate[
                    "detected_team_labels_by_subject"
                ],
            )

            self.assertTrue(result["recompute_deferred"])
            self.assertEqual(result["saved_decision"]["source_team_label"], "B")
            self.assertEqual(result["saved_decision"]["team_label"], "A")
            self.assertEqual(result["saved_decision"]["player_id"], "p1")
            self.assertTrue(result["saved_decision"]["team_correction"])
            self.assertTrue(
                (root / "reviewed_identity_recompute_required.json").exists()
            )

    def test_deferred_terminal_actions_persist_immediately(self) -> None:
        for action in ("unresolved", "referee", "false_detection"):
            with self.subTest(action=action), _workspace() as root:
                _fixture(root)
                _enable_materialized_candidate_context(root)
                result = persist_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", "action": action},
                )
                self.assertEqual(result["saved_decision"]["action"], action)
                self.assertTrue(result["recompute_deferred"])

    def test_team_attribution_actions_are_enforced_at_persistence_boundary(self) -> None:
        allowed = (
            {"action": "assign_team", "team_label": "A"},
            {"action": "assign_team", "team_label": "B"},
            {"action": "referee"},
            {"action": "false_detection"},
            {"action": "team_unknown"},
            {"action": "unresolved"},
        )
        forbidden = (
            {"action": "assign_roster_player", "player_id": "p1"},
            {"action": "assign_existing_slot", "stable_slot_id": "A03"},
            {"action": "create_new_stable_player", "team_label": "A"},
            {"action": "mixed_players", "mixed_hint": "unknown"},
        )
        contract = {"visual_evidence": {"kind": "team_attribution"}}
        for action_payload in allowed:
            with self.subTest(allowed=action_payload), _workspace() as root:
                _fixture(root)
                _configure_s1_detected_teams(root, set())
                saved = persist_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", **action_payload},
                    authorized_review_unit=contract,
                )
                self.assertEqual(saved["saved_decision"]["action"], action_payload["action"])
        for action_payload in forbidden:
            with self.subTest(forbidden=action_payload), _workspace() as root:
                _fixture(root)
                before = _decision_files(root)
                with self.assertRaises(ReviewedIdentityActionScopeError) as raised:
                    persist_reviewed_identity_correction(
                        root,
                        _match(),
                        {"candidate_subject_id": "s1", **action_payload},
                        authorized_review_unit=contract,
                    )
                self.assertEqual(raised.exception.code, "team_attribution_action_not_allowed")
                self.assertEqual(_decision_files(root), before)

    def test_synchronous_correction_cannot_bypass_team_attribution_contract(self) -> None:
        with _workspace() as root:
            _fixture(root)
            progress = {
                "next_cases": [
                    {
                        "candidate_subject_id": "s1",
                        "visual_evidence": {"kind": "team_attribution"},
                    }
                ],
                "optional_audit_cases": [],
            }
            before = _decision_files(root)
            with patch(
                "app.services.identity_reviewed_corrections.build_reviewed_identity_progress",
                return_value=progress,
            ), self.assertRaises(ReviewedIdentityActionScopeError) as raised:
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s1",
                        "action": "assign_roster_player",
                        "player_id": "p1",
                    },
                )
            self.assertEqual(raised.exception.code, "team_attribution_action_not_allowed")
            self.assertEqual(_decision_files(root), before)

    def test_normal_review_unit_retains_roster_assignment_action(self) -> None:
        with _workspace() as root:
            _fixture(root)
            saved = persist_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p1",
                },
                authorized_review_unit={"visual_evidence": {"kind": "player_identity"}},
            )
            self.assertEqual(saved["saved_decision"]["player_id"], "p1")

    def test_deferred_create_new_player_retry_reuses_allocated_slot(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _enable_materialized_candidate_context(root)
            _materialize_deferred_context(root)
            payload = {
                "candidate_subject_id": "s1",
                "action": "create_new_stable_player",
                "team_label": "A",
            }
            def guarded_load(path: Path) -> dict:
                if path.name == "tracklets.json":
                    raise AssertionError("deferred active-cap validation read tracklets")
                return load_reviewed_required(path)

            with patch(
                "app.services.identity_reviewed_corrections.load_required",
                side_effect=guarded_load,
            ), patch(
                "app.services.identity_reviewed_corrections.resolve_stable_anonymous_entities",
                side_effect=AssertionError("deferred active-cap validation ran resolver"),
            ) as resolver:
                first = persist_reviewed_identity_correction(root, _match(), payload)
                second = persist_reviewed_identity_correction(root, _match(), payload)
            self.assertEqual(first["allocated_stable_slot_id"], "A04")
            self.assertEqual(second["allocated_stable_slot_id"], "A04")
            resolver.assert_not_called()

    def test_deferred_create_new_player_cached_context_preserves_cap_and_constraints(self) -> None:
        with _workspace() as root:
            _fixture(root, active_team_a=7)
            _enable_materialized_candidate_context(root)
            _materialize_deferred_context(root)
            with patch(
                "app.services.identity_reviewed_corrections.resolve_stable_anonymous_entities",
                side_effect=AssertionError("cached cap check must not run resolver"),
            ) as resolver:
                with self.assertRaisesRegex(ValueError, "Eighth simultaneous"):
                    persist_reviewed_identity_correction(
                        root,
                        _match(),
                        {
                            "candidate_subject_id": "s1",
                            "action": "create_new_stable_player",
                            "team_label": "A",
                        },
                    )
            resolver.assert_not_called()

        with _workspace() as root:
            _fixture(root)
            _enable_materialized_candidate_context(root)
            _materialize_deferred_context(root)
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                persist_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s1",
                        "action": "create_new_stable_player",
                        "team_label": "B",
                    },
                )
            allocated = persist_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                    "stable_slot_id": "A09",
                },
            )
            self.assertEqual(allocated["allocated_stable_slot_id"], "A04")

    def test_materialized_mixed_team_subject_rejects_every_team_binding_action(self) -> None:
        payloads = (
            {"action": "assign_roster_player", "player_id": "p1"},
            {"action": "assign_roster_player", "player_id": "p2"},
            {"action": "assign_existing_slot", "stable_slot_id": "A03"},
            {"action": "assign_existing_slot", "stable_slot_id": "B03"},
            {"action": "assign_team", "team_label": "A"},
            {"action": "assign_team", "team_label": "B"},
            {"action": "create_new_stable_player", "team_label": "A"},
            {"action": "create_new_stable_player", "team_label": "B"},
        )
        for action_payload in payloads:
            with self.subTest(payload=action_payload), _workspace() as root:
                _fixture(root)
                _enable_materialized_candidate_context(root)
                _configure_s1_detected_teams(root, {"A", "B"})
                _materialize_deferred_context(root)

                before = _decision_files(root)
                with self.assertRaisesRegex(ValueError, "mixed-team subject"):
                    persist_reviewed_identity_correction(
                        root,
                        _match(),
                        {"candidate_subject_id": "s1", **action_payload},
                    )
                self.assertEqual(_decision_files(root), before)

    def test_materialized_single_and_unknown_team_semantics_match_exact_path(self) -> None:
        cases = (
            ({"A"}, "A", True),
            ({"A"}, "B", False),
            ({"B"}, "B", True),
            ({"B"}, "A", False),
            (set(), "A", True),
            (set(), "B", True),
        )
        for detected_teams, requested_team, should_succeed in cases:
            with (
                self.subTest(
                    detected_teams=detected_teams,
                    requested_team=requested_team,
                ),
                _workspace() as root,
            ):
                _fixture(root)
                _enable_materialized_candidate_context(root)
                _configure_s1_detected_teams(root, detected_teams)
                _materialize_deferred_context(root)
                payload = {
                    "candidate_subject_id": "s1",
                    "action": "assign_team",
                    "team_label": requested_team,
                }

                if should_succeed:
                    result = persist_reviewed_identity_correction(
                        root,
                        _match(),
                        payload,
                    )
                    self.assertEqual(
                        result["saved_decision"]["team_label"],
                        requested_team,
                    )
                else:
                    with self.assertRaisesRegex(ValueError, "team mismatch"):
                        persist_reviewed_identity_correction(
                            root,
                            _match(),
                            payload,
                        )

    def test_materialized_named_player_can_correct_single_wrong_detected_team(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _enable_materialized_candidate_context(root)
            _configure_s1_detected_teams(root, {"A"})
            _materialize_deferred_context(root)

            result = persist_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p2",
                },
            )

            self.assertEqual(result["saved_decision"]["source_team_label"], "A")
            self.assertEqual(result["saved_decision"]["team_label"], "B")
            self.assertTrue(result["saved_decision"]["team_correction"])
            self.assertIsNone(result["saved_decision"]["stable_slot_id"])

    def test_materialized_team_validation_is_equivalent_to_old_exact_validation(self) -> None:
        cases = (
            ({"A"}, "A"),
            ({"B"}, "B"),
            (set(), "A"),
            ({"A", "B"}, "A"),
        )
        for detected_teams, requested_team in cases:
            with (
                self.subTest(
                    detected_teams=detected_teams,
                    requested_team=requested_team,
                ),
                _workspace() as exact_root,
                _workspace() as materialized_root,
            ):
                for root in (exact_root, materialized_root):
                    _fixture(root)
                    _enable_materialized_candidate_context(root)
                    _configure_s1_detected_teams(root, detected_teams)
                _materialize_deferred_context(materialized_root)
                payload = {
                    "candidate_subject_id": "s1",
                    "action": "assign_team",
                    "team_label": requested_team,
                }

                exact_outcome = _persist_outcome(
                    exact_root,
                    payload,
                    use_materialized_context=False,
                )
                materialized_outcome = _persist_outcome(
                    materialized_root,
                    payload,
                    use_materialized_context=True,
                )

                self.assertEqual(materialized_outcome, exact_outcome)

    def test_deferred_batch_final_snapshot_matches_immediate_recompute_semantics(self) -> None:
        with _workspace() as immediate_root, _workspace() as deferred_root:
            for root in (immediate_root, deferred_root):
                _fixture(root)
                _enable_materialized_candidate_context(root)
                global_identity = _load(root / "global_identity.json")
                next(
                    row
                    for row in global_identity["slots"]
                    if row["stable_player_id"] == "A03"
                )["tracklet_ids"] = ["t1", "t1b"]
                _write(root / "global_identity.json", global_identity)

            decisions = [
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p1",
                },
                {"candidate_subject_id": "s2", "action": "referee"},
                {
                    "candidate_subject_id": "su",
                    "action": "assign_team",
                    "team_label": "B",
                },
            ]
            immediate_snapshot = None
            for payload in decisions:
                save_reviewed_identity_correction(
                    immediate_root,
                    _match(),
                    payload,
                )
                immediate_snapshot = finalize_reviewed_identity(
                    immediate_root,
                    _match(),
                )
            for payload in decisions:
                persist_reviewed_identity_correction(
                    deferred_root,
                    _match(),
                    payload,
                )
            deferred_snapshot = finalize_reviewed_identity(deferred_root, _match())
            immediate_progress = build_reviewed_identity_progress(
                immediate_root,
                _match(),
            )
            deferred_progress = build_reviewed_identity_progress(
                deferred_root,
                _match(),
            )
            immediate_workflow = get_review_workflow_state(immediate_root, _match())
            deferred_workflow = get_review_workflow_state(deferred_root, _match())

            self.assertIsNotNone(immediate_snapshot)
            self.assertEqual(
                immediate_snapshot["semantic_digest"],
                deferred_snapshot["semantic_digest"],
            )
            self.assertEqual(
                immediate_snapshot["tracklet_assignments"],
                deferred_snapshot["tracklet_assignments"],
            )
            self.assertEqual(
                reviewed_decisions_semantic_digest(immediate_root),
                reviewed_decisions_semantic_digest(deferred_root),
            )
            self.assertEqual(
                immediate_progress["summary"],
                deferred_progress["summary"],
            )
            self.assertEqual(
                immediate_progress["observations"],
                deferred_progress["observations"],
            )
            self.assertEqual(
                immediate_workflow["phase"],
                deferred_workflow["phase"],
            )
            self.assertEqual(
                immediate_workflow["issues"],
                deferred_workflow["issues"],
            )

    def test_deferred_save_does_not_hash_or_recompute_large_production_files(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _enable_materialized_candidate_context(root)
            (root / "tracks.json").write_text("x" * 5_000_000, encoding="utf-8")
            immutable_before = _immutable_identity_files(root)
            with patch(
                "app.services.identity_initial_audit_store._file_sha256"
            ) as file_sha, patch(
                "app.services.identity_seeded_candidate_assignments.production_identity_snapshot"
            ) as production_snapshot, patch(
                "app.services.identity_seeded_candidate_assignments.rebuild_identity_seeded_candidate_assignments"
            ) as seeded_rebuild, patch(
                "app.services.identity_reviewed_snapshot.finalize_reviewed_identity"
            ) as finalize_snapshot, patch(
                "app.services.identity_reviewed_corrections.get_reviewed_identity_status"
            ) as reviewed_status, patch(
                "app.services.identity_reviewed_corrections.build_reviewed_identity_progress"
            ) as progress_build, patch(
                "app.services.identity_reviewed_segments.render_segment_review_evidence"
            ) as segment_render, patch(
                "app.services.identity_reviewed_stats.build_reviewed_stats"
            ) as reviewed_stats, patch(
                "app.services.identity_reviewed_output_jobs.generate_reviewed_output"
            ) as reviewed_output:
                result = persist_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", "action": "unresolved"},
                )

            self.assertTrue(result["recompute_deferred"])
            file_sha.assert_not_called()
            production_snapshot.assert_not_called()
            seeded_rebuild.assert_not_called()
            finalize_snapshot.assert_not_called()
            reviewed_status.assert_not_called()
            progress_build.assert_not_called()
            segment_render.assert_not_called()
            reviewed_stats.assert_not_called()
            reviewed_output.assert_not_called()
            self.assertEqual(_immutable_identity_files(root), immutable_before)

    def test_timestamp_lookup_returns_complete_real_detected_entity(self) -> None:
        with _workspace() as root:
            _fixture(root)
            result = finalize_reviewed_identity(root, _match())
            self.assertEqual(reviewed_assignment_at(result, _tracklets(root), 0.2, 10), [])
            entity = reviewed_assignment_at(result, _tracklets(root), 0.3, 10)[0]
            required = {
                "frame",
                "time_sec",
                "tracklet_id",
                "candidate_subject_id",
                "candidate_subject_ids",
                "team_label",
                "stable_anonymous_slot_id",
                "canonical_player_id",
                "player_name",
                "display_label",
                "identity_status",
                "identity_source",
                "fallback_label",
                "requires_review",
                "hard_blockers",
                "conflicts",
                "detected_evidence_count",
                "frame_start",
                "frame_end",
            }
            self.assertTrue(required.issubset(entity))


def _fixture(root: Path, active_team_a: int | None = None) -> None:
    tracklets = [
        {
            "tracklet_id": "t1",
            "team_label": "A",
            "team_id": "ta",
            "positions_m": [
                {"frame": 1, "status": "predicted", "source": "predicted"},
                {"frame": 3, "status": "detected", "pitch_m": [1.0, 1.0], "bbox_xyxy": [1, 1, 5, 8]},
                {"frame": 4, "status": "detected", "pitch_m": [1.5, 1.0], "bbox_xyxy": [2, 1, 6, 8]},
            ],
        },
        {
            "tracklet_id": "t1b",
            "team_label": "A",
            "team_id": "ta",
            "positions_m": [
                {"frame": 8, "status": "detected", "pitch_m": [3.0, 1.0], "bbox_xyxy": [3, 1, 7, 8]},
                {"frame": 9, "status": "detected", "pitch_m": [3.5, 1.0], "bbox_xyxy": [4, 1, 8, 8]},
            ],
        },
        {
            "tracklet_id": "t2",
            "team_label": "B",
            "team_id": "tb",
            "positions_m": [
                {"frame": 20, "status": "detected", "pitch_m": [1.0, 2.0], "bbox_xyxy": [1, 2, 5, 9]},
            ],
        },
        {
            "tracklet_id": "tu",
            "team_label": "U",
            "team_id": "",
            "positions_m": [
                {"frame": 30, "status": "detected", "pitch_m": [5.0, 2.0], "bbox_xyxy": [5, 2, 9, 9]},
                {"frame": 31, "status": "detected", "pitch_m": [5.5, 2.0], "bbox_xyxy": [6, 2, 10, 9]},
            ],
        },
    ]
    _write(root / "match.json", _match())
    _write(root / "tracklets.json", {"tracklets": tracklets})
    _write(
        root / "identity_candidate_shadow.json",
        {
            "subjects": [
                {"candidate_subject_id": "s1", "tracklet_ids": ["t1", "t1b"]},
                {"candidate_subject_id": "s2", "tracklet_ids": ["t2"]},
                {"candidate_subject_id": "su", "tracklet_ids": ["tu"]},
            ]
        },
    )
    slots = [
        {"stable_player_id": slot_id, "team_label": slot_id[0], "tracklet_ids": []}
        for slot_id in ("A01", "A02", "A03", "B01", "B02", "B03")
    ]
    global_identity: dict = {"slots": slots}
    if active_team_a is not None:
        global_identity["frames"] = [
            {"frame": frame, "active_team_a": active_team_a}
            for frame in (3, 4, 8, 9)
        ]
    _write(root / "global_identity.json", global_identity)
    detected_frames = (3, 4, 8, 9, 20, 30, 31)
    _write(
        root / "frame_detection_counts.json",
        {
            "schema_version": "1.0.0",
            "target_players": 14,
            "frames": [
                {
                    "frame": frame,
                    "active_team_a": (
                        active_team_a
                        if active_team_a is not None and frame in {3, 4, 8, 9}
                        else 0
                    ),
                    "active_team_b": 0,
                }
                for frame in detected_frames
            ],
        },
    )
    _write(root / "stable_players.json", {"players": []})
    _write(
        root / "identity_roster_subject_review_shadow.json",
        {
            "cards": [
                _card("s1", "A", "card-s1", "p1"),
                _card("s2", "B", "card-s2", "p2"),
            ]
        },
    )


def _enable_materialized_candidate_context(root: Path) -> None:
    document = _load(root / "identity_candidate_shadow.json")
    by_subject = {
        row["candidate_subject_id"]: row for row in document["subjects"]
    }
    by_subject["s1"].update(
        team_label="A",
        production_player_ids=["A03"],
        production_subject_ids=["slot-A03"],
    )
    by_subject["s2"].update(
        team_label="B",
        production_player_ids=["B03"],
        production_subject_ids=["slot-B03"],
    )
    by_subject["su"].update(team_label="U")
    document["subjects"].extend(
        [
            {
                "candidate_subject_id": f"materialized-{slot_id}",
                "team_label": slot_id[0],
                "tracklet_ids": [],
                "production_player_ids": [slot_id],
                "production_subject_ids": [f"slot-{slot_id}"],
            }
            for slot_id in ("A01", "A02", "B01", "B02")
        ]
    )
    _write(root / "identity_candidate_shadow.json", document)


def _add_material_continuity_members(root: Path) -> None:
    tracklets = _load(root / "tracklets.json")
    candidates = _load(root / "identity_candidate_shadow.json")
    for index in range(4):
        subject_id = f"mc{index + 1}"
        tracklet_id = f"tm{index + 1}"
        frame = 100 + index * 10
        tracklets["tracklets"].append(
            {
                "tracklet_id": tracklet_id,
                "team_label": "A",
                "team_id": "ta",
                "positions_m": [
                    {
                        "frame": frame,
                        "status": "detected",
                        "pitch_m": [float(index + 1), 2.0],
                        "bbox_xyxy": [10, 10, 20, 30],
                    },
                    {
                        "frame": frame + 1,
                        "status": "detected",
                        "pitch_m": [float(index + 1.2), 2.0],
                        "bbox_xyxy": [11, 10, 21, 30],
                    },
                ],
            }
        )
        candidates["subjects"].append(
            {
                "candidate_subject_id": subject_id,
                "tracklet_ids": [tracklet_id],
                "team_label": "A",
                "production_player_ids": ["A12"],
                "production_subject_ids": ["slot-A12"],
            }
        )
    _write(root / "tracklets.json", tracklets)
    _write(root / "identity_candidate_shadow.json", candidates)


def _materialize_deferred_context(root: Path) -> None:
    progress = build_reviewed_identity_progress(root, _match())
    _write(root / "reviewed_identity_progress.json", progress)


def _configure_s1_detected_teams(root: Path, teams: set[str]) -> None:
    tracklets = _load(root / "tracklets.json")
    labels = (
        ("A", "B")
        if teams == {"A", "B"}
        else ((next(iter(teams)),) * 2 if teams else ("U", "U"))
    )
    by_tracklet = {row["tracklet_id"]: row for row in tracklets["tracklets"]}
    by_tracklet["t1"]["team_label"] = labels[0]
    by_tracklet["t1b"]["team_label"] = labels[1]
    _write(root / "tracklets.json", tracklets)

    candidate_document = _load(root / "identity_candidate_shadow.json")
    s1 = next(
        row
        for row in candidate_document["subjects"]
        if row["candidate_subject_id"] == "s1"
    )
    s1["team_label"] = next(iter(teams)) if len(teams) == 1 else "U"
    _write(root / "identity_candidate_shadow.json", candidate_document)


def _persist_outcome(
    root: Path,
    payload: dict,
    *,
    use_materialized_context: bool,
) -> tuple[str, str]:
    try:
        result = persist_reviewed_identity_correction(
            root,
            _match(),
            payload,
            use_materialized_context=use_materialized_context,
        )
    except ValueError as exc:
        message = str(exc).lower()
        if "mixed-team" in message:
            return ("error", "mixed-team")
        if "team mismatch" in message or "cross-team" in message:
            return ("error", "team-mismatch")
        return ("error", message)
    return ("saved", str(result["saved_decision"]["team_label"]))


def _card(subject: str, team: str, key: str, player: str) -> dict:
    return {
        "review_card_key": key,
        "candidate_subject_id": subject,
        "team_label": team,
        "review_status": "ready_for_operator_review",
        "roster_candidates": [{"player_id": player}],
        "allowed_actions": ["assign_roster_player", "mark_unresolved"],
        "visual_evidence": {"anchor_crops": []},
    }


def _match() -> dict:
    return {
        "id": "m1",
        "teams": [
            {"id": "ta", "players": [{"id": "p1", "name": "One", "number": "8"}]},
            {"id": "tb", "players": [{"id": "p2", "name": "Two", "number": "9"}]},
        ],
    }


def _tracklets(root: Path) -> dict[str, dict]:
    return {
        row["tracklet_id"]: row
        for row in json.loads((root / "tracklets.json").read_text(encoding="utf-8"))["tracklets"]
    }


def _subject_row(snapshot: dict, subject_id: str) -> dict:
    return next(row for row in snapshot["tracklet_assignments"] if row["candidate_subject_id"] == subject_id)


def _immutable_identity_files(root: Path) -> dict[str, bytes]:
    return {
        name: (root / name).read_bytes()
        for name in (
            "tracklets.json",
            "global_identity.json",
            "stable_players.json",
            "tracks.json",
        )
        if (root / name).exists()
    }


def _decision_files(root: Path) -> dict[str, bytes | None]:
    names = (
        "identity_roster_subject_review_decisions_shadow.json",
        "reviewed_identity_slot_assignments.json",
    )
    return {
        name: (root / name).read_bytes() if (root / name).exists() else None
        for name in names
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _workspace:
    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        return Path(self.temporary.name)

    def __exit__(self, *args: object) -> None:
        self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
