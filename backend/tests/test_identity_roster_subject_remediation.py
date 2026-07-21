from __future__ import annotations

import unittest

from app.services.identity_roster_subject_remediation import (
    build_identity_roster_subject_remediation_plan,
    stable_fragment_key,
)


def _row(subject: str, frame: int, player: str = "p1", tracklet: str = "t1") -> dict:
    return {
        "candidate_subject_id": subject,
        "frame": frame,
        "time_sec": frame / 30,
        "player_id": player,
        "tracklet_id": tracklet,
        "status": "detected",
        "eligible_for_distance": True,
        "eligible_for_heatmap": True,
    }


class IdentityRosterSubjectRemediationTests(unittest.TestCase):
    def test_structural_subject_is_excluded_but_safe_rows_remain(self) -> None:
        plan = {
            "canonical_coverage": [{"frame_records": [_row("safe", 1), _row("safe", 2)]}],
            "structural_subjects": [{
                "candidate_subject_id": "bad",
                "start_frame": 3,
                "end_frame": 4,
                "tracklet_ids": ["t2"],
                "frame_records": [_row("bad", 3, tracklet="t2"), _row("bad", 4, tracklet="t2")],
            }],
            "duplicate_observations": [],
        }
        result = build_identity_roster_subject_remediation_plan(plan, generated_at="fixed")
        self.assertEqual(result["status"], "ready_for_partial_candidate")
        self.assertEqual(len(result["eligible_observations"]), 2)
        self.assertEqual(result["summary"]["structural_subjects_auto_excluded"], 1)

    def test_assign_fragment_makes_exact_structural_range_eligible(self) -> None:
        plan = {
            "canonical_coverage": [],
            "structural_subjects": [{
                "candidate_subject_id": "bad",
                "start_frame": 3,
                "end_frame": 5,
                "tracklet_ids": ["t2"],
                "frame_records": [_row("bad", frame, tracklet="t2") for frame in range(3, 6)],
            }],
            "duplicate_observations": [],
        }
        from app.services.identity_promotion_safety import canonical_document_digest
        decisions = {
            "source_promotion_plan_digest": canonical_document_digest(plan),
            "decisions": [{
                "action": "assign_fragment",
                "candidate_subject_id": "bad",
                "start_frame": 4,
                "end_frame": 5,
                "tracklet_ids": ["t2"],
                "player_id": "p2",
            }],
        }
        result = build_identity_roster_subject_remediation_plan(plan, decisions, generated_at="fixed")
        self.assertEqual([row["frame"] for row in result["eligible_observations"]], [4, 5])
        self.assertTrue(all(row["player_id"] == "p2" for row in result["eligible_observations"]))

    def test_stale_decisions_block(self) -> None:
        result = build_identity_roster_subject_remediation_plan(
            {"canonical_coverage": [], "structural_subjects": [], "duplicate_observations": []},
            {"source_promotion_plan_digest": "old", "decisions": [{"action": "mark_fragment_unresolved"}]},
            generated_at="fixed",
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("stale_remediation_decisions", {row["code"] for row in result["errors"]})

    def test_unsafe_duplicate_removes_winner_frame(self) -> None:
        kept = _row("safe", 7)
        result = build_identity_roster_subject_remediation_plan({
            "canonical_coverage": [{"frame_records": [kept, _row("safe", 8)]}],
            "structural_subjects": [],
            "duplicate_observations": [{
                "frame": 7,
                "safe_to_deduplicate": False,
                "classification": "parallel_nearby_duplicate",
                "kept_candidate_subject_id": "safe",
                "kept_tracklet_id": "t1",
                "kept_observation": kept,
            }],
        }, generated_at="fixed")
        self.assertEqual([row["frame"] for row in result["eligible_observations"]], [8])

    def test_fragment_key_is_deterministic(self) -> None:
        left = stable_fragment_key(candidate_subject_id="s", start_frame=1, end_frame=2, tracklet_ids=["b", "a"])
        right = stable_fragment_key(candidate_subject_id="s", start_frame=1, end_frame=2, tracklet_ids=["a", "b"])
        self.assertEqual(left, right)

    def test_clear_decision_removes_previous_action(self) -> None:
        from app.services.identity_promotion_safety import canonical_document_digest
        plan = {
            "canonical_coverage": [],
            "structural_subjects": [{
                "candidate_subject_id": "bad", "start_frame": 1, "end_frame": 2,
                "tracklet_ids": ["t"], "frame_records": [_row("bad", 1, tracklet="t")],
            }],
            "duplicate_observations": [],
        }
        key = stable_fragment_key(candidate_subject_id="bad", start_frame=1, end_frame=2, tracklet_ids=["t"])
        decisions = {
            "source_promotion_plan_digest": canonical_document_digest(plan),
            "decisions": [
                {"decision_key": key, "action": "assign_fragment", "candidate_subject_id": "bad", "start_frame": 1, "end_frame": 2, "tracklet_ids": ["t"], "player_id": "p2"},
                {"action": "clear_remediation_decision", "target_decision_key": key, "candidate_subject_id": "bad", "start_frame": 1, "end_frame": 2, "tracklet_ids": ["t"]},
            ],
        }
        result = build_identity_roster_subject_remediation_plan(plan, decisions, generated_at="fixed")
        self.assertEqual(result["summary"]["actions_applied"], 0)
        self.assertEqual(result["summary"]["structural_subjects_auto_excluded"], 1)

    def test_split_at_tracklet_boundary_creates_stable_fragments(self) -> None:
        from app.services.identity_promotion_safety import canonical_document_digest
        plan = {
            "canonical_coverage": [],
            "structural_subjects": [{
                "candidate_subject_id": "bad", "start_frame": 1, "end_frame": 4,
                "tracklet_ids": ["t1", "t2"],
                "frame_records": [
                    _row("bad", 1, tracklet="t1"), _row("bad", 2, tracklet="t1"),
                    _row("bad", 3, tracklet="t2"), _row("bad", 4, tracklet="t2"),
                ],
            }],
            "duplicate_observations": [],
        }
        decisions = {
            "source_promotion_plan_digest": canonical_document_digest(plan),
            "decisions": [{
                "action": "split_subject_at_tracklet_boundary",
                "candidate_subject_id": "bad", "start_frame": 1, "end_frame": 4,
            }],
        }
        result = build_identity_roster_subject_remediation_plan(plan, decisions, generated_at="fixed")
        self.assertEqual(result["status"], "ready_for_partial_candidate")
        self.assertEqual([(row["start_frame"], row["end_frame"]) for row in result["split_fragments"]], [(1, 2), (3, 4)])
        self.assertEqual(result["summary"]["split_fragments"], 2)
        self.assertEqual(len(result["eligible_observations"]), 0)

    def test_split_at_transition_frame_creates_two_fragments(self) -> None:
        from app.services.identity_promotion_safety import canonical_document_digest
        plan = {
            "canonical_coverage": [],
            "structural_subjects": [{
                "candidate_subject_id": "bad", "start_frame": 10, "end_frame": 13,
                "tracklet_ids": ["t"],
                "frame_records": [_row("bad", frame, tracklet="t") for frame in range(10, 14)],
            }],
            "duplicate_observations": [],
        }
        decisions = {
            "source_promotion_plan_digest": canonical_document_digest(plan),
            "decisions": [{
                "action": "split_subject_at_transition_frame",
                "candidate_subject_id": "bad", "start_frame": 10, "end_frame": 13,
                "transition_frame": 12,
            }],
        }
        result = build_identity_roster_subject_remediation_plan(plan, decisions, generated_at="fixed")
        self.assertEqual([(row["start_frame"], row["end_frame"]) for row in result["split_fragments"]], [(10, 11), (12, 13)])


if __name__ == "__main__":
    unittest.main()
