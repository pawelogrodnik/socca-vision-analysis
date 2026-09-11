from __future__ import annotations

import unittest
from pathlib import Path

from app.services.shot_candidates import (
    POLICY_VERSION,
    build_logical_shot_candidates_document,
    build_shot_candidates_document,
)


PHASE_Y_MIN = {
    "periods": [
        {
            "period_id": "first",
            "start_time_sec": 0.0,
            "end_time_sec": 90.0,
            "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
            "direction_source": "test",
        }
    ]
}


def event(event_id: str, *, start: float, end: float, team: str | None = "A", player: str | None = "A01") -> dict:
    return {
        "event_id": event_id,
        "event_type": "ball_contact",
        "source_candidate_key": f"contact:v1:{event_id}",
        "review_status": "accepted",
        "stable_player_id": player,
        "team_label": team,
        "team_id": f"team-{team}" if team else None,
        "team_name": f"Team {team}" if team else None,
        "start_time_sec": start,
        "end_time_sec": end,
        "end_frame": round(end * 30),
    }


def ball_rows(points: list[tuple[float, float, float]], *, unknown_at: float | None = None) -> dict:
    rows = [
        {
            "frame": round(time_sec * 30),
            "time_sec": time_sec,
            "position_m": [x, y],
            "source": "detected",
            "confidence": 0.9,
        }
        for time_sec, x, y in points
    ]
    if unknown_at is not None:
        rows.append({"frame": round(unknown_at * 30), "time_sec": unknown_at, "position_m": None, "source": "unknown", "confidence": 0.0})
    return {"positions": sorted(rows, key=lambda row: row["time_sec"])}


def candidates(events: list[dict], points: list[tuple[float, float, float]], phase: dict | None = PHASE_Y_MIN, *, unknown_at: float | None = None) -> dict:
    return build_shot_candidates_document(
        {"events": events},
        ball_rows(points, unknown_at=unknown_at),
        phase,
        source_match_id="m1",
        pitch_width_m=30.0,
        pitch_length_m=47.4,
    )


