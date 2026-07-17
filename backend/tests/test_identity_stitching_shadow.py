from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.identity_stitching_shadow import (
    build_shadow_stitching_candidates,
    score_stitching_edge,
)
from app.services.stabilization import _build_identity_diagnostics_safely


FPS = 30.0


def tracklet(
    tracklet_id: str,
    start_frame: int,
    end_frame: int,
    *,
    x_start: float,
    x_end: float,
    team: str = "A",
    team_confidence: float = 0.95,
    source_tracker_id: int | None = None,
    role: str = "field_player",
    role_confidence: float = 0.0,
    appearance_rgb: list[float] | None = None,
) -> dict:
    positions = []
    span = max(1, end_frame - start_frame)
    for frame in range(start_frame, end_frame + 1):
        ratio = (frame - start_frame) / span
        x = x_start + (x_end - x_start) * ratio
        positions.append(
            {
                "frame": frame,
                "time_sec": frame / FPS,
                "pitch_m": [x, 20.0],
                "smoothed_pitch_m": [x, 20.0],
                "bbox_xyxy": [int(x * 10), 100, int(x * 10) + 24, 170],
                "confidence": 0.9,
                "play_area_status": "inside_play",
            }
        )
    return {
        "tracklet_id": tracklet_id,
        "source_tracker_id": source_tracker_id if source_tracker_id is not None else int(tracklet_id.split(":")[0]),
        "start_time_sec": start_frame / FPS,
        "end_time_sec": end_frame / FPS,
        "duration_sec": (end_frame - start_frame) / FPS,
        "positions_count": len(positions),
        "positions": positions,
        "first_pitch_m": positions[0]["pitch_m"],
        "last_pitch_m": positions[-1]["pitch_m"],
        "first_bbox_xyxy": positions[0]["bbox_xyxy"],
        "last_bbox_xyxy": positions[-1]["bbox_xyxy"],
        "team_label": team,
        "team_confidence": team_confidence,
        "role": role,
        "role_confidence": role_confidence,
        "mean_confidence": 0.9,
        "appearance_rgb": appearance_rgb or [120.0, 120.0, 120.0],
        "appearance_quality": 0.8,
        "appearance_samples": 8,
        "appearance_feature": [1.0, 2.0, 3.0],
    }


def quality(tracklet_id: str, quality_class: str = "recoverable", confidence: float = 0.9) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "status": "clean",
        "quality_class": quality_class,
        "quality_confidence": confidence,
        "team_label": "A",
        "inside_pitch_ratio": 1.0,
    }


def empty_identity() -> dict:
    return {"slots": [], "suppressed_duplicate_observations": [], "unmatched_observations": []}


