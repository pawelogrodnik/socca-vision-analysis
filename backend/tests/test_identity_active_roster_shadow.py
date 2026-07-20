from __future__ import annotations

import unittest

from app.services.identity_active_roster_shadow import build_identity_active_roster_shadow


def player(
    subject_id: str,
    *,
    team: str = "A",
    x: float = 10.0,
    confidence: float = 0.9,
    eligible: bool = True,
    anchored: bool = True,
    frame: int = 0,
) -> dict:
    return {
        "stable_player_id": subject_id,
        "candidate_subject_id": subject_id,
        "team_label": team,
        "role": "field_player",
        "anchored": anchored,
        "requires_review": not anchored,
        "overlay_positions": [
            {
                "frame": frame,
                "source": "detected",
                "bbox_xyxy": [x, 10.0, x + 20.0, 70.0],
                "pitch_m": [x, 20.0],
                "confidence": confidence,
                "eligible_for_distance": eligible,
                "eligible_for_heatmap": eligible,
                "play_area_status": "inside_play" if eligible else "outside_play",
                "footpoint_reliable": eligible,
                "appearance_reliable": eligible,
                "tracklet_id": f"tracklet-{subject_id}",
            }
        ],
    }


def build(players: list[dict]) -> dict:
    candidate = {
        "algorithm": {"name": "candidate", "version": "test"},
        "subjects": [
            {
                "candidate_subject_id": row["candidate_subject_id"],
                "candidate_player_id": row["stable_player_id"],
                "team_label": row["team_label"],
                "production_subject_ids": [row["candidate_subject_id"]] if row.get("anchored") else [],
            }
            for row in players
        ],
    }
    overlay = {"players": players, "summary": {"candidate_subjects": len(players)}}
    return build_identity_active_roster_shadow(
        candidate,
        overlay,
        generated_at="fixed",
        include_overlay=True,
    )


class IdentityActiveRosterShadowTests(unittest.TestCase):
    def test_caps_each_team_at_seven_without_fabricating_positions(self) -> None:
        documents = build([player(f"A{index:02d}", x=float(index * 30)) for index in range(1, 10)])
        report = documents["identity_active_roster_shadow_report"]
        overlay = documents["identity_active_roster_shadow_overlay"]

        self.assertEqual(report["summary"]["max_active_before"]["A"], 9)
        self.assertEqual(report["summary"]["max_active_after"]["A"], 7)
        self.assertEqual(report["summary"]["frames_over_cap_after"], 0)
        self.assertEqual(sum(len(row["overlay_positions"]) for row in overlay["players"]), 7)

    def test_suppresses_exact_duplicate_before_applying_team_cap(self) -> None:
        anchored = player("A01", x=20.0, confidence=0.7, anchored=True)
        duplicate = player("A-new01", x=20.0, confidence=0.95, anchored=False)
        duplicate["overlay_positions"][0]["tracklet_id"] = anchored["overlay_positions"][0]["tracklet_id"]

        documents = build([anchored, duplicate])
        roster = documents["identity_active_roster_shadow"]
        report = documents["identity_active_roster_shadow_report"]

        by_subject = {row["candidate_subject_id"]: row for row in roster["subjects"]}
        self.assertEqual(by_subject["A01"]["active_frames"], 1)
        self.assertEqual(by_subject["A-new01"]["suppressed_frames"], 1)
        self.assertEqual(report["summary"]["suppression_counts"]["duplicate_same_observation"], 1)

    def test_unknown_team_is_not_promoted_into_active_roster(self) -> None:
        documents = build([player("U01", team="U")])
        roster = documents["identity_active_roster_shadow"]

        self.assertEqual(roster["subjects"][0]["active_frames"], 0)
        self.assertEqual(roster["subjects"][0]["decision_runs"][0]["reason"], "unknown_team_not_roster")

    def test_overlapping_anchored_players_are_not_treated_as_duplicates(self) -> None:
        first = player("A01", x=20.0, anchored=True)
        second = player("A02", x=20.0, anchored=True)

        documents = build([first, second])
        report = documents["identity_active_roster_shadow_report"]

        self.assertEqual(report["summary"]["active_positions"], 2)
        self.assertNotIn("duplicate_same_observation", report["summary"]["suppression_counts"])

    def test_same_input_is_deterministic(self) -> None:
        players = [player(f"A{index:02d}", x=float(index * 30)) for index in range(1, 9)]
        self.assertEqual(build(players), build(players))


if __name__ == "__main__":
    unittest.main()