class ShotCandidatesTests(unittest.TestCase):
    def test_obvious_goalward_shot_is_a_review_only_candidate(self) -> None:
        document = candidates([event("contact-1", start=0.0, end=1.0)], [(1.0, 15.0, 29.0), (1.2, 15.0, 22.0), (1.4, 15.0, 12.0), (1.6, 15.0, 4.0)])

        self.assertEqual(document["policy_version"], POLICY_VERSION)
        self.assertEqual(len(document["candidates"]), 1)
        candidate = document["candidates"][0]
        self.assertEqual(candidate["review_status"], "needs_review")
        self.assertFalse(candidate["final_stat_eligible"])
        self.assertEqual(candidate["suggested_outcome"], None)
        self.assertIn("towards_opponent_goal", candidate["reasons"])

    def test_forward_pass_to_same_team_receiver_is_suppressed(self) -> None:
        document = candidates(
            [event("contact-1", start=0.0, end=1.0), event("receiver", start=1.7, end=2.0, player="A02")],
            [(1.0, 15.0, 32.0), (1.2, 15.0, 26.0), (1.4, 15.0, 20.0), (1.6, 15.0, 15.0)],
        )

        self.assertEqual(document["candidates"], [])
        self.assertGreater(document["summary"]["skipped_evidence_reasons"]["same_team_receiver_before_goal"], 0)

    def test_cross_like_trajectory_is_explicitly_labeled_and_lower_confidence(self) -> None:
        document = candidates([event("cross", start=0.0, end=1.0)], [(1.0, 2.0, 28.0), (1.2, 9.0, 22.0), (1.4, 18.0, 15.0), (1.6, 27.0, 7.0)])

        candidate = document["candidates"][0]
        self.assertIn("cross_like_trajectory", candidate["reasons"])
        self.assertTrue(candidate["trajectory_evidence"]["cross_like"])
        self.assertLess(candidate["confidence"], 0.8)

    def test_goalward_long_pass_to_receiver_is_handled_conservatively(self) -> None:
        document = candidates(
            [event("long-pass", start=0.0, end=1.0), event("receiver", start=1.8, end=2.1, player="A02")],
            [(1.0, 15.0, 35.0), (1.2, 15.0, 27.0), (1.4, 15.0, 15.0), (1.6, 15.0, 5.0)],
        )

        self.assertEqual(len(document["candidates"]), 1)
        self.assertTrue(document["candidates"][0]["receiver_evidence"]["same_team_receiver_before_goal"])
        self.assertLess(document["candidates"][0]["confidence"], 0.8)

    def test_attack_direction_is_respected(self) -> None:
        phase = {"periods": [{**PHASE_Y_MIN["periods"][0], "team_attack_directions": {"A": "towards_y_max", "B": "towards_y_min"}}]}
        document = candidates([event("wrong-way", start=0.0, end=1.0)], [(1.0, 15.0, 30.0), (1.2, 15.0, 22.0), (1.4, 15.0, 12.0), (1.6, 15.0, 4.0)], phase)

        self.assertEqual(document["candidates"], [])
        self.assertGreater(document["summary"]["skipped_evidence_reasons"]["not_goalward"], 0)

    def test_unknown_ball_gap_does_not_fabricate_trajectory(self) -> None:
        document = candidates([event("gap", start=0.0, end=1.0)], [(1.0, 15.0, 30.0), (1.2, 15.0, 24.0), (2.0, 15.0, 6.0)], unknown_at=1.5)

        self.assertEqual(document["candidates"], [])

    def test_missing_player_and_ambiguous_team_do_not_block_candidate_or_fabricate_attribution(self) -> None:
        known_team = candidates([event("no-player", start=0.0, end=1.0, player=None)], [(1.0, 15.0, 28.0), (1.2, 15.0, 20.0), (1.4, 15.0, 10.0), (1.6, 15.0, 4.0)])
        unknown_team = candidates([event("no-team", start=0.0, end=1.0, team=None, player=None)], [(1.0, 15.0, 28.0), (1.2, 15.0, 20.0), (1.4, 15.0, 10.0), (1.6, 15.0, 4.0)], None)

        self.assertEqual(known_team["candidates"][0]["suggested_player_id"], None)
        self.assertEqual(unknown_team["candidates"][0]["suggested_team_label"], None)
        self.assertEqual(unknown_team["candidates"][0]["attack_direction"], "unknown")

    def test_keys_are_deterministic_and_unchanged_evidence_regenerates_identically(self) -> None:
        args = ([event("stable", start=0.0, end=1.0)], [(1.0, 15.0, 28.0), (1.2, 15.0, 20.0), (1.4, 15.0, 10.0), (1.6, 15.0, 4.0)])
        first = candidates(*args)
        second = candidates(*args)

        self.assertEqual(first["candidates"][0]["candidate_key"], second["candidates"][0]["candidate_key"])
        self.assertEqual(first["candidates"][0]["candidate_id"], second["candidates"][0]["candidate_id"])

    def test_nearby_hypotheses_deduplicate_but_two_seconds_apart_remain_distinct(self) -> None:
        nearby = candidates(
            [event("one", start=3.0, end=1.0), event("two", start=4.0, end=1.3)],
            [(1.0, 15.0, 28.0), (1.1, 15.0, 21.0), (1.2, 15.0, 12.0), (1.3, 15.0, 5.0), (1.4, 15.0, 4.0), (1.5, 15.0, 3.0), (1.6, 15.0, 2.0)],
        )
        separate = candidates(
            [event("one", start=5.0, end=1.0), event("two", start=6.0, end=3.05)],
            [(1.0, 15.0, 28.0), (1.2, 15.0, 20.0), (1.4, 15.0, 4.0), (3.05, 15.0, 28.0), (3.25, 15.0, 20.0), (3.45, 15.0, 4.0)],
        )

        self.assertEqual(len(nearby["candidates"]), 1)
        self.assertEqual(nearby["candidates"][0]["deduplicated_hypothesis_count"], 2)
        self.assertEqual(len(separate["candidates"]), 2)

    def test_logical_projection_preserves_source_provenance(self) -> None:
        physical = candidates([event("source", start=0.0, end=1.0)], [(1.0, 15.0, 28.0), (1.2, 15.0, 20.0), (1.4, 15.0, 10.0), (1.6, 15.0, 4.0)])
        logical = build_logical_shot_candidates_document([{"source_match_id": "m1", "logical_offset_sec": 100.0, "shot_candidates": physical}], timeline_span_sec=200.0)

        candidate = logical["candidates"][0]
        self.assertEqual(candidate["source_match_id"], "m1")
        self.assertEqual(candidate["source_timestamp_sec"], 1.0)
        self.assertEqual(candidate["logical_timestamp_sec"], 101.0)
        self.assertTrue(candidate["physical_candidate_key"])

    def test_production_module_has_no_goldset_dependency(self) -> None:
        runtime_root = Path(__file__).resolve().parents[1] / "app"
        for path in runtime_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("shot_goldset_v1", source, path.as_posix())
            self.assertNotIn("shot-goldset:v1", source, path.as_posix())


if __name__ == "__main__":
    unittest.main()