class ShadowStitchingTests(unittest.TestCase):
    def test_hard_constraints_block_team_speed_and_role_conflicts(self) -> None:
        source = tracklet("1:1", 0, 10, x_start=0.0, x_end=1.0, team="A")
        wrong_team = tracklet("2:1", 11, 20, x_start=1.1, x_end=2.0, team="B")
        too_fast = tracklet("3:1", 11, 20, x_start=10.0, x_end=11.0, team="A")
        goalkeeper = tracklet(
            "4:1",
            11,
            20,
            x_start=1.1,
            x_end=2.0,
            team="A",
            role="goalkeeper",
            role_confidence=0.95,
        )
        source["role_confidence"] = 0.95
        qualities = {row["tracklet_id"]: quality(row["tracklet_id"]) for row in (source, wrong_team, too_fast, goalkeeper)}

        team_result = score_stitching_edge(
            source,
            wrong_team,
            quality_by_id=qualities,
            occlusion_event_ids=[],
            subject_by_tracklet={},
            fps=FPS,
        )
        speed_result = score_stitching_edge(
            source,
            too_fast,
            quality_by_id=qualities,
            occlusion_event_ids=[],
            subject_by_tracklet={},
            fps=FPS,
        )
        role_result = score_stitching_edge(
            source,
            goalkeeper,
            quality_by_id=qualities,
            occlusion_event_ids=[],
            subject_by_tracklet={},
            fps=FPS,
        )

        self.assertIn("certain_team_mismatch", team_result["blocked_reasons"])
        self.assertIn("impossible_required_speed", speed_result["blocked_reasons"])
        self.assertIn("certain_role_mismatch", role_result["blocked_reasons"])

    def test_same_raw_continuation_is_ranked_as_recommended(self) -> None:
        source = tracklet("1:1", 0, 20, x_start=0.0, x_end=2.0, source_tracker_id=1)
        continuation = tracklet("1:2", 21, 40, x_start=2.1, x_end=4.0, source_tracker_id=1)
        competitor = tracklet("2:1", 21, 40, x_start=3.5, x_end=5.0, source_tracker_id=2)
        qualities = {
            "1:1": quality("1:1", "trusted"),
            "1:2": quality("1:2"),
            "2:1": quality("2:1", "trusted"),
        }
        document = build_shadow_stitching_candidates(
            [source, continuation, competitor],
            {"tracklets": list(qualities.values())},
            {"events": []},
            empty_identity(),
            fps=FPS,
            generated_at="fixed",
        )

        focus = next(row for row in document["focus_tracklets"] if row["tracklet_id"] == "1:2")
        self.assertEqual(focus["predecessor_decision"]["status"], "recommended")
        self.assertEqual(focus["predecessor_decision"]["counterpart_tracklet_id"], "1:1")

    def test_close_alternatives_remain_ambiguous(self) -> None:
        first = tracklet("1:1", 0, 20, x_start=0.0, x_end=2.0)
        second = tracklet("2:1", 0, 20, x_start=0.1, x_end=2.1)
        target = tracklet("3:1", 21, 40, x_start=2.2, x_end=4.0)
        qualities = {
            "1:1": quality("1:1", "trusted"),
            "2:1": quality("2:1", "trusted"),
            "3:1": quality("3:1"),
        }
        document = build_shadow_stitching_candidates(
            [first, second, target],
            {"tracklets": list(qualities.values())},
            {"events": []},
            empty_identity(),
            fps=FPS,
            generated_at="fixed",
        )

        focus = next(row for row in document["focus_tracklets"] if row["tracklet_id"] == "3:1")
        self.assertEqual(focus["predecessor_decision"]["status"], "ambiguous")
        self.assertEqual(len(focus["predecessor_decision"]["candidates"]), 2)

    def test_missing_raw_tracker_ids_do_not_create_continuity_bonus(self) -> None:
        source = tracklet("1:1", 0, 20, x_start=0.0, x_end=2.0)
        target = tracklet("2:1", 21, 40, x_start=2.1, x_end=4.0)
        source.pop("source_tracker_id")
        target.pop("source_tracker_id")
        qualities = {"1:1": quality("1:1"), "2:1": quality("2:1")}

        result = score_stitching_edge(
            source,
            target,
            quality_by_id=qualities,
            occlusion_event_ids=[],
            subject_by_tracklet={},
            fps=FPS,
        )

        self.assertNotIn("same_raw_tracker", result["bonuses"])
        self.assertNotIn("same_raw_tracker", result["evidence"])

    def test_uncertain_known_team_mismatch_is_never_recommended(self) -> None:
        source = tracklet(
            "1:1",
            0,
            20,
            x_start=0.0,
            x_end=2.0,
            team="A",
            team_confidence=0.5,
            source_tracker_id=1,
        )
        target = tracklet(
            "1:2",
            21,
            40,
            x_start=2.1,
            x_end=4.0,
            team="B",
            team_confidence=1.0,
            source_tracker_id=1,
        )
        quality_doc = {"tracklets": [quality("1:1"), quality("1:2", "trusted")]}

        document = build_shadow_stitching_candidates(
            [source, target],
            quality_doc,
            {"events": []},
            empty_identity(),
            fps=FPS,
            generated_at="fixed",
        )

        edge = document["candidate_edges"][0]
        self.assertFalse(edge["recommended"])
        self.assertIn("team_mismatch_not_safe_for_recommendation", edge["recommendation_guard_reasons"])

    def test_output_is_deterministic_and_does_not_mutate_inputs(self) -> None:
        source = tracklet("1:1", 0, 20, x_start=0.0, x_end=2.0)
        target = tracklet("1:2", 21, 40, x_start=2.1, x_end=4.0, source_tracker_id=1)
        rows = [source, target]
        original = deepcopy(rows)
        quality_doc = {"tracklets": [quality("1:1", "trusted"), quality("1:2")]}

        first = build_shadow_stitching_candidates(
            rows,
            quality_doc,
            {"events": []},
            empty_identity(),
            fps=FPS,
            generated_at="fixed",
        )
        second = build_shadow_stitching_candidates(
            rows,
            quality_doc,
            {"events": []},
            empty_identity(),
            fps=FPS,
            generated_at="fixed",
        )

        self.assertEqual(first, second)
        self.assertEqual(rows, original)

    def test_stitching_failure_keeps_p0_diagnostic_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "app.services.stabilization.build_shadow_stitching_candidates",
                side_effect=RuntimeError("stitching boom"),
            ):
                documents, warning = _build_identity_diagnostics_safely(
                    Path(directory),
                    [],
                    [],
                    empty_identity(),
                    fps=FPS,
                    enabled=True,
                )

        self.assertIn("identity_tracklet_quality", documents)
        self.assertNotIn("identity_stitching_candidates", documents)
        self.assertIn("stitching boom", warning or "")

    def test_joint_assignment_failure_keeps_stitching_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "app.services.stabilization.build_shadow_occlusion_assignments",
                side_effect=RuntimeError("joint assignment boom"),
            ):
                documents, warning = _build_identity_diagnostics_safely(
                    Path(directory),
                    [],
                    [],
                    empty_identity(),
                    fps=FPS,
                    enabled=True,
                )

        self.assertIn("identity_stitching_candidates", documents)
        self.assertNotIn("identity_occlusion_assignments", documents)
        self.assertIn("joint assignment boom", warning or "")

    def test_offline_resolver_failure_keeps_all_p0_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "app.services.stabilization.build_shadow_offline_identity",
                side_effect=RuntimeError("offline resolver boom"),
            ):
                documents, warning = _build_identity_diagnostics_safely(
                    Path(directory),
                    [],
                    [],
                    empty_identity(),
                    fps=FPS,
                    enabled=True,
                )

        self.assertIn("identity_stitching_candidates", documents)
        self.assertIn("identity_occlusion_assignments", documents)
        self.assertNotIn("identity_offline_shadow", documents)
        self.assertNotIn("identity_offline_shadow_report", documents)
        self.assertIn("offline resolver boom", warning or "")


if __name__ == "__main__":
    unittest.main()
