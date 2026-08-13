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
)
from app.services.identity_reviewed_corrections import persist_reviewed_identity_correction
from app.services.identity_reviewed_mixed_resolution import save_mixed_player_resolution
from app.services.identity_reviewed_mixed_store import (
    build_mixed_boundary_refinement,
    build_mixed_review_queue,
)
from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.identity_reviewed_segments import (
    build_segment_review_document,
    load_segment_decisions,
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
            _write(root / "tracklets.json", tracklets)
            _classify(root, match)
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
            self.assertTrue(all(after_frame <= crop["frame"] <= before_frame for crop in refinement["anchor_crops"]))
            self.assertEqual(refinement["after_frame"], after_frame)
            self.assertEqual(refinement["before_frame"], before_frame)
            render.assert_called_once()

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
            self.assertEqual([crop["frame"] for crop in response["anchor_crops"]], [1, 2])

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
            workflow = derive_review_workflow_state(_workflow_evidence(normal=0, mixed=1))
            self.assertEqual(workflow["phase"], "mixed_players")
            self.assertIn("review_mixed_players", workflow["allowed_actions"])

    def test_resolved_or_legacy_matches_do_not_force_mixed_step(self) -> None:
        legacy = derive_review_workflow_state(_workflow_evidence(normal=0, mixed=0))
        self.assertEqual(legacy["phase"], "ready_to_finalize")
        self.assertEqual(next(row for row in legacy["steps"] if row["id"] == "mixed_players")["status"], "completed")


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
