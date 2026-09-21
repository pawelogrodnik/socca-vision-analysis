from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.contact_action_baseline import (
    _contact_compatible,
    _is_scored_pass,
    _is_strict_contact_anchor,
    _match_contacts,
    _match_passes,
    assert_source_tree_unchanged,
    evaluate_contact_action_baseline,
    snapshot_source_tree,
    summarize_source_tree_snapshot,
)
from evaluation.contact_action_goldset import validate_contact_action_goldset


def _window() -> dict:
    return {
        "window_id": "W1", "merged_start_time_sec": 100.0, "merged_end_time_sec": 175.0,
        "source_match_id": "source-1", "source_start_time_sec": 0.0, "source_end_time_sec": 75.0,
        "source_to_merged_offset_sec": 100.0,
    }


def _pass(event_id: str, time: float, *, action: str = "PASS", outcome: str = "COMPLETED", modifiers: list[str] | None = None, context_only: bool = False) -> dict:
    return {
        "event_id": event_id, "window_id": "W1", "approx_time_sec": time, "timing_tolerance_sec": 1.0,
        "action": action, "outcome": outcome, "actor": {"team": "Corgi"}, "target": {"team": "Corgi"},
        "modifiers": modifiers or [], "context_only": context_only, "manual_note": event_id,
    }


def _candidate(candidate_id: str, source_time: float, *, outcome: str = "completed_pass", from_team: str = "Corgi", to_team: str = "Corgi", from_restart: bool = False) -> dict:
    return {
        "candidate_id": candidate_id, "start_time_sec": source_time, "end_time_sec": source_time + 0.2,
        "outcome": outcome, "from_team_name": from_team, "to_team_name": to_team,
        "count_for_team_label": from_team, "from_restart": from_restart, "review_status": "needs_review",
    }


def _contact(candidate_id: str, start: float, end: float, *, team: str = "Corgi", review: str = "accepted") -> dict:
    return {"candidate_id": candidate_id, "start_time_sec": start, "end_time_sec": end, "team_name": team, "review_status": review}


