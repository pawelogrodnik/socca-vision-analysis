from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.responses import FileResponse
from fastapi import HTTPException, Response

from app.main import (
    get_artifact,
    get_match_reviewed_correction_context,
    get_match_reviewed_identity_mixed_boundary_refinement,
    get_match_reviewed_identity_progress,
    get_match_reviewed_identity_temporal_split_refinement,
    post_match_reviewed_identity_mixed_resolution,
    post_match_reviewed_identity_temporal_split,
)
from app.services.identity_reviewed_action_scope import (
    ReviewedIdentityActionScopeError,
    reviewed_identity_action_capabilities,
    validate_review_unit_action_scope,
)
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_corrections import persist_reviewed_identity_correction
from app.services.identity_reviewed_decision_audit import AUDIT_FILENAME
from app.services.identity_reviewed_mixed_resolution import (
    MixedPlayerTargetError,
    save_inline_temporal_split,
    save_mixed_player_resolution,
)
from app.services.identity_reviewed_mixed_topology import MixedTemporalTopologyError
from app.services.identity_reviewed_mixed_store import (
    _source_team_for_observations,
    build_focused_mixed_review_case,
    current_mixed_subject_digest,
    load_mixed_player_cases,
    materialize_mixed_review_artifact,
    operator_concurrent_targets_for_marker,
    operator_mixed_targets,
    operator_targets_for_mixed_marker,
    _materialize_mixed_review_case,
    save_mixed_case_document,
    unresolved_mixed_observation_assignments,
)
from app.services.identity_reviewed_slot_review import load_reviewed_slot_assignments
from app.services.identity_reviewed_slot_registry import build_reviewed_slot_registry
from app.services.identity_reviewed_correction_context import reviewed_correction_context
from app.services.identity_reviewed_snapshot import finalize_reviewed_identity
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_reviewed_review_source import (
    build_review_source_boundary_refinement,
    build_concurrent_lane_boundary_refinement,
    resolve_review_source,
    source_case_id,
    source_storage_payload,
)
from app.services.identity_reviewed_mixed_store import (
    build_mixed_boundary_refinement,
    build_mixed_review_queue,
)
from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.identity_reviewed_hot_state import (
    FILENAME,
    hot_context,
    load_existing_fresh_hot_state,
    load_or_rebuild_review_hot_state,
)
from app.services.identity_reviewed_segments import (
    build_segment_review_document,
    load_segment_decisions,
    load_segment_review,
    segment_observation_assignments,
)
from app.services.review_workflow_state import derive_review_workflow_state
from app.services.review_workflow_orchestrator import finalize_review_for_qa
from app.services.review_workflow_state import WorkflowActionError


