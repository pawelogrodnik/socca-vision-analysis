from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

import app.services.shot_candidates as shot_candidates
from app.services.shot_candidates import (
    POLICY_VERSION,
    PREVIOUS_POLICY_VERSION,
    V3_POLICY_VERSION,
    V4_CONTINUITY_BRIDGE_POLICY_VERSION,
    V5_WEAK_BOUNDARY_POLICY_VERSION,
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


def candidates(
    events: list[dict],
    points: list[tuple[float, float, float]],
    phase: dict | None = PHASE_Y_MIN,
    *,
    unknown_at: float | None = None,
    ball_document: dict | None = None,
    policy_version: str = POLICY_VERSION,
) -> dict:
    return build_shot_candidates_document(
        {"events": events},
        ball_document or ball_rows(points, unknown_at=unknown_at),
        phase,
        source_match_id="m1",
        pitch_width_m=30.0,
        pitch_length_m=47.4,
        policy_version=policy_version,
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

    def test_v3_suppresses_a_long_goalward_pass_to_a_same_team_receiver(self) -> None:
        events = [event("long-pass", start=0.0, end=1.0), event("receiver", start=1.8, end=2.1, player="A02")]
        points = [(1.0, 15.0, 35.0), (1.2, 15.0, 27.0), (1.4, 15.0, 15.0), (1.6, 15.0, 5.0)]

        current = candidates(events, points, policy_version=POLICY_VERSION)
        v3 = candidates(events, points, policy_version=V3_POLICY_VERSION)

        self.assertEqual(len(current["candidates"]), 1)
        self.assertEqual(v3["candidates"], [])
        self.assertEqual(v3["summary"]["suppressed_evidence_reasons"], {"pass_like_same_team_receiver": 1})

    def test_v3_suppresses_an_earlier_nonterminal_touch_before_a_stronger_later_shot(self) -> None:
        document = candidates(
            [event("pass", start=0.0, end=1.0), event("shot", start=0.0, end=2.2)],
            [(1.0, 15.0, 35.0), (1.2, 15.0, 32.0), (1.4, 15.0, 29.0), (2.2, 15.0, 13.0), (2.4, 15.0, 8.0), (2.6, 15.0, 3.0)],
            unknown_at=1.6,
            policy_version=V3_POLICY_VERSION,
        )

        self.assertEqual([row["source_event_id"] for row in document["candidates"]], ["shot"])
        self.assertEqual(document["summary"]["suppressed_evidence_reasons"], {"stronger_later_shot_action": 1})

    def test_v3_retains_a_standalone_terminal_shot(self) -> None:
        document = candidates(
            [event("shot", start=0.0, end=1.0)],
            [(1.0, 15.0, 15.0), (1.2, 15.0, 9.0), (1.4, 15.0, 3.0)],
            policy_version=V3_POLICY_VERSION,
        )

        self.assertEqual([row["source_event_id"] for row in document["candidates"]], ["shot"])
        self.assertEqual(document["suppressed_candidate_diagnostics"], [])

    def test_v3_retains_a_strong_terminal_shot_followed_by_a_teammate_touch(self) -> None:
        document = candidates(
            [event("shot", start=0.0, end=1.0), event("rebound", start=1.6, end=1.8, player="A02")],
            [(1.0, 15.0, 15.0), (1.2, 15.0, 9.0), (1.4, 15.0, 3.0)],
            policy_version=V3_POLICY_VERSION,
        )

        self.assertEqual([row["source_event_id"] for row in document["candidates"]], ["shot"])
        self.assertTrue(document["candidates"][0]["receiver_evidence"]["same_team_receiver_before_goal"])

    def test_v3_keeps_two_terminal_shots_around_an_immediate_rebound(self) -> None:
        document = candidates(
            [event("first-shot", start=0.0, end=1.0), event("rebound-shot", start=0.0, end=2.0)],
            [(1.0, 15.0, 15.0), (1.1, 15.0, 9.0), (1.2, 15.0, 3.0), (2.0, 15.0, 15.0), (2.1, 15.0, 9.0), (2.2, 15.0, 3.0)],
            unknown_at=1.3,
            policy_version=V3_POLICY_VERSION,
        )

        self.assertEqual([row["source_event_id"] for row in document["candidates"]], ["first-shot", "rebound-shot"])

    def test_v3_keeps_a_cross_like_trajectory_under_existing_cross_handling(self) -> None:
        document = candidates(
            [event("cross", start=0.0, end=1.0)],
            [(1.0, 2.0, 28.0), (1.2, 9.0, 22.0), (1.4, 18.0, 15.0), (1.6, 27.0, 7.0)],
            policy_version=V3_POLICY_VERSION,
        )

        self.assertEqual(len(document["candidates"]), 1)
        self.assertIn("cross_like_trajectory", document["candidates"][0]["reasons"])

    def test_v3_later_shot_lookahead_never_crosses_source_boundaries(self) -> None:
        earlier = {"source_match_id": "one", "source_timestamp_sec": 1.0, "suggested_team_label": "A", "confidence": .4, "trajectory_evidence": {"endpoint_goal_distance_m": 30.0, "goal_corridor_distance_m": .5}}
        later_other_source = {"source_match_id": "two", "source_timestamp_sec": 1.2, "suggested_team_label": "A", "confidence": .9, "trajectory_evidence": {"endpoint_goal_distance_m": 2.0, "goal_corridor_distance_m": .5}}

        retained, suppressed = shot_candidates._apply_v3_structural_suppression([earlier, later_other_source])

        self.assertEqual((len(retained), suppressed), (2, []))

    def test_attack_direction_is_respected(self) -> None:
        phase = {"periods": [{**PHASE_Y_MIN["periods"][0], "team_attack_directions": {"A": "towards_y_max", "B": "towards_y_min"}}]}
        document = candidates([event("wrong-way", start=0.0, end=1.0)], [(1.0, 15.0, 30.0), (1.2, 15.0, 22.0), (1.4, 15.0, 12.0), (1.6, 15.0, 4.0)], phase)

        self.assertEqual(document["candidates"], [])
        self.assertGreater(document["summary"]["skipped_evidence_reasons"]["not_goalward"], 0)

    def test_towards_y_max_shot_is_a_candidate(self) -> None:
        phase = {"periods": [{**PHASE_Y_MIN["periods"][0], "team_attack_directions": {"A": "towards_y_max", "B": "towards_y_min"}}]}
        document = candidates(
            [event("bottom-goal", start=0.0, end=1.0)],
            [(1.0, 15.0, 18.0), (1.2, 15.0, 27.0), (1.4, 15.0, 38.0), (1.6, 15.0, 44.0)],
            phase,
        )

        self.assertEqual(len(document["candidates"]), 1)
        self.assertEqual(document["candidates"][0]["attack_direction"], "towards_y_max")

    def test_y_attack_direction_switch_between_phases_is_respected(self) -> None:
        phase = {
            "periods": [
                {"period_id": "first", "start_time_sec": 0.0, "end_time_sec": 5.0, "team_attack_directions": {"A": "towards_y_min"}, "direction_source": "test"},
                {"period_id": "second", "start_time_sec": 5.0, "end_time_sec": 90.0, "team_attack_directions": {"A": "towards_y_max"}, "direction_source": "test"},
            ]
        }
        document = candidates(
            [event("first-half", start=1.0, end=1.0), event("second-half", start=6.0, end=6.0)],
            [
                (1.0, 15.0, 29.0), (1.2, 15.0, 21.0), (1.4, 15.0, 12.0), (1.6, 15.0, 4.0),
                (6.0, 15.0, 18.0), (6.2, 15.0, 28.0), (6.4, 15.0, 38.0), (6.6, 15.0, 44.0),
            ],
            phase,
        )

        self.assertEqual([row["attack_direction"] for row in document["candidates"]], ["towards_y_min", "towards_y_max"])
        v3 = candidates(
            [event("first-half", start=1.0, end=1.0), event("second-half", start=6.0, end=6.0)],
            [
                (1.0, 15.0, 29.0), (1.2, 15.0, 21.0), (1.4, 15.0, 12.0), (1.6, 15.0, 4.0),
                (6.0, 15.0, 18.0), (6.2, 15.0, 28.0), (6.4, 15.0, 38.0), (6.6, 15.0, 44.0),
            ],
            phase,
            policy_version=V3_POLICY_VERSION,
        )
        self.assertEqual([row["attack_direction"] for row in v3["candidates"]], ["towards_y_min", "towards_y_max"])

    def test_unsupported_x_attack_axis_does_not_fabricate_candidate(self) -> None:
        phase = {"periods": [{**PHASE_Y_MIN["periods"][0], "team_attack_directions": {"A": "towards_x_min", "B": "towards_x_max"}}]}
        document = candidates([event("horizontal-goal", start=0.0, end=1.0)], [(1.0, 15.0, 29.0), (1.2, 15.0, 22.0), (1.4, 15.0, 12.0), (1.6, 15.0, 4.0)], phase)

        self.assertEqual(document["candidates"], [])
        self.assertGreater(document["summary"]["skipped_evidence_reasons"]["unsupported_attack_axis"], 0)

    def test_short_explicit_unknown_ball_row_breaks_trajectory(self) -> None:
        ball_document = {
            "positions": [
                {"frame": 30, "time_sec": 1.0, "position_m": [15.0, 30.0], "source": "detected", "confidence": 0.9},
                {"frame": 33, "time_sec": 1.1, "position_m": [15.0, 24.0], "source": "detected", "confidence": 0.9},
                {"frame": 36, "time_sec": 1.2, "position_m": None, "source": "unknown", "confidence": 0.0},
                {"frame": 39, "time_sec": 1.3, "position_m": [15.0, 12.0], "source": "detected", "confidence": 0.9},
                {"frame": 42, "time_sec": 1.4, "position_m": [15.0, 4.0], "source": "detected", "confidence": 0.9},
            ]
        }
        document = candidates([event("gap", start=0.0, end=1.0)], [], ball_document=ball_document)

        self.assertEqual(document["candidates"], [])

    def test_v4_bridges_a_short_plausible_post_contact_gap(self) -> None:
        ball_document = ball_rows(
            [(1.0, 15.0, 30.0), (1.1, 15.0, 26.0), (1.3, 15.0, 20.0), (1.4, 15.0, 14.0), (1.5, 15.0, 8.0)],
            unknown_at=1.2,
        )

        v2 = candidates([event("bridge", start=0.0, end=1.0)], [], ball_document=ball_document)
        v4 = candidates(
            [event("bridge", start=0.0, end=1.0)],
            [],
            ball_document=ball_document,
            policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION,
        )

        self.assertEqual(v2["candidates"], [])
        self.assertEqual(len(v4["candidates"]), 1)
        evidence = v4["candidates"][0]["trajectory_evidence"]["continuity_bridge"]
        self.assertTrue(evidence["applied"])
        self.assertEqual(evidence["gap_frames"], 5)
        self.assertEqual(evidence["gap_sec"], 0.2)
        self.assertEqual(evidence["endpoint_support"], {"pre_trusted_samples": 2, "post_trusted_samples": 2, "bridge_samples": 1})
        self.assertIn("continuity_bridge_applied", v4["candidates"][0]["reasons"])

    def test_v4_does_not_bridge_a_long_gap(self) -> None:
        ball_document = ball_rows(
            [(1.0, 15.0, 30.0), (1.1, 15.0, 26.0), (1.7, 15.0, 20.0), (1.8, 15.0, 14.0)],
            unknown_at=1.2,
        )
        document = candidates(
            [event("long-gap", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION
        )
        self.assertEqual(document["candidates"], [])

    def test_v4_does_not_bridge_an_implausible_spatial_jump(self) -> None:
        ball_document = ball_rows(
            [(1.0, 15.0, 30.0), (1.1, 15.0, 26.0), (1.3, 15.0, 2.0), (1.4, 15.0, 1.0)],
            unknown_at=1.2,
        )
        document = candidates(
            [event("teleport", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION
        )
        self.assertEqual(document["candidates"], [])

    def test_v4_does_not_bridge_an_explicit_timeline_boundary(self) -> None:
        ball_document = ball_rows(
            [(1.0, 15.0, 30.0), (1.1, 15.0, 26.0), (1.3, 15.0, 20.0), (1.4, 15.0, 14.0)], unknown_at=1.2
        )
        for row in ball_document["positions"]:
            row["timeline_segment_id"] = "one" if row["time_sec"] < 1.3 else "two"
        document = candidates(
            [event("boundary", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION
        )
        self.assertEqual(document["candidates"], [])

    def test_v4_does_not_bridge_across_a_new_contact_boundary(self) -> None:
        ball_document = ball_rows(
            [(1.0, 15.0, 30.0), (1.1, 15.0, 26.0), (1.3, 15.0, 20.0), (1.4, 15.0, 14.0)], unknown_at=1.2
        )
        document = candidates(
            [event("launch", start=0.0, end=1.0), event("new-contact", start=1.2, end=1.25, player="B01", team="B")],
            [],
            ball_document=ball_document,
            policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION,
        )
        self.assertEqual(document["candidates"], [])

    def test_v4_is_deterministic_and_no_gap_trajectory_remains_unbridged(self) -> None:
        args = ([event("stable-v4", start=0.0, end=1.0)], [(1.0, 15.0, 30.0), (1.1, 15.0, 24.0), (1.2, 15.0, 16.0), (1.3, 15.0, 8.0)])
        first = candidates(*args, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION)
        second = candidates(*args, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION)
        self.assertEqual(first, second)
        self.assertNotIn("continuity_bridge", first["candidates"][0]["trajectory_evidence"])

    def test_v4_bridge_diagnostics_are_stable(self) -> None:
        ball_document = ball_rows(
            [(1.0, 15.0, 30.0), (1.1, 15.0, 26.0), (1.3, 15.0, 20.0), (1.4, 15.0, 14.0)], unknown_at=1.2
        )
        first = candidates([event("diag", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION)
        second = candidates([event("diag", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION)
        self.assertEqual(first["candidates"][0]["trajectory_evidence"]["continuity_bridge"], second["candidates"][0]["trajectory_evidence"]["continuity_bridge"])

    def test_v5_reinterprets_one_spatially_consistent_weak_detected_boundary(self) -> None:
        ball_document = {
            "positions": [
                {"frame": 30, "time_sec": 1.0, "position_m": [15.0, 30.0], "source": "detected", "confidence": 0.9},
                {"frame": 33, "time_sec": 1.1, "position_m": [15.0, 26.0], "source": "detected", "confidence": 0.9},
                {"frame": 36, "time_sec": 1.2, "position_m": [15.0, 23.0], "source": "detected", "confidence": 0.1},
                {"frame": 39, "time_sec": 1.3, "position_m": [15.0, 20.0], "source": "detected", "confidence": 0.9},
                {"frame": 42, "time_sec": 1.4, "position_m": [15.0, 14.0], "source": "detected", "confidence": 0.9},
            ]
        }

        v4 = candidates([event("weak", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION)
        v5 = candidates([event("weak", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)

        self.assertEqual(v4["candidates"], [])
        self.assertEqual(len(v5["candidates"]), 1)
        evidence = v5["candidates"][0]["trajectory_evidence"]["continuity_bridge"]
        self.assertEqual(evidence["weak_boundary_state"], "weak_boundary_reinterpreted")
        self.assertTrue(evidence["weak_boundary_considered"])
        self.assertEqual(v5["weak_boundary_diagnostics"][0]["weak_boundary_state"], "weak_boundary_reinterpreted")
        self.assertIn("continuity_bridge_applied", v5["candidates"][0]["reasons"])

    def test_v5_keeps_v2_v3_and_v4_semantics_unchanged(self) -> None:
        ball_document = {
            "positions": [
                {"frame": 30, "time_sec": 1.0, "position_m": [15.0, 30.0], "source": "detected", "confidence": 0.9},
                {"frame": 33, "time_sec": 1.1, "position_m": [15.0, 26.0], "source": "detected", "confidence": 0.9},
                {"frame": 36, "time_sec": 1.2, "position_m": [15.0, 23.0], "source": "detected", "confidence": 0.1},
                {"frame": 39, "time_sec": 1.3, "position_m": [15.0, 20.0], "source": "detected", "confidence": 0.9},
                {"frame": 42, "time_sec": 1.4, "position_m": [15.0, 14.0], "source": "detected", "confidence": 0.9},
            ]
        }

        v2 = candidates([event("weak", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=POLICY_VERSION)
        v3 = candidates([event("weak", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V3_POLICY_VERSION)
        v4 = candidates([event("weak", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V4_CONTINUITY_BRIDGE_POLICY_VERSION)
        v5 = candidates([event("weak", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)

        self.assertEqual(v2["candidates"], [])
        self.assertEqual(v3["candidates"], [])
        self.assertEqual(v4["candidates"], [])
        self.assertNotIn("weak_boundary_diagnostics", v2)
        self.assertNotIn("weak_boundary_diagnostics", v3)
        self.assertNotIn("weak_boundary_diagnostics", v4)
        self.assertEqual(len(v5["candidates"]), 1)

    def test_v5_rejects_trusted_or_multiple_detected_boundaries(self) -> None:
        trusted = ball_rows([(1.0, 15.0, 30.0), (1.1, 15.0, 26.0), (1.2, 15.0, 20.0), (1.3, 15.0, 14.0), (1.4, 15.0, 8.0)])
        trusted_result = candidates([event("trusted", start=0.0, end=1.0)], [], ball_document=trusted, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)
        self.assertEqual(len(trusted_result["candidates"]), 1)
        self.assertEqual(trusted_result["weak_boundary_diagnostics"], [])

        multiple = {
            "positions": [
                {"frame": 30, "time_sec": 1.0, "position_m": [15.0, 30.0], "source": "detected", "confidence": 0.9},
                {"frame": 33, "time_sec": 1.1, "position_m": [15.0, 26.0], "source": "detected", "confidence": 0.9},
                {"frame": 36, "time_sec": 1.2, "position_m": [15.0, 20.0], "source": "detected", "confidence": 0.1},
                {"frame": 37, "time_sec": 1.233, "position_m": [15.0, 18.0], "source": "detected", "confidence": 0.1},
                {"frame": 39, "time_sec": 1.3, "position_m": [15.0, 14.0], "source": "detected", "confidence": 0.9},
                {"frame": 42, "time_sec": 1.4, "position_m": [15.0, 8.0], "source": "detected", "confidence": 0.9},
            ]
        }
        result = candidates([event("multiple", start=0.0, end=1.0)], [], ball_document=multiple, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["weak_boundary_diagnostics"][0]["weak_boundary_state"], "weak_boundary_rejected_isolation")

    def test_v5_rejects_unsafe_gap_speed_and_spatial_alternate_ball(self) -> None:
        cases = {
            "weak_boundary_rejected_gap": [
                (1.0, 15.0, 30.0, .9), (1.1, 15.0, 26.0, .9), (1.2, 15.0, 20.0, .1), (1.7, 15.0, 14.0, .9), (1.8, 15.0, 8.0, .9),
            ],
            "weak_boundary_rejected_speed": [
                (1.0, 15.0, 30.0, .9), (1.1, 15.0, 26.0, .9), (1.2, 15.0, 20.0, .1), (1.3, 15.0, 2.0, .9), (1.4, 15.0, 1.0, .9),
            ],
            "weak_boundary_rejected_spatially": [
                (1.0, 15.0, 30.0, .9), (1.1, 15.0, 26.0, .9), (1.2, 29.0, 40.0, .1), (1.45, 15.0, 14.0, .9), (1.55, 15.0, 8.0, .9),
            ],
        }
        for expected, points in cases.items():
            ball_document = {"positions": [
                {"frame": round(time_sec * 30), "time_sec": time_sec, "position_m": [x, y], "source": "detected", "confidence": confidence}
                for time_sec, x, y, confidence in points
            ]}
            result = candidates([event(expected, start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)
            self.assertEqual(result["candidates"], [])
            self.assertEqual(result["weak_boundary_diagnostics"][0]["weak_boundary_state"], expected)

    def test_v5_rejects_contact_segment_post_support_and_heading_boundaries(self) -> None:
        base = {
            "positions": [
                {"frame": 30, "time_sec": 1.0, "position_m": [15.0, 30.0], "source": "detected", "confidence": 0.9},
                {"frame": 33, "time_sec": 1.1, "position_m": [15.0, 26.0], "source": "detected", "confidence": 0.9},
                {"frame": 36, "time_sec": 1.2, "position_m": [15.0, 23.0], "source": "detected", "confidence": 0.1},
                {"frame": 39, "time_sec": 1.3, "position_m": [15.0, 20.0], "source": "detected", "confidence": 0.9},
                {"frame": 42, "time_sec": 1.4, "position_m": [15.0, 14.0], "source": "detected", "confidence": 0.9},
            ]
        }
        contact_result = candidates(
            [event("launch", start=0.0, end=1.0), event("intervening", start=1.2, end=1.25, team="B", player="B01")],
            [], ball_document=deepcopy(base), policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION,
        )
        self.assertEqual(contact_result["weak_boundary_diagnostics"][0]["weak_boundary_state"], "weak_boundary_rejected_contact_boundary")

        segmented = deepcopy(base)
        for row in segmented["positions"]:
            row["timeline_segment_id"] = "one" if row["time_sec"] <= 1.2 else "two"
        segment_result = candidates([event("segment", start=0.0, end=1.0)], [], ball_document=segmented, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)
        self.assertEqual(segment_result["weak_boundary_diagnostics"][0]["weak_boundary_state"], "weak_boundary_rejected_segment_boundary")

        insufficient = deepcopy(base)
        insufficient["positions"] = insufficient["positions"][:-1]
        support_result = candidates([event("support", start=0.0, end=1.0)], [], ball_document=insufficient, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)
        self.assertEqual(support_result["weak_boundary_diagnostics"][0]["weak_boundary_state"], "weak_boundary_rejected_insufficient_post_support")

        heading = deepcopy(base)
        heading["positions"][-1]["position_m"] = [15.0, 26.0]
        heading_result = candidates([event("heading", start=0.0, end=1.0)], [], ball_document=heading, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)
        self.assertEqual(heading_result["weak_boundary_diagnostics"][0]["weak_boundary_state"], "weak_boundary_rejected_heading")

    def test_v5_is_deterministic_and_never_mutates_the_ball_timeline(self) -> None:
        ball_document = {
            "positions": [
                {"frame": 30, "time_sec": 1.0, "position_m": [15.0, 30.0], "source": "detected", "confidence": 0.9},
                {"frame": 33, "time_sec": 1.1, "position_m": [15.0, 26.0], "source": "detected", "confidence": 0.9},
                {"frame": 36, "time_sec": 1.2, "position_m": [15.0, 23.0], "source": "detected", "confidence": 0.1},
                {"frame": 39, "time_sec": 1.3, "position_m": [15.0, 20.0], "source": "detected", "confidence": 0.9},
                {"frame": 42, "time_sec": 1.4, "position_m": [15.0, 14.0], "source": "detected", "confidence": 0.9},
            ]
        }
        frozen = deepcopy(ball_document)
        first = candidates([event("stable-v5", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)
        second = candidates([event("stable-v5", start=0.0, end=1.0)], [], ball_document=ball_document, policy_version=V5_WEAK_BOUNDARY_POLICY_VERSION)

        self.assertEqual(first, second)
        self.assertEqual(ball_document, frozen)

    def test_v5_deduplication_keeps_an_existing_candidate_identity(self) -> None:
        existing = {
            "candidate_id": "shot-existing",
            "candidate_key": "shot:v1:existing",
            "confidence": .8,
            "source_timestamp_sec": 1.0,
            "trajectory_evidence": {"distance_m": 4.0},
            "reasons": ["continuous_ball_trajectory"],
        }
        weak_bridge = {
            "candidate_id": "shot-weak",
            "candidate_key": "shot:v1:weak",
            "confidence": .7,
            "source_timestamp_sec": 1.1,
            "trajectory_evidence": {"distance_m": 4.0, "continuity_bridge": {"weak_boundary_state": "weak_boundary_reinterpreted"}},
            "reasons": ["continuity_bridge_applied"],
        }

        merged = shot_candidates._merge_cluster(
            [existing, weak_bridge],
            "source-1",
            preserve_primary_candidate_identity=True,
        )

        self.assertEqual(merged["candidate_id"], "shot-existing")
        self.assertEqual(merged["candidate_key"], "shot:v1:existing")
        self.assertEqual(merged["deduplicated_hypothesis_count"], 2)

    def test_strong_short_goalward_prefix_before_explicit_boundary_is_a_candidate(self) -> None:
        document = candidates(
            [event("short-prefix", start=0.0, end=1.0)],
            [(1.0, 15.0, 14.5), (1.1, 15.0, 13.85), (1.2, 15.0, 13.2)],
            unknown_at=1.3,
        )

        self.assertEqual(len(document["candidates"]), 1)
        self.assertIn("strong_trusted_prefix_before_boundary", document["candidates"][0]["reasons"])

    def test_short_fast_forward_pass_before_boundary_is_not_a_candidate(self) -> None:
        document = candidates(
            [event("short-pass", start=0.0, end=1.0)],
            [(1.0, 15.0, 31.0), (1.1, 15.0, 30.3), (1.2, 15.0, 29.6)],
            unknown_at=1.3,
        )

        self.assertEqual(document["candidates"], [])

    def test_short_goalward_prefix_to_a_teammate_remains_suppressed(self) -> None:
        document = candidates(
            [event("short-pass", start=0.0, end=1.0), event("receiver", start=1.2, end=1.25, player="A02")],
            [(1.0, 15.0, 14.5), (1.1, 15.0, 13.85), (1.2, 15.0, 13.2)],
            unknown_at=1.3,
        )

        self.assertEqual(document["candidates"], [])
        self.assertGreater(document["summary"]["skipped_evidence_reasons"]["same_team_receiver_before_goal"], 0)

    def test_v1_reproduction_does_not_apply_the_v2_short_prefix_path(self) -> None:
        document = build_shot_candidates_document(
            {"events": [event("short-prefix", start=0.0, end=1.0)]},
            ball_rows([(1.0, 15.0, 14.5), (1.1, 15.0, 13.85), (1.2, 15.0, 13.2)], unknown_at=1.3),
            PHASE_Y_MIN,
            source_match_id="m1",
            policy_version=PREVIOUS_POLICY_VERSION,
        )

        self.assertEqual(document["policy_version"], PREVIOUS_POLICY_VERSION)
        self.assertEqual(document["candidates"], [])

    def test_trusted_interpolated_ball_rows_remain_continuous_trajectory(self) -> None:
        ball_document = {
            "positions": [
                {"frame": 30, "time_sec": 1.0, "position_m": [15.0, 30.0], "source": "detected", "confidence": 0.9},
                {"frame": 33, "time_sec": 1.1, "position_m": [15.0, 24.0], "source": "interpolated", "confidence": 0.9},
                {"frame": 36, "time_sec": 1.2, "position_m": [15.0, 12.0], "source": "interpolated", "confidence": 0.9},
                {"frame": 39, "time_sec": 1.3, "position_m": [15.0, 4.0], "source": "detected", "confidence": 0.9},
            ]
        }
        document = candidates([event("interpolated", start=0.0, end=1.0)], [], ball_document=ball_document)

        self.assertEqual(len(document["candidates"]), 1)

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

        first_v3 = candidates(*args, policy_version=V3_POLICY_VERSION)
        second_v3 = candidates(*args, policy_version=V3_POLICY_VERSION)
        self.assertEqual(first_v3, second_v3)

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
            self.assertNotIn("shot_goldset_v2", source, path.as_posix())
            self.assertNotIn("shot-goldset:v2", source, path.as_posix())
        detector_source = (runtime_root / "services" / "shot_candidates.py").read_text(encoding="utf-8")
        self.assertNotIn("shot_review_editor", detector_source)


if __name__ == "__main__":
    unittest.main()
