from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.responses import FileResponse

from app.main import (
    get_artifact,
    get_match_reviewed_identity_mixed_boundary_refinement,
    get_match_reviewed_identity_temporal_split_refinement,
    post_match_reviewed_identity_temporal_split,
)
from app.services.identity_reviewed_corrections import persist_reviewed_identity_correction
from app.services.identity_reviewed_mixed_resolution import (
    MixedPlayerTargetError,
    save_inline_temporal_split,
    save_mixed_player_resolution,
)
from app.services.identity_reviewed_mixed_store import current_mixed_subject_digest
from app.services.identity_reviewed_correction_context import reviewed_correction_context
from app.services.identity_reviewed_review_source import build_review_source_boundary_refinement
from app.services.identity_reviewed_mixed_store import (
    build_mixed_boundary_refinement,
    build_mixed_review_queue,
)
from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.identity_reviewed_segments import (
    build_segment_review_document,
    load_segment_decisions,
    load_segment_review,
    segment_observation_assignments,
)
from app.services.review_workflow_state import derive_review_workflow_state


class ReviewedIdentityMixedPlayersTests(unittest.TestCase):
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
            review = build_segment_review_document(root, match)
            targets = [row for row in review["targets"] if row.get("target_origin") == "operator_mixed_players"]
            rows = segment_observation_assignments(review, load_segment_decisions(root), _roster(match))

            self.assertEqual(response["saved_case"]["resolution_status"], "resolved")
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
            {"frame": frame, "time_sec": float(frame), "x_m": float(frame), "y_m": 1.0, "detected": True, "play_area_status": "inside_play", "bbox_xyxy": [10, 10, 20, 30]}
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


def _workspace():
    @contextmanager
    def workspace():
        with tempfile.TemporaryDirectory() as value:
            yield Path(value)

    return workspace()