class ReviewedIdentityMixedPlayersTests(unittest.TestCase):
    def test_child_target_team_keeps_single_known_team_despite_neutral_observations(self) -> None:
        self.assertEqual(
            _source_team_for_observations(
                [
                    {"team_label": "A"},
                    {"team_label": "A"},
                    *[{"team_label": "U"} for _ in range(8)],
                ]
            ),
            "A",
        )
        self.assertEqual(
            _source_team_for_observations(
                [{"team_label": "A"}, {"team_label": "B"}, {"team_label": "U"}]
            ),
            "U",
        )

    def test_concurrent_lane_resolution_reaches_snapshot_and_reviewed_stats(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            review_case = build_mixed_review_queue(root, match)["cases"][0]
            lanes = review_case["concurrent_resolution"]["lanes"]

            result = save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "case_id": marker.get("case_id"),
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "concurrent_lanes",
                    "lane_resolutions": [
                        _direct_lane(lanes[0], "player-a"),
                        _direct_lane(lanes[1], "player-b"),
                    ],
                },
            )
            snapshot = finalize_reviewed_identity(root, match)
            rows = snapshot["segment_observation_assignments"]
            with patch(
                "app.services.identity_reviewed_stats.read_match_video_metadata",
                return_value={
                    "fps": 1.0,
                    "frame_count": 20,
                    "duration_sec": 20.0,
                    "source": "test",
                    "filename": "test.mp4",
                },
            ):
                stats = build_reviewed_stats(root, snapshot, match)

            self.assertEqual(result["saved_case"]["resolution_model"], "concurrent_lanes")
            self.assertEqual(
                {(row["tracklet_id"], row["canonical_player_id"]) for row in rows},
                {("t1", "player-a"), ("t2", "player-b")},
            )
            players = {
                row["player_id"]: row
                for row in stats["reviewed_player_stats.json"]["players"]
            }
            self.assertEqual(players["player-a"]["confirmed_detected_observations"], 9)
            self.assertEqual(players["player-b"]["confirmed_detected_observations"], 4)
            self.assertEqual(
                {row["player_id"] for row in stats["reviewed_player_timeline.json"]["players"]},
                {"player-a", "player-b"},
            )
            self.assertEqual(
                {row["player_id"] for row in stats["reviewed_player_heatmaps.json"]["heatmaps"]},
                {"player-a", "player-b"},
            )

    def test_same_player_on_overlapping_lanes_uses_frame_uniqueness_guard(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]
            save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "concurrent_lanes",
                    "lane_resolutions": [
                        _direct_lane(lanes[0], "player-a"),
                        _direct_lane(lanes[1], "player-a"),
                    ],
                },
            )

            snapshot = finalize_reviewed_identity(root, match)
            diagnostics = snapshot["frame_uniqueness_diagnostics"]

            self.assertEqual(diagnostics["frames_with_duplicate_canonical_player_claims"], 4)
            self.assertEqual(diagnostics["demoted_canonical_player_observations"], 8)
            self.assertTrue(all(
                row["eligible_for_player_stats"] is False
                for row in snapshot["observation_demotions"]
            ))

    def test_concurrent_lane_save_builds_and_projects_once(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]
            from app.services.identity_reviewed_mixed_resolution import (
                build_segment_review_document as build_real,
                project_segment_decisions_onto_materialized_review as project_real,
            )

            with (
                patch(
                    "app.services.identity_reviewed_mixed_resolution.build_segment_review_document",
                    wraps=build_real,
                ) as build,
                patch(
                    "app.services.identity_reviewed_mixed_resolution.project_segment_decisions_onto_materialized_review",
                    wraps=project_real,
                ) as project,
            ):
                save_mixed_player_resolution(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "concurrent_lanes",
                        "lane_resolutions": [
                            _direct_lane(lanes[0], "player-a"),
                            _direct_lane(lanes[1], "player-b"),
                        ],
                    },
                )

            self.assertEqual(build.call_count, 1)
            self.assertEqual(project.call_count, 1)

    def test_concurrent_save_derives_only_current_parent_before_one_global_review_build(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]

            # The save path must not call its old global helper to derive the
            # current parent. The one canonical segment-review build still
            # derives the full durable topology through its own module.
            with patch(
                "app.services.identity_reviewed_mixed_resolution.operator_mixed_targets",
                side_effect=AssertionError("current concurrent parent must not enumerate sibling cases"),
            ):
                result = save_mixed_player_resolution(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "concurrent_lanes",
                        "lane_resolutions": [
                            _direct_lane(lanes[0], "player-a"),
                            _direct_lane(lanes[1], "player-b"),
                        ],
                    },
                )

            self.assertEqual(result["saved_case"]["resolution_model"], "concurrent_lanes")
            self.assertIn("segment_review_operator_targets_ms", result["performance"])

    def test_focused_concurrent_targets_match_global_derivation_for_direct_and_split_lanes(self) -> None:
        def assert_equivalent(lane_resolutions: list[dict]) -> None:
            with _workspace() as root:
                match = _fixture(root)
                _make_concurrent(root)
                marker = _classify(root, match)
                result = save_mixed_player_resolution(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "concurrent_lanes",
                        "lane_resolutions": lane_resolutions,
                    },
                )
                case = result["saved_case"]
                case_id = str(case["case_id"])
                global_targets = [
                    target
                    for target in operator_mixed_targets(root)
                    if str(target.get("split_parent_case_id") or "") == case_id
                ]

                self.assertEqual(
                    operator_concurrent_targets_for_marker(root, case),
                    global_targets,
                )

        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]
            direct = [_direct_lane(lanes[0], "player-a"), _direct_lane(lanes[1], "player-b")]
            split = [
                {
                    "lane_id": lanes[0]["lane_id"],
                    "lane_source_digest": lanes[0]["source_ownership_digest"],
                    "resolution": "temporal_split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_roster_player", "player_id": "player-a"},
                        {"action": "assign_team", "team_label": "A"},
                    ],
                },
                _direct_lane(lanes[1], "player-b"),
            ]

        assert_equivalent(direct)
        assert_equivalent(split)

    def test_concurrent_lane_save_rolls_back_every_partial_write(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]
            before = _path_snapshots(_split_state_paths(root))

            with (
                patch(
                    "app.services.identity_reviewed_mixed_resolution.project_segment_decisions_onto_materialized_review",
                    side_effect=RuntimeError("projection failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "projection failed"),
            ):
                save_mixed_player_resolution(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "concurrent_lanes",
                        "lane_resolutions": [
                            _direct_lane(lanes[0], "player-a"),
                            _direct_lane(lanes[1], "player-b"),
                        ],
                    },
                )

            self.assertEqual(_path_snapshots(_split_state_paths(root)), before)

    def test_concurrent_lane_local_split_preserves_exact_parent_union(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]
            save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "concurrent_lanes",
                    "lane_resolutions": [
                        {
                            "lane_id": lanes[0]["lane_id"],
                            "lane_source_digest": lanes[0]["source_ownership_digest"],
                            "resolution": "temporal_split",
                            "split_after_frames": [4],
                            "segment_assignments": [
                                {"action": "assign_roster_player", "player_id": "player-a"},
                                {"action": "assign_team", "team_label": "A"},
                            ],
                        },
                        _direct_lane(lanes[1], "player-b"),
                    ],
                },
            )
            targets = [
                row for row in load_segment_review(root)["targets"]
                if str(row.get("target_origin") or "").startswith("operator_concurrent_lane")
            ]
            owned = [
                {(row["tracklet_id"], row["frame"]) for row in target["owned_observations"]}
                for target in targets
            ]

            self.assertEqual(len(targets), 3)
            self.assertEqual(set().union(*owned), {
                ("t1", frame) for frame in range(1, 10)
            } | {("t2", frame) for frame in range(4, 8)})
            self.assertEqual(sum(map(len, owned)), len(set().union(*owned)))
            target_tracklets = [
                {row["tracklet_id"] for row in target["owned_observations"]}
                for target in targets
            ]
            self.assertEqual(target_tracklets.count({"t1"}), 2)
            self.assertEqual(target_tracklets.count({"t2"}), 1)

    def test_stale_concurrent_lane_set_has_zero_persistence(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]
            before = _path_snapshots(_split_state_paths(root))

            with self.assertRaisesRegex(ValueError, "concurrent_lane_set_stale"):
                save_mixed_player_resolution(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "concurrent_lanes",
                        "lane_resolutions": [_direct_lane(lanes[0], "player-a")],
                    },
                )

            self.assertEqual(_path_snapshots(_split_state_paths(root)), before)

    def test_both_http_write_paths_reject_incomplete_lane_sets_before_mutation(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]
            before = _path_snapshots(_split_state_paths(root))
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.main.build_compact_review_workflow_state",
                    return_value={"phase": "mixed_players", "allowed_actions": ["review_mixed_players"]},
                ),
                patch("app.main.invalidate_review_hot_state") as invalidate,
                self.assertRaises(HTTPException) as raised,
            ):
                post_match_reviewed_identity_mixed_resolution(
                    "m1",
                    Response(),
                    {
                        "candidate_subject_id": "subject-mixed",
                        "case_id": marker.get("case_id") or marker["candidate_subject_id"],
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "concurrent_lanes",
                        "lane_resolutions": [_direct_lane(lanes[0], "player-a")],
                    },
                )
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.detail["code"], "concurrent_lane_set_stale")
            invalidate.assert_not_called()
            self.assertEqual(_path_snapshots(_split_state_paths(root)), before)

        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            context = reviewed_correction_context(root, match, "subject-mixed")
            lanes = context["concurrent_resolution"]["lanes"]
            before = _path_snapshots(_split_state_paths(root))
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.main.get_review_workflow_state",
                    return_value={"phase": "exceptions", "allowed_actions": ["review_identity_issue"]},
                ),
                patch("app.main.load_or_rebuild_review_hot_state") as hot_state,
                patch("app.main.invalidate_review_hot_state") as invalidate,
                self.assertRaises(HTTPException) as raised,
            ):
                post_match_reviewed_identity_temporal_split(
                    "m1",
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_ownership_digest": context["source_ownership_digest"],
                        "resolution": "concurrent_lanes",
                        "lane_resolutions": [_direct_lane(lanes[0], "player-a")],
                    },
                )
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.detail["code"], "concurrent_lane_set_stale")
            hot_state.assert_not_called()
            invalidate.assert_not_called()
            self.assertEqual(_path_snapshots(_split_state_paths(root)), before)

    def test_historical_unsafe_split_is_read_only_until_explicit_lane_repair(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            source = resolve_review_source(
                root,
                match,
                candidate_subject_id="subject-mixed",
                source_ownership_digest=current_mixed_subject_digest(
                    root,
                    "subject-mixed",
                ),
            )
            case_id = "legacy-inline-a10-case"
            save_mixed_case_document(
                root,
                {
                    "schema_version": "2.0.0",
                    "cases": [{
                        "case_id": case_id,
                        "candidate_subject_id": "subject-mixed",
                        "original_issue": "inline_temporal_split",
                        "source": source_storage_payload(source),
                        "source_subject_digest": source["source_ownership_digest"],
                        "resolution_status": "resolved",
                        "split_after_frames": [3],
                        "split_semantic_digest": "historical-unsafe-split",
                        "segment_assignments": [
                            {"action": "assign_team", "team_label": "A"},
                            {"action": "assign_team", "team_label": "B"},
                        ],
                        "segment_target_ids": ["historical-a", "historical-b"],
                    }],
                },
            )
            before = (root / "reviewed_identity_mixed_players.json").read_bytes()

            context = reviewed_correction_context(root, match, "subject-mixed")

            # The production correction API reads the durable hot document,
            # not the direct service above.  It must preserve the historical
            # parent case id and expose repair metadata without mutating it.
            _write(root / "reviewed_identity_snapshot.json", {})
            load_or_rebuild_review_hot_state(root, match)
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
            ):
                hot_response = get_match_reviewed_correction_context(
                    "m1",
                    Response(),
                    candidate_subject_id="subject-mixed",
                    review_target_id=None,
                )

            self.assertEqual(
                (root / "reviewed_identity_mixed_players.json").read_bytes(),
                before,
            )
            self.assertTrue(context["historical_concurrent_repair"])
            self.assertEqual(context["temporal_topology"]["kind"], "concurrent")
            self.assertEqual(
                context["concurrent_resolution"]["parent_case_id"],
                case_id,
            )
            self.assertEqual(hot_response["temporal_topology"]["kind"], "concurrent")
            self.assertTrue(hot_response["historical_concurrent_repair"])
            self.assertIsNotNone(hot_response["concurrent_resolution"])
            self.assertIsNotNone(hot_response["temporal_split"])
            self.assertEqual(
                hot_response["concurrent_resolution"]["parent_case_id"],
                case_id,
            )
            self.assertEqual(
                (root / "reviewed_identity_mixed_players.json").read_bytes(),
                before,
            )
            # The public hot response is the actual client contract used to
            # construct an explicit, atomic historical repair.
            lanes = hot_response["concurrent_resolution"]["lanes"]
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": source["source_ownership_digest"],
                "existing_split_semantic_digest": "historical-unsafe-split",
                "resolution": "concurrent_lanes",
                "lane_resolutions": [
                    _direct_lane(lanes[0], "player-a"),
                    _direct_lane(lanes[1], "player-b"),
                ],
            }
            # Exercise the public write path too: its preflight must derive
            # lane ids from the legacy durable parent, not a new canonical id.
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.main.get_review_workflow_state",
                    return_value={
                        "phase": "ready_to_finalize",
                        "allowed_actions": ["review_identity_issue"],
                    },
                ),
            ):
                result = post_match_reviewed_identity_temporal_split("m1", payload)

            self.assertEqual(
                result["saved_case"]["resolution_model"],
                "concurrent_lanes",
            )
            self.assertEqual(result["saved_case"]["case_id"], case_id)
            self.assertEqual(result["saved_case"]["split_after_frames"], [])
            self.assertEqual(len(result["saved_case"]["segment_target_ids"]), 2)

    def test_full_and_focused_reads_share_concurrent_topology_and_lane_evidence(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)

            full_case = build_mixed_review_queue(root, match)["cases"][0]
            focused = build_focused_mixed_review_case(
                root,
                match,
                str(marker.get("case_id") or marker["candidate_subject_id"]),
            )
            correction_context = reviewed_correction_context(
                root,
                match,
                "subject-mixed",
            )

            self.assertEqual(focused["status"], "current_blocking")
            self.assertEqual(full_case["temporal_topology"], focused["case"]["temporal_topology"])
            self.assertEqual(full_case["temporal_topology"]["kind"], "concurrent")
            self.assertFalse(full_case["temporal_topology"]["simple_split_allowed"])
            self.assertEqual(
                correction_context["temporal_topology"],
                full_case["temporal_topology"],
            )
            self.assertEqual(
                focused["case"]["concurrent_resolution"],
                full_case["concurrent_resolution"],
            )
            self.assertEqual(
                correction_context["concurrent_resolution"]["parent_source_digest"],
                full_case["concurrent_resolution"]["parent_source_digest"],
            )
            self.assertTrue(all(
                1 <= len(lane["evidence"]["anchor_crops"]) <= 5
                for lane in full_case["concurrent_resolution"]["lanes"]
            ))
            self.assertEqual(
                full_case["temporal_topology"]["overlap_ranges"],
                [{"frame_start": 4, "frame_end": 7, "tracklet_ids": ["t1", "t2"]}],
            )
            self.assertEqual(
                {crop["tracklet_id"] for crop in full_case["temporal_evidence"]["anchor_crops"]},
                {"t1", "t2"},
            )
            self.assertTrue(full_case["action_capabilities"]["assign_existing_slot"]["allowed"])
            self.assertTrue(full_case["action_capabilities"]["create_new_stable_player"]["allowed"])
            self.assertTrue(all(
                lane["split_allowed"]
                for lane in full_case["concurrent_resolution"]["lanes"]
            ))

    def test_hot_correction_context_http_exposes_the_concurrent_contract_without_rebuild(self) -> None:
        """The production context route projects concurrent hot-state fields.

        This intentionally calls the route rather than the direct correction
        service: correction context is served from fresh hot state in normal
        operation and must remain warm/read-only.
        """
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            _write(root / "reviewed_identity_snapshot.json", {})
            direct = reviewed_correction_context(root, match, "subject-mixed")
            load_or_rebuild_review_hot_state(root, match)
            persisted = load_existing_fresh_hot_state(root, match)
            assert persisted is not None
            self.assertEqual(
                hot_context(persisted, "subject-mixed")["temporal_topology"],
                direct["temporal_topology"],
            )
            guarded_paths = [
                root / name
                for name in (
                    "reviewed_identity_mixed_players.json",
                    "reviewed_identity_segment_review.json",
                    "reviewed_identity_segment_decisions.json",
                    "reviewed_identity_slot_assignments.json",
                    "reviewed_identity_recompute_required.json",
                )
            ]
            before = _path_snapshots(guarded_paths)
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
                    side_effect=AssertionError("warm correction context rebuilt Review"),
                ),
            ):
                response = get_match_reviewed_correction_context(
                    "m1",
                    Response(),
                    candidate_subject_id="subject-mixed",
                    review_target_id=None,
                )

            self.assertEqual(response["temporal_topology"], direct["temporal_topology"])
            self.assertEqual(
                response["concurrent_resolution"]["parent_case_id"],
                direct["concurrent_resolution"]["parent_case_id"],
            )
            self.assertEqual(
                response["concurrent_resolution"]["parent_source_digest"],
                direct["concurrent_resolution"]["parent_source_digest"],
            )
            self.assertEqual(
                [
                    (
                        lane["lane_id"],
                        lane["source_ownership_digest"],
                        lane["frame_start"],
                        lane["frame_end"],
                        lane["observation_count"],
                        lane["split_allowed"],
                    )
                    for lane in response["concurrent_resolution"]["lanes"]
                ],
                [
                    (
                        lane["lane_id"],
                        lane["source_ownership_digest"],
                        lane["frame_start"],
                        lane["frame_end"],
                        lane["observation_count"],
                        lane["split_allowed"],
                    )
                    for lane in direct["concurrent_resolution"]["lanes"]
                ],
            )
            self.assertTrue(response["action_capabilities"]["assign_existing_slot"]["allowed"])
            self.assertFalse(response["historical_concurrent_repair"])
            self.assertTrue(all(
                lane["split_allowed"]
                and 1 <= len(lane["evidence"]["anchor_crops"]) <= 5
                and "owned_observations" not in lane
                and "observations" not in lane
                for lane in response["concurrent_resolution"]["lanes"]
            ))
            self.assertEqual(_path_snapshots(guarded_paths), before)

    def test_hot_correction_context_keeps_serial_sources_lightweight(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _write(root / "reviewed_identity_snapshot.json", {})
            load_or_rebuild_review_hot_state(root, match)
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
            ):
                response = get_match_reviewed_correction_context(
                    "m1",
                    Response(),
                    candidate_subject_id="subject-mixed",
                    review_target_id=None,
                )

            self.assertEqual(response["temporal_topology"]["kind"], "serial")
            self.assertIsNone(response["concurrent_resolution"])
            self.assertFalse(response["historical_concurrent_repair"])

    def test_concurrent_refinement_never_reads_another_lane(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            context = reviewed_correction_context(root, match, "subject-mixed")
            lane = context["concurrent_resolution"]["lanes"][0]
            overview = lane["evidence"]["anchor_crops"]

            with patch(
                "app.services.identity_reviewed_review_source.render_mixed_review_evidence"
            ):
                refined = build_concurrent_lane_boundary_refinement(
                    root,
                    match,
                    candidate_subject_id="subject-mixed",
                    parent_case_id=context["concurrent_resolution"]["parent_case_id"],
                    parent_source_digest=context["concurrent_resolution"]["parent_source_digest"],
                    lane_id=lane["lane_id"],
                    lane_source_digest=lane["source_ownership_digest"],
                    after_frame=overview[0]["frame"],
                    before_frame=overview[1]["frame"],
                )

            self.assertEqual(refined["lane_id"], lane["lane_id"])
            self.assertTrue(refined["anchor_crops"])
            self.assertEqual(refined["boundary_crops"]["after"], overview[0])
            self.assertEqual(refined["boundary_crops"]["before"], overview[1])
            self.assertEqual(refined["anchor_crops"][-1]["frame"], overview[1]["frame"])
            self.assertEqual(refined["anchor_crops"][-1]["tracklet_id"], overview[1]["tracklet_id"])
            self.assertEqual(
                {crop["tracklet_id"] for crop in refined["anchor_crops"]},
                {lane["tracklet_id"]},
            )

    def test_durable_concurrent_refinement_never_builds_full_review_progress(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            lanes = build_mixed_review_queue(root, match)["cases"][0]["concurrent_resolution"]["lanes"]
            saved = save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "concurrent_lanes",
                    "lane_resolutions": [
                        _direct_lane(lanes[0], "player-a"),
                        _direct_lane(lanes[1], "player-b"),
                    ],
                },
            )["saved_case"]
            overview = lanes[0]["evidence"]["anchor_crops"]

            with patch(
                "app.services.identity_reviewed_progress.build_reviewed_identity_progress",
                side_effect=AssertionError("durable focused refinement must not build full progress"),
            ), patch(
                "app.services.identity_reviewed_review_source.render_mixed_review_evidence"
            ):
                refined = build_concurrent_lane_boundary_refinement(
                    root,
                    match,
                    candidate_subject_id="subject-mixed",
                    parent_case_id=saved["case_id"],
                    parent_source_digest=saved["source_subject_digest"],
                    lane_id=lanes[0]["lane_id"],
                    lane_source_digest=lanes[0]["source_ownership_digest"],
                    after_frame=overview[0]["frame"],
                    before_frame=overview[1]["frame"],
                )

            self.assertEqual(refined["lane_id"], lanes[0]["lane_id"])

    def test_concurrent_lanes_use_distinct_artifacts_for_shared_frames(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)

            context = reviewed_correction_context(root, match, "subject-mixed")
            lanes = context["concurrent_resolution"]["lanes"]
            shared_frame = 5
            shared_crops = [
                next(crop for crop in lane["evidence"]["anchor_crops"] if crop["frame"] == shared_frame)
                for lane in lanes
            ]

            self.assertEqual({crop["tracklet_id"] for crop in shared_crops}, {"t1", "t2"})
            self.assertEqual(len({crop["artifact"] for crop in shared_crops}), 2)

    def test_concurrent_refinement_returns_structured_conflict(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)

            digest = current_mixed_subject_digest(root, "subject-mixed")
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                self.assertRaises(HTTPException) as exact_raised,
            ):
                get_match_reviewed_identity_temporal_split_refinement(
                    "m1",
                    "subject-mixed",
                    digest,
                    1,
                    2,
                    None,
                    None,
                )
            self.assertEqual(exact_raised.exception.status_code, 409)
            self.assertEqual(
                exact_raised.exception.detail["code"],
                "temporal_split_not_separable",
            )

            _classify(root, match)

            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                self.assertRaises(HTTPException) as raised,
            ):
                get_match_reviewed_identity_mixed_boundary_refinement(
                    "m1",
                    "subject-mixed",
                    1,
                    2,
                )

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(
                raised.exception.detail,
                {
                    "code": "temporal_split_not_separable",
                    "message": "temporal_split_not_separable",
                },
            )

    def test_concurrent_legacy_split_rejects_before_persistence_but_complex_is_allowed(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            marker = _classify(root, match)
            guarded_paths = _split_state_paths(root)
            before = _path_snapshots(guarded_paths)

            with self.assertRaisesRegex(
                MixedTemporalTopologyError,
                "temporal_split_not_separable",
            ):
                save_mixed_player_resolution(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "split",
                        "split_after_frames": [3],
                        "segment_assignments": [
                            {"action": "assign_team", "team_label": "A"},
                            {"action": "assign_team", "team_label": "B"},
                        ],
                    },
                )

            self.assertEqual(_path_snapshots(guarded_paths), before)
            complex_result = save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "unresolved_complex_mix",
                },
            )
            self.assertEqual(
                complex_result["saved_case"]["resolution_status"],
                "unresolved_complex_mix",
            )

    def test_concurrent_exact_source_split_rejects_before_any_structural_write(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            from app.services.identity_reviewed_review_source import resolve_review_source

            source = resolve_review_source(
                root,
                match,
                candidate_subject_id="subject-mixed",
                source_ownership_digest=current_mixed_subject_digest(root, "subject-mixed"),
            )
            guarded_paths = _split_state_paths(root)
            before = _path_snapshots(guarded_paths)

            with self.assertRaisesRegex(
                MixedTemporalTopologyError,
                "temporal_split_not_separable",
            ):
                save_inline_temporal_split(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_ownership_digest": source["source_ownership_digest"],
                        "resolution": "split",
                        "split_after_frames": [3],
                        "segment_assignments": [
                            {"action": "assign_team", "team_label": "A"},
                            {"action": "assign_team", "team_label": "B"},
                        ],
                    },
                )

            self.assertEqual(_path_snapshots(guarded_paths), before)

    def test_focused_case_materializes_only_the_exact_durable_marker(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            marker = _classify(root, match)
            document = load_mixed_player_cases(root)
            markers = [
                {**marker, "case_id": f"M{index:02d}"}
                for index in range(28)
            ]
            save_mixed_case_document(root, {**document, "cases": markers})

            with patch(
                "app.services.identity_reviewed_mixed_store._materialize_mixed_review_case",
                wraps=_materialize_mixed_review_case,
            ) as materialize:
                focused = build_focused_mixed_review_case(root, match, "M17")

            self.assertEqual(focused["status"], "current_blocking")
            self.assertEqual(focused["case"]["case_id"], "M17")
            self.assertEqual(materialize.call_count, 1)
            self.assertEqual(materialize.call_args.args[2]["case_id"], "M17")

    def test_focused_case_reports_resolved_stale_nonblocking_and_missing_exactly(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            marker = _classify(root, match)

            resolved_document = load_mixed_player_cases(root)
            resolved_document["cases"][0]["resolution_status"] = "resolved"
            save_mixed_case_document(root, resolved_document)
            resolved = build_focused_mixed_review_case(root, match, "subject-mixed")
            self.assertEqual(resolved["status"], "no_longer_unresolved")
            self.assertIsNone(resolved["case"])

            resolved_document["cases"][0] = marker
            save_mixed_case_document(root, resolved_document)
            tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
            tracklets["tracklets"][0]["positions_m"].pop()
            _write(root / "tracklets.json", tracklets)
            stale = build_focused_mixed_review_case(root, match, "subject-mixed")
            self.assertEqual(stale["status"], "stale_or_unclassifiable_blocking")
            self.assertEqual(stale["case"]["scope_status"], "stale_or_unclassifiable_blocking")

            missing = build_focused_mixed_review_case(root, match, "not-this-subject")
            self.assertEqual(missing["status"], "missing")
            self.assertIsNone(missing["case"])

        with _workspace() as root:
            match = _fixture(root)
            match["identity_review_scope"] = {
                "schema_version": "1.0.0",
                "teams": {"A": "complete_roster", "B": "team_stats_only"},
            }
            tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
            tracklets["tracklets"][0]["team_label"] = "B"
            _write(root / "tracklets.json", tracklets)
            source_digest = current_mixed_subject_digest(root, "subject-mixed")
            unit = {
                "scope_kind": "whole_subject",
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": source_digest,
                "detected_observation_count": 9,
                "detected_pairs": [("t1", frame) for frame in range(1, 10)],
                "effective_team_label": "B",
            }
            marker = persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": source_digest,
                    "action": "mixed_players",
                    "mixed_hint": "same_team_b",
                },
                authorized_review_unit=unit,
            )["saved_decision"]
            scoped_document = load_mixed_player_cases(root)
            scoped_document["cases"][0]["source"]["effective_team_label"] = "B"
            save_mixed_case_document(root, scoped_document)
            nonblocking = build_focused_mixed_review_case(
                root,
                match,
                str(marker.get("case_id") or marker["candidate_subject_id"]),
            )
            self.assertEqual(nonblocking["status"], "not_in_mandatory_queue")
            self.assertIsNone(nonblocking["case"])

    def test_staged_mixed_marker_is_exact_source_and_resolves_through_shared_split_engine(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            before = build_reviewed_identity_progress(root, match)
            from app.services.identity_reviewed_review_source import resolve_review_source

            source = resolve_review_source(
                root,
                match,
                candidate_subject_id="subject-mixed",
                source_ownership_digest=current_mixed_subject_digest(root, "subject-mixed"),
            )
            unit = {
                **before["next_cases"][0],
                "scope_kind": "whole_subject",
                "source_ownership_digest": source["source_ownership_digest"],
                "detected_pairs": [
                    (row["tracklet_id"], row["frame"])
                    for row in source["owned_observations"]
                ],
            }
            result = persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "action": "mixed_players",
                    "mixed_hint": "unknown",
                    "source_ownership_digest": source["source_ownership_digest"],
                },
                authorized_review_unit=unit,
            )

            marker = result["saved_decision"]
            self.assertTrue(str(marker["case_id"]).startswith("inline-temporal-split:v1:"))
            self.assertEqual(marker["source"]["owned_observations"], [
                {"tracklet_id": "t1", "frame": frame} for frame in range(1, 10)
            ])
            self.assertEqual(build_reviewed_identity_progress(root, match)["next_cases"], [])

            resolved = save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "case_id": marker["case_id"],
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            self.assertEqual(resolved["saved_case"]["original_issue"], "inline_temporal_split")
            self.assertEqual(resolved["saved_case"]["resolution_status"], "resolved")
            self.assertEqual(
                {row["target_origin"] for row in load_segment_review(root)["targets"]},
                {"operator_temporal_split"},
            )

    def test_staged_material_continuity_keeps_exact_source_until_later_split(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            unit = {
                "scope_kind": "material_continuity",
                "candidate_subject_id": "continuity:mixed",
                "continuity_group_id": "continuity:mixed",
                "source_ownership_digest": "material-source-digest",
                "detected_observation_count": 3,
                "detected_pairs": [("t1", 1), ("t1", 2), ("t1", 3)],
                "effective_team_label": "A",
            }
            staged = persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "continuity:mixed",
                    "continuity_group_id": "continuity:mixed",
                    "source_ownership_digest": "material-source-digest",
                    "action": "mixed_players",
                },
                authorized_review_unit=unit,
            )["saved_decision"]

            self.assertEqual(staged["source"]["scope_kind"], "material_continuity")
            self.assertEqual(
                staged["source"]["owned_observations"],
                [{"tracklet_id": "t1", "frame": frame} for frame in (1, 2, 3)],
            )

            queued = build_mixed_review_queue(root, match)["cases"]
            self.assertEqual(len(queued), 1)
            self.assertFalse(queued[0]["action_capabilities"]["assign_existing_slot"]["allowed"])
            self.assertFalse(queued[0]["action_capabilities"]["create_new_stable_player"]["allowed"])

            resolved = save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "continuity:mixed",
                    "case_id": staged["case_id"],
                    "source_subject_digest": staged["source_subject_digest"],
                    "resolution": "split",
                    "split_after_frames": [2],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )

            self.assertEqual(resolved["saved_case"]["original_issue"], "inline_temporal_split")
            self.assertEqual(resolved["saved_case"]["source"]["scope_kind"], "material_continuity")

    def test_unresolved_material_continuity_marker_does_not_block_recompute(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            unit = {
                "scope_kind": "material_continuity",
                "candidate_subject_id": "continuity:mixed",
                "continuity_group_id": "continuity:mixed",
                "source_ownership_digest": "material-source-digest",
                "detected_observation_count": 3,
                "detected_pairs": [("t1", 1), ("t1", 2), ("t1", 3)],
                "effective_team_label": "A",
            }
            persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "continuity:mixed",
                    "continuity_group_id": "continuity:mixed",
                    "source_ownership_digest": "material-source-digest",
                    "action": "mixed_players",
                },
                authorized_review_unit=unit,
            )

            rows = unresolved_mixed_observation_assignments(root)

            self.assertEqual(
                [(row["tracklet_id"], row["frame"]) for row in rows],
                [("t1", frame) for frame in (1, 2, 3)],
            )
            self.assertTrue(all(row["team_label"] == "A" for row in rows))
            self.assertTrue(all(row["identity_status"] == "unresolved" for row in rows))

    def test_staged_canonical_segments_do_not_collapse_by_raw_subject(self) -> None:
        with _workspace() as root:
            match = _fixture(root)

            def stage(target_id: str, frames: list[int]) -> dict:
                unit = {
                    "scope_kind": "canonical_segment",
                    "candidate_subject_id": "subject-mixed",
                    "review_target_id": target_id,
                    "source_ownership_digest": f"digest-{target_id}",
                    "detected_observation_count": len(frames),
                    "detected_pairs": [("t1", frame) for frame in frames],
                    "effective_team_label": "A",
                }
                return persist_reviewed_identity_correction(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "review_target_id": target_id,
                        "source_ownership_digest": f"digest-{target_id}",
                        "action": "mixed_players",
                    },
                    authorized_review_unit=unit,
                )["saved_decision"]

            first = stage("canonical-a", [1, 2, 3])
            second = stage("canonical-b", [4, 5, 6])
            cases = load_mixed_player_cases(root)["cases"]

            self.assertNotEqual(first["case_id"], second["case_id"])
            self.assertEqual(len(cases), 2)
            self.assertEqual(
                {row["source"]["review_target_id"] for row in cases},
                {"canonical-a", "canonical-b"},
            )
            subject_fallback = build_focused_mixed_review_case(
                root,
                match,
                "subject-mixed",
            )
            self.assertEqual(subject_fallback["status"], "missing")
            self.assertIsNone(subject_fallback["case"])

    def test_optional_max_capabilities_hide_staged_mixed_but_keep_direct_split(self) -> None:
        optional = reviewed_identity_action_capabilities({
            "scope_kind": "whole_subject",
            "detected_observation_count": 5,
            "priority": "optional",
        })
        self.assertFalse(optional["mixed_players"]["allowed"])
        self.assertEqual(optional["mixed_players"]["reason"], "optional_max_direct_split_only")
        self.assertTrue(optional["split"]["allowed"])

        required = reviewed_identity_action_capabilities({
            "scope_kind": "whole_subject",
            "detected_observation_count": 5,
            "priority": "high",
        })
        self.assertTrue(required["mixed_players"]["allowed"])
        self.assertTrue(required["split"]["allowed"])

    def test_optional_max_staged_mixed_save_is_rejected_with_stable_code(self) -> None:
        unit = {
            "scope_kind": "whole_subject",
            "detected_observation_count": 5,
            "priority": "optional",
        }
        with self.assertRaises(ReviewedIdentityActionScopeError) as raised:
            validate_review_unit_action_scope(
                {"action": "mixed_players", "candidate_subject_id": "subject-mixed"},
                unit,
            )
        self.assertEqual(raised.exception.code, "optional_max_staged_mixed_not_allowed")

    def test_optional_max_source_cannot_persist_staged_marker(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            queued_unit = build_reviewed_identity_progress(root, match)["next_cases"][0]
            subject_id = str(queued_unit["candidate_subject_id"])
            unit = {**queued_unit, "priority": "optional"}
            with self.assertRaises(ReviewedIdentityActionScopeError) as raised:
                persist_reviewed_identity_correction(
                    root,
                    match,
                    {
                        "candidate_subject_id": subject_id,
                        "action": "mixed_players",
                        "mixed_hint": "unknown",
                        "source_ownership_digest": current_mixed_subject_digest(root, subject_id),
                    },
                    authorized_review_unit=unit,
                )
            self.assertEqual(raised.exception.code, "optional_max_staged_mixed_not_allowed")
            self.assertFalse((root / "reviewed_identity_mixed_players.json").exists())

    def test_optional_max_complex_inline_split_becomes_required_blocker_and_rejects_finalization(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            result = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                    "resolution": "unresolved_complex_mix",
                },
            )

            queue = build_mixed_review_queue(root, match)
            state = derive_review_workflow_state(
                _workflow_evidence(normal=0, mixed=queue["summary"]["unresolved"])
            )

            self.assertTrue(result["complex_mix"])
            self.assertEqual(queue["summary"]["complex_unresolved"], 1)
            self.assertEqual(state["phase"], "mixed_players")
            self.assertIn("review_mixed_players", state["allowed_actions"])
            with (
                patch(
                    "app.services.review_workflow_orchestrator.build_cheap_finalize_preflight_state",
                    return_value={"issues": {"blocking": 0}, "allowed_actions": ["finalize_identity"], "phase": "ready_to_finalize"},
                ),
                patch(
                    "app.services.review_workflow_orchestrator.get_review_workflow_state",
                    side_effect=[
                        {"issues": {"blocking": 0}, "allowed_actions": ["finalize_identity"], "phase": "ready_to_finalize"},
                    ],
                ),
                patch(
                    "app.services.review_workflow_orchestrator.refresh_review_after_identity_mutation",
                    return_value={"workflow": state, "snapshot": {}},
                ),
                patch("app.services.review_workflow_orchestrator.build_reviewed_stats") as stats,
                patch("app.services.review_workflow_orchestrator.generate_reviewed_output") as render,
            ):
                with self.assertRaises(WorkflowActionError) as raised:
                    finalize_review_for_qa(root, match)

            self.assertEqual(raised.exception.code, "identity_issues_remaining")
            stats.assert_not_called()
            render.assert_not_called()

    def test_temporal_split_requires_the_same_workflow_authorization_as_corrections(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                "resolution": "split",
                "split_after_frames": [4],
                "segment_assignments": [
                    {"action": "assign_team", "team_label": "A"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.main.get_review_workflow_state",
                    return_value={"phase": "initial_audit", "allowed_actions": [], "blockers": [{"code": "initial_audit_incomplete"}]},
                ),
                patch("app.main.save_inline_temporal_split") as save,
            ):
                with self.assertRaises(HTTPException) as raised:
                    post_match_reviewed_identity_temporal_split("m1", payload)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.detail["code"], "initial_audit_incomplete")
            save.assert_not_called()
            self.assertFalse((root / "reviewed_identity_mixed_players.json").exists())

    def test_temporal_split_allows_normal_review_and_video_qa_correction_phases(self) -> None:
        for state in (
            {"phase": "exceptions", "allowed_actions": ["review_identity_issue"]},
            {"phase": "video_qa", "allowed_actions": ["correct_video_identity"]},
        ):
            with self.subTest(state=state), _workspace() as root:
                match = _fixture(root)
                payload = {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                }
                with (
                    patch("app.main.match_dir", return_value=root),
                    patch("app.main.read_match_meta", return_value=match),
                    patch("app.main.get_review_workflow_state", return_value=state),
                ):
                    result = post_match_reviewed_identity_temporal_split("m1", payload)

                self.assertEqual(result["saved_case"]["resolution_status"], "resolved")
                self.assertTrue(result["recompute_deferred"])
                self.assertTrue(result["review_state_rebuild_required"])
                audit = json.loads((root / AUDIT_FILENAME).read_text(encoding="utf-8"))
                self.assertEqual(audit["events"][-1]["decision_stage"], "temporal_split")
                self.assertEqual(
                    audit["events"][-1]["source"]["source_ownership_digest"],
                    payload["source_ownership_digest"],
                )

    def test_temporal_split_route_rejects_concurrency_before_hot_state_generation(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _make_concurrent(root)
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                "resolution": "split",
                "split_after_frames": [3],
                "segment_assignments": [
                    {"action": "assign_team", "team_label": "A"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }
            guarded_paths = _split_state_paths(root)
            before = _path_snapshots(guarded_paths)
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.main.get_review_workflow_state",
                    return_value={"phase": "exceptions", "allowed_actions": ["review_identity_issue"]},
                ),
                patch("app.main.load_or_rebuild_review_hot_state") as hot_state,
                patch("app.main.invalidate_review_hot_state") as invalidate,
                self.assertRaises(HTTPException) as raised,
            ):
                post_match_reviewed_identity_temporal_split("m1", payload)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(
                raised.exception.detail["code"],
                "temporal_split_not_separable",
            )
            hot_state.assert_not_called()
            invalidate.assert_not_called()
            self.assertEqual(_path_snapshots(guarded_paths), before)

    def test_temporal_split_response_invalidates_hot_state_and_next_progress_rebuilds_once(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _write(root / "reviewed_identity_snapshot.json", {})
            hot_state = load_or_rebuild_review_hot_state(root, match)
            self.assertTrue((root / FILENAME).exists())
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                "review_state_version": hot_state["state_version"],
                "resolution": "split",
                "split_after_frames": [4],
                "segment_assignments": [
                    {"action": "assign_team", "team_label": "A"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }
            workflow = {"phase": "exceptions", "allowed_actions": ["review_identity_issue"]}
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch("app.main.get_review_workflow_state", return_value=workflow),
            ):
                result = post_match_reviewed_identity_temporal_split("m1", payload)

            self.assertTrue(result["recompute_deferred"])
            self.assertTrue(result["review_state_rebuild_required"])
            self.assertFalse((root / FILENAME).exists())

            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
                    wraps=build_reviewed_identity_progress,
                ) as rebuild,
            ):
                get_match_reviewed_identity_progress("m1", Response())

            self.assertEqual(rebuild.call_count, 1)
            self.assertTrue((root / FILENAME).exists())

    def test_unresolved_complex_temporal_split_requires_reload_and_preserves_blocker(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _write(root / "reviewed_identity_snapshot.json", {})
            hot_state = load_or_rebuild_review_hot_state(root, match)
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                "review_state_version": hot_state["state_version"],
                "resolution": "unresolved_complex_mix",
            }
            workflow = {"phase": "exceptions", "allowed_actions": ["review_identity_issue"]}
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch("app.main.get_review_workflow_state", return_value=workflow),
            ):
                result = post_match_reviewed_identity_temporal_split("m1", payload)

            self.assertTrue(result["recompute_deferred"])
            self.assertTrue(result["review_state_rebuild_required"])
            self.assertFalse((root / FILENAME).exists())
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
            ):
                progress = get_match_reviewed_identity_progress("m1", Response())
            self.assertEqual(progress["mixed_players"]["summary"]["complex_unresolved"], 1)

    def test_persisted_temporal_split_cannot_be_edited_from_a_forbidden_workflow_phase(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            initial = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            before = (root / "reviewed_identity_mixed_players.json").read_bytes()
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": digest,
                "existing_split_semantic_digest": initial["saved_case"]["split_semantic_digest"],
                "resolution": "split",
                "split_after_frames": [5],
                "segment_assignments": [
                    {"action": "assign_team", "team_label": "A"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.main.get_review_workflow_state",
                    return_value={"phase": "initial_audit", "allowed_actions": [], "blockers": [{"code": "initial_audit_incomplete"}]},
                ),
            ):
                with self.assertRaises(HTTPException) as raised:
                    post_match_reviewed_identity_temporal_split("m1", payload)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual((root / "reviewed_identity_mixed_players.json").read_bytes(), before)
    def test_generated_mixed_crop_is_available_through_match_artifact_route(self) -> None:
        with _workspace() as root:
            relative = Path("reviewed_identity_mixed") / ("a" * 16) / "01_f000001.jpg"
            artifact = root / relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"jpeg")

            with patch("app.main.match_dir", return_value=root):
                response = get_artifact("match", str(relative))

            self.assertIsInstance(response, FileResponse)
            self.assertEqual(response.media_type, "image/jpeg")
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_missing_mixed_crop_is_not_cacheable(self) -> None:
        with _workspace() as root:
            relative = Path("reviewed_identity_mixed") / ("a" * 16) / "01_f000001.jpg"

            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value={}),
                patch("app.main.materialize_mixed_review_artifact", return_value=False),
            ):
                with self.assertRaises(HTTPException) as raised:
                    get_artifact("match", str(relative))

            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(raised.exception.headers, {"Cache-Control": "no-store"})

    def test_missing_current_mixed_crop_materializes_its_exact_card_on_read(self) -> None:
        with _workspace() as root:
            relative = Path("reviewed_identity_mixed") / ("a" * 16) / "01_f000001.jpg"
            artifact = root / relative

            def materialize(path: Path, _match: dict[str, object], requested: str) -> bool:
                self.assertEqual(path, root)
                self.assertEqual(requested, str(relative))
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(b"jpeg")
                return True

            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value={}),
                patch("app.main.materialize_mixed_review_artifact", side_effect=materialize) as recovery,
            ):
                response = get_artifact("match", str(relative))

            self.assertIsInstance(response, FileResponse)
            recovery.assert_called_once()
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_artifact_recovery_renders_only_its_current_authoritative_case(self) -> None:
        with _workspace() as root:
            subject_id = "subject-current"
            requested = "reviewed_identity_mixed/" + canonical_digest(subject_id)[:16] + "/01_f000001.jpg"
            current_case = {
                "case_id": "current",
                "candidate_subject_id": subject_id,
                "resolution_status": "unresolved",
                "temporal_evidence": {"anchor_crops": [{
                    "artifact": requested,
                    "generated_for_segment_review": True,
                    "frame": 1,
                    "bbox_xyxy": [0, 0, 1, 1],
                }]},
            }
            stale_case = {
                "case_id": "stale",
                "candidate_subject_id": subject_id,
                "resolution_status": "unresolved",
                "temporal_evidence": {"anchor_crops": [{
                    "artifact": "reviewed_identity_mixed/" + ("b" * 16) + "/01_f000001.jpg",
                }]},
            }

            def render(path: Path, _match: dict[str, object], queue: dict[str, object]) -> set[str]:
                self.assertEqual(queue["cases"], [current_case])
                artifact = path / requested
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(b"jpeg")
                return {requested}

            with (
                patch(
                    "app.services.identity_reviewed_mixed_store.load_mixed_player_cases",
                    return_value={"cases": [stale_case, current_case]},
                ),
                patch(
                    "app.services.identity_reviewed_mixed_store.build_mixed_review_queue",
                    side_effect=AssertionError("artifact recovery must not build the full queue"),
                ),
                patch(
                    "app.services.identity_reviewed_mixed_store._materialize_mixed_review_case",
                    side_effect=[
                        {"status": "no_longer_unresolved", "case": None},
                        {"status": "current_blocking", "case": current_case},
                    ],
                ),
                patch(
                    "app.services.identity_reviewed_mixed_store.render_mixed_review_evidence",
                    side_effect=render,
                ) as render_evidence,
            ):
                recovered = materialize_mixed_review_artifact(root, {}, requested)

            self.assertTrue(recovered)
            render_evidence.assert_called_once()

    def test_classification_moves_case_to_mixed_queue_without_mutating_raw_tracks(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            raw_before = (root / "tracklets.json").read_bytes()

            result = persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "action": "mixed_players",
                    "mixed_hint": "cross_team",
                },
                use_materialized_context=False,
            )
            progress = build_reviewed_identity_progress(root, match)
            queue = build_mixed_review_queue(root, match)

            self.assertEqual(result["saved_decision"]["original_issue"], "mixed_players")
            self.assertEqual(result["saved_decision"]["mixed_hint"], "cross_team")
            self.assertEqual(progress["next_cases"], [])
            self.assertEqual(progress["mixed_players"]["summary"]["unresolved"], 1)
            self.assertEqual(queue["cases"][0]["candidate_subject_id"], "subject-mixed")
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)
            self.assertFalse((root / "reviewed_identity_slot_assignments.json").exists())

    def test_one_and_multiple_boundaries_create_exact_ordered_segment_decisions(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            marker = _classify(root, match)
            response = save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "split",
                    "split_after_frames": [3, 6],
                    "segment_assignments": [
                        {"action": "assign_roster_player", "player_id": "player-a"},
                        {"action": "referee"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            projected = load_segment_review(root)
            review = build_segment_review_document(root, match)
            targets = [row for row in review["targets"] if row.get("target_origin") == "operator_mixed_players"]
            rows = segment_observation_assignments(review, load_segment_decisions(root), _roster(match))

            self.assertEqual(response["saved_case"]["resolution_status"], "resolved")
            self.assertEqual(_semantic_segment_review(projected), _semantic_segment_review(review))
            self.assertIn("segment_review_build_ms", response["performance"])
            self.assertIn("segment_review_projection_ms", response["performance"])
            self.assertIn("total_ms", response["performance"])
            self.assertEqual([row["frame_start"] for row in targets], [1, 4, 7])
            self.assertEqual([row["frame_end"] for row in targets], [3, 6, 9])
            self.assertEqual({(row["tracklet_id"], row["frame"]) for row in rows}, {("t1", frame) for frame in range(1, 10)})
            self.assertEqual({row["identity_status"] for row in rows if row["frame"] <= 3}, {"confirmed"})
            self.assertEqual({row["identity_status"] for row in rows if 4 <= row["frame"] <= 6}, {"referee"})
            self.assertEqual({row["identity_status"] for row in rows if row["frame"] >= 7}, {"unresolved"})

    def test_boundary_refinement_returns_dense_local_evidence_without_expanding_overview(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
            tracklets["tracklets"][0]["positions_m"] = [
                {"frame": frame, "time_sec": float(frame), "x_m": float(frame), "y_m": 1.0, "detected": True, "play_area_status": "inside_play", "bbox_xyxy": [10, 10, 20, 30]}
                for frame in range(1, 122)
            ]
            tracklets["tracklets"].append({
                "tracklet_id": "unrelated-tracklet",
                "team_label": "B",
                "positions_m": [
                    {"frame": frame, "time_sec": float(frame), "x_m": 40.0, "y_m": 20.0, "detected": True, "play_area_status": "inside_play", "bbox_xyxy": [30, 30, 40, 50]}
                    for frame in range(1, 122)
                ],
            })
            _write(root / "tracklets.json", tracklets)
            marker = _classify(root, match)
            raw_before = (root / "tracklets.json").read_bytes()
            overview = build_mixed_review_queue(root, match)["cases"][0]["temporal_evidence"]["anchor_crops"]
            after_frame = overview[4]["frame"]
            before_frame = overview[5]["frame"]

            with patch(
                "app.services.identity_reviewed_mixed_store.render_mixed_review_evidence",
                return_value=set(),
            ) as render:
                refinement = build_mixed_boundary_refinement(
                    root,
                    match,
                    "subject-mixed",
                    after_frame,
                    before_frame,
                    limit=10,
                )

            self.assertEqual(len(overview), 12)
            self.assertEqual(len(refinement["anchor_crops"]), 10)
            refinement_frames = [crop["frame"] for crop in refinement["anchor_crops"]]
            self.assertEqual(refinement_frames, sorted(refinement_frames))
            self.assertTrue(all(after_frame < frame <= before_frame for frame in refinement_frames))
            self.assertEqual({crop["tracklet_id"] for crop in refinement["anchor_crops"]}, {"t1"})
            overview_spacing = before_frame - after_frame
            refined_spacing = max(right - left for left, right in zip(refinement_frames, refinement_frames[1:]))
            self.assertLess(refined_spacing, overview_spacing)
            self.assertEqual(refinement["after_frame"], after_frame)
            self.assertEqual(refinement["before_frame"], before_frame)
            self.assertEqual(refinement["boundary_crops"]["after"], overview[4])
            self.assertEqual(refinement["boundary_crops"]["before"], overview[5])
            self.assertEqual(refinement["anchor_crops"][-1]["frame"], before_frame)
            self.assertEqual(refinement["anchor_crops"][-1]["anchor_crop_id"], overview[5]["anchor_crop_id"])
            render.assert_called_once()
            selected_frame = refinement_frames[len(refinement_frames) // 2]
            save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "split",
                    "split_after_frames": [selected_frame],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            targets = [
                row for row in build_segment_review_document(root, match)["targets"]
                if row.get("target_origin") == "operator_mixed_players"
            ]
            self.assertEqual([row["frame_end"] for row in targets], [selected_frame, 121])
            self.assertEqual([row["frame_start"] for row in targets], [1, selected_frame + 1])
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)

    def test_boundary_refinement_rejects_stale_case_and_invalid_interval(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _classify(root, match)
            with self.assertRaisesRegex(ValueError, "increasing frame boundaries"):
                build_mixed_boundary_refinement(root, match, "subject-mixed", 5, 5)
            with self.assertRaisesRegex(ValueError, "neighboring overview samples"):
                build_mixed_boundary_refinement(root, match, "subject-mixed", 2, 8)

            tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
            tracklets["tracklets"][0]["positions_m"].pop()
            _write(root / "tracklets.json", tracklets)
            with self.assertRaisesRegex(ValueError, "mixed_player_case_stale"):
                build_mixed_boundary_refinement(root, match, "subject-mixed", 1, 8)

    def test_boundary_refinement_route_returns_local_crops(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _classify(root, match)
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.services.identity_reviewed_mixed_store.render_mixed_review_evidence",
                    return_value=set(),
                ),
            ):
                response = get_match_reviewed_identity_mixed_boundary_refinement(
                    "m1",
                    "subject-mixed",
                    1,
                    2,
                )

            self.assertEqual(response["candidate_subject_id"], "subject-mixed")
            self.assertEqual([crop["frame"] for crop in response["anchor_crops"]], [2])

    def test_invalid_split_and_partial_assignment_are_rejected_atomically(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            marker = _classify(root, match)
            before = (root / "reviewed_identity_mixed_players.json").read_bytes()
            for split, assignments in (([9], [{"action": "unresolved"}, {"action": "unresolved"}]), ([4], [{"action": "unresolved"}])):
                with self.assertRaises(ValueError):
                    save_mixed_player_resolution(
                        root,
                        match,
                        {
                            "candidate_subject_id": "subject-mixed",
                            "source_subject_digest": marker["source_subject_digest"],
                            "resolution": "split",
                            "split_after_frames": split,
                            "segment_assignments": assignments,
                        },
                    )
                self.assertEqual((root / "reviewed_identity_mixed_players.json").read_bytes(), before)

    def test_changed_source_observations_make_the_mixed_case_stale(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            marker = _classify(root, match)
            tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
            tracklets["tracklets"][0]["positions_m"].pop()
            _write(root / "tracklets.json", tracklets)

            with self.assertRaisesRegex(ValueError, "mixed_player_case_stale"):
                save_mixed_player_resolution(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "unresolved_complex_mix",
                    },
                )

    def test_assignment_failure_rolls_back_partial_segment_and_slot_writes(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            marker = _classify(root, match)
            marker_before = (root / "reviewed_identity_mixed_players.json").read_bytes()
            build_segment_review_document(root, match)
            review_before = (root / "reviewed_identity_segment_review.json").read_bytes()

            with self.assertRaisesRegex(ValueError, "Invalid player_id"):
                save_mixed_player_resolution(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_subject_digest": marker["source_subject_digest"],
                        "resolution": "split",
                        "split_after_frames": [4],
                        "segment_assignments": [
                            {"action": "create_new_stable_player", "team_label": "A"},
                            {"action": "assign_roster_player", "player_id": "missing-player"},
                        ],
                    },
                )

            self.assertEqual((root / "reviewed_identity_mixed_players.json").read_bytes(), marker_before)
            self.assertEqual((root / "reviewed_identity_segment_review.json").read_bytes(), review_before)
            self.assertFalse((root / "reviewed_identity_segment_decisions.json").exists())
            self.assertFalse((root / "reviewed_identity_slot_assignments.json").exists())

    def test_complex_mix_remains_explicit_and_blocks_readiness(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            marker = _classify(root, match)
            response = save_mixed_player_resolution(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_subject_digest": marker["source_subject_digest"],
                    "resolution": "unresolved_complex_mix",
                },
            )
            self.assertEqual(response["saved_case"]["original_issue"], "mixed_players")
            self.assertEqual(response["saved_case"]["resolution_status"], "unresolved_complex_mix")
            revisited = build_mixed_review_queue(root, match)["cases"][0]
            self.assertTrue(revisited["reviewed_complex"])
            self.assertIsNotNone(revisited["reviewed_complex_at"])
            workflow = derive_review_workflow_state(_workflow_evidence(normal=0, mixed=1))
            self.assertEqual(workflow["phase"], "mixed_players")
            self.assertIn("review_mixed_players", workflow["allowed_actions"])

    def test_resolved_or_legacy_matches_do_not_force_mixed_step(self) -> None:
        legacy = derive_review_workflow_state(_workflow_evidence(normal=0, mixed=0))
        self.assertEqual(legacy["phase"], "ready_to_finalize")
        self.assertEqual(next(row for row in legacy["steps"] if row["id"] == "mixed_players")["status"], "completed")

    def test_inline_split_uses_exact_whole_subject_source_and_is_idempotent(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            raw_before = (root / "tracklets.json").read_bytes()
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": digest,
                "resolution": "split",
                "split_after_frames": [4],
                "segment_assignments": [
                    {"action": "assign_roster_player", "player_id": "player-a"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }

            first = save_inline_temporal_split(root, match, payload)
            second = save_inline_temporal_split(root, match, payload)
            persisted_targets = [
                row for row in load_segment_review(root)["targets"]
                if row.get("target_origin") == "operator_temporal_split"
            ]
            targets = [
                row for row in build_segment_review_document(root, match)["targets"]
                if row.get("target_origin") == "operator_temporal_split"
            ]
            rows = segment_observation_assignments(
                build_segment_review_document(root, match),
                load_segment_decisions(root),
                _roster(match),
            )

            self.assertEqual(first["saved_case"]["resolution_status"], "resolved")
            self.assertTrue(second["idempotent"])
            self.assertTrue(persisted_targets)
            self.assertEqual(
                {row["decision_status"] for row in persisted_targets},
                {"reviewed"},
            )
            self.assertEqual([(row["frame_start"], row["frame_end"]) for row in targets], [(1, 4), (5, 9)])
            self.assertEqual(
                {(row["tracklet_id"], row["frame"]) for row in rows},
                {("t1", frame) for frame in range(1, 10)},
            )
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)

    def test_inline_split_projects_all_decision_fields_without_second_topology_build(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _reserve_canonical_slots(root, 1)
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                "resolution": "split",
                "split_after_frames": [1, 2, 3, 5, 7, 8],
                "segment_assignments": [
                    {"action": "assign_roster_player", "player_id": "player-a"},
                    {"action": "assign_team", "team_label": "B"},
                    {"action": "assign_existing_slot", "stable_slot_id": "A01"},
                    {"action": "create_new_stable_player", "team_label": "B"},
                    {"action": "team_unknown"},
                    {"action": "unresolved"},
                    {"action": "referee"},
                ],
            }

            with patch(
                "app.services.identity_reviewed_mixed_resolution.build_segment_review_document",
                wraps=build_segment_review_document,
            ) as structural_build:
                result = save_inline_temporal_split(root, match, payload)

            projected = load_segment_review(root)
            canonical = build_segment_review_document(root, match)

            self.assertEqual(structural_build.call_count, 1)
            self.assertEqual(len(result["saved_segment_decisions"]), 7)
            self.assertEqual(
                {row["action"] for row in result["saved_segment_decisions"]},
                {
                    "assign_roster_player",
                    "assign_team",
                    "assign_existing_slot",
                    "create_new_stable_player",
                    "team_unknown",
                    "unresolved",
                    "referee",
                },
            )
            self.assertEqual(_semantic_segment_review(projected), _semantic_segment_review(canonical))
            self.assertEqual(projected["summary"]["targets_reviewed"], 7)
            self.assertEqual(projected["summary"]["targets_pending"], 0)
            self.assertEqual(
                set(result["performance"]),
                {
                    "source_resolution_ms",
                    "mixed_case_load_ms",
                    "split_validation_ms",
                    "target_derivation_ms",
                    "segment_review_build_ms",
                    "segment_review_operator_targets_ms",
                    "segment_decision_batch_ms",
                    "segment_assignment_validation_ms",
                    "segment_decision_persistence_ms",
                    "reviewed_slot_persistence_ms",
                    "mixed_case_persistence_ms",
                    "superseded_decision_cleanup_ms",
                    "slot_cleanup_ms",
                    "segment_review_projection_ms",
                    "semantic_digest_ms",
                    "recompute_marker_ms",
                    "total_ms",
                },
            )

    def test_resolved_inline_split_retires_its_exact_canonical_parent_target(self) -> None:
        """A split's reviewed children must replace, not duplicate, its parent."""
        with _workspace() as root:
            match = _fixture(root)
            _make_single_canonical_parent_target(root)
            parent = next(
                row
                for row in build_segment_review_document(root, match)["targets"]
                if row["source_team_label"] == "A"
            )

            result = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "review_target_id": parent["review_target_id"],
                    "source_ownership_digest": parent["source_ownership_digest"],
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "B"},
                        {"action": "assign_roster_player", "player_id": "player-a"},
                    ],
                },
            )

            child_target_ids = set(result["saved_case"]["segment_target_ids"])
            projected = load_segment_review(root)
            rebuilt = build_segment_review_document(root, match)
            progress = build_reviewed_identity_progress(root, match, include_internal_units=True)

            for review in (projected, rebuilt):
                target_ids = {str(row["review_target_id"]) for row in review["targets"]}
                self.assertNotIn(parent["review_target_id"], target_ids)
                self.assertTrue(child_target_ids.issubset(target_ids))
                self.assertTrue(all(
                    row["decision_status"] == "reviewed"
                    for row in review["targets"]
                    if row["review_target_id"] in child_target_ids
                ))
            self.assertFalse(any(
                row.get("review_target_id") == parent["review_target_id"]
                for row in progress["_internal_review_units"]
            ))
            self.assertTrue(all(
                row.get("review_target_id") != parent["review_target_id"]
                for row in progress["next_cases"]
            ))

            decisions = load_segment_decisions(root)
            decisions["decisions"] = [
                row
                for row in decisions["decisions"]
                if row["review_target_id"] != result["saved_case"]["segment_target_ids"][0]
            ]
            _write(root / "reviewed_identity_segment_decisions.json", decisions)
            fail_closed = build_segment_review_document(root, match)
            self.assertIn(
                parent["review_target_id"],
                {row["review_target_id"] for row in fail_closed["targets"]},
            )

    def test_focused_serial_targets_match_global_parent_targets(self) -> None:
        def assert_equivalent(split_after_frames: list[int], assignments: list[dict]) -> None:
            with _workspace() as root:
                match = _fixture(root)
                result = save_inline_temporal_split(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                        "resolution": "split",
                        "split_after_frames": split_after_frames,
                        "segment_assignments": assignments,
                    },
                )
                case = result["saved_case"]
                case_id = str(case["case_id"])
                global_targets = [
                    target
                    for target in operator_mixed_targets(root)
                    if str(target.get("split_parent_case_id") or "") == case_id
                ]
                focused = operator_targets_for_mixed_marker(root, case)

                self.assertEqual(focused, global_targets)
                self.assertEqual(
                    [target["review_target_id"] for target in focused],
                    list(case["segment_target_ids"]),
                )
                self.assertEqual(
                    {
                        (str(row["tracklet_id"]), int(row["frame"]))
                        for target in focused
                        for row in target["owned_observations"]
                    },
                    {
                        (str(row["tracklet_id"]), int(row["frame"]))
                        for row in case["source"]["owned_observations"]
                    },
                )

        assert_equivalent(
            [4],
            [
                {"action": "assign_roster_player", "player_id": "player-a"},
                {"action": "assign_team", "team_label": "B"},
            ],
        )
        assert_equivalent(
            [2, 5],
            [
                {"action": "assign_roster_player", "player_id": "player-a"},
                {"action": "assign_team", "team_label": "B"},
                {"action": "assign_team", "team_label": "A"},
            ],
        )

    def test_inline_serial_save_focuses_parent_before_one_global_review_build(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": current_mixed_subject_digest(root, "subject-mixed"),
                "resolution": "split",
                "split_after_frames": [4],
                "segment_assignments": [
                    {"action": "assign_roster_player", "player_id": "player-a"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }
            from app.services.identity_reviewed_segments import operator_mixed_targets as build_targets

            with patch(
                "app.services.identity_reviewed_mixed_resolution.operator_mixed_targets",
                side_effect=AssertionError("serial target validation must not enumerate sibling Mixed cases"),
            ), patch(
                "app.services.identity_reviewed_segments.operator_mixed_targets",
                wraps=build_targets,
            ) as global_build_targets:
                result = save_inline_temporal_split(root, match, payload)

            self.assertEqual(result["saved_case"]["resolution_status"], "resolved")
            self.assertEqual(global_build_targets.call_count, 1)
            self.assertIn("segment_review_operator_targets_ms", result["performance"])

    def test_legacy_serial_save_also_focuses_its_exact_parent(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            marker = _classify(root, match)
            payload = {
                "candidate_subject_id": "subject-mixed",
                "case_id": marker.get("case_id"),
                "source_subject_digest": marker["source_subject_digest"],
                "resolution": "split",
                "split_after_frames": [4],
                "segment_assignments": [
                    {"action": "assign_roster_player", "player_id": "player-a"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }
            from app.services.identity_reviewed_segments import operator_mixed_targets as build_targets

            with patch(
                "app.services.identity_reviewed_mixed_resolution.operator_mixed_targets",
                side_effect=AssertionError("legacy serial validation must not enumerate sibling Mixed cases"),
            ), patch(
                "app.services.identity_reviewed_segments.operator_mixed_targets",
                wraps=build_targets,
            ) as global_build_targets:
                result = save_mixed_player_resolution(root, match, payload)

            self.assertEqual(result["saved_case"]["resolution_status"], "resolved")
            self.assertEqual(global_build_targets.call_count, 1)

    def test_inline_split_rejects_stale_source_and_conflicting_replay(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": digest,
                "resolution": "split",
                "split_after_frames": [4],
                "segment_assignments": [
                    {"action": "assign_team", "team_label": "A"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }
            save_inline_temporal_split(root, match, payload)
            with self.assertRaisesRegex(MixedPlayerTargetError, "temporal_split_conflict"):
                save_inline_temporal_split(
                    root,
                    match,
                    {**payload, "split_after_frames": [5]},
                )

            tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
            tracklets["tracklets"][0]["positions_m"].pop()
            _write(root / "tracklets.json", tracklets)
            with self.assertRaisesRegex(ValueError, "review_target_stale"):
                save_inline_temporal_split(root, match, payload)

    def test_inline_split_reopens_and_controlled_edit_replaces_old_children(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            initial = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_roster_player", "player_id": "player-a"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            old_ids = set(initial["saved_case"]["segment_target_ids"])
            restored = reviewed_correction_context(root, match, "subject-mixed")

            self.assertEqual(restored["temporal_split"]["split_after_frames"], [4])
            self.assertEqual(
                [row["action"] for row in restored["temporal_split"]["segment_assignments"]],
                ["assign_roster_player", "assign_team"],
            )

            updated_payload = {
                "candidate_subject_id": "subject-mixed",
                "source_ownership_digest": digest,
                "existing_split_semantic_digest": initial["saved_case"]["split_semantic_digest"],
                "resolution": "split",
                "split_after_frames": [5],
                "segment_assignments": [
                    {"action": "assign_roster_player", "player_id": "player-a"},
                    {"action": "assign_team", "team_label": "B"},
                ],
            }
            # After the first save the source is no longer an active queue
            # item. The public endpoint must nevertheless permit a controlled
            # reopen/edit when the exact persisted split is supplied.
            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch(
                    "app.main.get_review_workflow_state",
                    return_value={
                        "phase": "ready_to_finalize",
                        "allowed_actions": ["review_identity_issue"],
                    },
                ),
            ):
                updated = post_match_reviewed_identity_temporal_split("m1", updated_payload)
            current_ids = set(updated["saved_case"]["segment_target_ids"])
            decision_ids = {
                row["review_target_id"]
                for row in load_segment_decisions(root)["decisions"]
            }

            self.assertNotEqual(old_ids, current_ids)
            self.assertTrue(current_ids <= decision_ids)
            self.assertFalse(old_ids & decision_ids)
            projected = load_segment_review(root)
            canonical = build_segment_review_document(root, match)
            self.assertEqual(_semantic_segment_review(projected), _semantic_segment_review(canonical))
            self.assertEqual(projected["summary"]["orphaned_decisions_requiring_review"], 0)

    def test_direct_parent_decision_atomically_retires_exact_saved_inline_split(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            split = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_roster_player", "player_id": "player-a"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            child_ids = set(split["saved_case"]["segment_target_ids"])
            raw_before = (root / "tracklets.json").read_bytes()

            direct = persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "action": "assign_team",
                    "team_label": "A",
                    "source_ownership_digest": digest,
                },
                use_materialized_context=False,
            )
            replay = persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "action": "assign_team",
                    "team_label": "A",
                    "source_ownership_digest": digest,
                },
                use_materialized_context=False,
            )

            self.assertEqual(direct["effective_action"], "assign_team")
            self.assertEqual(replay["effective_action"], "assign_team")
            self.assertIsNone(reviewed_correction_context(root, match, "subject-mixed")["temporal_split"])
            self.assertFalse(
                any(row.get("case_id") == split["saved_case"]["case_id"] for row in load_mixed_player_cases(root)["cases"])
            )
            decision_ids = {
                row["review_target_id"] for row in load_segment_decisions(root)["decisions"]
            }
            self.assertFalse(child_ids & decision_ids)
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)

    def test_direct_supersede_removes_orphan_manual_slot_created_by_split_child(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _reserve_canonical_slots(root, 7)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            raw_before = (root / "tracklets.json").read_bytes()
            split = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "create_new_stable_player", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            self.assertIn("A08", build_reviewed_slot_registry(root))

            persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "action": "assign_team",
                    "team_label": "A",
                    "source_ownership_digest": digest,
                },
                use_materialized_context=False,
            )

            self.assertFalse(any(
                row.get("case_id") == split["saved_case"]["case_id"]
                for row in load_mixed_player_cases(root)["cases"]
            ))
            self.assertNotIn("A08", build_reviewed_slot_registry(root))
            self.assertEqual((root / "tracklets.json").read_bytes(), raw_before)

    def test_split_edit_removes_orphan_manual_slot_from_replaced_child(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _reserve_canonical_slots(root, 7)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            initial = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "create_new_stable_player", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            self.assertIn("A08", build_reviewed_slot_registry(root))

            save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "existing_split_semantic_digest": initial["saved_case"]["split_semantic_digest"],
                    "resolution": "split",
                    "split_after_frames": [5],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )

            self.assertNotIn("A08", build_reviewed_slot_registry(root))

    def test_superseding_child_keeps_manual_slot_with_surviving_reference(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _reserve_canonical_slots(root, 7)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "create_new_stable_player", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            document = load_reviewed_slot_assignments(root)
            document["decisions"] = [{
                "candidate_subject_id": "surviving-subject",
                "action": "assign_existing_slot",
                "stable_slot_id": "A08",
            }]
            _write(root / "reviewed_identity_slot_assignments.json", document)

            persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "action": "assign_team",
                    "team_label": "A",
                    "source_ownership_digest": digest,
                },
                use_materialized_context=False,
            )

            self.assertIn("A08", build_reviewed_slot_registry(root))

    def test_canonical_slot_is_never_collected_when_split_is_replaced(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _reserve_canonical_slots(root, 8)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            initial = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_existing_slot", "stable_slot_id": "A08"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "existing_split_semantic_digest": initial["saved_case"]["split_semantic_digest"],
                    "resolution": "split",
                    "split_after_frames": [5],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )

            self.assertEqual(build_reviewed_slot_registry(root)["A08"]["status"], "canonical")

    def test_direct_supersede_cleanup_failure_restores_split_children_and_manual_slot(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _reserve_canonical_slots(root, 7)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            split = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "create_new_stable_player", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            artifacts = {
                name: (root / name).read_bytes()
                for name in (
                    "reviewed_identity_mixed_players.json",
                    "reviewed_identity_segment_review.json",
                    "reviewed_identity_segment_decisions.json",
                    "reviewed_identity_slot_assignments.json",
                )
            }
            with patch(
                "app.services.identity_reviewed_corrections.cleanup_unreferenced_manual_reviewed_slots",
                side_effect=RuntimeError("cleanup failed"),
            ), self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                persist_reviewed_identity_correction(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "action": "assign_team",
                        "team_label": "A",
                        "source_ownership_digest": digest,
                    },
                    use_materialized_context=False,
                )

            self.assertTrue(any(
                row.get("case_id") == split["saved_case"]["case_id"]
                for row in load_mixed_player_cases(root)["cases"]
            ))
            for name, before in artifacts.items():
                self.assertEqual((root / name).read_bytes(), before)

    def test_split_edit_cleanup_failure_restores_old_children_and_manual_slot(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _reserve_canonical_slots(root, 7)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            initial = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "create_new_stable_player", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            artifacts = {
                name: (root / name).read_bytes()
                for name in (
                    "reviewed_identity_mixed_players.json",
                    "reviewed_identity_segment_review.json",
                    "reviewed_identity_segment_decisions.json",
                    "reviewed_identity_slot_assignments.json",
                )
            }
            with patch(
                "app.services.identity_reviewed_mixed_resolution.cleanup_unreferenced_manual_reviewed_slots",
                side_effect=RuntimeError("cleanup failed"),
            ), self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                save_inline_temporal_split(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_ownership_digest": digest,
                        "existing_split_semantic_digest": initial["saved_case"]["split_semantic_digest"],
                        "resolution": "split",
                        "split_after_frames": [5],
                        "segment_assignments": [
                            {"action": "assign_team", "team_label": "A"},
                            {"action": "assign_team", "team_label": "B"},
                        ],
                    },
                )

            for name, before in artifacts.items():
                self.assertEqual((root / name).read_bytes(), before)

    def test_resolved_split_to_complex_cleanup_failure_restores_child_review_snapshot(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            _reserve_canonical_slots(root, 7)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            initial = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "create_new_stable_player", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            artifacts = {
                name: (root / name).read_bytes()
                for name in (
                    "reviewed_identity_mixed_players.json",
                    "reviewed_identity_segment_review.json",
                    "reviewed_identity_segment_decisions.json",
                    "reviewed_identity_slot_assignments.json",
                )
            }
            with patch(
                "app.services.identity_reviewed_mixed_resolution.cleanup_unreferenced_manual_reviewed_slots",
                side_effect=RuntimeError("cleanup failed"),
            ), self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                save_inline_temporal_split(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_ownership_digest": digest,
                        "existing_split_semantic_digest": initial["saved_case"]["split_semantic_digest"],
                        "resolution": "unresolved_complex_mix",
                    },
                )

            for name, before in artifacts.items():
                self.assertEqual((root / name).read_bytes(), before)

    def test_invalid_direct_parent_decision_keeps_saved_inline_split_intact(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            split = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "split",
                    "split_after_frames": [4],
                    "segment_assignments": [
                        {"action": "assign_team", "team_label": "A"},
                        {"action": "assign_team", "team_label": "B"},
                    ],
                },
            )
            before = (root / "reviewed_identity_mixed_players.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "Invalid player_id"):
                persist_reviewed_identity_correction(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "action": "assign_roster_player",
                        "player_id": "missing-player",
                        "source_ownership_digest": digest,
                    },
                    use_materialized_context=False,
                )
            self.assertEqual((root / "reviewed_identity_mixed_players.json").read_bytes(), before)
            restored = reviewed_correction_context(root, match, "subject-mixed")["temporal_split"]
            self.assertEqual(restored["split_semantic_digest"], split["saved_case"]["split_semantic_digest"])

    def test_direct_parent_decision_retires_exact_complex_mix_blocker(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            complex_case = save_inline_temporal_split(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "source_ownership_digest": digest,
                    "resolution": "unresolved_complex_mix",
                },
            )

            persist_reviewed_identity_correction(
                root,
                match,
                {
                    "candidate_subject_id": "subject-mixed",
                    "action": "assign_team",
                    "team_label": "A",
                    "source_ownership_digest": digest,
                },
                use_materialized_context=False,
            )

            self.assertFalse(any(
                row.get("case_id") == complex_case["saved_case"]["case_id"]
                for row in load_mixed_player_cases(root)["cases"]
            ))

    def test_inline_split_rolls_back_partial_child_decisions(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            before = (root / "tracklets.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "Invalid player_id"):
                save_inline_temporal_split(
                    root,
                    match,
                    {
                        "candidate_subject_id": "subject-mixed",
                        "source_ownership_digest": digest,
                        "resolution": "split",
                        "split_after_frames": [4],
                        "segment_assignments": [
                            {"action": "assign_team", "team_label": "A"},
                            {"action": "assign_roster_player", "player_id": "missing-player"},
                        ],
                    },
                )

            self.assertEqual((root / "tracklets.json").read_bytes(), before)
            self.assertFalse((root / "reviewed_identity_mixed_players.json").exists())
            self.assertFalse((root / "reviewed_identity_segment_decisions.json").exists())

    def test_inline_split_refinement_is_dense_and_bound_to_source_digest(self) -> None:
        with _workspace() as root:
            match = _fixture(root)
            tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
            tracklets["tracklets"][0]["positions_m"] = [
                {"frame": frame, "time_sec": float(frame), "x_m": float(frame), "y_m": 1.0, "detected": True, "play_area_status": "inside_play", "bbox_xyxy": [10, 10, 20, 30]}
                for frame in range(1, 122)
            ]
            _write(root / "tracklets.json", tracklets)
            digest = current_mixed_subject_digest(root, "subject-mixed")
            from app.services.identity_reviewed_mixed_store import temporal_evidence_for_observations
            from app.services.identity_reviewed_review_source import resolve_review_source
            source = resolve_review_source(root, match, candidate_subject_id="subject-mixed", source_ownership_digest=digest)
            crops = temporal_evidence_for_observations("subject-mixed", source["observations"], limit=12)
            after_frame, before_frame = crops[4]["frame"], crops[5]["frame"]
            with patch("app.services.identity_reviewed_review_source.render_mixed_review_evidence", return_value=set()):
                refinement = build_review_source_boundary_refinement(
                    root,
                    match,
                    candidate_subject_id="subject-mixed",
                    review_target_id=None,
                    continuity_group_id=None,
                    source_ownership_digest=digest,
                    after_frame=after_frame,
                    before_frame=before_frame,
                )
            self.assertEqual(len(refinement["anchor_crops"]), 10)
            self.assertTrue(all(after_frame < crop["frame"] <= before_frame for crop in refinement["anchor_crops"]))
            self.assertEqual(refinement["boundary_crops"]["after"], crops[4])
            self.assertEqual(refinement["boundary_crops"]["before"], crops[5])
            self.assertEqual(refinement["anchor_crops"][-1]["anchor_crop_id"], crops[5]["anchor_crop_id"])

            with (
                patch("app.main.match_dir", return_value=root),
                patch("app.main.read_match_meta", return_value=match),
                patch("app.services.identity_reviewed_review_source.render_mixed_review_evidence", return_value=set()),
            ):
                route = get_match_reviewed_identity_temporal_split_refinement(
                    "m1",
                    "subject-mixed",
                    digest,
                    after_frame,
                    before_frame,
                    review_target_id=None,
                    continuity_group_id=None,
                )
            self.assertEqual(route["source_ownership_digest"], digest)


def _classify(root: Path, match: dict) -> dict:
    return persist_reviewed_identity_correction(
        root,
        match,
        {"candidate_subject_id": "subject-mixed", "action": "mixed_players"},
        use_materialized_context=False,
    )["saved_decision"]


def _direct_lane(lane: dict, player_id: str) -> dict:
    return {
        "lane_id": lane["lane_id"],
        "lane_source_digest": lane["source_ownership_digest"],
        "resolution": "direct",
        "assignment": {"action": "assign_roster_player", "player_id": player_id},
    }


def _fixture(root: Path) -> dict:
    match = {
        "id": "m1",
        "status": "analyzed",
        "fps": 1,
        "teams": [
            {"team_label": "A", "players": [{"id": "player-a", "name": "Patryk"}]},
            {"team_label": "B", "players": [{"id": "player-b", "name": "Verisk"}]},
        ],
    }
    _write(root / "match.json", match)
    _write(root / "identity_candidate_shadow.json", {"subjects": [{"candidate_subject_id": "subject-mixed", "tracklet_ids": ["t1"]}]})
    _write(root / "tracklets.json", {"tracklets": [{
        "tracklet_id": "t1",
        "team_label": "A",
        "positions_m": [
            {"frame": frame, "time_sec": float(frame), "x_m": float(frame), "y_m": 1.0, "pitch_m": [float(frame), 1.0], "detected": True, "play_area_status": "inside_play", "bbox_xyxy": [10, 10, 20, 30]}
            for frame in range(1, 10)
        ],
    }]})
    _write(root / "identity_roster_subject_review_shadow.json", {"cards": [{
        "candidate_subject_id": "subject-mixed",
        "review_status": "blocked_conflict",
        "requires_operator_review": True,
        "reason_codes": ["parallel_roster_candidate_conflict"],
        "visual_evidence": {"anchor_crops": [
            {"anchor_crop_id": f"c{frame}", "artifact": f"c{frame}.jpg", "frame": frame, "time_sec": float(frame), "tracklet_id": "t1"}
            for frame in (1, 3, 5, 7, 9)
        ]},
    }]})
    _write(root / "global_identity.json", {"slots": []})
    return match


def _make_concurrent(root: Path) -> None:
    candidates = json.loads((root / "identity_candidate_shadow.json").read_text(encoding="utf-8"))
    candidates["subjects"][0]["tracklet_ids"] = ["t1", "t2"]
    _write(root / "identity_candidate_shadow.json", candidates)
    tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
    tracklets["tracklets"].append({
        "tracklet_id": "t2",
        "team_label": "A",
        "positions_m": [
            {
                "frame": frame,
                "time_sec": float(frame),
                "x_m": float(frame),
                "y_m": 2.0,
                "pitch_m": [float(frame), 2.0],
                "detected": True,
                "play_area_status": "inside_play",
                "bbox_xyxy": [30, 10, 40, 30],
            }
            for frame in range(4, 8)
        ],
    })
    _write(root / "tracklets.json", tracklets)


def _make_single_canonical_parent_target(root: Path) -> None:
    """Make t1 a canonical mixed-owner target over its complete frame range."""
    tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
    tracklets["tracklets"][0]["positions_m"].append(
        {
            "frame": 10,
            "time_sec": 10.0,
            "x_m": 10.0,
            "y_m": 1.0,
            "pitch_m": [10.0, 1.0],
            "detected": True,
            "play_area_status": "inside_play",
            "bbox_xyxy": [10, 10, 20, 30],
        }
    )
    _write(root / "tracklets.json", tracklets)
    _write(
        root / "global_identity.json",
        {
            "slots": [
                {
                    "stable_player_id": "A01",
                    "team_label": "A",
                    "tracklet_ids": ["t1"],
                    "positions_m": [
                        {"tracklet_id": "t1", "frame": frame}
                        for frame in range(1, 9)
                    ],
                },
                {
                    "stable_player_id": "B01",
                    "team_label": "B",
                    "tracklet_ids": ["t1"],
                    "positions_m": [{"tracklet_id": "t1", "frame": 10}],
                },
            ]
        },
    )


def _split_state_paths(root: Path) -> list[Path]:
    return [
        root / "reviewed_identity_mixed_players.json",
        root / "reviewed_identity_segment_review.json",
        root / "reviewed_identity_segment_decisions.json",
        root / "reviewed_identity_slot_assignments.json",
        root / "reviewed_identity_recompute_required.json",
        root / "reviewed_identity_hot_state.json",
        root / "reviewed_identity_hot_state_revision.json",
    ]


def _path_snapshots(paths: list[Path]) -> dict[str, bytes | None]:
    return {
        path.name: path.read_bytes() if path.exists() else None
        for path in paths
    }


def _reserve_canonical_slots(root: Path, count: int) -> None:
    _write(
        root / "global_identity.json",
        {"slots": [{"slot_id": f"A{index:02d}"} for index in range(1, count + 1)]},
    )


def _workflow_evidence(*, normal: int, mixed: int) -> dict:
    return {
        "match_id": "m1",
        "analysis_completed": True,
        "initial_audit": {"complete": True},
        "issues": {"blocking": normal + mixed, "normal_blocking": normal, "mixed_blocking": mixed},
        "freshness": {"review_progress_current": True},
        "render": {"status": "missing"},
    }


def _roster(match: dict) -> dict[str, dict]:
    return {
        player["id"]: {"player_id": player["id"], "player_name": player["name"], "team_label": team["team_label"]}
        for team in match["teams"]
        for player in team["players"]
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _semantic_segment_review(document: dict) -> dict:
    normalized = json.loads(json.dumps(document))
    normalized.pop("generated_at", None)
    return normalized


def _workspace():
    @contextmanager
    def workspace():
        with tempfile.TemporaryDirectory() as value:
            yield Path(value)

    return workspace()
