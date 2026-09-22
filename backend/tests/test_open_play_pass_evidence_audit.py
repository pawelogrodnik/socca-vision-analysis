from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from evaluation.open_play_pass_evidence_audit import (
    _completion_bias,
    _distribution,
    _all_candidate_cluster_summary,
    _all_candidate_clusters,
    _false_positive_clusters,
    _missed_gold_pass_root_causes,
    _oracle_diagnostics,
    _team_bias,
    audit_open_play_pass_evidence,
)
from evaluation.open_play_pass_v2 import derive_active_play_mask


def _window() -> dict:
    return {
        "window_id": "W1", "source_match_id": "source", "merged_start_time_sec": 0.0,
        "merged_end_time_sec": 20.0, "source_start_time_sec": 0.0, "source_end_time_sec": 20.0,
        "source_to_merged_offset_sec": 0.0,
    }


def _gold(event_id: str, time: float, *, team: str = "Corgi", outcome: str = "COMPLETED") -> dict:
    return {
        "event_id": event_id, "window_id": "W1", "approx_time_sec": time, "timing_tolerance_sec": 1.0,
        "action": "PASS", "outcome": outcome, "actor": {"team": team}, "target": {"team": team}, "modifiers": [],
    }


def _candidate(candidate_id: str, time: float, *, team: str = "Corgi", outcome: str = "completed_pass") -> dict:
    return {
        "candidate_id": candidate_id, "start_time_sec": time, "end_time_sec": time + 0.5,
        "source_event_id": f"source-{candidate_id}", "target_event_id": f"target-{candidate_id}",
        "outcome": outcome, "from_restart": False, "from_team_name": team, "to_team_name": team,
        "duration_sec": 0.5, "distance_m": 5.0, "confidence": 0.75, "pass_type": "same_team_pass",
        "auto_review_status": "strong_candidate", "review_status": "needs_review", "rejection_reasons": [],
        "release_evidence": {"duration_sec": 0.5, "source_clearance_m": 4.0, "immediate_contested_frames": 0, "controlled_by_source_frames": 1, "controlled_by_target_frames": 1},
        "trajectory_evidence": {"sampled_frames": 4, "ball_path_distance_m": 5.0, "ball_displacement_m": 4.5, "mean_ball_speed_mps": 10.0, "ball_path_straightness": 0.9, "status_counts": {"controlled": 2, "free": 2}},
        "receiver_evidence": {"min_distance_m": 0.2, "confidence": 0.8},
        "local_contact_context": {"nearby_contact_count": 2, "prior_gap_sec": None, "next_gap_sec": None, "rapid_neighbor_gaps": 0},
    }


def _documents(candidates: list[dict], events: list[dict] | None = None) -> dict:
    return {"source": {
        "pass_candidates": {"candidates": candidates}, "event_candidates": {"events": events or []},
        "contact_candidates": {"candidates": []}, "restart_candidates": {"candidates": []},
    }}


