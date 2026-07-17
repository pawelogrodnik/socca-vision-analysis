from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.identity_offline_resolver_shadow import build_shadow_offline_identity


FPS = 30.0


def tracklet(tracklet_id: str, start: int, end: int, *, team: str = "A") -> dict:
    positions = [
        {
            "frame": frame,
            "time_sec": frame / FPS,
            "pitch_m": [float(frame), 10.0],
            "bbox_xyxy": [10, 10, 30, 70],
            "confidence": 0.9,
            "play_area_status": "inside_play",
        }
        for frame in range(start, end + 1)
    ]
    return {
        "tracklet_id": tracklet_id,
        "team_label": team,
        "start_time_sec": start / FPS,
        "end_time_sec": end / FPS,
        "positions": positions,
    }


def quality(rows: list[dict]) -> dict:
    return {
        "tracklets": [
            {
                "tracklet_id": row["tracklet_id"],
                "status": "clean",
                "quality_class": "recoverable",
                "quality_confidence": 0.9,
                "footpoint_reliable_ratio": 0.95,
                "appearance_reliable_ratio": 0.95,
            }
            for row in rows
        ]
    }


def stitching(*pairs: tuple[str, str]) -> dict:
    return {
        "candidate_edges": [
            {
                "candidate_key": f"{source}-{target}",
                "source_tracklet_id": source,
                "target_tracklet_id": target,
                "recommended": True,
                "base_confidence": 0.9,
                "cost": 0.1,
                "occlusion_event_ids": [],
            }
            for source, target in pairs
        ]
    }


def joint(
    case_key: str,
    assignment_id: str | None,
    pairs_a: list[tuple[str, str]],
    pairs_b: list[tuple[str, str]],
    *,
    partial_pair: tuple[str, str] | None = None,
    confidence: float = 0.9,
) -> dict:
    return {
        "cases": [
            {
                "case_key": case_key,
                "occlusion_event_ids": ["occ-1"],
                "assignments": [
                    {
                        "assignment_id": "assignment_a",
                        "pairs": [
                            {"source_tracklet_id": source, "target_tracklet_id": target}
                            for source, target in pairs_a
                        ],
                    },
                    {
                        "assignment_id": "assignment_b",
                        "pairs": [
                            {"source_tracklet_id": source, "target_tracklet_id": target}
                            for source, target in pairs_b
                        ],
                    },
                ],
                "decision": {
                    "recommended_assignment_id": assignment_id,
                    "recommended_pairs": (
                        [{"source_tracklet_id": partial_pair[0], "target_tracklet_id": partial_pair[1]}]
                        if partial_pair
                        else []
                    ),
                    "confidence": confidence,
                },
            }
        ]
    }


def identity(*groups: tuple[str, list[str]]) -> dict:
    return {
        "slots": [
            {"stable_subject_id": subject_id, "tracklet_ids": tracklet_ids}
            for subject_id, tracklet_ids in groups
        ]
    }


