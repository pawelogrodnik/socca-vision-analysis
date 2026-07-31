from __future__ import annotations

import unittest

from app.services.identity_match_resolver import build_identity_resolver_shadow


class MatchIdentityResolverTests(unittest.TestCase):
    def test_operator_anchor_retained_and_reid_disabled_when_gate_fails(self) -> None:
        result = build_identity_resolver_shadow(
            tracklets_doc={"tracklets": [_tracklet("t1", 0, 10), _tracklet("t2", 20, 30)]},
            subjects_doc={"subjects": [{"candidate_subject_id": "s1", "team_label": "A", "tracklet_ids": ["t1", "t2"]}]},
            seeds_doc={"decisions": [_anchor("t1", "p1")]},
            match_doc=_match(), reid_gate_passed=False,
        )
        self.assertEqual(result["reid_evidence"]["weight"], 0.0)
        first = next(row for row in result["variants"]["A"]["assignments"] if row["tracklet_id"] == "t1")
        self.assertEqual(first["status"], "confirmed")
        self.assertEqual(first["proposed_player_id"], "p1")

    def test_overlapping_lineage_tracklets_cannot_share_a_player(self) -> None:
        result = build_identity_resolver_shadow(
            tracklets_doc={"tracklets": [_tracklet("t1", 0, 10), _tracklet("t2", 5, 15)]},
            subjects_doc={"subjects": [{"candidate_subject_id": "s1", "team_label": "A", "tracklet_ids": ["t1", "t2"]}]},
            seeds_doc={"decisions": [_anchor("t1", "p1")]}, match_doc=_match(), reid_gate_passed=False,
        )
        second = next(row for row in result["variants"]["B"]["assignments"] if row["tracklet_id"] == "t2")
        self.assertEqual(second["status"], "conflicted")
        self.assertIsNone(second["proposed_player_id"])


def _tracklet(tracklet_id: str, start: int, end: int) -> dict:
    return {"tracklet_id": tracklet_id, "team_label": "A", "start_time_sec": start / 30, "end_time_sec": end / 30, "source_tracker_id": 1}


def _anchor(tracklet_id: str, player_id: str) -> dict:
    return {"action": "assign_roster_player", "provenance": {"tracklet_id": tracklet_id}, "assigned_player": {"player_id": player_id}}


def _match() -> dict:
    return {"teams": [{"name": "Corgi", "players": [{"id": "p1"}, {"id": "p2"}]}]}


if __name__ == "__main__":
    unittest.main()
