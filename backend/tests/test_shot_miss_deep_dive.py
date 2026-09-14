from __future__ import annotations

import unittest
from pathlib import Path

from evaluation.shot_miss_deep_dive import _classify, analyze_shot_miss_deep_dive


PHASE = {
    "periods": [{
        "period_id": "one",
        "start_time_sec": 0.0,
        "end_time_sec": 10.0,
        "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
        "direction_source": "test",
    }]
}


def contact(team: str = "A") -> dict:
    return {
        "event_id": "contact-1",
        "event_type": "ball_contact",
        "review_status": "accepted",
        "team_label": team,
        "team_name": team,
        "team_id": f"team-{team}",
        "stable_player_id": "A01",
        "start_time_sec": 1.0,
        "end_time_sec": 1.0,
        "end_frame": 30,
    }


def rows(values: list[tuple[float, float, float, str, float]]) -> dict:
    return {
        "positions": [
            {"frame": round(time * 30), "time_sec": time, "position_m": [x, y] if source != "unknown" else None, "source": source, "confidence": confidence}
            for time, x, y, source, confidence in values
        ]
    }


def source(events: list[dict], positions: dict) -> dict:
    return {
        "source_match_id": "m1",
        "logical_start_sec": 0.0,
        "logical_end_sec": 10.0,
        "event_candidates": {"events": events},
        "ball_candidates": {"frames": []},
        "ball_tracks": positions,
        "match_phase_config": PHASE,
        "pitch_width_m": 30.0,
        "pitch_length_m": 47.4,
    }


def gold(team: str = "A") -> dict:
    return {
        "schema_version": "shot-goldset:v2",
        "shots": [{
            "id": "gold-1",
            "timestamp_sec": 1.0,
            "timestamp_display": "00:01.0",
            "team": team,
            "player": "Player A",
            "outcome": "blocked",
            "origin": "manual",
        }],
    }


V4 = {"policy_version": "shot-candidate-shadow:v4-continuity-bridge", "candidates": []}


class ShotMissDeepDiveTests(unittest.TestCase):
    def test_deep_dive_is_deterministic_and_funnel_is_monotonic(self) -> None:
        inputs = (V4, gold(), [source([contact()], rows([
            (1.0, 15.0, 30.0, "detected", .9),
            (1.1, 15.0, 28.0, "detected", .1),
            (1.2, 15.0, 26.0, "detected", .9),
            (1.3, 15.0, 20.0, "detected", .9),
        ]))])
        first = analyze_shot_miss_deep_dive(*inputs)
        second = analyze_shot_miss_deep_dive(*inputs)

        self.assertEqual(first, second)
        self.assertEqual(first["miss_count"], 1)
        funnel = first["funnel"]["all_canonical_shots"]
        values = [funnel[key] for key in ("canonical_shots", "plausible_contact", "trusted_launch", "usable_trajectory", "meaningful_trajectory", "direction_accepted", "candidate_emitted", "benchmark_matched")]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_weak_detected_boundary_is_precisely_classified(self) -> None:
        report = analyze_shot_miss_deep_dive(
            V4,
            gold(),
            [source([contact()], rows([
                (1.0, 15.0, 30.0, "detected", .9),
                (1.1, 15.0, 28.0, "detected", .1),
                (1.2, 15.0, 26.0, "detected", .9),
                (1.3, 15.0, 20.0, "detected", .9),
            ]))],
        )

        miss = report["misses"][0]
        self.assertEqual(miss["primary_cause"], "WEAK_DETECTED_BOUNDARY")
        boundary = miss["weak_detected_boundaries"][0]
        self.assertEqual(boundary["spatial_classification"], "likely_correct_ball_low_confidence")
        self.assertTrue(boundary["counterfactual_without_weak_detected"]["would_recover_within_tolerance"])

    def test_not_goalward_reports_attack_geometry(self) -> None:
        report = analyze_shot_miss_deep_dive(
            V4,
            gold(),
            [source([contact()], rows([
                (1.0, 15.0, 20.0, "detected", .9),
                (1.1, 15.0, 24.0, "detected", .9),
                (1.2, 15.0, 29.0, "detected", .9),
            ]))],
        )

        detail = report["not_goalward_analysis"][0]
        self.assertEqual(detail["goal_used"], "y_min")
        self.assertLess(detail["signed_goalward_progress_m"], 0)
        self.assertEqual(detail["interpretation"], "trajectory_evidence_moves_away_from_resolved_goal")

    def test_multi_stage_paths_remain_multi_stage(self) -> None:
        primary, secondary, stage, confidence, _ = _classify(
            {"benchmark_matched": False, "contact_paths": [{}, {}]},
            [
                {"shot_policy": {"rejection_reason": "not_goalward"}},
                {"shot_policy": {"rejection_reason": "same_team_receiver_before_goal"}},
            ],
            {}, [], [], [],
        )

        self.assertEqual(primary, "MULTI_STAGE_FAILURE")
        self.assertEqual(secondary, ["DIRECTION_WRONG", "PASS_RECEIVER_CONFLICT"])
        self.assertEqual(stage, "multiple")
        self.assertEqual(confidence, "LOW")

    def test_trajectory_not_meaningful_names_the_failed_thresholds(self) -> None:
        report = analyze_shot_miss_deep_dive(
            V4,
            gold(),
            [source([contact()], rows([
                (1.0, 15.0, 30.0, "detected", .9),
                (1.1, 15.0, 29.9, "detected", .9),
                (1.2, 15.0, 29.8, "detected", .9),
            ]))],
        )

        detail = report["misses"][0]["trajectory_not_meaningful"][0]
        self.assertIn("distance", detail["failed_conditions"])
        self.assertIn("speed", detail["failed_conditions"])

    def test_runtime_modules_do_not_import_deep_dive_or_goldset_truth(self) -> None:
        runtime_root = Path(__file__).resolve().parents[1] / "app"
        for path in runtime_root.rglob("*.py"):
            contents = path.read_text(encoding="utf-8")
            self.assertNotIn("shot_miss_deep_dive", contents, path.as_posix())
            self.assertNotIn("shot_weak_boundary_shadow", contents, path.as_posix())
            self.assertNotIn("shot_candidate_operator_review", contents, path.as_posix())
            self.assertNotIn("shot_goldset_v2", contents, path.as_posix())


if __name__ == "__main__":
    unittest.main()
