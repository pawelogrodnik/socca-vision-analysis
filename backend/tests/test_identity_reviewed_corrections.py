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
from app.services.identity_reviewed_action_gate import (
    DeferredReviewActionError,
    validate_deferred_review_action,
)
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
from app.services.identity_reviewed_hot_state import (
    hot_context,
    load_or_rebuild_review_hot_state,
)
from app.services.identity_reviewed_mixed_resolution import save_inline_temporal_split
from app.services.identity_reviewed_segments import (
    build_segment_review_document,
    load_segment_decisions,
    load_segment_review,
)
from app.services.identity_reviewed_mixed_store import (
    build_mixed_review_queue,
    load_mixed_player_cases,
    resolved_material_continuity_observation_pairs,
)
from app.services.review_workflow_state import derive_review_workflow_state
from app.services.review_workflow_state import get_review_workflow_state


class ReviewedIdentityCorrectionTests(unittest.TestCase):
    def test_whole_subject_hot_context_digest_authorizes_exact_save_and_rejects_stale(self) -> None:
        """A materialized whole subject has the same stale contract as segments.

        Exercise the public correction-context GET followed by the public
        deferred-save route.  The warm save must use the server-owned unit,
        rather than rebuild progress, and a browser-supplied wrong digest must
        fail before it reaches persistence.
        """
        from fastapi import HTTPException, Response
        from app.main import (
            get_match_reviewed_correction_context,
            post_match_reviewed_identity_correction,
        )

        with _workspace() as root:
            _fixture(root)
            candidate = _load(root / "identity_candidate_shadow.json")
            candidate["subjects"] = [
                row for row in candidate["subjects"]
                if row.get("candidate_subject_id") != "su"
            ]
            _write(root / "identity_candidate_shadow.json", candidate)
            progress = _whole_subject_hot_progress()
            with patch("app.main.match_dir", return_value=root), patch(
                "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
                return_value=progress,
            ) as build_progress:
                context = get_match_reviewed_correction_context(
                    "m1",
                    Response(),
                    candidate_subject_id="s1",
                    review_target_id=None,
                )
                self.assertEqual(context["scope_kind"], "whole_subject")
                self.assertTrue(context["source_ownership_digest"])

                saved = post_match_reviewed_identity_correction(
                    "m1",
                    {
                        "candidate_subject_id": "s1",
                        "action": "assign_roster_player",
                        "player_id": "p1",
                        "source_ownership_digest": context["source_ownership_digest"],
                        "review_state_version": context["review_state_version"],
                        "defer_recompute": True,
                    },
                )

            self.assertTrue(saved["recompute_deferred"])
            # One cold GET built the state; the ordinary save projected it in
            # memory and did not re-run match-wide progress materialization.
            self.assertEqual(build_progress.call_count, 1)

        with _workspace() as root:
            _fixture(root)
            candidate = _load(root / "identity_candidate_shadow.json")
            candidate["subjects"] = [
                row for row in candidate["subjects"]
                if row.get("candidate_subject_id") != "su"
            ]
            _write(root / "identity_candidate_shadow.json", candidate)
            with patch("app.main.match_dir", return_value=root), patch(
                "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
                return_value=_whole_subject_hot_progress(),
            ):
                context = get_match_reviewed_correction_context(
                    "m1",
                    Response(),
                    candidate_subject_id="s1",
                    review_target_id=None,
                )
                with self.assertRaises(HTTPException) as raised:
                    post_match_reviewed_identity_correction(
                        "m1",
                        {
                            "candidate_subject_id": "s1",
                            "action": "assign_roster_player",
                            "player_id": "p1",
                            "source_ownership_digest": "wrong-digest",
                            "review_state_version": context["review_state_version"],
                            "defer_recompute": True,
                        },
                    )

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.detail["code"], "review_target_stale")
            self.assertFalse((root / "identity_roster_subject_review_decisions_shadow.json").exists())

    def test_hot_context_preserves_legacy_operator_evidence_for_every_scope(self) -> None:
        """The hot read model must not make split evidence poorer than legacy.

        This exercises the actual legacy context builder for a whole subject,
        its team-attribution provenance, a material continuity parent, and
        canonical child targets created by a persisted temporal split.
        """
        with _workspace() as root:
            _fixture(root)
            cards = _load(root / "identity_roster_subject_review_shadow.json")
            cards["cards"][0]["visual_evidence"] = {
                "kind": "team_attribution",
                "anchor_crops": [],
            }
            _write(root / "identity_roster_subject_review_shadow.json", cards)
            _add_natural_material_continuity_case(root)
            match = _match()

            state = load_or_rebuild_review_hot_state(root, match)
            _assert_hot_context_equivalent(
                self,
                reviewed_correction_context(root, match, "s1"),
                hot_context(state, "s1"),
            )

            material = next(
                unit for unit in state["internal_review_units"]
                if unit.get("scope_kind") == "material_continuity"
            )
            _assert_hot_context_equivalent(
                self,
                reviewed_correction_context(root, match, material["candidate_subject_id"]),
                hot_context(state, material["candidate_subject_id"]),
            )

            split = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": material["candidate_subject_id"],
                    "continuity_group_id": material["continuity_group_id"],
                    "source_ownership_digest": material["source_ownership_digest"],
                    "resolution": "split",
                    "split_after_frames": [349],
                    "segment_assignments": [
                        {"action": "assign_roster_player", "player_id": "p1"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            state = load_or_rebuild_review_hot_state(root, match)
            for target_id in split["saved_case"]["segment_target_ids"]:
                _assert_hot_context_equivalent(
                    self,
                    reviewed_correction_context(
                        root,
                        match,
                        material["candidate_subject_id"],
                        target_id,
                    ),
                    hot_context(state, material["candidate_subject_id"], target_id),
                )

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

    def test_material_continuity_assignment_changes_exact_pairs_not_outside_subject_run(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_material_continuity_members(root)
            tracklets = _load(root / "tracklets.json")
            next(row for row in tracklets["tracklets"] if row["tracklet_id"] == "tm1")["positions_m"].append(
                {
                    "frame": 900,
                    "status": "detected",
                    "pitch_m": [12.0, 2.0],
                    "bbox_xyxy": [30, 10, 40, 30],
                }
            )
            _write(root / "tracklets.json", tracklets)
            owned_observations = [
                {"tracklet_id": f"tm{index + 1}", "frame": 100 + index * 10 + offset}
                for index in range(4)
                for offset in (0, 1)
            ]
            unit = {
                "candidate_subject_id": "continuity:A12:100-139",
                "continuity_group_id": "continuity:A12:100-139",
                "continuity_subject_ids": ["mc1", "mc2", "mc3", "mc4"],
                "scope_kind": "material_continuity",
                "effective_team_label": "A",
                "priority": "continuity",
                "current_resolution_status": "pending_material_continuity_review",
                "source_ownership_digest": "exact-owned-pairs-v1",
                "owned_observations": owned_observations,
                "continuity_members": [
                    {
                        "candidate_subject_id": f"mc{index + 1}",
                        "detected_pairs": [
                            [f"tm{index + 1}", 100 + index * 10],
                            [f"tm{index + 1}", 101 + index * 10],
                        ],
                    }
                    for index in range(4)
                ],
            }
            progress = {"_internal_review_units": [unit]}
            with patch(
                "app.services.identity_reviewed_progress.materialize_reviewed_identity_units",
                return_value=[unit],
            ):
                saved = persist_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": unit["candidate_subject_id"],
                        "action": "assign_roster_player",
                        "player_id": "p1",
                        "source_ownership_digest": unit["source_ownership_digest"],
                    },
                    authorized_review_unit=unit,
                )
                snapshot = finalize_reviewed_identity(root, _match())

            self.assertNotIn("owned_observations", saved["saved_decision"])
            self.assertEqual(
                saved["saved_decision"]["owned_observations_count"], len(owned_observations)
            )
            self.assertFalse((root / "reviewed_identity_slot_assignments.json").exists())
            material = snapshot["segment_observation_assignments"]
            self.assertEqual(
                {(row["tracklet_id"], row["frame"]) for row in material},
                {(row["tracklet_id"], row["frame"]) for row in owned_observations},
            )
            self.assertEqual({row["canonical_player_id"] for row in material}, {"p1"})
            self.assertNotIn(("tm1", 900), {(row["tracklet_id"], row["frame"]) for row in material})
            outside_rows = reviewed_assignment_at(snapshot, _tracklets(root), 90.0, 10.0)
            self.assertNotIn(
                "p1",
                {
                    row.get("canonical_player_id")
                    for row in outside_rows
                    if row.get("tracklet_id") == "tm1"
                },
            )
            with patch(
                "app.services.identity_reviewed_stats.read_match_video_metadata",
                return_value={
                    "fps": 10.0,
                    "frame_count": 1_000,
                    "duration_sec": 100.0,
                    "source": "fixture",
                    "filename": "fixture.mp4",
                },
            ):
                stats = build_reviewed_stats(root, snapshot, _match())
            player = next(
                row
                for row in stats["reviewed_player_stats.json"]["players"]
                if row["player_id"] == "p1"
            )
            self.assertEqual(player["confirmed_detected_observations"], len(owned_observations))
            self.assertEqual(player["heatmap_samples"], len(owned_observations))

    def test_material_direct_save_without_split_skips_superseding_source_resolve(self) -> None:
        with _workspace() as root:
            _fixture(root)
            unit = {
                "candidate_subject_id": "continuity:A12:100-101",
                "continuity_group_id": "continuity:A12:100-101",
                "scope_kind": "material_continuity",
                "effective_team_label": "A",
                "source_ownership_digest": "material-digest",
                "continuity_subject_ids": ["mc1"],
                "continuity_members": [{"candidate_subject_id": "mc1", "detected_pairs": [["tm1", 100]]}],
                "owned_observations": [{"tracklet_id": "tm1", "frame": 100}],
            }
            progress = {"_internal_review_units": [unit]}
            with (
                patch(
                    "app.services.identity_reviewed_progress.materialize_reviewed_identity_units",
                    return_value=[unit],
                ) as progress_builder,
                patch(
                    "app.services.identity_reviewed_corrections._direct_correction_source",
                    side_effect=AssertionError("superseding resolve must be skipped"),
                ) as direct_source,
            ):
                persist_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": unit["candidate_subject_id"],
                        "action": "assign_team",
                        "team_label": "A",
                        "source_ownership_digest": unit["source_ownership_digest"],
                    },
                    authorized_review_unit=unit,
                )

            direct_source.assert_not_called()
            self.assertEqual(progress_builder.call_count, 1)

    def test_material_continuity_false_detection_is_excluded_during_authoritative_recompute(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            case = _materialize_natural_material_deferred_case(root)
            saved = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": case["candidate_subject_id"],
                    "action": "false_detection",
                    "source_ownership_digest": case["source_ownership_digest"],
                },
            )
            snapshot = finalize_reviewed_identity(root, _match())

            self.assertEqual(saved["effective_action"], "false_detection")
            material = [
                row for row in snapshot["segment_observation_assignments"]
                if row.get("tracklet_id") == "material-tracklet" and row.get("frame") == 100
            ]
            self.assertEqual(len(material), 1)
            self.assertEqual(material[0]["identity_status"], "false_detection")
            self.assertEqual(material[0]["team_label"], "U")
            self.assertIsNone(material[0]["canonical_player_id"])
            self.assertFalse(material[0]["eligible_for_player_stats"])
            self.assertNotIn(
                ("material-tracklet", 100),
                {
                    (row.get("tracklet_id"), row.get("frame"))
                    for row in reviewed_assignment_at(snapshot, _tracklets(root), 10.0, 10.0)
                },
            )
            # Unrelated source observations remain in raw/effective resolution.
            self.assertTrue(any(
                row.get("tracklet_id") == "t1"
                for row in reviewed_assignment_at(snapshot, _tracklets(root), 0.3, 10.0)
            ))

    def test_material_continuity_terminal_and_team_actions_materialize_exact_owned_pairs(self) -> None:
        expectations = {
            "assign_team": {"payload": {"team_label": "B"}, "team_label": "B", "status": "unresolved"},
            "team_unknown": {"payload": {}, "team_label": "U", "status": "team_unknown"},
            "referee": {"payload": {}, "team_label": "U", "status": "referee"},
            "false_detection": {"payload": {}, "team_label": "U", "status": "false_detection"},
        }
        for action, expected in expectations.items():
            with self.subTest(action=action), _workspace() as root:
                _fixture(root)
                _add_natural_material_continuity_case(root)
                case = _materialize_natural_material_deferred_case(root)
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": case["candidate_subject_id"],
                        "action": action,
                        "source_ownership_digest": case["source_ownership_digest"],
                        **expected["payload"],
                    },
                )
                snapshot = finalize_reviewed_identity(root, _match())
                rows = [
                    row
                    for row in snapshot["segment_observation_assignments"]
                    if row.get("tracklet_id") == "material-tracklet"
                    and int(row.get("frame") or -1) == 100
                ]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["team_label"], expected["team_label"])
                self.assertEqual(rows[0]["identity_status"], expected["status"])
                self.assertIsNone(rows[0]["stable_anonymous_slot_id"])
                self.assertIsNone(rows[0]["stable_anonymous_entity_id"])
                self.assertFalse(rows[0]["eligible_for_player_stats"])

    def test_team_attribution_context_preserves_semantic_origin_separately_from_crop_kind(self) -> None:
        with _workspace() as root:
            _fixture(root)
            artifact = _load(root / "identity_roster_subject_review_shadow.json")
            artifact["cards"][0]["visual_evidence"] = {"kind": "team_attribution"}
            _write(root / "identity_roster_subject_review_shadow.json", artifact)
            with patch(
                "app.services.identity_reviewed_correction_context.build_reviewed_identity_progress",
                side_effect=AssertionError("whole correction context must not rebuild progress"),
                create=True,
            ):
                context = reviewed_correction_context(root, _match(), "s1")

            self.assertEqual(context["source_evidence_kind"], "team_attribution")
            self.assertEqual(context["visual_evidence"]["kind"], "identity_continuity")
            self.assertIsNone(context["legacy_suggestion"])

    def test_whole_subject_context_uses_authoritative_source_frame_bounds(self) -> None:
        with _workspace() as root:
            _fixture(root)
            context = reviewed_correction_context(root, _match(), "s1")

            self.assertEqual(context["frame_start"], 3)
            self.assertEqual(context["frame_end"], 9)

    def test_material_continuity_rejects_stale_exact_ownership_before_writing(self) -> None:
        with _workspace() as root:
            _fixture(root)
            unit = {
                "candidate_subject_id": "continuity:A12:100-101",
                "continuity_group_id": "continuity:A12:100-101",
                "scope_kind": "material_continuity",
                "effective_team_label": "A",
                "source_ownership_digest": "ownership-before",
                "owned_observations": [
                    {"tracklet_id": "tm1", "frame": 100},
                    {"tracklet_id": "tm1", "frame": 101},
                ],
            }
            changed_ownership = {**unit, "source_ownership_digest": "ownership-after"}

            with patch(
                "app.services.identity_reviewed_progress.build_reviewed_identity_progress",
                return_value={"_internal_review_units": [changed_ownership]},
            ), self.assertRaisesRegex(ValueError, "material_continuity_target_stale"):
                persist_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": unit["candidate_subject_id"],
                        "action": "assign_roster_player",
                        "player_id": "p1",
                        "source_ownership_digest": unit["source_ownership_digest"],
                    },
                    authorized_review_unit=unit,
                )

            self.assertFalse(
                (root / "reviewed_identity_material_continuity_decisions.json").exists()
            )

    def test_deferred_material_correction_recovers_exact_internal_ownership(self) -> None:
        """The browser-visible queue never supplies raw tracklet/frame pairs."""
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            snapshot = finalize_reviewed_identity(root, _match())
            public_progress = build_reviewed_identity_progress(root, _match())
            public_case = next(
                row
                for row in public_progress["next_cases"]
                if row.get("scope_kind") == "material_continuity"
            )
            self.assertNotIn("owned_observations", public_case)
            self.assertNotIn("detected_pairs", public_case)
            internal_progress = build_reviewed_identity_progress(
                root,
                _match(),
                include_internal_units=True,
            )
            internal_case = next(
                row
                for row in internal_progress["_internal_review_units"]
                if row.get("continuity_group_id")
                == public_case.get("continuity_group_id")
            )
            expected_owned = list(internal_case["owned_observations"])
            self.assertTrue(expected_owned)
            _write(root / "reviewed_identity_progress.json", public_progress)
            _write(
                root / "reviewed_identity_report.json",
                {"snapshot_digest": snapshot["semantic_digest"]},
            )
            payload = {
                "candidate_subject_id": public_case["candidate_subject_id"],
                "action": "assign_roster_player",
                "player_id": "p1",
                "source_ownership_digest": public_case["source_ownership_digest"],
                "defer_recompute": True,
            }

            gate = validate_deferred_review_action(root, _match(), payload)
            self.assertNotIn("owned_observations", gate["review_unit"])
            result = persist_reviewed_identity_correction(
                root,
                _match(),
                payload,
                authorized_review_unit=gate["review_unit"],
            )

            stored = _load(root / "reviewed_identity_material_continuity_decisions.json")[
                "decisions"
            ][0]
            self.assertNotIn("owned_observations", result["saved_decision"])
            self.assertEqual(
                result["saved_decision"]["owned_observations_count"], len(expected_owned)
            )
            self.assertEqual(stored["owned_observations"], expected_owned)
            stored_pairs = {
                (row["tracklet_id"], row["frame"])
                for row in stored["owned_observations"]
            }
            self.assertNotIn(("material-tracklet", 900), stored_pairs)

    def test_inline_split_can_divide_material_continuity_without_expanding_ownership(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            public_case = _materialize_natural_material_deferred_case(root)
            expected_pairs = {
                ("material-tracklet", frame)
                for frame in range(100, 600)
            }
            with patch(
                "app.services.identity_reviewed_review_source.render_mixed_review_evidence",
                return_value=set(),
            ):
                result = save_inline_temporal_split(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": public_case["candidate_subject_id"],
                        "continuity_group_id": public_case["continuity_group_id"],
                        "source_ownership_digest": public_case["source_ownership_digest"],
                        "resolution": "split",
                        "split_after_frames": [349],
                        "segment_assignments": [
                            {"action": "assign_roster_player", "player_id": "p1"},
                            {"action": "assign_team", "team_label": "A"},
                        ],
                    },
                )
            targets = [
                row for row in build_segment_review_document(root, _match())["targets"]
                if row.get("target_origin") == "operator_temporal_split"
            ]
            target_pairs = {
                (str(pair["tracklet_id"]), int(pair["frame"]))
                for target in targets
                for pair in target["owned_observations"]
            }

            self.assertEqual(result["saved_case"]["resolution_status"], "resolved")
            self.assertEqual(target_pairs, expected_pairs)
            self.assertNotIn(("material-tracklet", 900), target_pairs)

    def test_resolved_material_split_prevents_parent_recoalescing_and_preserves_snapshot_partition(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            raw_before = (root / "tracklets.json").read_bytes()
            parent = _materialize_natural_material_deferred_case(root)
            parent_pairs = {
                (str(row["tracklet_id"]), int(row["frame"]))
                for row in build_reviewed_identity_progress(
                    root, _match(), include_internal_units=True
                )["_internal_review_units"]
                if row.get("continuity_group_id") == parent["continuity_group_id"]
                for row in row["owned_observations"]
            }
            with patch(
                "app.services.identity_reviewed_review_source.render_mixed_review_evidence",
                return_value=set(),
            ):
                split = save_inline_temporal_split(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": parent["candidate_subject_id"],
                        "continuity_group_id": parent["continuity_group_id"],
                        "source_ownership_digest": parent["source_ownership_digest"],
                        "resolution": "split",
                        "split_after_frames": [349],
                        "segment_assignments": [
                            {"action": "assign_roster_player", "player_id": "p1"},
                            {"action": "assign_team", "team_label": "B"},
                        ],
                    },
                )

            progress_after = build_reviewed_identity_progress(
                root, _match(), include_internal_units=True
            )
            self.assertEqual(split["saved_case"]["resolution_status"], "resolved")
            self.assertFalse(any(
                row.get("continuity_group_id") == parent["continuity_group_id"]
                for row in progress_after["_internal_review_units"]
            ))
            self.assertFalse(any(
                row.get("candidate_subject_id") == parent["candidate_subject_id"]
                and row.get("scope_kind") == "material_continuity"
                for row in progress_after["next_cases"]
            ))
            self.assertEqual(
                progress_after["summary"]["material_continuity_decisions_remaining"], 0
            )

            split_target_ids = set(split["saved_case"]["segment_target_ids"])
            split_units = [
                row
                for row in progress_after["_internal_review_units"]
                if str(row.get("review_target_id") or "") in split_target_ids
            ]
            non_child_units = [
                row
                for row in progress_after["_internal_review_units"]
                if str(row.get("review_target_id") or "") not in split_target_ids
            ]
            self.assertFalse(any(
                {
                    (str(pair[0]), int(pair[1]))
                    for pair in row.get("detected_pairs") or []
                }
                & parent_pairs
                for row in non_child_units
            ))
            self.assertEqual(
                {
                    (str(pair[0]), int(pair[1]))
                    for row in split_units
                    for pair in row.get("detected_pairs") or []
                },
                parent_pairs,
            )
            underlying = next(
                (
                    row
                    for row in progress_after["_internal_review_units"]
                    if row.get("candidate_subject_id") == "material-subject"
                    and row.get("correction_scope") == "whole_subject"
                ),
                None,
            )
            self.assertIsNotNone(underlying)
            self.assertEqual(
                set(underlying["detected_pairs"]),
                {("material-tracklet", 900)},
            )
            self.assertFalse(any(
                row.get("candidate_subject_id") == "material-subject"
                for row in [
                    *progress_after["next_cases"],
                    *progress_after["optional_audit_cases"],
                ]
            ))

            children = [
                row for row in load_segment_review(root)["targets"]
                if row.get("review_target_id") in split["saved_case"]["segment_target_ids"]
            ]
            child_pair_sets = [
                {
                    (str(row["tracklet_id"]), int(row["frame"]))
                    for row in child["owned_observations"]
                }
                for child in children
            ]
            self.assertEqual(len(children), 2)
            self.assertFalse(child_pair_sets[0] & child_pair_sets[1])
            self.assertEqual(set().union(*child_pair_sets), parent_pairs)

            snapshot = finalize_reviewed_identity(root, _match())
            child_assignments = {
                int(row["frame"]): row
                for row in snapshot["segment_observation_assignments"]
                if row["tracklet_id"] == "material-tracklet"
            }
            self.assertEqual(child_assignments[100]["canonical_player_id"], "p1")
            self.assertEqual(child_assignments[100]["team_label"], "A")
            self.assertEqual(child_assignments[100]["identity_status"], "confirmed")
            self.assertTrue(child_assignments[100]["eligible_for_player_stats"])
            self.assertIsNone(child_assignments[599]["canonical_player_id"])
            self.assertEqual(child_assignments[599]["team_label"], "B")
            self.assertEqual(child_assignments[599]["identity_status"], "unresolved")
            self.assertFalse(child_assignments[599]["eligible_for_player_stats"])
            self.assertNotIn(900, child_assignments)
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)

    def test_unresolved_complex_material_mix_stays_a_workflow_blocker(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            parent = _materialize_natural_material_deferred_case(root)
            raw_before = (root / "tracklets.json").read_bytes()
            complex_case = save_inline_temporal_split(
                root,
                _match(),
                {
                    "candidate_subject_id": parent["candidate_subject_id"],
                    "continuity_group_id": parent["continuity_group_id"],
                    "source_ownership_digest": parent["source_ownership_digest"],
                    "resolution": "unresolved_complex_mix",
                },
            )

            progress = build_reviewed_identity_progress(root, _match())
            queue = build_mixed_review_queue(root, _match())
            state = derive_review_workflow_state(
                _workflow_evidence(normal=0, mixed=queue["summary"]["unresolved"])
            )
            self.assertEqual(complex_case["saved_case"]["resolution_status"], "unresolved_complex_mix")
            self.assertEqual(queue["summary"]["complex_unresolved"], 1)
            self.assertEqual(progress["mixed_players"]["summary"]["complex_unresolved"], 1)
            self.assertEqual(state["phase"], "mixed_players")
            self.assertIn("review_mixed_players", state["allowed_actions"])
            self.assertEqual(resolved_material_continuity_observation_pairs(root), set())
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)

    def test_incomplete_resolved_material_split_fails_closed_without_trimming_parent(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            raw_before = (root / "tracklets.json").read_bytes()
            parent = _materialize_natural_material_deferred_case(root)
            with patch(
                "app.services.identity_reviewed_review_source.render_mixed_review_evidence",
                return_value=set(),
            ):
                split = save_inline_temporal_split(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": parent["candidate_subject_id"],
                        "continuity_group_id": parent["continuity_group_id"],
                        "source_ownership_digest": parent["source_ownership_digest"],
                        "resolution": "split",
                        "split_after_frames": [349],
                        "segment_assignments": [
                            {"action": "assign_roster_player", "player_id": "p1"},
                            {"action": "assign_team", "team_label": "B"},
                        ],
                    },
                )
            decisions = load_segment_decisions(root)
            missing_target_id = split["saved_case"]["segment_target_ids"][0]
            decisions["decisions"] = [
                row
                for row in decisions["decisions"]
                if row.get("review_target_id") != missing_target_id
            ]
            _write(root / "reviewed_identity_segment_decisions.json", decisions)

            self.assertEqual(resolved_material_continuity_observation_pairs(root), set())
            progress = build_reviewed_identity_progress(
                root, _match(), include_internal_units=True
            )
            reappeared = next(
                row
                for row in progress["_internal_review_units"]
                if row.get("scope_kind") == "material_continuity"
                and row.get("continuity_group_id") == parent["continuity_group_id"]
            )
            self.assertEqual(
                set(reappeared["detected_pairs"]),
                {
                    ("material-tracklet", frame)
                    for frame in range(100, 600)
                },
            )
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)

    def test_resolved_material_split_to_complex_retires_old_child_targets(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            parent = _materialize_natural_material_deferred_case(root)
            raw_before = (root / "tracklets.json").read_bytes()
            with patch(
                "app.services.identity_reviewed_review_source.render_mixed_review_evidence",
                return_value=set(),
            ):
                resolved = save_inline_temporal_split(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": parent["candidate_subject_id"],
                        "continuity_group_id": parent["continuity_group_id"],
                        "source_ownership_digest": parent["source_ownership_digest"],
                        "resolution": "split",
                        "split_after_frames": [349],
                        "segment_assignments": [
                            {"action": "assign_roster_player", "player_id": "p1"},
                            {"action": "assign_team", "team_label": "B"},
                        ],
                    },
                )
            old_target_ids = set(resolved["saved_case"]["segment_target_ids"])
            self.assertTrue(old_target_ids)
            self.assertTrue(old_target_ids <= {
                str(row["review_target_id"])
                for row in load_segment_decisions(root)["decisions"]
            })

            transitioned = save_inline_temporal_split(
                root,
                _match(),
                {
                    "candidate_subject_id": parent["candidate_subject_id"],
                    "continuity_group_id": parent["continuity_group_id"],
                    "source_ownership_digest": parent["source_ownership_digest"],
                    "existing_split_semantic_digest": resolved["saved_case"]["split_semantic_digest"],
                    "resolution": "unresolved_complex_mix",
                },
            )

            self.assertEqual(transitioned["saved_case"]["resolution_status"], "unresolved_complex_mix")
            self.assertFalse(old_target_ids & {
                str(row["review_target_id"])
                for row in load_segment_decisions(root)["decisions"]
            })
            self.assertFalse(old_target_ids & {
                str(row.get("review_target_id") or "")
                for row in load_segment_review(root)["targets"]
            })
            stored_case = next(
                row for row in load_mixed_player_cases(root)["cases"]
                if row.get("case_id") == resolved["saved_case"]["case_id"]
            )
            self.assertEqual(stored_case["segment_target_ids"], [])
            self.assertEqual(build_mixed_review_queue(root, _match())["summary"]["complex_unresolved"], 1)
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)

    def test_deferred_material_identical_retry_uses_material_decision_store(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            public_case = _materialize_natural_material_deferred_case(root)
            payload = {
                "candidate_subject_id": public_case["candidate_subject_id"],
                "action": "assign_roster_player",
                "player_id": "p1",
                "source_ownership_digest": public_case["source_ownership_digest"],
                "defer_recompute": True,
            }

            first_gate = validate_deferred_review_action(root, _match(), payload)
            self.assertFalse(first_gate["idempotent_replay"])
            persist_reviewed_identity_correction(
                root,
                _match(),
                payload,
                authorized_review_unit=first_gate["review_unit"],
            )

            retry = validate_deferred_review_action(root, _match(), payload)
            self.assertTrue(retry["idempotent_replay"])
            decisions = _load(
                root / "reviewed_identity_material_continuity_decisions.json"
            )["decisions"]
            matching = [
                row
                for row in decisions
                if row["continuity_group_id"] == public_case["continuity_group_id"]
            ]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["action"], "assign_roster_player")
            self.assertEqual(matching[0]["player_id"], "p1")

    def test_deferred_material_conflicting_retry_fails_closed(self) -> None:
        scenarios = (
            (
                {"action": "assign_roster_player", "player_id": "p1"},
                {"action": "unresolved"},
            ),
            (
                {"action": "unresolved"},
                {"action": "assign_roster_player", "player_id": "p1"},
            ),
        )
        for first_action, second_action in scenarios:
            with (
                self.subTest(first_action=first_action, second_action=second_action),
                _workspace() as root,
            ):
                _fixture(root)
                _add_natural_material_continuity_case(root)
                public_case = _materialize_natural_material_deferred_case(root)
                common = {
                    "candidate_subject_id": public_case["candidate_subject_id"],
                    "source_ownership_digest": public_case["source_ownership_digest"],
                    "defer_recompute": True,
                }
                first_payload = {**common, **first_action}
                first_gate = validate_deferred_review_action(root, _match(), first_payload)
                persist_reviewed_identity_correction(
                    root,
                    _match(),
                    first_payload,
                    authorized_review_unit=first_gate["review_unit"],
                )

                with self.assertRaises(DeferredReviewActionError) as raised:
                    validate_deferred_review_action(
                        root,
                        _match(),
                        {**common, **second_action},
                    )
                self.assertEqual(raised.exception.code, "review_unit_already_decided")
                stored = _load(
                    root / "reviewed_identity_material_continuity_decisions.json"
                )["decisions"]
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0]["action"], first_action["action"])

    def test_stale_material_decision_does_not_make_changed_case_idempotent(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            original_case = _materialize_natural_material_deferred_case(root)
            original_payload = {
                "candidate_subject_id": original_case["candidate_subject_id"],
                "action": "assign_roster_player",
                "player_id": "p1",
                "source_ownership_digest": original_case["source_ownership_digest"],
                "defer_recompute": True,
            }
            gate = validate_deferred_review_action(root, _match(), original_payload)
            persist_reviewed_identity_correction(
                root,
                _match(),
                original_payload,
                authorized_review_unit=gate["review_unit"],
            )

            tracklets = _load(root / "tracklets.json")
            material_tracklet = next(
                row
                for row in tracklets["tracklets"]
                if row["tracklet_id"] == "material-tracklet"
            )
            material_tracklet["positions_m"] = [
                position
                for position in material_tracklet["positions_m"]
                if position["frame"] != 300
            ]
            _write(root / "tracklets.json", tracklets)

            changed_case = _materialize_natural_material_deferred_case(root)
            self.assertNotEqual(
                changed_case["source_ownership_digest"],
                original_case["source_ownership_digest"],
            )
            changed_payload = {
                **original_payload,
                "source_ownership_digest": changed_case["source_ownership_digest"],
            }
            result = validate_deferred_review_action(root, _match(), changed_payload)
            self.assertFalse(result["idempotent_replay"])

    def test_stale_persisted_unresolved_material_decision_does_not_hide_new_case(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _add_natural_material_continuity_case(root)
            finalize_reviewed_identity(root, _match())
            progress = build_reviewed_identity_progress(
                root,
                _match(),
                include_internal_units=True,
            )
            original = next(
                row
                for row in progress["_internal_review_units"]
                if row.get("scope_kind") == "material_continuity"
            )
            persist_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": original["candidate_subject_id"],
                    "action": "unresolved",
                    "source_ownership_digest": original["source_ownership_digest"],
                },
                authorized_review_unit=original,
            )
            stored = _load(root / "reviewed_identity_material_continuity_decisions.json")[
                "decisions"
            ][0]
            self.assertEqual(stored["action"], "unresolved")
            tracklets = _load(root / "tracklets.json")
            material_tracklet = next(
                row
                for row in tracklets["tracklets"]
                if row["tracklet_id"] == "material-tracklet"
            )
            material_tracklet["positions_m"].append(
                {
                    "frame": 600,
                    "status": "detected",
                    "pitch_m": [5.0, 2.0],
                    "bbox_xyxy": [10, 10, 20, 30],
                }
            )
            _write(root / "tracklets.json", tracklets)

            changed_progress = build_reviewed_identity_progress(
                root,
                _match(),
                include_internal_units=True,
            )
            changed = next(
                row
                for row in changed_progress["_internal_review_units"]
                if row.get("scope_kind") == "material_continuity"
            )

            self.assertNotEqual(
                changed["source_ownership_digest"],
                original["source_ownership_digest"],
            )
            self.assertIsNone(changed["current_decision"])
            self.assertEqual(
                changed["current_resolution_status"], "pending_material_continuity_review")

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

    def test_manual_team_corrections_allow_cross_team_assignment_but_slots_remain_scoped(self) -> None:
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
            cross_team = save_reviewed_identity_correction(
                root, _match(), {"candidate_subject_id": "s1", "action": "assign_team", "team_label": "B"}
            )
            self.assertEqual(cross_team["saved_decision"]["team_label"], "B")

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

    def test_team_attribution_persistence_keeps_manual_roster_override_available(self) -> None:
        allowed = (
            {"action": "assign_team", "team_label": "A"},
            {"action": "assign_team", "team_label": "B"},
            {"action": "referee"},
            {"action": "false_detection"},
            {"action": "team_unknown"},
            {"action": "unresolved"},
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
                self.assertEqual(
                    saved["saved_decision"].get("action")
                    or ("mixed_players" if saved["saved_decision"].get("original_issue") == "mixed_players" else None),
                    action_payload["action"],
                )
    def test_synchronous_correction_honors_team_attribution_roster_override(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _enable_materialized_candidate_context(root)
            result = persist_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "s1", "action": "assign_roster_player", "player_id": "p1"},
                authorized_review_unit={"visual_evidence": {"kind": "team_attribution"}},
            )
            self.assertEqual(result["saved_decision"]["action"], "assign_roster_player")

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

    def test_materialized_single_and_unknown_team_semantics_allow_explicit_team_corrections(self) -> None:
        cases = (
            ({"A"}, "A", True),
            ({"A"}, "B", True),
            ({"B"}, "B", True),
            ({"B"}, "A", True),
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


def _add_natural_material_continuity_case(root: Path) -> None:
    """Create one real 20-second material unit plus one outside observation."""
    tracklets = _load(root / "tracklets.json")
    candidates = _load(root / "identity_candidate_shadow.json")
    cards = _load(root / "identity_roster_subject_review_shadow.json")
    positions = [
        {
            "frame": frame,
            "status": "detected",
            "pitch_m": [float(frame - 100) / 100.0, 2.0],
            "bbox_xyxy": [10, 10, 20, 30],
        }
        for frame in range(100, 600)
    ]
    positions.append(
        {
            "frame": 900,
            "status": "detected",
            "pitch_m": [12.0, 2.0],
            "bbox_xyxy": [30, 10, 40, 30],
        }
    )
    tracklets["tracklets"].append(
        {
            "tracklet_id": "material-tracklet",
            "team_label": "A",
            "team_id": "ta",
            "positions_m": positions,
        }
    )
    candidates["subjects"].append(
        {
            "candidate_subject_id": "material-subject",
            "tracklet_ids": ["material-tracklet"],
            "team_label": "A",
            "production_player_ids": ["A12"],
            "production_subject_ids": ["slot-A12"],
        }
    )
    cards["cards"].append(
        {
            **_card("material-subject", "A", "card-material", "p1"),
            "visual_evidence": {
                "anchor_crops": [
                    {
                        "anchor_crop_id": "material-100",
                        "artifact": "anchor_crops/material-100.jpg",
                        "frame": 100,
                        "tracklet_id": "material-tracklet",
                        "bbox_xyxy": [10, 10, 20, 30],
                    },
                    {
                        "anchor_crop_id": "material-599",
                        "artifact": "anchor_crops/material-599.jpg",
                        "frame": 599,
                        "tracklet_id": "material-tracklet",
                        "bbox_xyxy": [10, 10, 20, 30],
                    },
                ]
            },
        }
    )
    _write(root / "tracklets.json", tracklets)
    _write(root / "identity_candidate_shadow.json", candidates)
    _write(root / "identity_roster_subject_review_shadow.json", cards)


def _materialize_natural_material_deferred_case(root: Path) -> dict:
    snapshot = finalize_reviewed_identity(root, _match())
    progress = build_reviewed_identity_progress(root, _match())
    public_case = next(
        row
        for row in progress["next_cases"]
        if row.get("scope_kind") == "material_continuity"
    )
    _write(root / "reviewed_identity_progress.json", progress)
    _write(
        root / "reviewed_identity_report.json",
        {"snapshot_digest": snapshot["semantic_digest"]},
    )
    return public_case


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


def _workflow_evidence(*, normal: int, mixed: int) -> dict:
    return {
        "match_id": "m1",
        "analysis_completed": True,
        "initial_audit": {"complete": True},
        "issues": {
            "blocking": normal + mixed,
            "normal_blocking": normal,
            "mixed_blocking": mixed,
        },
        "freshness": {"review_progress_current": True},
        "render": {"status": "missing"},
    }


def _whole_subject_hot_progress() -> dict:
    """Minimal authoritative progress input for the public hot-state route."""
    unit = {
        "candidate_subject_id": "s1",
        "scope_kind": "whole_subject",
        "source_team_label": "A",
        "effective_team_label": "A",
        "tracklet_ids": ["t1", "t1b"],
        "detected_pairs": [("t1", 3), ("t1", 4), ("t1b", 8), ("t1b", 9)],
        "detected_observation_count": 4,
        "detected_frame_count": 4,
        "detected_time_sec": 0.16,
        "frame_start": 3,
        "frame_end": 9,
        "current_resolution_status": "pending_high_priority",
        "priority": "high",
        "operator_actionable": True,
        "has_operator_visual_evidence": True,
        "visual_evidence": {"kind": "identity_continuity", "anchor_crops": []},
    }
    return {
        "schema_version": "2.8.0",
        "status": "ready",
        "source_snapshot_digest": "fixture-snapshot",
        "next_cases": [dict(unit)],
        "optional_audit_cases": [],
        "summary": {},
        "deferred_correction_context": {
            "schema_version": "1.0.0",
            "status": "ready",
            "detected_team_evidence_status": "ready",
            "subjects": [{
                "candidate_subject_id": "s1",
                "source_team_label": "A",
                "detected_team_labels": ["A"],
                "detected_frames": [3, 4, 8, 9],
            }, {
                "candidate_subject_id": "s2",
                "source_team_label": "B",
                "detected_team_labels": ["B"],
                "detected_frames": [20],
            }],
        },
        "_internal_review_units": [unit],
        "_projection_inputs": {
            "match_id": "m1",
            "coverage": {},
            "pair_index": [],
            "observed_pairs": [],
            "technical_diagnostics": {},
            "mixed_players": {},
            "deferred_correction_context": {},
        },
    }


def _tracklets(root: Path) -> dict[str, dict]:
    return {
        row["tracklet_id"]: row
        for row in json.loads((root / "tracklets.json").read_text(encoding="utf-8"))["tracklets"]
    }


def _subject_row(snapshot: dict, subject_id: str) -> dict:
    return next(row for row in snapshot["tracklet_assignments"] if row["candidate_subject_id"] == subject_id)


def _assert_hot_context_equivalent(
    test: unittest.TestCase,
    legacy: dict,
    hot: dict,
) -> None:
    """Compare the complete operator contract, not implementation internals."""
    fields = (
        "scope_kind",
        "source_team_label",
        "effective_team_label",
        "roster_options",
        "slot_options",
        "current_decision",
        "source_ownership_digest",
        "frame_ranges",
        "frame_start",
        "frame_end",
        "detected_observation_count",
        "action_capabilities",
        "source_evidence_kind",
        "temporal_split",
        "legacy_suggestion",
        "visual_evidence",
    )
    for field in fields:
        with test.subTest(field=field, scope=legacy.get("scope_kind")):
            test.assertEqual(hot.get(field), legacy.get(field))


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
