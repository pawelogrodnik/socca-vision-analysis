from __future__ import annotations

import unittest

from app.services.attacking_momentum import build_attacking_momentum_document


PITCH_WIDTH_M = 30.0
PITCH_LENGTH_M = 50.0


class AttackingMomentumTests(unittest.TestCase):
    def test_final_third_scores_more_than_own_half(self) -> None:
        own = self._build([self._frame(0.0, "A", [15.0, 45.0])])
        final = self._build([self._frame(0.0, "A", [15.0, 5.0])])
        self.assertGreater(final["points"][0]["team_a_positional_raw"], own["points"][0]["team_a_positional_raw"])

    def test_same_position_reverses_pressure_for_opposite_direction(self) -> None:
        document = self._build(
            [self._frame(0.0, "A", [15.0, 5.0]), self._frame(0.1, "B", [15.0, 5.0])]
        )
        point = document["points"][0]
        self.assertGreater(point["team_a_positional_raw"], point["team_b_positional_raw"])

    def test_second_half_uses_period_direction(self) -> None:
        phase = {
            "periods": [
                {
                    "period_id": "first",
                    "start_time_sec": 0.0,
                    "end_time_sec": 10.0,
                    "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
                },
                {
                    "period_id": "second",
                    "start_time_sec": 10.01,
                    "end_time_sec": 20.0,
                    "team_attack_directions": {"A": "towards_y_max", "B": "towards_y_min"},
                },
            ],
            "summary": {"needs_review": False},
        }
        result = self._build(
            [self._frame(5.0, "A", [15.0, 5.0]), self._frame(15.0, "A", [15.0, 45.0])],
            phase=phase,
            bin_sec=10.0,
        )
        self.assertAlmostEqual(
            result["points"][0]["team_a_positional_raw"],
            result["points"][1]["team_a_positional_raw"],
            places=5,
        )

    def test_uses_ball_position_m_without_reapplying_camera_motion(self) -> None:
        frame = self._frame(0.0, "A", [15.0, 5.0])
        frame["ball_position_px"] = [99999.0, -99999.0]
        result = self._build([frame])
        self.assertGreater(result["points"][0]["team_a_positional_raw"], 0.5)
        self.assertFalse(result["parameters"]["camera_motion_reapplied"])

    def test_progression_over_lookback_increases_pressure(self) -> None:
        static = self._build([self._frame(0.0, "A", [15.0, 30.0]), self._frame(1.0, "A", [15.0, 30.0])])
        moving = self._build([self._frame(0.0, "A", [15.0, 35.0]), self._frame(1.0, "A", [15.0, 25.0])])
        self.assertGreater(moving["points"][0]["team_a_positional_raw"], static["points"][0]["team_a_positional_raw"])

    def test_adjacent_jitter_does_not_create_large_progression_bonus(self) -> None:
        static = self._build([self._frame(0.0, "A", [15.0, 25.0]), self._frame(1.0, "A", [15.0, 25.0])])
        jitter = self._build([self._frame(0.0, "A", [15.0, 25.1]), self._frame(1.0, "A", [15.0, 25.0])])
        difference = jitter["points"][0]["team_a_positional_raw"] - static["points"][0]["team_a_positional_raw"]
        self.assertLess(difference, 0.02)

    def test_completed_pass_bonus_is_greater_than_failed(self) -> None:
        completed = self._build([], passes=[self._pass("completed_pass")])
        failed = self._build([], passes=[self._pass("failed_pass")])
        self.assertGreater(completed["points"][0]["team_a_event_bonus"], failed["points"][0]["team_a_event_bonus"])

    def test_progressive_completed_pass_scores_more_than_regular(self) -> None:
        regular = self._build([], passes=[self._pass("completed_pass")])
        progressive = self._build([], passes=[self._pass("completed_pass", forward_progress_m=10.0)])
        self.assertGreater(progressive["points"][0]["team_a_event_bonus"], regular["points"][0]["team_a_event_bonus"])

    def test_excluded_and_rejected_passes_score_zero(self) -> None:
        result = self._build(
            [],
            passes=[self._pass("excluded_non_pass"), self._pass("completed_pass", review_status="rejected")],
        )
        self.assertEqual(result["points"][0]["team_a_event_bonus"], 0.0)
        self.assertEqual(result["summary"]["excluded_non_pass_ignored"], 1)

    def test_needs_review_pass_keeps_limited_bonus(self) -> None:
        accepted = self._build([], passes=[self._pass("completed_pass", review_status="accepted")])
        review = self._build([], passes=[self._pass("completed_pass", review_status="needs_review")])
        self.assertGreater(review["points"][0]["team_a_event_bonus"], 0.0)
        self.assertLess(review["points"][0]["team_a_event_bonus"], accepted["points"][0]["team_a_event_bonus"])

    def test_count_for_team_label_is_canonical(self) -> None:
        candidate = self._pass("completed_pass")
        candidate["count_for_team_label"] = "B"
        candidate["from_team_label"] = "A"
        result = self._build([], passes=[candidate])
        self.assertEqual(result["points"][0]["team_a_event_bonus"], 0.0)
        self.assertGreater(result["points"][0]["team_b_event_bonus"], 0.0)

    def test_restart_pass_is_not_counted_twice(self) -> None:
        candidate = self._pass("completed_pass")
        candidate.update({"from_restart": True, "restart_candidate_id": "restart-1"})
        restart = {"candidate_id": "restart-1", "actor_team_label": "A", "confidence": 1.0, "start_time_sec": 0.0}
        result = self._build([], passes=[candidate], restarts=[restart])
        self.assertEqual(result["summary"]["restart_passes_used"], 1)
        self.assertEqual(result["summary"]["restart_setup_bonuses"], 0)

    def test_free_contested_unknown_frames_do_not_score(self) -> None:
        frames = []
        for index, status in enumerate(("free", "contested", "unknown")):
            frame = self._frame(float(index), "A", [15.0, 5.0])
            frame["status"] = status
            frames.append(frame)
        result = self._build(frames)
        self.assertEqual(result["points"][0]["team_a_positional_raw"], 0.0)

    def test_high_confidence_scores_more_than_low_confidence(self) -> None:
        high = self._build([self._frame(0.0, "A", [15.0, 5.0], confidence=1.0)])
        low = self._build([self._frame(0.0, "A", [15.0, 5.0], confidence=0.2)])
        self.assertGreater(high["points"][0]["team_a_positional_raw"], low["points"][0]["team_a_positional_raw"])

    def test_longer_pressure_period_outweighs_single_frame(self) -> None:
        single = [self._frame(0.0, "A", [15.0, 5.0])] + [self._free(index / 10) for index in range(1, 10)]
        long = [self._frame(index / 10, "A", [15.0, 5.0]) for index in range(10)]
        self.assertGreater(
            self._build(long)["points"][0]["team_a_positional_raw"],
            self._build(single)["points"][0]["team_a_positional_raw"],
        )

    def test_score_is_clamped(self) -> None:
        passes = [self._pass("completed_pass", forward_progress_m=100.0) for _ in range(30)]
        result = self._build([], passes=passes)
        self.assertTrue(all(-100.0 <= point["signed_score"] <= 100.0 for point in result["points"]))

    def test_smoothing_is_causal(self) -> None:
        first = [self._frame(0.0, "A", [15.0, 5.0])]
        baseline = self._build(first, bin_sec=5.0)
        future = self._build(first + [self._frame(6.0, "B", [15.0, 45.0])], bin_sec=5.0)
        self.assertEqual(baseline["points"][0]["smoothed_signed_raw"], future["points"][0]["smoothed_signed_raw"])

    def test_output_is_sorted_and_deterministic(self) -> None:
        frames = [self._frame(6.0, "B", [15.0, 45.0]), self._frame(0.0, "A", [15.0, 5.0])]
        first = self._build(frames)
        second = self._build(list(reversed(frames)))
        self.assertEqual(first["points"], second["points"])
        self.assertEqual([point["index"] for point in first["points"]], [0, 1])

    def test_missing_optional_event_documents_are_supported(self) -> None:
        result = self._build([self._frame(0.0, "A", [15.0, 5.0])])
        self.assertEqual(len(result["points"]), 1)
        self.assertIn("Pass candidates were missing", " ".join(item["message"] for item in result["warnings"]))

    def test_empty_input_returns_low_quality_warning(self) -> None:
        result = self._build([])
        self.assertEqual(result["points"], [])
        self.assertEqual(result["summary"]["quality"], "low")
        self.assertTrue(result["warnings"])

    def test_progression_resets_across_free_ball(self) -> None:
        result = self._build(
            [
                self._frame(0.0, "A", [15.0, 35.0]),
                self._free(0.5),
                self._frame(1.0, "A", [15.0, 25.0]),
            ]
        )
        static = self._build(
            [self._frame(0.0, "A", [15.0, 25.0]), self._frame(1.0, "A", [15.0, 25.0])]
        )
        self.assertLessEqual(result["points"][0]["team_a_positional_raw"], static["points"][0]["team_a_positional_raw"])

    def test_failed_progressive_pass_uses_reduced_multiplier(self) -> None:
        completed = self._build([], passes=[self._pass("completed_pass", forward_progress_m=10.0)])
        failed = self._build([], passes=[self._pass("failed_pass", forward_progress_m=10.0)])
        self.assertLess(failed["points"][0]["team_a_event_bonus"], completed["points"][0]["team_a_event_bonus"])

    def test_restart_review_multiplier_and_supported_type(self) -> None:
        accepted = self._build([], restarts=[self._restart("accepted", "kick_in")])
        uncertain = self._build([], restarts=[self._restart("uncertain", "kick_in")])
        ignored = self._build([], restarts=[self._restart("accepted", "ignored_goal_line_restart")])
        self.assertGreater(accepted["points"][0]["team_a_event_bonus"], uncertain["points"][0]["team_a_event_bonus"])
        self.assertEqual(ignored["points"][0]["team_a_event_bonus"], 0.0)

    def test_event_only_bin_has_event_confidence(self) -> None:
        point = self._build([], passes=[self._pass("completed_pass")])["points"][0]
        self.assertEqual(point["positional_confidence"], 0.0)
        self.assertGreater(point["event_confidence"], 0.0)
        self.assertGreater(point["confidence"], 0.0)

    def test_event_bonus_is_capped_per_five_second_bin(self) -> None:
        result = self._build(
            [], passes=[self._pass("completed_pass", forward_progress_m=20.0) for _ in range(20)]
        )
        self.assertLessEqual(result["points"][0]["team_a_event_bonus"], 0.4)
        self.assertGreater(result["points"][0]["team_a_event_bonus_uncapped"], 0.4)

    def test_timeline_uses_explicit_duration_and_half_open_boundary(self) -> None:
        phase = {
            "periods": [
                {
                    "period_id": "full",
                    "start_time_sec": 0.0,
                    "end_time_sec": 10.0,
                    "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
                }
            ],
            "summary": {"needs_review": False},
        }
        at_boundary = self._pass("completed_pass")
        at_boundary["end_time_sec"] = 10.0
        result = self._build([], passes=[at_boundary], phase=phase)
        self.assertEqual(len(result["points"]), 2)
        self.assertEqual(sum(point["evidence"]["completed_passes"] for point in result["points"]), 0)

    def test_ema_resets_after_twenty_second_data_gap(self) -> None:
        first = self._pass("completed_pass")
        first["end_time_sec"] = 0.0
        second = self._pass("completed_pass")
        second["end_time_sec"] = 25.0
        result = build_attacking_momentum_document(
            {"frames": []},
            {"periods": [], "summary": {"needs_review": False}},
            pitch_width_m=PITCH_WIDTH_M,
            pitch_length_m=PITCH_LENGTH_M,
            pass_candidates_doc={"candidates": [first, second]},
            match_duration_sec=30.0,
        )
        self.assertAlmostEqual(
            result["points"][0]["smoothed_signed_raw"],
            result["points"][5]["smoothed_signed_raw"],
        )

    def _build(
        self,
        frames: list[dict],
        *,
        passes: list[dict] | None = None,
        restarts: list[dict] | None = None,
        phase: dict | None = None,
        bin_sec: float = 5.0,
    ) -> dict:
        return build_attacking_momentum_document(
            {"frames": frames},
            phase or self._phase(),
            pitch_width_m=PITCH_WIDTH_M,
            pitch_length_m=PITCH_LENGTH_M,
            pass_candidates_doc={"candidates": passes or []} if passes is not None else None,
            restart_candidates_doc={"candidates": restarts or []} if restarts is not None else None,
            bin_sec=bin_sec,
        )

    @staticmethod
    def _phase() -> dict:
        return {
            "periods": [
                {
                    "period_id": "full",
                    "start_time_sec": 0.0,
                    "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
                }
            ],
            "summary": {"needs_review": False},
        }

    @staticmethod
    def _frame(time_sec: float, team: str, position: list[float], confidence: float = 1.0) -> dict:
        return {
            "frame": round(time_sec * 30),
            "time_sec": time_sec,
            "status": "controlled",
            "team_label": team,
            "ball_position_m": position,
            "confidence": confidence,
            "ball_source": "detected",
            "nearest_player_source": "detected",
        }

    @staticmethod
    def _free(time_sec: float) -> dict:
        return {
            "frame": round(time_sec * 30),
            "time_sec": time_sec,
            "status": "free",
            "team_label": None,
            "ball_position_m": [15.0, 25.0],
            "confidence": 1.0,
        }

    @staticmethod
    def _pass(outcome: str, *, review_status: str = "accepted", forward_progress_m: float = 0.0) -> dict:
        return {
            "candidate_id": f"pass-{outcome}-{review_status}-{forward_progress_m}",
            "outcome": outcome,
            "count_for_team_label": "A",
            "from_team_label": "A",
            "confidence": 1.0,
            "review_status": review_status,
            "forward_progress_m": forward_progress_m,
            "is_progressive": forward_progress_m >= 5.0,
            "start_time_sec": 0.0,
            "end_time_sec": 0.5,
        }

    @staticmethod
    def _restart(review_status: str, restart_type: str) -> dict:
        return {
            "candidate_id": f"restart-{review_status}-{restart_type}",
            "actor_team_label": "A",
            "confidence": 1.0,
            "review_status": review_status,
            "restart_type": restart_type,
            "start_time_sec": 0.0,
        }


if __name__ == "__main__":
    unittest.main()