class OpenPlayPassEvidenceAuditTests(unittest.TestCase):
    def test_uses_exact_v171_active_play_mask(self) -> None:
        goldset = {"windows": [_window()], "game_state_intervals": [{"window_id": "W1", "start_time_sec": 0, "end_time_sec": 3, "state": "NOT_IN_PLAY"}], "events": [_gold("pass", 3.0)]}
        report = audit_open_play_pass_evidence(goldset, _documents([_candidate("pass", 3.0)]))
        self.assertEqual(report["active_play_mask"], derive_active_play_mask(goldset))
        self.assertEqual(report["current_error_budget"]["gold_open_play_passes"], 1)

    def test_labels_are_evaluation_only_and_preserve_true_subgroups(self) -> None:
        gold = _gold("match", 5.0)
        gold["modifiers"] = ["HEADER", "ONE_TOUCH"]
        report = audit_open_play_pass_evidence({"windows": [_window()], "game_state_intervals": [], "events": [gold]}, _documents([_candidate("match", 5.0)]))
        row = report["evidence_rows"][0]
        self.assertEqual(row["evaluation_label"], "TRUE_PASS_MATCH")
        self.assertEqual(row["matched_gold_event_id"], "match")
        self.assertEqual(row["gold_subgroups"], ["COMPLETED", "HEADER", "ONE_TOUCH"])
        self.assertNotIn("evaluation_label", _candidate("match", 5.0))

    def test_false_positive_labels_are_evaluation_only(self) -> None:
        report = audit_open_play_pass_evidence({"windows": [_window()], "game_state_intervals": [], "events": []}, _documents([_candidate("fp", 5.0)]))
        self.assertEqual(report["evidence_rows"][0]["evaluation_label"], "UNMATCHED_PASS_CANDIDATE")
        self.assertNotIn("evaluation_label", _candidate("fp", 5.0))

    def test_distribution_handles_missing_and_uses_deterministic_interpolation(self) -> None:
        values = _distribution([None, 0.0, 10.0, 20.0, 30.0])
        self.assertEqual(values["missing_count"], 1)
        self.assertEqual(values["p10"], 3.0)
        self.assertEqual(values["p25"], 7.5)
        self.assertEqual(values["median"], 15.0)
        self.assertEqual(values["p75"], 22.5)
        self.assertEqual(values["p90"], 27.0)

    def test_team_and_outcome_confusion_matrices_are_deterministic(self) -> None:
        gold = [_gold("a", 1, team="Corgi"), _gold("b", 2, team="Verisk", outcome="INTERCEPTED")]
        matches = [
            {"expected_actor_team": "Corgi", "actual_actor_team": "Verisk", "expected_broad_outcome": "completed_pass", "actual_outcome": "failed_pass"},
            {"expected_actor_team": "Verisk", "actual_actor_team": "Verisk", "expected_broad_outcome": "failed_pass", "actual_outcome": "failed_pass"},
        ]
        rows = [
            {"evaluation_label": "TRUE_PASS_MATCH", "automatic": {"actor_team": "Verisk", "outcome": "failed_pass"}},
            {"evaluation_label": "UNMATCHED_PASS_CANDIDATE", "automatic": {"actor_team": "Corgi", "outcome": "completed_pass"}},
        ]
        self.assertEqual(_team_bias(gold, matches, rows, {1})["matched_actor_team_confusion_matrix"], {"Corgi -> Verisk": 1, "Verisk -> Verisk": 1})
        self.assertEqual(_completion_bias(gold, [], matches, rows)["matched_outcome_confusion_matrix"], {"completed_pass -> failed_pass": 1, "failed_pass -> failed_pass": 1})

    def test_miss_root_cause_traces_release_rejection(self) -> None:
        gold = _gold("miss", 5.0)
        pair = _candidate("excluded", 5.0, outcome="excluded_non_pass")
        pair["merged_release_time_sec"] = 5.0
        pair["rejection_reasons"] = ["ball_path_too_short"]
        result = _missed_gold_pass_root_causes([gold], {"event": []}, [pair], [])
        self.assertEqual(result[0]["root_cause"], "EXCLUDED_BY_RELEASE_POLICY")
        self.assertEqual(result[0]["rejection_reasons"], {"excluded": ["ball_path_too_short"]})

    def test_nearby_same_player_skip_is_diagnostic_not_causal_without_chain(self) -> None:
        gold = _gold("miss", 5.0)
        result = _missed_gold_pass_root_causes(
            [gold],
            {"event": [{"event_id": "contact", "event_type": "ball_contact", "merged_start_time_sec": 5.0, "review_status": "accepted"}]},
            [],
            [{"source_time_sec": 5.0, "skip_reason": "same_player_consecutive_contacts"}],
        )
        self.assertEqual(result[0]["root_cause"], "UNKNOWN")
        self.assertEqual(result[0]["diagnostic_signals"], ["NEARBY_SAME_PLAYER_SKIP", "SINGLE_NEARBY_CONTACT"])

    def test_structural_same_player_skip_requires_later_distinct_receiver_context(self) -> None:
        gold = _gold("miss", 5.0)
        events = [
            {"event_id": "source", "event_type": "ball_contact", "merged_start_time_sec": 5.0, "stable_player_id": "A01"},
            {"event_id": "same-player", "event_type": "ball_contact", "merged_start_time_sec": 5.2, "stable_player_id": "A01"},
            {"event_id": "receiver", "event_type": "ball_contact", "merged_start_time_sec": 5.3, "stable_player_id": "B02"},
        ]
        result = _missed_gold_pass_root_causes(
            [gold],
            {"event": events},
            [],
            [{
                "source_event_id": "source", "target_event_id": "same-player",
                "source_time_sec": 5.0, "target_time_sec": 5.2,
                "source_stable_player_id": "A01", "target_stable_player_id": "A01",
                "skip_reason": "same_player_consecutive_contacts",
            }],
        )
        self.assertEqual(result[0]["root_cause"], "SAME_PLAYER_SKIP")

    def test_false_positive_clustering_is_deterministic(self) -> None:
        rows = [
            {"candidate_ref": "s:a", "candidate_id": "a", "source_match_id": "s", "merged_release_time_sec": 1.0, "evaluation_label": "UNMATCHED_PASS_CANDIDATE", "window_id": "W1"},
            {"candidate_ref": "s:b", "candidate_id": "b", "source_match_id": "s", "merged_release_time_sec": 1.5, "evaluation_label": "SHOT_AS_PASS", "window_id": "W1"},
            {"candidate_ref": "s:c", "candidate_id": "c", "source_match_id": "s", "merged_release_time_sec": 2.6, "evaluation_label": "UNMATCHED_PASS_CANDIDATE", "window_id": "W1"},
        ]
        clusters = _false_positive_clusters(rows)
        self.assertEqual([row["size"] for row in clusters], [2, 1])
        self.assertEqual(clusters[0]["candidate_ids"], ["a", "b"])

    def test_all_candidate_clusters_expose_true_false_and_mixed_composition(self) -> None:
        rows = [
            {"candidate_ref": "s:t1", "candidate_id": "t1", "source_match_id": "s", "merged_release_time_sec": 1.0, "evaluation_label": "TRUE_PASS_MATCH", "window_id": "W1"},
            {"candidate_ref": "s:t2", "candidate_id": "t2", "source_match_id": "s", "merged_release_time_sec": 1.5, "evaluation_label": "TRUE_PASS_MATCH", "window_id": "W1"},
            {"candidate_ref": "s:f1", "candidate_id": "f1", "source_match_id": "s", "merged_release_time_sec": 4.0, "evaluation_label": "UNMATCHED_PASS_CANDIDATE", "window_id": "W1"},
            {"candidate_ref": "s:f2", "candidate_id": "f2", "source_match_id": "s", "merged_release_time_sec": 4.5, "evaluation_label": "SHOT_AS_PASS", "window_id": "W1"},
            {"candidate_ref": "s:m1", "candidate_id": "m1", "source_match_id": "s", "merged_release_time_sec": 7.0, "evaluation_label": "TRUE_PASS_MATCH", "window_id": "W2"},
            {"candidate_ref": "s:m2", "candidate_id": "m2", "source_match_id": "s", "merged_release_time_sec": 7.5, "evaluation_label": "UNMATCHED_PASS_CANDIDATE", "window_id": "W2"},
        ]
        clusters = _all_candidate_clusters(rows)
        summary = _all_candidate_cluster_summary(clusters)
        self.assertEqual([row["composition"] for row in clusters], ["TRUE_ONLY", "FALSE_ONLY", "MIXED"])
        self.assertEqual(summary["multi_candidate_clusters"], 3)
        self.assertEqual((summary["TRUE_ONLY"], summary["FALSE_ONLY"], summary["MIXED"]), (1, 1, 1))
        self.assertEqual((summary["true_candidates_in_multi_clusters"], summary["false_candidates_in_multi_clusters"]), (3, 3))

    def test_controlled_frames_are_diagnostic_signal_not_false_positive_cause(self) -> None:
        candidate = _candidate("fp", 5.0)
        report = audit_open_play_pass_evidence({"windows": [_window()], "game_state_intervals": [], "events": []}, _documents([candidate]))
        root = report["false_positive_root_causes"][0]
        self.assertEqual(root["root_cause"], "UNEXPLAINED_FALSE_POSITIVE")
        self.assertIn("CONTROLLED_FRAMES_PRESENT", root["diagnostic_signals"])
        self.assertNotEqual(root["root_cause"], "CONTINUED_CONTROL_EVIDENCE")

    def test_oracles_do_not_mutate_source_candidates(self) -> None:
        gold = [_gold("a", 5.0)]
        candidates = [_candidate("a", 5.0)]
        before = copy.deepcopy(candidates)
        matches = [{"candidate_source_match_id": "source", "candidate_id": "a", "expected_broad_outcome": "failed_pass", "expected_actor_team": "Verisk"}]
        _oracle_diagnostics(gold, candidates, matches, [])
        self.assertEqual(candidates, before)

    def test_oracle_outcome_correction_changes_asymmetric_completion_without_mutating_candidates(self) -> None:
        gold = [_gold("a", 5.0, outcome="INTERCEPTED")]
        candidates = [_candidate("a", 5.0, outcome="completed_pass")]
        candidates[0]["source_match_id"] = "source"
        before = copy.deepcopy(candidates)
        matches = [{"candidate_source_match_id": "source", "candidate_id": "a", "expected_broad_outcome": "failed_pass", "expected_actor_team": "Corgi"}]
        result = _oracle_diagnostics(gold, candidates, matches, [])
        self.assertEqual(result["B_PERFECT_OUTCOME_ON_MATCHED_EVENTS"]["aggregate"]["completion_rate"], 0.0)
        self.assertEqual(candidates, before)

    def test_oracle_team_correction_changes_asymmetric_team_share_without_mutating_candidates(self) -> None:
        gold = [_gold("a", 5.0, team="Verisk")]
        candidates = [_candidate("a", 5.0, team="Corgi")]
        candidates[0]["source_match_id"] = "source"
        before = copy.deepcopy(candidates)
        matches = [{"candidate_source_match_id": "source", "candidate_id": "a", "expected_broad_outcome": "completed_pass", "expected_actor_team": "Verisk"}]
        result = _oracle_diagnostics(gold, candidates, matches, [])
        teams = result["C_PERFECT_TEAM_ATTRIBUTION_ON_MATCHED_EVENTS"]["aggregate"]["teams"]
        self.assertEqual(teams["Corgi"]["share"], 0.0)
        self.assertEqual(teams["Verisk"]["share"], 1.0)
        self.assertEqual(candidates, before)

    def test_real_goldset_keeps_v171_primary_count(self) -> None:
        goldset = json.loads((Path(__file__).parent / "fixtures" / "contact_action_goldset_v1.json").read_text(encoding="utf-8"))
        report = audit_open_play_pass_evidence(goldset, _documents([]))
        self.assertEqual(report["current_error_budget"]["gold_open_play_passes"], 48)

    def test_runtime_pass_generation_does_not_import_audit_or_goldset(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "app" / "services" / "pass_candidates.py").read_text(encoding="utf-8")
        self.assertNotIn("open_play_pass_evidence_audit", source)
        self.assertNotIn("contact_action_goldset", source)


if __name__ == "__main__":
    unittest.main()
