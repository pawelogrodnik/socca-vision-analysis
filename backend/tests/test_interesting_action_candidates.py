from __future__ import annotations

import unittest

from app.services.interesting_action_benchmark import benchmark_candidates
from app.services.interesting_action_candidates import build_interesting_action_candidates, build_logical_candidate_signals


class InterestingActionCandidateTests(unittest.TestCase):
    def test_rebases_direction_aware_progression_for_both_teams_deterministically(self) -> None:
        sources = [
            _source(offset=100.0, team="A", opponent="B", forward=11.0),
            _source(offset=200.0, team="B", opponent="A", forward=12.0),
        ]
        first = build_logical_candidate_signals(sources)
        second = build_logical_candidate_signals(sources)
        self.assertEqual(first, second)
        candidates = build_interesting_action_candidates(first, timeline_span_sec=300.0)
        self.assertEqual({row["team_id"] for row in candidates}, {"team-A", "team-B"})
        self.assertEqual({row["start_time_sec"] for row in candidates}, {103.0, 203.0})
        self.assertTrue(all(row["end_time_sec"] - row["start_time_sec"] != 5.0 for row in candidates))

    def test_clusters_duplicate_seeds_into_one_variable_window(self) -> None:
        logical = {"signals": [
            _signal("regain", 10, 10, "team-A"),
            _signal("pass", 11, 12, "team-A", forward_progress_m=8, progressive=True),
            _signal("pass", 13, 14, "team-A", forward_progress_m=9, progressive=True),
        ], "evidence_gaps": []}
        candidates = build_interesting_action_candidates(logical, timeline_span_sec=60.0)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["start_time_sec"], 10.0)
        self.assertEqual(candidates[0]["end_time_sec"], 17.0)

    def test_unknown_gap_and_low_confidence_do_not_bridge_into_candidate(self) -> None:
        gap = {"signals": [
            _signal("regain", 10, 10, "team-A"),
            _signal("pass", 12, 13, "team-A", forward_progress_m=10, progressive=True),
        ], "evidence_gaps": [(10.5, 11.5)]}
        gap_candidates = build_interesting_action_candidates(gap, timeline_span_sec=60.0)
        self.assertEqual(gap_candidates, [])
        low_confidence = {"signals": [
            _signal("regain", 10, 10, "team-A", confidence=0.1),
            _signal("pass", 11, 12, "team-A", confidence=0.1, forward_progress_m=10, progressive=True),
        ], "evidence_gaps": []}
        self.assertEqual(build_interesting_action_candidates(low_confidence, timeline_span_sec=60.0), [])

    def test_benchmark_preserves_signed_start_and_unlabeled_semantics(self) -> None:
        benchmark = benchmark_candidates(
            [
                {"candidate_id": "early", "start_time_sec": 8, "peak_time_sec": 16, "end_time_sec": 20, "interestingness_score": 0.8, "confidence": 0.7, "evidence": []},
                {"candidate_id": "outside", "start_time_sec": 40, "peak_time_sec": None, "end_time_sec": 46, "interestingness_score": 0.7, "confidence": 0.6, "evidence": []},
            ],
            [{"window_id": "manual", "start_time_sec": 10, "end_time_sec": 18}],
        )
        matched = benchmark["manual_matches"][0]
        self.assertTrue(matched["matched"])
        self.assertEqual(matched["signed_start_error_sec"], -2.0)
        self.assertEqual(matched["manual_action_coverage"], 1.0)
        self.assertTrue(matched["peak_inside_manual_window"])
        self.assertEqual(benchmark["unlabeled_candidates"][0]["classification"], "unlabeled_candidate")


def _source(*, offset: float, team: str, opponent: str, forward: float) -> dict:
    return {
        "source_match_id": f"source-{team}",
        "logical_offset_sec": offset,
        "possession_segments": [
            {"status": "controlled", "team_label": opponent, "team_id": f"team-{opponent}", "start_time_sec": 0, "end_time_sec": 2, "mean_confidence": 0.8},
            {"status": "controlled", "team_label": team, "team_id": f"team-{team}", "start_time_sec": 3, "end_time_sec": 6, "mean_confidence": 0.8},
        ],
        "pass_candidates": [{"pass_type": "same_team_pass", "from_team_id": f"team-{team}", "start_time_sec": 4, "end_time_sec": 7, "forward_progress_m": forward, "is_progressive": True, "confidence": 0.8}],
        "restart_candidates": [],
        "momentum_points": [],
    }


def _signal(kind: str, start: float, end: float, team: str, confidence: float = 0.8, **extra: object) -> dict:
    return {"kind": kind, "start_time_sec": start, "end_time_sec": end, "team_id": team, "confidence": confidence, **extra}
