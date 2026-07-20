from __future__ import annotations

import unittest

from app.services.identity_candidate_shadow import build_identity_candidate_shadow


FPS = 30.0


def global_identity(*slots: tuple[str, str, list[str]]) -> dict:
    return {
        "slots": [
            {
                "stable_player_id": player_id,
                "stable_subject_id": subject_id,
                "team_label": player_id[0],
                "tracklet_ids": tracklet_ids,
                "role": "field_player",
            }
            for player_id, subject_id, tracklet_ids in slots
        ]
    }


def observation(frame: int, tracklet_id: str, *, x: float = 10.0) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / FPS,
        "status": "detected",
        "tracklet_id": tracklet_id,
        "pitch_m": [x, 20.0],
        "bbox_xyxy": [x, 10.0, x + 20.0, 70.0],
        "confidence": 0.9,
        "eligible_for_distance": True,
        "eligible_for_heatmap": True,
    }


def subject(
    subject_id: str,
    tracklet_ids: list[str],
    observations: list[dict],
    *,
    production_subject_ids: list[str],
    state_runs: list[dict] | None = None,
) -> dict:
    return {
        "shadow_subject_id": subject_id,
        "team_label": "A",
        "tracklet_ids": tracklet_ids,
        "production_subject_ids": production_subject_ids,
        "start_frame": observations[0]["frame"],
        "end_frame": observations[-1]["frame"],
        "observations": observations,
        "state_runs": state_runs
        or [
            {
                "status": "detected",
                "start_frame": observations[0]["frame"],
                "end_frame": observations[-1]["frame"],
            }
        ],
    }


def build(subjects: list[dict], identity: dict, transitions: list[dict] | None = None) -> dict:
    offline = {
        "algorithm": {"name": "test_graph", "version": "1"},
        "subjects": [
            {
                "shadow_subject_id": row["shadow_subject_id"],
                "team_label": row["team_label"],
                "tracklet_ids": row["tracklet_ids"],
            }
            for row in subjects
        ],
    }
    timeline = {
        "algorithm": {"name": "test_timeline", "version": "1"},
        "subjects": subjects,
        "transition_events": transitions or [],
    }
    return build_identity_candidate_shadow(
        offline,
        timeline,
        identity,
        fps=FPS,
        generated_at="fixed",
        include_overlay=True,
    )


class IdentityCandidateShadowTests(unittest.TestCase):
    def test_one_to_one_candidate_preserves_production_label(self) -> None:
        row = subject(
            "shadow-1",
            ["t1"],
            [observation(0, "t1"), observation(1, "t1")],
            production_subject_ids=["slot-A03"],
        )

        documents = build([row], global_identity(("A03", "slot-A03", ["t1"])))
        candidate = documents["identity_candidate_shadow"]

        self.assertEqual(candidate["subjects"][0]["candidate_player_id"], "A03")
        self.assertFalse(candidate["subjects"][0]["requires_review"])
        self.assertFalse(candidate["safety"]["eligible_for_player_stats"])

    def test_split_candidate_uses_deterministic_suffix_and_requires_review(self) -> None:
        longer = subject(
            "shadow-long",
            ["t1"],
            [observation(frame, "t1") for frame in range(3)],
            production_subject_ids=["slot-A01"],
        )
        shorter = subject(
            "shadow-short",
            ["t2"],
            [observation(5, "t2"), observation(6, "t2")],
            production_subject_ids=["slot-A01"],
        )

        documents = build(
            [shorter, longer],
            global_identity(("A01", "slot-A01", ["t1", "t2"])),
        )
        rows = {row["candidate_subject_id"]: row for row in documents["identity_candidate_shadow"]["subjects"]}

        self.assertEqual(rows["shadow-long"]["candidate_player_id"], "A01")
        self.assertEqual(rows["shadow-short"]["candidate_player_id"], "A01~2")
        self.assertIn("splits_production_subject", rows["shadow-short"]["quality_flags"])
        self.assertTrue(rows["shadow-short"]["requires_review"])

    def test_cross_production_merge_is_flagged_for_review(self) -> None:
        row = subject(
            "shadow-merge",
            ["t1", "t2"],
            [observation(0, "t1"), observation(1, "t1"), observation(3, "t2")],
            production_subject_ids=["slot-A01", "slot-A02"],
        )

        documents = build(
            [row],
            global_identity(
                ("A01", "slot-A01", ["t1"]),
                ("A02", "slot-A02", ["t2"]),
            ),
        )
        candidate = documents["identity_candidate_shadow"]["subjects"][0]

        self.assertEqual(candidate["candidate_player_id"], "A01")
        self.assertIn("merges_production_subjects", candidate["quality_flags"])
        self.assertTrue(candidate["requires_review"])

    def test_cross_team_production_anchor_is_not_used_as_visual_label(self) -> None:
        row = subject(
            "shadow-team-b",
            ["t1"],
            [observation(0, "t1")],
            production_subject_ids=["slot-A06"],
        )
        row["team_label"] = "B"

        documents = build([row], global_identity(("A06", "slot-A06", ["t1"])))
        candidate = documents["identity_candidate_shadow"]["subjects"][0]

        self.assertEqual(candidate["candidate_player_id"], "B-new01")
        self.assertEqual(candidate["label_source"], "unanchored_candidate")
        self.assertIn("production_anchor_team_mismatch", candidate["quality_flags"])
        self.assertTrue(candidate["requires_review"])
        self.assertEqual(
            documents["identity_candidate_shadow_report"]["summary"]["team_mismatched_anchor_subjects"],
            1,
        )
        self.assertTrue(
            documents["identity_candidate_shadow_report"]["gates"]["no_cross_team_visual_labels"]
        )

    def test_visual_gap_positions_are_never_statistics_eligible(self) -> None:
        row = subject(
            "shadow-gap",
            ["t1", "t2"],
            [observation(0, "t1", x=10.0), observation(3, "t2", x=16.0)],
            production_subject_ids=["slot-A05"],
            state_runs=[
                {"status": "detected", "start_frame": 0, "end_frame": 0},
                {"status": "predicted", "start_frame": 1, "end_frame": 2},
                {"status": "detected", "start_frame": 3, "end_frame": 3},
                {"status": "missing", "start_frame": 4, "end_frame": 6},
            ],
        )

        documents = build([row], global_identity(("A05", "slot-A05", ["t1", "t2"])))
        positions = documents["identity_candidate_shadow_overlay"]["players"][0]["overlay_positions"]

        self.assertEqual([position["frame"] for position in positions], [0, 1, 2, 3])
        predicted = [position for position in positions if position["source"] == "predicted"]
        self.assertEqual(len(predicted), 2)
        self.assertTrue(all(not position["eligible_for_distance"] for position in predicted))
        self.assertTrue(all(not position["eligible_for_heatmap"] for position in predicted))

    def test_same_inputs_produce_same_candidate_without_timestamp_noise(self) -> None:
        row = subject(
            "shadow-1",
            ["t1"],
            [observation(0, "t1")],
            production_subject_ids=["slot-A03"],
        )
        identity = global_identity(("A03", "slot-A03", ["t1"]))

        self.assertEqual(build([row], identity), build([row], identity))


if __name__ == "__main__":
    unittest.main()
