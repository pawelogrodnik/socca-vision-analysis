from __future__ import annotations

import unittest

from app.services.identity_roster_subject_promotion import (
    build_identity_roster_subject_promotion_plan,
)
from app.services.identity_roster_subject_review_store import (
    identity_review_artifact_digest,
)


def review_artifact() -> dict:
    return {
        "schema_version": "0.1.0",
        "generated_at": "ignored",
        "cards": [
            {
                "review_card_key": "card-1",
                "candidate_subject_id": "subject-1",
                "team_label": "A",
                "start_frame": 10,
                "end_frame": 12,
                "recommended_player": {"player_id": "p1"},
            },
            {
                "review_card_key": "card-2",
                "candidate_subject_id": "subject-2",
                "team_label": "A",
                "start_frame": 12,
                "end_frame": 14,
                "recommended_player": None,
            },
            {
                "review_card_key": "card-b",
                "candidate_subject_id": "subject-b",
                "team_label": "B",
                "start_frame": 0,
                "end_frame": 1,
            },
        ],
    }


def decisions(artifact: dict, second_player: str = "p1") -> dict:
    return {
        "source_artifact_digest": identity_review_artifact_digest(artifact),
        "decisions": [
            {
                "review_card_key": "card-1",
                "candidate_subject_id": "subject-1",
                "decision": "confirm_recommended_player",
                "player_id": "p1",
            },
            {
                "review_card_key": "card-2",
                "candidate_subject_id": "subject-2",
                "decision": "assign_roster_player",
                "player_id": second_player,
            },
        ],
    }


def candidate_doc() -> dict:
    return {
        "subjects": [
            {
                "candidate_subject_id": "subject-1",
                "candidate_player_id": "A01",
                "team_label": "A",
                "start_frame": 10,
                "end_frame": 12,
                "tracklet_ids": ["track-1"],
                "production_subject_ids": ["slot-A01"],
            },
            {
                "candidate_subject_id": "subject-2",
                "candidate_player_id": "A01~2",
                "team_label": "A",
                "start_frame": 12,
                "end_frame": 14,
                "tracklet_ids": ["track-1"],
                "production_subject_ids": ["slot-A01"],
            },
        ]
    }


def timeline_doc() -> dict:
    def observation(frame: int) -> dict:
        return {
            "frame": frame,
            "time_sec": frame / 30,
            "status": "detected",
            "tracklet_id": "track-1",
            "confidence": 0.9,
            "play_area_status": "inside_play",
            "eligible_for_distance": True,
            "eligible_for_heatmap": True,
        }

    return {
        "subjects": [
            {
                "shadow_subject_id": "subject-1",
                "team_label": "A",
                "start_frame": 10,
                "end_frame": 12,
                "tracklet_ids": ["track-1"],
                "observations": [observation(10), observation(11), observation(12)],
            },
            {
                "shadow_subject_id": "subject-2",
                "team_label": "A",
                "start_frame": 12,
                "end_frame": 14,
                "tracklet_ids": ["track-1"],
                "observations": [observation(12), observation(13), observation(14)],
            },
        ]
    }


def match_doc() -> dict:
    return {
        "teams": [
            {"players": [{"id": "p1", "name": "One"}, {"id": "p2", "name": "Two"}]},
            {"players": []},
        ]
    }


class IdentityRosterSubjectPromotionTests(unittest.TestCase):
    def test_builds_exact_frame_plan_and_deduplicates_parallel_subjects(self) -> None:
        artifact = review_artifact()
        plan = build_identity_roster_subject_promotion_plan(
            artifact,
            decisions(artifact),
            candidate_doc(),
            timeline_doc(),
            match_doc(),
            team_label="A",
            generated_at="fixed",
        )

        self.assertEqual(plan["status"], "ready_for_controlled_apply")
        self.assertEqual(plan["summary"]["source_observations"], 6)
        self.assertEqual(plan["summary"]["canonical_observations"], 5)
        self.assertEqual(plan["summary"]["duplicate_observations_removed"], 1)
        self.assertEqual(plan["canonical_coverage"][0]["unique_detected_frames"], 5)
        self.assertEqual(
            [row["frame"] for row in plan["canonical_coverage"][0]["frame_records"]],
            [10, 11, 12, 13, 14],
        )
        self.assertFalse(plan["safety"]["writes_player_identity_assignments"])

    def test_same_source_assigned_to_different_players_blocks_plan(self) -> None:
        artifact = review_artifact()
        plan = build_identity_roster_subject_promotion_plan(
            artifact,
            decisions(artifact, second_player="p2"),
            candidate_doc(),
            timeline_doc(),
            match_doc(),
            team_label="A",
            generated_at="fixed",
        )

        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["summary"]["hard_conflicts"], 1)
        self.assertEqual(
            plan["errors"][0]["code"],
            "same_source_observation_maps_to_multiple_players",
        )

    def test_stale_or_incomplete_audit_blocks_plan(self) -> None:
        artifact = review_artifact()
        decision_doc = decisions(artifact)
        decision_doc["source_artifact_digest"] = "stale"
        decision_doc["decisions"].pop()

        plan = build_identity_roster_subject_promotion_plan(
            artifact,
            decision_doc,
            candidate_doc(),
            timeline_doc(),
            match_doc(),
            team_label="A",
            generated_at="fixed",
        )

        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(
            {row["code"] for row in plan["errors"]},
            {"stale_operator_decisions", "incomplete_team_audit"},
        )

    def test_unresolved_decision_is_audited_but_not_promoted(self) -> None:
        artifact = review_artifact()
        decision_doc = decisions(artifact)
        decision_doc["decisions"][1] = {
            "review_card_key": "card-2",
            "candidate_subject_id": "subject-2",
            "decision": "mark_unresolved",
            "player_id": None,
        }

        plan = build_identity_roster_subject_promotion_plan(
            artifact,
            decision_doc,
            candidate_doc(),
            timeline_doc(),
            match_doc(),
            team_label="A",
            generated_at="fixed",
        )

        self.assertEqual(plan["status"], "ready_for_controlled_apply")
        self.assertEqual(plan["summary"]["resolved_subjects"], 1)
        self.assertEqual(plan["summary"]["unresolved_subjects"], 1)

    def test_output_is_deterministic_for_fixed_timestamp(self) -> None:
        artifact = review_artifact()
        args = (
            artifact,
            decisions(artifact),
            candidate_doc(),
            timeline_doc(),
            match_doc(),
        )
        first = build_identity_roster_subject_promotion_plan(
            *args,
            team_label="A",
            generated_at="fixed",
        )
        second = build_identity_roster_subject_promotion_plan(
            *args,
            team_label="A",
            generated_at="fixed",
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
