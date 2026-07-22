from __future__ import annotations

import unittest

from app.services.identity_promotion_safety import (
    build_promotion_safety_sections,
    canonicalize_promoted_observations,
    structural_conflict_reasons,
)


def observation(
    frame: int,
    tracklet: str,
    *,
    player: str = "p1",
    subject: str = "s1",
    pitch: list[float] | None = None,
    status: str = "detected",
) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 10,
        "tracklet_id": tracklet,
        "player_id": player,
        "candidate_subject_id": subject,
        "status": status,
        "play_area_status": "inside_play",
        "footpoint_reliable": True,
        "eligible_for_distance": True,
        "eligible_for_heatmap": True,
        "confidence": 0.9,
        "pitch_m": pitch,
        "bbox_xyxy": [10, 10, 20, 30],
        "subject_start_frame": frame,
        "subject_end_frame": frame,
        "production_subject_ids": ["slot-1"],
    }


class IdentityPromotionSafetyTests(unittest.TestCase):
    def test_same_source_boundary_duplicate_is_safe(self) -> None:
        rows = [
            observation(10, "t1", subject="s1", pitch=[1, 1]),
            observation(10, "t1", subject="s2", pitch=[1, 1]),
        ]
        canonical, duplicates, conflicts = canonicalize_promoted_observations(rows)

        self.assertEqual(len(canonical), 1)
        self.assertEqual(duplicates[0]["classification"], "same_source_duplicate")
        self.assertTrue(duplicates[0]["safe_to_deduplicate"])
        self.assertFalse(conflicts)

    def test_near_identical_spatial_duplicate_is_safe(self) -> None:
        left = observation(10, "t1", subject="s1", pitch=[1.0, 1.0])
        right = observation(10, "t2", subject="s2", pitch=[1.2, 1.1])
        right["bbox_xyxy"] = [11, 10, 21, 30]
        _, duplicates, conflicts = canonicalize_promoted_observations([left, right])

        self.assertEqual(duplicates[0]["classification"], "near_identical_spatial_duplicate")
        self.assertFalse(conflicts)

    def test_distant_parallel_observations_block(self) -> None:
        rows = [
            observation(10, "t1", subject="s1", pitch=[1, 1]),
            observation(10, "t2", subject="s2", pitch=[20, 20]),
        ]
        _, duplicates, conflicts = canonicalize_promoted_observations(rows)

        self.assertEqual(duplicates[0]["classification"], "parallel_distant_conflict")
        self.assertEqual(conflicts[0]["code"], "same_player_parallel_spatial_conflict")

    def test_structural_flag_is_not_treated_as_review_only(self) -> None:
        reasons = structural_conflict_reasons(
            {},
            {"quality_flags": ["merges_multiple_production_subjects"]},
            {},
        )
        self.assertEqual(reasons, ["merges_production_subjects"])
        self.assertFalse(
            structural_conflict_reasons(
                {"blockers": ["insufficient_visual_evidence"]}, {}, {}
            )
        )

    def test_one_frame_overflow_warns_and_sustained_overflow_blocks(self) -> None:
        roster = {f"p{index}": {"id": f"p{index}"} for index in range(1, 9)}
        one_frame = [
            observation(10, f"t{index}", player=f"p{index}", subject=f"s{index}")
            for index in range(1, 9)
        ]
        sections = build_promotion_safety_sections(
            canonical_observations=one_frame,
            all_review_observations=one_frame,
            unresolved_observations=[],
            structural_subjects=[],
            roster=roster,
            match_doc={},
            team_label="A",
            fps=10,
        )
        self.assertEqual(
            sections["active_player_validation"]["warnings"][0]["code"],
            "team_active_player_limit_spike",
        )
        self.assertFalse(sections["active_player_validation"]["errors"])

        sustained = [
            observation(frame, f"t{index}", player=f"p{index}", subject=f"s{index}")
            for frame in range(10, 16)
            for index in range(1, 9)
        ]
        sections = build_promotion_safety_sections(
            canonical_observations=sustained,
            all_review_observations=sustained,
            unresolved_observations=[],
            structural_subjects=[],
            roster=roster,
            match_doc={},
            team_label="A",
            fps=10,
        )
        self.assertEqual(
            sections["active_player_validation"]["errors"][0]["code"],
            "team_active_player_limit_sustained",
        )

    def test_trusted_goalkeepers_block_but_visual_guess_only_warns(self) -> None:
        rows = [
            observation(10, "t1", player="g1", subject="s1"),
            observation(10, "t2", player="g2", subject="s2", pitch=[2, 2]),
        ]
        roster = {
            "g1": {"id": "g1", "role": "goalkeeper"},
            "g2": {"id": "g2", "number": "gk"},
        }
        sections = build_promotion_safety_sections(
            canonical_observations=rows,
            all_review_observations=rows,
            unresolved_observations=[],
            structural_subjects=[],
            roster=roster,
            match_doc={},
            team_label="A",
            fps=10,
        )
        self.assertEqual(
            sections["goalkeeper_validation"]["errors"][0]["code"],
            "multiple_goalkeepers_active",
        )

        rows[1]["role"] = "goalkeeper"
        sections = build_promotion_safety_sections(
            canonical_observations=rows,
            all_review_observations=rows,
            unresolved_observations=[],
            structural_subjects=[],
            roster={"g1": {"id": "g1", "role": "goalkeeper"}, "g2": {"id": "g2"}},
            match_doc={},
            team_label="A",
            fps=10,
        )
        self.assertFalse(sections["goalkeeper_validation"]["errors"])
        self.assertTrue(sections["goalkeeper_validation"]["warnings"])

    def test_unknown_player_denominator_and_missing_ball_do_not_block(self) -> None:
        rows = [observation(10, "t1")]
        sections = build_promotion_safety_sections(
            canonical_observations=rows,
            all_review_observations=rows,
            unresolved_observations=[],
            structural_subjects=[],
            roster={"p1": {"id": "p1", "name": "One"}},
            match_doc={},
            team_label="A",
            fps=10,
        )
        player = sections["player_readiness"][0]
        self.assertEqual(player["coverage_denominator"], "unknown")
        self.assertIsNone(player["detected_coverage_ratio"])
        self.assertFalse(sections["errors"])
        self.assertFalse(sections["downstream_readiness"]["ball_artifacts_required"])


if __name__ == "__main__":
    unittest.main()
