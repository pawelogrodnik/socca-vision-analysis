from __future__ import annotations

import unittest

from app.services.identity_operator_benchmark import build_identity_operator_benchmark


def row(frame: int, x: float, player_id: str = "p1") -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 10,
        "pitch_m": [x, 5.0],
        "bbox_xyxy": [10, 10, 20, 30],
        "status": "detected",
        "player_id": player_id,
    }


class IdentityOperatorBenchmarkTests(unittest.TestCase):
    def test_limits_comparison_to_requested_window(self) -> None:
        rows = [row(0, 0.0), row(30, 1.0), row(60, 2.0)]
        production = {"fps": 30, "players": {"p1": {"rows": rows}}}
        candidate = {"players": [{"player_id": "p1", "observations": rows}]}

        report = build_identity_operator_benchmark(
            production_timeline=production,
            candidate_timeline=candidate,
            match_doc={"video": {"fps": 30, "duration_sec": 3}, "teams": []},
            label="window",
            start_sec=1.0,
            max_seconds=1.0,
            generated_at="2026-01-01T00:00:00+00:00",
        )

        self.assertEqual(report["benchmark"]["start_frame"], 30)
        self.assertEqual(report["benchmark"]["end_frame"], 59)
        self.assertEqual(report["metrics"]["production_observations"], 1)
        self.assertEqual(report["metrics"]["candidate_observations"], 1)

    def test_groups_adjacent_candidate_only_frames(self) -> None:
        production = {"fps": 10, "players": {"p1": {"rows": [row(0, 0), row(1, 1)]}}}
        candidate = {"players": [{"player_id": "p1", "observations": [row(0, 0), row(1, 1), row(2, 2), row(3, 3)]}]}
        result = build_identity_operator_benchmark(
            production_timeline=production,
            candidate_timeline=candidate,
            match_doc={"video": {"fps": 10, "duration_sec": 1}, "teams": []},
            label="test",
        )

        cards = [card for card in result["cards"] if card["category"] == "candidate_only"]
        self.assertEqual(len(cards), 1)
        self.assertEqual((cards[0]["start_frame"], cards[0]["end_frame"]), (2, 3))

    def test_reports_position_disagreement_and_large_jump(self) -> None:
        production = {"fps": 10, "players": {"p1": {"rows": [row(0, 0), row(1, 0)]}}}
        candidate = {"players": [{"player_id": "p1", "observations": [row(0, 0), row(1, 5)]}]}
        result = build_identity_operator_benchmark(
            production_timeline=production,
            candidate_timeline=candidate,
            match_doc={"video": {"fps": 10, "duration_sec": 1}, "teams": []},
            label="test",
        )

        categories = {card["category"] for card in result["cards"]}
        self.assertIn("position_disagreement", categories)
        self.assertIn("candidate_large_jump", categories)

    def test_includes_review_telemetry_and_candidate_coverage(self) -> None:
        candidate = {"players": [{"player_id": "p1", "observations": [row(0, 0)]}]}
        result = build_identity_operator_benchmark(
            production_timeline={"fps": 10, "players": {}},
            candidate_timeline=candidate,
            match_doc={"video": {"fps": 10, "duration_sec": 1}, "teams": []},
            candidate_manifest={"coverage": {"eligible_observations": 8, "excluded_fragments": 1, "unresolved_fragments": 1}},
            review_decisions={"decisions": [{"decision": "mark_unresolved"}], "operator_telemetry": {"active_review_seconds": 12}},
            label="test",
        )

        self.assertEqual(result["metrics"]["manual_review_time_sec"], 12)
        self.assertEqual(result["metrics"]["subjects_unresolved"], 1)
        self.assertEqual(result["metrics"]["promoted_detected_ratio"], 0.8)

    def test_prefers_temporal_promotion_coverage_over_card_counts(self) -> None:
        result = build_identity_operator_benchmark(
            production_timeline={"fps": 10, "players": {}},
            candidate_timeline={"players": [{"player_id": "p1", "observations": [row(0, 0)]}]},
            match_doc={"video": {"fps": 10, "duration_sec": 1}, "teams": []},
            candidate_manifest={"coverage": {"eligible_observations": 99, "unresolved_subjects": 1}},
            promotion_plan={
                "coverage": {
                    "all_reliable_detected_team_observations": 100,
                    "promoted_reliable_detected_team_observations": 67,
                    "unresolved_detected_frames": 33,
                }
            },
            label="test",
        )

        self.assertEqual(result["metrics"]["promoted_detected_ratio"], 0.67)
        self.assertEqual(result["metrics"]["unresolved_detected_ratio"], 0.33)

    def test_missing_production_timeline_uses_candidate_safety_audit(self) -> None:
        result = build_identity_operator_benchmark(
            production_timeline={"fps": 10, "players": {}},
            candidate_timeline={
                "players": [
                    {
                        "player_id": "p1",
                        "observations": [row(0, 0), row(1, 1)],
                    }
                ]
            },
            match_doc={"video": {"fps": 10, "duration_sec": 1}, "teams": []},
            label="candidate-only",
        )

        self.assertEqual(result["benchmark"]["mode"], "candidate_safety_audit")
        self.assertFalse(result["benchmark"]["production_baseline_available"])
        self.assertEqual(
            [card for card in result["cards"] if card["category"] == "candidate_only"],
            [],
        )
        self.assertEqual(
            result["warnings"][0]["code"],
            "production_player_timeline_unavailable",
        )
        self.assertEqual(
            result["review_contract"]["allowed_decisions"],
            ["candidate_correct", "candidate_wrong", "unclear"],
        )


if __name__ == "__main__":
    unittest.main()
