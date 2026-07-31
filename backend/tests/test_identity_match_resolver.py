from __future__ import annotations

import unittest

from app.services.identity_match_resolver import build_identity_resolver_shadow, evaluate_stability_metrics


class MatchIdentityResolverTests(unittest.TestCase):
    def test_generic_two_team_roster_and_non_30_fps(self) -> None:
        result = _resolve([_tracklet("t1", "A", 2, 3, "alpha"), _tracklet("t2", "B", 4, 5, "bravo")], [], [], fps=25)
        self.assertEqual(result["fps"]["fps_value"], 25)
        self.assertEqual(result["roster"]["players"]["a1"]["team_label"], "A")
        self.assertEqual(result["roster"]["players"]["b1"]["team_label"], "B")
        first = result["variants"]["A"]["assignments"][0]
        self.assertEqual(first["frame_start"], 50)

    def test_cross_team_anchor_is_hard_blocked(self) -> None:
        result = _resolve([_tracklet("t1", "B", 0, 1, "bravo")], [], [_anchor("t1", "a1")])
        row = result["variants"]["A"]["assignments"][0]
        self.assertEqual(row["status"], "blocked")
        self.assertIn("cross_team_candidate_hard_rejected", row["hard_blockers"])

    def test_overlapping_operator_anchors_are_retained_but_conflicted(self) -> None:
        result = _resolve([_tracklet("t1", "A", 0, 3, "alpha"), _tracklet("t2", "A", 2, 4, "alpha")], [], [_anchor("t1", "a1"), _anchor("t2", "a1")])
        rows = result["variants"]["A"]["assignments"]
        self.assertEqual({row["status"] for row in rows}, {"confirmed_but_conflicted"})
        self.assertEqual(len(result["operator_anchor_conflicts"]), 1)

    def test_reid_gate_failure_makes_c_equal_b(self) -> None:
        evidence = {"tracklets": [{"tracklet_id": "t1", "team_label": "A", "top1_player_id": "a2", "top1_distance": .01, "margin": .4, "eligible": True}]}
        result = _resolve([_tracklet("t1", "A", 0, 1, "alpha")], [], [], evidence=evidence, gate=False)
        self.assertEqual(result["variants"]["B"]["assignments"], result["variants"]["C"]["assignments"])

    def test_reid_top1_can_affect_c_but_never_overwrite_anchor(self) -> None:
        evidence = {"tracklets": [{"tracklet_id": "t1", "team_label": "A", "top1_player_id": "a2", "top1_distance": .01, "margin": .4, "eligible": True}]}
        result = _resolve([_tracklet("t1", "A", 0, 1, "alpha")], [], [], evidence=evidence, gate=True)
        self.assertIsNone(result["variants"]["B"]["assignments"][0]["proposed_player_id"])
        self.assertEqual(result["variants"]["C"]["assignments"][0]["proposed_player_id"], "a2")
        anchored = _resolve([_tracklet("t1", "A", 0, 1, "alpha")], [], [_anchor("t1", "a1")], evidence=evidence, gate=True)
        self.assertEqual(anchored["variants"]["C"]["assignments"][0]["proposed_player_id"], "a1")

    def test_low_margin_evidence_abstains(self) -> None:
        evidence = {"tracklets": [{"tracklet_id": "t1", "team_label": "A", "top1_player_id": "a2", "top1_distance": .01, "margin": .001, "eligible": False}]}
        result = _resolve([_tracklet("t1", "A", 0, 1, "alpha")], [], [], evidence=evidence, gate=True)
        self.assertEqual(result["variants"]["C"]["assignments"][0]["status"], "unresolved")

    def test_input_order_does_not_change_assignments(self) -> None:
        tracks = [_tracklet("t2", "A", 4, 5, "alpha"), _tracklet("t1", "A", 0, 1, "alpha")]
        subjects = [{"candidate_subject_id": "s1", "team_label": "A", "tracklet_ids": ["t1", "t2"]}]
        seeds = [_anchor("t1", "a1")]
        left = _resolve(tracks, subjects, seeds)["variants"]["B"]["assignments"]
        right = _resolve(list(reversed(tracks)), subjects, seeds)["variants"]["B"]["assignments"]
        self.assertEqual(left, right)

    def test_metrics_compute_merge_split_and_switches(self) -> None:
        roster = {"a1": {"team_label": "A"}, "a2": {"team_label": "A"}}
        rows = [
            {"tracklet_id": "x1", "ground_truth_player_id": "a1", "proposed_player_id": "a1", "status": "probable", "team_label": "A", "frame_start": 0, "start_time_sec": 0, "hard_blockers": [], "conflicts": []},
            {"tracklet_id": "x2", "ground_truth_player_id": "a1", "proposed_player_id": "a2", "status": "probable", "team_label": "A", "frame_start": 2, "start_time_sec": 2, "hard_blockers": [], "conflicts": []},
            {"tracklet_id": "x3", "ground_truth_player_id": "a2", "proposed_player_id": "a1", "status": "probable", "team_label": "A", "frame_start": 3, "start_time_sec": 3, "hard_blockers": [], "conflicts": []},
        ]
        metrics = evaluate_stability_metrics(rows, roster)
        self.assertEqual(metrics["false_merges"], 1)
        self.assertEqual(metrics["false_splits"], 1)
        self.assertEqual(metrics["id_switches"], 1)


def _resolve(tracklets, subjects, decisions, *, evidence=None, gate=False, fps=None):
    return build_identity_resolver_shadow(tracklets_doc={"tracklets": tracklets}, subjects_doc={"subjects": subjects}, seeds_doc={"decisions": decisions}, match_doc=_match(), reid_evidence_doc=evidence, reid_gate_passed=gate, fps_value=fps, fps_source="synthetic")


def _tracklet(tracklet_id: str, team: str, start: float, end: float, team_id: str) -> dict:
    return {"tracklet_id": tracklet_id, "team_label": team, "team_id": team_id, "start_time_sec": start, "end_time_sec": end, "source_tracker_id": tracklet_id, "first_pitch_m": [start, 0], "last_pitch_m": [end, 0], "first_bbox_xyxy": [0, 0, 10, 20], "last_bbox_xyxy": [0, 0, 10, 20]}


def _anchor(tracklet_id: str, player_id: str) -> dict:
    return {"action": "assign_roster_player", "provenance": {"tracklet_id": tracklet_id}, "assigned_player": {"player_id": player_id}}


def _match() -> list[dict]:
    return [{"id": "alpha", "name": "North", "players": [{"id": "a1"}, {"id": "a2"}]}, {"id": "bravo", "name": "South", "players": [{"id": "b1"}, {"id": "b2"}]}]


if __name__ == "__main__":
    unittest.main()