class ContactActionBaselineTests(unittest.TestCase):
    def test_projects_source_times_to_merged_timeline(self) -> None:
        report = self._evaluate(events=[_pass("P1", 103.0)], passes=[_candidate("c1", 3.0)])
        row = report["pass_matches"][0]
        self.assertEqual(row["candidate_release_merged_time_sec"], 103.0)
        self.assertEqual(row["candidate_release_source_time_sec"], 3.0)

    def test_candidate_at_tolerance_boundary_matches_and_outside_does_not(self) -> None:
        gold = [_pass("P1", 10.0)]
        inside = [{"merged_release_time_sec": 11.0, "candidate_id": "in", "outcome": "completed_pass"}]
        outside = [{"merged_release_time_sec": 11.01, "candidate_id": "out", "outcome": "completed_pass"}]
        self.assertEqual(_match_passes(gold, inside)[0], [(0, 0)])
        self.assertEqual(_match_passes(gold, outside)[0], [])

    def test_one_candidate_cannot_match_two_gold_events_and_dense_sequence_is_chronological(self) -> None:
        gold = [_pass("P1", 10.0), _pass("P2", 10.5)]
        only = [{"merged_release_time_sec": 10.25, "candidate_id": "c", "outcome": "completed_pass"}]
        self.assertEqual(_match_passes(gold, only)[0], [(0, 0)])
        dense = [
            {"merged_release_time_sec": 10.15, "candidate_id": "c1", "outcome": "completed_pass"},
            {"merged_release_time_sec": 10.55, "candidate_id": "c2", "outcome": "completed_pass"},
        ]
        self.assertEqual(_match_passes(gold, dense)[0], [(0, 0), (1, 1)])

    def test_outcome_and_team_errors_are_scored_after_temporal_match(self) -> None:
        report = self._evaluate(
            events=[_pass("P1", 103.0, outcome="INTERCEPTED")],
            passes=[_candidate("c1", 3.0, outcome="completed_pass", from_team="Verisk", to_team="Verisk")],
        )
        row = report["pass_matches"][0]
        self.assertEqual(row["primary_failure_stage"], "PASS_OUTCOME_ERROR")
        self.assertIn("ACTOR_TEAM_ERROR", row["error_tags"])
        self.assertEqual(report["summary"]["pass"]["matched_pass_attempts"], 1)
        self.assertEqual(report["summary"]["pass"]["unmatched_pass_candidates"], 0)

    def test_temporal_tie_ignores_candidate_team_and_outcome_labels(self) -> None:
        gold = [_pass("P1", 10.0, outcome="COMPLETED")]
        candidates = [
            {"candidate_id": "earlier-wrong", "merged_release_time_sec": 9.0, "outcome": "failed_pass", "from_team_name": "Verisk"},
            {"candidate_id": "later-correct", "merged_release_time_sec": 11.0, "outcome": "completed_pass", "from_team_name": "Corgi"},
        ]
        self.assertEqual(_match_passes(gold, candidates)[0], [(0, 0)])
        changed_outcome = [{**gold[0], "outcome": "INTERCEPTED"}]
        changed_team = [{**gold[0], "actor": {"team": "Verisk"}}]
        self.assertEqual(_match_passes(changed_outcome, candidates)[0], [(0, 0)])
        self.assertEqual(_match_passes(changed_team, candidates)[0], [(0, 0)])

    def test_contact_tie_ignores_gold_team(self) -> None:
        gold = [{**_pass("P1", 10.0), "timing_tolerance_sec": 1.0}]
        candidates = [
            {"candidate_id": "earlier-wrong-team", "merged_start_time_sec": 9.0, "merged_end_time_sec": 9.5, "team_name": "Verisk"},
            {"candidate_id": "later-correct-team", "merged_start_time_sec": 10.5, "merged_end_time_sec": 11.0, "team_name": "Corgi"},
        ]
        self.assertEqual(_match_contacts(gold, candidates)[0], [(0, 0)])
        self.assertEqual(_match_contacts([{**gold[0], "actor": {"team": "Verisk"}}], candidates)[0], [(0, 0)])

    def test_broad_outcome_mapping_unknown_and_ambiguous_exclusions(self) -> None:
        report = self._evaluate(events=[
            _pass("I", 103.0, outcome="INTERCEPTED"), _pass("M", 105.0, outcome="MISCONTROLLED"),
            _pass("O", 107.0, outcome="OUT"), _pass("U", 109.0, outcome="UNKNOWN"),
            _pass("A", 111.0, modifiers=["AMBIGUOUS"]), _pass("C", 113.0, context_only=True),
        ], passes=[
            _candidate("i", 3.0, outcome="failed_pass"), _candidate("m", 5.0, outcome="failed_pass"),
            _candidate("o", 7.0, outcome="failed_pass"), _candidate("u", 9.0, outcome="completed_pass"),
        ])
        self.assertEqual(report["summary"]["pass"]["gold_pass_attempts"], 4)
        self.assertEqual(report["summary"]["pass"]["outcome_scorable_matches"], 3)
        self.assertFalse(_is_scored_pass(_pass("A", 0, modifiers=["AMBIGUOUS"])))
        self.assertFalse(_is_scored_pass(_pass("C", 0, context_only=True)))

    def test_restart_is_scored_separately(self) -> None:
        report = self._evaluate(events=[_pass("R", 103.0, action="RESTART")], passes=[_candidate("r", 3.0, from_restart=True)])
        self.assertEqual(report["summary"]["pass"]["restart_gold_passes"], 1)
        self.assertEqual(report["summary"]["pass"]["restart_recall"], 1.0)

    def test_aggregate_fidelity_keeps_counts_shares_and_completion_bias_separate(self) -> None:
        report = self._evaluate(
            events=[
                _pass("C", 103.0, outcome="COMPLETED"),
                {**_pass("V", 105.0, outcome="INTERCEPTED"), "actor": {"team": "Verisk"}, "target": {"team": "Verisk"}},
                _pass("R", 107.0, action="RESTART", outcome="UNKNOWN"),
            ],
            passes=[
                _candidate("c", 3.0, outcome="completed_pass", from_team="Corgi"),
                _candidate("v", 5.0, outcome="completed_pass", from_team="Verisk"),
                _candidate("unknown", 7.0, outcome="failed_pass", from_team="", to_team=""),
            ],
        )
        aggregate = report["summary"]["aggregate_fidelity"]
        self.assertEqual(aggregate["gold"]["combined"]["teams"]["Corgi"]["attempts"], 2)
        self.assertEqual(aggregate["gold"]["combined"]["teams"]["Verisk"]["attempts"], 1)
        self.assertEqual(aggregate["automatic"]["combined"]["teams"]["unknown"]["attempts"], 1)
        self.assertEqual(aggregate["error"]["combined"]["teams"]["Verisk"]["pass_count_delta"], 0)
        self.assertEqual(aggregate["gold"]["combined"]["completion_rate"], 0.5)

    def test_contact_interval_overlap_and_attempted_contact_hard_negative(self) -> None:
        gold = _pass("P", 10.0)
        self.assertTrue(_contact_compatible(gold, {"merged_start_time_sec": 9.5, "merged_end_time_sec": 10.5}))
        self.assertEqual(_match_contacts([gold], [{"merged_start_time_sec": 9.5, "merged_end_time_sec": 10.5, "candidate_id": "c"}])[0], [(0, 0)])
        negative = {**_pass("N", 104.0), "action": "OTHER", "contact_confirmed": False, "modifiers": ["ATTEMPTED_CONTACT"]}
        report = self._evaluate(events=[negative], contacts=[])
        self.assertEqual(report["hard_negative_results"][0]["result"], "correct_negative")

    def test_shot_confusion_dead_ball_and_identity_context_are_diagnostic_not_matches(self) -> None:
        shot = {"event_id": "S", "window_id": "W1", "event_type": "SHOT_REFERENCE", "approx_time_sec": 103.0, "canonical_time_sec": 103.0, "shot_id": "shot-1", "outcome": "blocked", "manual_note": "shot"}
        event = _pass("P", 104.0)
        report = self._evaluate(
            events=[shot, event], passes=[_candidate("pass", 3.0), _candidate("dead", 10.0)],
            contacts=[_contact("contact", 3.0, 3.2)],
            intervals=[{"window_id": "W1", "start_time_sec": 109.0, "end_time_sec": 111.0, "state": "NOT_IN_PLAY"}],
            identity_notes=[{"window_id": "W1", "approx_time_sec": 104.0}],
        )
        self.assertEqual(report["shot_pass_confusions"][0]["nearby_pass_candidates"][0]["candidate_id"], "pass")
        self.assertEqual(report["summary"]["dead_ball_leakage"]["spurious_pass_candidates"], 1)
        self.assertIn("IDENTITY_CONTEXT", report["pass_matches"][0]["error_tags"])
        self.assertTrue(report["pass_matches"][0]["identity_context"])

    def test_auto_baseline_never_uses_manual_status_and_is_deterministic(self) -> None:
        report = self._evaluate(events=[_pass("P", 103.0)], passes=[_candidate("c", 3.0)], contacts=[_contact("x", 3.0, 3.1, review="rejected")])
        self.assertFalse(report["evaluation_layer"]["manual_review_status_used_for_headline_metrics"])
        again = self._evaluate(events=[_pass("P", 103.0)], passes=[_candidate("c", 3.0)], contacts=[_contact("x", 3.0, 3.1, review="rejected")])
        self.assertEqual(json.dumps(report, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_source_tree_snapshot_detects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "artifact.json"
            path.write_text("{}", encoding="utf-8")
            before = snapshot_source_tree({"source": root})
            assert_source_tree_unchanged(before, {"source": root})
            summary = summarize_source_tree_snapshot(before)
            self.assertEqual(summary[0]["file_count"], 1)
            self.assertEqual(len(summary[0]["tree_fingerprint"]), 64)
            path.write_text('{"changed":true}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                assert_source_tree_unchanged(before, {"source": root})

    def test_goldset_v1_remains_valid(self) -> None:
        root = Path(__file__).resolve().parent
        goldset = json.loads((root / "fixtures" / "contact_action_goldset_v1.json").read_text(encoding="utf-8"))
        shots = json.loads((root / "fixtures" / "shot_goldset_v3.json").read_text(encoding="utf-8"))["shots"]
        self.assertEqual(validate_contact_action_goldset(goldset, shots), [])

    def _evaluate(self, *, events: list[dict], passes: list[dict] | None = None, contacts: list[dict] | None = None, intervals: list[dict] | None = None, identity_notes: list[dict] | None = None) -> dict:
        goldset = {"schema_version": "contact-action-goldset:v1", "published_match_id": "published", "windows": [_window()], "events": events, "game_state_intervals": intervals or [], "identity_notes": identity_notes or []}
        docs = {"source-1": {
            "pass_candidates": {"candidates": passes or []}, "contact_candidates": {"candidates": contacts or []},
            "event_candidates": {"events": []}, "restart_candidates": {"candidates": []},
        }}
        return evaluate_contact_action_baseline(goldset, docs)
