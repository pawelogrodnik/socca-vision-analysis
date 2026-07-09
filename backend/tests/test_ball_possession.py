from __future__ import annotations

import unittest

from app.services.ball_possession import (
    _append_restart_pass_candidates,
    build_contact_candidates_document,
    build_possession_candidates_document,
    build_restart_candidates_document,
    build_possession_segments_document,
)


def ball(frame: int, x: float, y: float, source: str = "detected") -> dict:
    return {
        "frame": frame,
        "time_sec": round(frame / 30, 3),
        "position_m": [x, y] if source != "unknown" else None,
        "position_px": [x * 10, y * 10] if source != "unknown" else None,
        "source": source,
        "confidence": 0.7 if source == "detected" else 0.25,
    }


def stable_player(player_id: str, team: str, positions: list[tuple[int, float, float]]) -> dict:
    return {
        "stable_player_id": player_id,
        "stable_subject_id": f"slot-{player_id}",
        "slot_id": player_id,
        "team_label": team,
        "team_id": f"team-{team}",
        "team_name": f"Team {team}",
        "trajectory_m": [
            {
                "frame": frame,
                "time_sec": round(frame / 30, 3),
                "pitch_m": [x, y],
                "source": "detected",
                "status": "detected",
            }
            for frame, x, y in positions
        ],
    }


def stable_player_with_sources(player_id: str, team: str, rows: list[tuple[int, float, float, str]]) -> dict:
    return {
        "stable_player_id": player_id,
        "stable_subject_id": f"slot-{player_id}",
        "slot_id": player_id,
        "team_label": team,
        "team_id": f"team-{team}",
        "team_name": f"Team {team}",
        "trajectory_m": [
            {
                "frame": frame,
                "time_sec": round(frame / 30, 3),
                "pitch_m": [x, y],
                "source": source,
                "status": source,
            }
            for frame, x, y, source in rows
        ],
    }


def stable_player_with_overlay_and_sparse_trajectory(player_id: str, team: str) -> dict:
    return {
        "stable_player_id": player_id,
        "stable_subject_id": f"slot-{player_id}",
        "slot_id": player_id,
        "team_label": team,
        "team_id": f"team-{team}",
        "team_name": f"Team {team}",
        "trajectory_m": [
            {
                "frame": 0,
                "time_sec": 0.0,
                "pitch_m": [20.0, 20.0],
                "source": "detected",
                "status": "detected",
            }
        ],
        "overlay_positions": [
            {
                "frame": 12,
                "time_sec": 0.4,
                "bbox_xyxy": [10, 10, 20, 30],
                "pitch_m": [5.4, 5.0],
                "source": "detected",
                "status": "detected",
            }
        ],
    }