class OfflineIdentityResolverShadowTests(unittest.TestCase):
    def test_global_path_uses_strong_candidate_outside_local_recommendations(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("t1", 12, 20)]
        candidates = {
            "candidate_edges": [
                {
                    "candidate_key": "global-only",
                    "source_tracklet_id": "s1",
                    "target_tracklet_id": "t1",
                    "recommended": False,
                    "cost": 0.1,
                    "base_confidence": 0.9,
                    "recommendation_guard_reasons": [],
                }
            ]
        }

        timeline = build_shadow_offline_identity(
            rows,
            quality(rows),
            candidates,
            {"cases": []},
            identity(),
            fps=FPS,
            generated_at="fixed",
        )["identity_offline_shadow"]

        self.assertEqual(timeline["summary"]["accepted_edges"], 1)
        self.assertEqual(timeline["global_path_selection"]["admissible_candidate_count"], 1)

    def test_global_path_abstains_when_candidate_has_blocking_guard(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("t1", 12, 20)]
        candidates = {
            "candidate_edges": [
                {
                    "candidate_key": "unsafe-appearance",
                    "source_tracklet_id": "s1",
                    "target_tracklet_id": "t1",
                    "recommended": False,
                    "cost": 0.1,
                    "base_confidence": 0.9,
                    "recommendation_guard_reasons": [
                        "appearance_distance_too_high_for_recommendation"
                    ],
                }
            ]
        }

        timeline = build_shadow_offline_identity(
            rows,
            quality(rows),
            candidates,
            {"cases": []},
            identity(),
            fps=FPS,
            generated_at="fixed",
        )["identity_offline_shadow"]

        self.assertEqual(timeline["summary"]["accepted_edges"], 0)
        self.assertIn(
            "candidate_guard_blocks_global_link:appearance_distance_too_high_for_recommendation",
            timeline["rejected_recommendation_groups"][0]["rejection_reasons"],
        )

    def test_disjoint_forced_conflicts_are_resolved_per_component(self) -> None:
        rows = [
            tracklet("s1", 0, 9),
            tracklet("s2", 0, 9),
            tracklet("t1", 12, 20),
            tracklet("t2", 12, 20),
            tracklet("s3", 30, 39),
            tracklet("s4", 30, 39),
            tracklet("t3", 42, 50),
            tracklet("t4", 42, 50),
        ]
        cases = []
        for case_key, pair, confidence in (
            ("one-a", ("s1", "t1"), 0.9),
            ("one-b", ("s1", "t2"), 0.8),
            ("two-a", ("s3", "t3"), 0.95),
            ("two-b", ("s3", "t4"), 0.7),
        ):
            cases.extend(
                joint(
                    case_key,
                    "partial",
                    [pair],
                    [pair],
                    partial_pair=pair,
                    confidence=confidence,
                )["cases"]
            )

        documents = build_shadow_offline_identity(
            rows,
            quality(rows),
            {"candidate_edges": []},
            {"cases": cases},
            identity(),
            fps=FPS,
            generated_at="fixed",
        )
        timeline = documents["identity_offline_shadow"]

        self.assertEqual(timeline["global_path_selection"]["forced_conflict_count"], 2)
        self.assertEqual(timeline["summary"]["accepted_joint_groups"], 2)
        self.assertEqual(
            {
                (edge["source_tracklet_id"], edge["target_tracklet_id"])
                for edge in timeline["accepted_edges"]
            },
            {("s1", "t1"), ("s3", "t3")},
        )

    def test_weak_scored_candidate_abstains(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("t1", 12, 20)]
        weak = {"candidate_edges": [{"candidate_key": "weak", "source_tracklet_id": "s1", "target_tracklet_id": "t1", "cost": 0.9, "base_confidence": 0.4}]}
        timeline = build_shadow_offline_identity(rows, quality(rows), weak, {"cases": []}, identity(), fps=FPS, generated_at="fixed")["identity_offline_shadow"]
        self.assertEqual(timeline["summary"]["accepted_edges"], 0)
        self.assertIn("candidate_confidence_below_threshold", timeline["rejected_recommendation_groups"][0]["rejection_reasons"])

    def test_boundary_competition_reconciles_shared_endpoint(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("s2", 900, 909), tracklet("t1", 12, 20), tracklet("t2", 912, 920)]
        candidates = {"candidate_edges": [
            {"candidate_key": "early", "source_tracklet_id": "s1", "target_tracklet_id": "t2", "cost": 0.2, "base_confidence": 0.9},
            {"candidate_key": "late", "source_tracklet_id": "s2", "target_tracklet_id": "t2", "cost": 0.1, "base_confidence": 0.9},
        ]}
        timeline = build_shadow_offline_identity(rows, quality(rows), candidates, {"cases": []}, identity(), fps=FPS, generated_at="fixed")["identity_offline_shadow"]
        self.assertEqual(sum(edge["target_tracklet_id"] == "t2" for edge in timeline["accepted_edges"]), 1)

    def test_forced_conflict_is_audited(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("s2", 0, 9), tracklet("t1", 12, 20), tracklet("t2", 12, 20)]
        joint_doc = joint("one", "partial", [("s1", "t1")], [("s1", "t2")], partial_pair=("s1", "t1"))
        joint_doc["cases"].append(joint("two", "partial", [("s1", "t2")], [("s1", "t1")], partial_pair=("s1", "t2"))["cases"][0])
        documents = build_shadow_offline_identity(rows, quality(rows), {"candidate_edges": []}, joint_doc, identity(), fps=FPS, generated_at="fixed")
        timeline = documents["identity_offline_shadow"]
        self.assertEqual(timeline["global_path_selection"]["forced_conflict_count"], 1)
        self.assertFalse(documents["identity_offline_shadow_report"]["gates"]["forced_joint_constraints_feasible"])

    def test_report_estimates_manual_review_from_shadow_subjects_not_edge_subtraction(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("t1", 12, 20)]
        documents = build_shadow_offline_identity(rows, quality(rows), stitching(("s1", "t1")), {"cases": []}, identity(), fps=FPS, fragmentation_doc={"summary": {"estimated_manual_review_items": 4, "ambiguous_timeline_seconds": 2.0}}, generated_at="fixed")
        summary = documents["identity_offline_shadow_report"]["summary"]
        self.assertEqual(summary["manual_review_effort_after"], 1)
        self.assertEqual(summary["manual_review_effort_delta"], -3)
        self.assertEqual(
            summary["manual_review_estimate_method"],
            "one_item_per_shadow_subject_with_recoverable_or_ambiguous_tracklet",
        )
        self.assertEqual(summary["cross_production_subject_link_count"], 0)
    def test_global_path_beats_locally_cheapest_edge(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("s2", 0, 9), tracklet("t1", 12, 20), tracklet("t2", 12, 20)]
        doc = {
            "candidate_edges": [
                {"candidate_key": "s1-t1", "source_tracklet_id": "s1", "target_tracklet_id": "t1", "cost": 0.1, "base_confidence": 0.9},
                {"candidate_key": "s1-t2", "source_tracklet_id": "s1", "target_tracklet_id": "t2", "cost": 0.2, "base_confidence": 0.8},
                {"candidate_key": "s2-t1", "source_tracklet_id": "s2", "target_tracklet_id": "t1", "cost": 0.11, "base_confidence": 0.89},
            ]
        }
        timeline = build_shadow_offline_identity(rows, quality(rows), doc, {"cases": []}, identity(), fps=FPS, generated_at="fixed")["identity_offline_shadow"]
        self.assertEqual({(row["source_tracklet_id"], row["target_tracklet_id"]) for row in timeline["accepted_edges"]}, {("s1", "t2"), ("s2", "t1")})

    def test_global_path_is_deterministic_for_ties_and_team_isolated(self) -> None:
        rows = [tracklet("s1", 0, 9, team="A"), tracklet("t1", 12, 20, team="A"), tracklet("b1", 0, 9, team="B"), tracklet("b2", 12, 20, team="B")]
        doc = {"candidate_edges": [
            {"candidate_key": "s1-t1", "source_tracklet_id": "s1", "target_tracklet_id": "t1", "cost": 0.2},
            {"candidate_key": "s1-b2", "source_tracklet_id": "s1", "target_tracklet_id": "b2", "cost": 0.0},
            {"candidate_key": "b1-b2", "source_tracklet_id": "b1", "target_tracklet_id": "b2", "cost": 0.2},
        ]}
        first = build_shadow_offline_identity(rows, quality(rows), doc, {"cases": []}, identity(), fps=FPS, generated_at="fixed")
        second = build_shadow_offline_identity(rows, quality(rows), doc, {"cases": []}, identity(), fps=FPS, generated_at="fixed")
        self.assertEqual(first, second)
        self.assertNotIn(("s1", "b2"), {(row["source_tracklet_id"], row["target_tracklet_id"]) for row in first["identity_offline_shadow"]["accepted_edges"]})
    def test_recommended_stitch_builds_parallel_subject_and_explicit_gap(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("t1", 12, 20)]
        documents = build_shadow_offline_identity(
            rows,
            quality(rows),
            stitching(("s1", "t1")),
            {"cases": []},
            identity(("A01", ["s1", "t1"])),
            fps=FPS,
            generated_at="fixed",
        )

        timeline = documents["identity_offline_shadow"]
        self.assertEqual(timeline["summary"]["accepted_edges"], 1)
        self.assertEqual(timeline["summary"]["shadow_subjects"], 1)
        self.assertEqual(
            [row["status"] for row in timeline["subjects"][0]["timeline_segments"]],
            ["detected", "missing", "detected"],
        )

    def test_partial_joint_assignment_links_only_confirmed_pair(self) -> None:
        rows = [
            tracklet("s1", 0, 9),
            tracklet("s2", 0, 9),
            tracklet("t1", 12, 20),
            tracklet("t2", 12, 20),
        ]
        documents = build_shadow_offline_identity(
            rows,
            quality(rows),
            {"candidate_edges": []},
            joint(
                "case",
                "partial",
                [("s1", "t1"), ("s2", "t2")],
                [("s1", "t2"), ("s2", "t1")],
                partial_pair=("s2", "t1"),
            ),
            identity(),
            fps=FPS,
            generated_at="fixed",
        )

        timeline = documents["identity_offline_shadow"]
        self.assertEqual(timeline["summary"]["accepted_edges"], 1)
        self.assertEqual(timeline["summary"]["shadow_subjects"], 3)
        self.assertEqual(
            timeline["accepted_edges"][0]["source_tracklet_id"],
            "s2",
        )
        self.assertEqual(timeline["accepted_edges"][0]["target_tracklet_id"], "t1")

    def test_abstained_joint_assignment_does_not_merge_tracklets(self) -> None:
        rows = [
            tracklet("s1", 0, 9),
            tracklet("s2", 0, 9),
            tracklet("t1", 12, 20),
            tracklet("t2", 12, 20),
        ]
        documents = build_shadow_offline_identity(
            rows,
            quality(rows),
            {"candidate_edges": []},
            joint(
                "case",
                None,
                [("s1", "t1"), ("s2", "t2")],
                [("s1", "t2"), ("s2", "t1")],
            ),
            identity(),
            fps=FPS,
            generated_at="fixed",
        )

        self.assertEqual(documents["identity_offline_shadow"]["summary"]["accepted_edges"], 0)
        self.assertEqual(documents["identity_offline_shadow"]["summary"]["shadow_subjects"], 4)

    def test_joint_assignment_is_atomic_when_one_endpoint_is_already_taken(self) -> None:
        rows = [
            tracklet("s0", 0, 4),
            tracklet("s1", 5, 9),
            tracklet("s2", 5, 9),
            tracklet("t1", 12, 20),
            tracklet("t2", 12, 20),
        ]
        joint_doc = joint(
            "full",
            "assignment_a",
            [("s1", "t1"), ("s2", "t2")],
            [("s1", "t2"), ("s2", "t1")],
            confidence=0.8,
        )
        joint_doc["cases"].append(
            joint(
                "partial",
                "partial",
                [("s0", "t1"), ("s2", "t2")],
                [("s0", "t2"), ("s2", "t1")],
                partial_pair=("s0", "t1"),
                confidence=0.95,
            )["cases"][0]
        )
        documents = build_shadow_offline_identity(
            rows,
            quality(rows),
            {"candidate_edges": []},
            joint_doc,
            identity(),
            fps=FPS,
            generated_at="fixed",
        )

        timeline = documents["identity_offline_shadow"]
        self.assertEqual(timeline["summary"]["accepted_edges"], 1)
        rejected = next(row for row in timeline["rejected_recommendation_groups"] if row["source_key"] == "full")
        self.assertIn("target_predecessor_already_assigned", rejected["rejection_reasons"])

    def test_temporal_overlap_is_rejected_without_mutating_inputs(self) -> None:
        rows = [tracklet("s1", 0, 15), tracklet("t1", 10, 20)]
        original = deepcopy(rows)
        args = (
            rows,
            quality(rows),
            stitching(("s1", "t1")),
            {"cases": []},
            identity(),
        )
        first = build_shadow_offline_identity(*args, fps=FPS, generated_at="fixed")
        second = build_shadow_offline_identity(*args, fps=FPS, generated_at="fixed")

        self.assertEqual(first, second)
        self.assertEqual(rows, original)
        self.assertEqual(first["identity_offline_shadow"]["summary"]["accepted_edges"], 0)
        self.assertIn(
            "temporal_overlap",
            first["identity_offline_shadow"]["rejected_recommendation_groups"][0]["rejection_reasons"],
        )

    def test_safe_production_continuity_is_used_as_low_priority_baseline(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("t1", 12, 20)]
        documents = build_shadow_offline_identity(
            rows,
            quality(rows),
            {"candidate_edges": []},
            {"cases": []},
            identity(("A01", ["s1", "t1"])),
            fps=FPS,
            fragmentation_doc={"suspected_switches": []},
            generated_at="fixed",
        )

        timeline = documents["identity_offline_shadow"]
        self.assertEqual(timeline["summary"]["accepted_baseline_continuity_groups"], 1)
        self.assertEqual(timeline["summary"]["shadow_subjects"], 1)
        self.assertEqual(timeline["accepted_edges"][0]["recommendation_source"], "production_continuity")

    def test_suspected_switch_is_not_restored_by_baseline_continuity(self) -> None:
        rows = [tracklet("s1", 0, 9), tracklet("t1", 12, 20)]
        documents = build_shadow_offline_identity(
            rows,
            quality(rows),
            {"candidate_edges": []},
            {"cases": []},
            identity(("A01", ["s1", "t1"])),
            fps=FPS,
            fragmentation_doc={
                "suspected_switches": [
                    {"from_tracklet_id": "s1", "to_tracklet_id": "t1"},
                ]
            },
            generated_at="fixed",
        )

        timeline = documents["identity_offline_shadow"]
        self.assertEqual(timeline["summary"]["accepted_edges"], 0)
        self.assertEqual(timeline["summary"]["shadow_subjects"], 2)
        self.assertEqual(
            timeline["baseline_continuity_audit"]["skipped_reason_counts"]["baseline_suspected_switch"],
            1,
        )

    def test_p0_recommendation_preempts_conflicting_baseline_edge(self) -> None:
        rows = [
            tracklet("s1", 0, 9),
            tracklet("t1", 12, 20),
            tracklet("t2", 12, 20),
        ]
        documents = build_shadow_offline_identity(
            rows,
            quality(rows),
            stitching(("s1", "t2")),
            {"cases": []},
            identity(("A01", ["s1", "t1"]), ("A02", ["t2"])),
            fps=FPS,
            fragmentation_doc={"suspected_switches": []},
            generated_at="fixed",
        )

        timeline = documents["identity_offline_shadow"]
        self.assertEqual(timeline["summary"]["accepted_edges"], 1)
        self.assertEqual(timeline["accepted_edges"][0]["target_tracklet_id"], "t2")
        rejected_baseline = next(
            row
            for row in timeline["rejected_recommendation_groups"]
            if row["source"] == "production_continuity"
        )
        self.assertIn("source_successor_already_assigned", rejected_baseline["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