class BallPossessionTests(unittest.TestCase):
    def test_possession_marks_controlled_when_one_player_is_close(self) -> None:
        doc = build_possession_candidates_document(
            {"positions": [ball(10, 5.0, 5.0)]},
            {"players": [stable_player("A01", "A", [(10, 5.5, 5.0)])]},
            fps=30,
        )

        frame = doc["frames"][0]
        self.assertEqual(frame["status"], "controlled")
        self.assertEqual(frame["stable_player_id"], "A01")
        self.assertEqual(frame["team_label"], "A")
        self.assertLess(frame["nearest_distance_m"], 1.0)

    def test_fast_straight_fly_through_near_player_is_not_controlled(self) -> None:
        ball_rows = [ball(frame, 20.0 - frame * 0.5, 10.0) for frame in range(6)]
        player_rows = [(frame, 18.75, 11.1) for frame in range(6)]

        candidates = build_possession_candidates_document(
            {"positions": ball_rows},
            {"players": [stable_player("A02", "A", player_rows)]},
            fps=30,
        )
        segments = build_possession_segments_document(candidates, fps=30)
        contacts = build_contact_candidates_document(candidates, segments)

        self.assertTrue(all(frame["status"] == "free" for frame in candidates["frames"]))
        self.assertTrue(all(frame["reason"] == "fly_through_no_close_control" for frame in candidates["frames"]))
        self.assertEqual(candidates["summary"]["fly_through_suppressed_frames"], 6)
        self.assertEqual(contacts["summary"]["contact_candidates"], 0)

    def test_fast_ball_that_gets_very_close_to_player_can_remain_controlled(self) -> None:
        ball_rows = [ball(frame, 20.0 - frame * 0.5, 10.0) for frame in range(6)]
        player_rows = [(frame, 18.75, 10.2) for frame in range(6)]

        candidates = build_possession_candidates_document(
            {"positions": ball_rows},
            {"players": [stable_player("A01", "A", player_rows)]},
            fps=30,
        )

        self.assertTrue(any(frame["status"] == "controlled" for frame in candidates["frames"]))
        self.assertEqual(candidates["summary"]["fly_through_suppressed_frames"], 0)

    def test_possession_prefers_full_overlay_positions_over_sparse_trajectory(self) -> None:
        doc = build_possession_candidates_document(
            {"positions": [ball(12, 5.0, 5.0)]},
            {"players": [stable_player_with_overlay_and_sparse_trajectory("A01", "A")]},
            fps=30,
        )

        frame = doc["frames"][0]
        self.assertEqual(frame["status"], "controlled")
        self.assertEqual(frame["stable_player_id"], "A01")
        self.assertEqual(frame["nearest_distance_m"], 0.4)

    def test_possession_uses_short_gap_interpolated_player_position_with_lower_confidence(self) -> None:
        detected = build_possession_candidates_document(
            {"positions": [ball(2, 5.0, 5.0)]},
            {"players": [stable_player("A01", "A", [(2, 5.4, 5.0)])]},
            fps=30,
        )
        interpolated = build_possession_candidates_document(
            {"positions": [ball(2, 5.0, 5.0)]},
            {
                "players": [
                    stable_player_with_sources(
                        "A01",
                        "A",
                        [
                            (0, 5.2, 5.0, "detected"),
                            (1, 5.3, 5.0, "missing"),
                            (2, 5.4, 5.0, "missing"),
                            (3, 5.5, 5.0, "missing"),
                            (4, 5.6, 5.0, "detected"),
                        ],
                    )
                ]
            },
            fps=30,
        )

        detected_frame = detected["frames"][0]
        interpolated_frame = interpolated["frames"][0]
        self.assertEqual(interpolated_frame["status"], "controlled")
        self.assertEqual(interpolated_frame["nearest_player_source"], "short_gap_interpolated")
        self.assertLess(interpolated_frame["confidence"], detected_frame["confidence"])
        self.assertEqual(interpolated["summary"]["interpolated_player_position_frames"], 1)

    def test_possession_marks_contested_when_two_players_are_close(self) -> None:
        doc = build_possession_candidates_document(
            {"positions": [ball(10, 5.0, 5.0)]},
            {
                "players": [
                    stable_player("A01", "A", [(10, 5.6, 5.0)]),
                    stable_player("B01", "B", [(10, 4.5, 5.0)]),
                ]
            },
            fps=30,
        )

        frame = doc["frames"][0]
        self.assertEqual(frame["status"], "contested")
        self.assertEqual(len(frame["nearest_players"]), 2)
        self.assertIsNone(frame.get("stable_player_id"))

    def test_possession_stays_unknown_without_trusted_player_positions(self) -> None:
        doc = build_possession_candidates_document(
            {"positions": [ball(10, 5.0, 5.0)]},
            {"players": []},
            fps=30,
        )

        frame = doc["frames"][0]
        self.assertEqual(frame["status"], "unknown")
        self.assertEqual(frame["reason"], "no_trusted_detected_player_positions")

    def test_contact_candidates_are_built_from_controlled_segments(self) -> None:
        candidates = build_possession_candidates_document(
            {"positions": [ball(10, 5.0, 5.0), ball(11, 5.1, 5.0)]},
            {"players": [stable_player("A01", "A", [(10, 5.5, 5.0), (11, 5.6, 5.0)])]},
            fps=30,
        )
        segments = build_possession_segments_document(candidates, fps=30)
        contacts = build_contact_candidates_document(candidates, segments)

        self.assertEqual(contacts["summary"]["contact_candidates"], 1)
        self.assertEqual(contacts["candidates"][0]["stable_player_id"], "A01")
        self.assertEqual(contacts["candidates"][0]["detected_ball_frames"], 2)
        self.assertEqual(contacts["candidates"][0]["start_ball_position_m"], [5.0, 5.0])
        self.assertEqual(contacts["candidates"][0]["end_ball_position_m"], [5.1, 5.0])
        self.assertEqual(contacts["candidates"][0]["start_player_position_m"], [5.5, 5.0])
        self.assertEqual(contacts["candidates"][0]["end_player_position_m"], [5.6, 5.0])
        self.assertIn(contacts["candidates"][0]["review_status"], {"accepted", "uncertain", "rejected"})
        self.assertEqual(contacts["candidates"][0]["review_source"], "auto_contact_review_v1")

    def test_ground_kick_in_restart_becomes_same_team_pass_candidate(self) -> None:
        ball_rows = [ball(frame, 0.2, 20.0) for frame in range(10)]
        ball_rows.extend([ball(10, 1.2, 20.0), ball(11, 2.2, 20.0), ball(12, 3.2, 20.0)])
        stable_doc = {
            "players": [
                stable_player("A01", "A", [(frame, 0.45, 20.0) for frame in range(10)]),
                stable_player("A02", "A", [(12, 3.35, 20.0)]),
            ]
        }
        possession = build_possession_candidates_document({"positions": ball_rows}, stable_doc, fps=30)

        restarts = build_restart_candidates_document(
            {"positions": ball_rows},
            possession,
            stable_doc,
            fps=30,
            parameters={"pitch_width_m": 30.0, "pitch_length_m": 47.4},
        )
        pass_doc = {"summary": {}, "candidates": []}
        _append_restart_pass_candidates(pass_doc, restarts)

        self.assertEqual(restarts["summary"]["restart_candidates"], 1)
        candidate = restarts["candidates"][0]
        self.assertEqual(candidate["restart_type"], "kick_in")
        self.assertEqual(candidate["actor_stable_player_id"], "A01")
        self.assertEqual(candidate["receiver_stable_player_id"], "A02")
        self.assertEqual(candidate["result_type"], "restart_pass")
        self.assertEqual(pass_doc["summary"]["same_team_pass_candidates"], 1)
        self.assertEqual(pass_doc["candidates"][0]["restart_type"], "kick_in")
        self.assertEqual(pass_doc["candidates"][0]["from_team_label"], "A")

    def test_ground_restart_can_infer_team_from_last_touch_out_of_play(self) -> None:
        ball_rows = [ball(0, 29.0, 20.0)]
        ball_rows.extend(ball(frame, 0.0, 0.0, source="unknown") for frame in range(1, 5))
        ball_rows.extend(ball(frame, 29.8, 20.0) for frame in range(5, 14))
        ball_rows.extend([ball(14, 28.7, 20.0), ball(15, 27.8, 20.0), ball(16, 26.8, 20.0)])
        stable_doc = {
            "players": [
                stable_player("A01", "A", [(0, 29.1, 20.0)]),
                stable_player("B01", "B", [(16, 26.7, 20.0)]),
            ]
        }
        possession = build_possession_candidates_document({"positions": ball_rows}, stable_doc, fps=30)

        restarts = build_restart_candidates_document(
            {"positions": ball_rows},
            possession,
            stable_doc,
            fps=30,
            parameters={"pitch_width_m": 30.0, "pitch_length_m": 47.4},
        )

        self.assertEqual(restarts["summary"]["restart_candidates"], 1)
        candidate = restarts["candidates"][0]
        self.assertEqual(candidate["actor_team_label"], "B")
        self.assertEqual(candidate["actor_source"], "last_touch_out_of_play_opponent")
        self.assertEqual(candidate["last_touch_team_label"], "A")
        self.assertEqual(candidate["receiver_team_label"], "B")
        self.assertEqual(candidate["result_type"], "restart_pass")

    def test_central_goal_line_restart_is_ignored_for_now(self) -> None:
        ball_rows = [ball(frame, 15.0, 0.2) for frame in range(10)]
        ball_rows.extend([ball(10, 15.5, 1.4), ball(11, 15.7, 2.5)])
        stable_doc = {"players": [stable_player("A01", "A", [(frame, 15.0, 0.5) for frame in range(10)])]}
        possession = build_possession_candidates_document({"positions": ball_rows}, stable_doc, fps=30)

        restarts = build_restart_candidates_document(
            {"positions": ball_rows},
            possession,
            stable_doc,
            fps=30,
            parameters={"pitch_width_m": 30.0, "pitch_length_m": 47.4},
        )

        self.assertEqual(restarts["summary"]["restart_candidates"], 0)


if __name__ == "__main__":
    unittest.main()
