from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from evaluation.pass_upstream_root_cause_audit import (
    _deterministic_fp_sample,
    _evaluate_shared_fix_candidates,
    _fp_structural_chain,
    _fp_root_cause,
    _fp_sample_coverage,
    _all_candidate_cluster_index,
    _cluster_membership,
    _contact_generation_prerequisites,
    _miss_root_cause,
    _go_no_go_decision,
    _root_cause_counts,
    _structural_pairing_chain,
    _team_error_root_cause,
    build_upstream_trace,
)


def _contact(candidate_id: str, subject: str, player: str, team: str, start: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_key": f"contact:v1:{candidate_id}",
        "start_frame": int(start * 30),
        "end_frame": int(start * 30) + 2,
        "start_time_sec": start,
        "end_time_sec": start + 0.1,
        "stable_player_id": player,
        "stable_subject_id": subject,
        "team_label": team,
        "team_name": "Corgi" if team == "A" else "Verisk",
        "frames": 3,
        "detected_ball_frames": 3,
        "detected_player_frames": 3,
        "mean_distance_m": 0.8,
        "min_distance_m": 0.4,
        "player_source_counts": {"detected": 3},
        "start_ball_position_m": [10.0, 20.0],
        "end_ball_position_m": [10.3, 20.1],
        "start_player_position_m": [10.0, 20.2],
        "end_player_position_m": [10.2, 20.2],
        "review_status": "accepted",
        "auto_review": {"review_status": "accepted"},
    }


def _event(event_id: str, contact: dict) -> dict:
    return {
        "event_id": event_id,
        "source_candidate_id": contact["candidate_id"],
        "source_candidate_key": contact["candidate_key"],
        "start_time_sec": contact["start_time_sec"],
        "end_time_sec": contact["end_time_sec"],
        "stable_player_id": contact["stable_player_id"],
        "stable_subject_id": contact["stable_subject_id"],
        "team_label": contact["team_label"],
        "team_name": contact["team_name"],
        "review_status": "accepted",
        "confidence": 0.8,
    }


def _indexes(*, operator_team: str | None = None) -> dict:
    contact_1 = _contact("contact-1", "slot-a", "A01", "A", 10.0)
    contact_2 = _contact("contact-2", "slot-b", "B01", "B", 10.2)
    slot = {
        "slot_id": "A01", "stable_subject_id": "slot-a", "stable_player_id": "A01",
        "team_label": "A", "team_id": "team-a", "team_name": "Corgi",
        "tracklet_count": 2, "tracklet_id_samples": ["12:1"], "raw_track_count": 1,
        "raw_track_id_samples": [12], "operator_identity_decision": None,
    }
    if operator_team:
        slot["operator_identity_decision"] = {
            "team_label": "B" if operator_team == "Verisk" else "A",
            "team_name": operator_team,
            "source": "operator_review",
        }
    return {"source": {
        "events": {"event-1": _event("event-1", contact_1), "event-2": _event("event-2", contact_2)},
        "contacts": {"contact-1": contact_1, "contact-2": contact_2},
        "passes": {"pass-1": {
            "candidate_id": "pass-1", "start_time_sec": 10.1, "end_time_sec": 10.2,
            "source_event_id": "event-1", "target_event_id": "event-2", "pass_type": "turnover_or_interception",
        }},
        "slots": {"slot-a": slot},
        "effective_ball_positions": [{
            "frame": 300, "time_sec": 10.0, "source": "detected", "candidate_id": "ball-300-c00",
            "confidence": 0.8, "position_m": [10.0, 20.0], "position_px": [100.0, 200.0],
        }],
        "effective_ball_metadata": {"artifact": "resolved_ball_tracks.json", "provenance": "resolved_operator_projection"},
        "possession": [{
            "frame": 300, "time_sec": 10.0, "status": "controlled", "stable_subject_id": "slot-a",
            "stable_player_id": "A01", "team_label": "A", "nearest_distance_m": 1.0,
            "nearest_players": [
                {"stable_subject_id": "slot-a", "stable_player_id": "A01", "team_label": "A", "distance_m": 1.0},
                {"stable_subject_id": "slot-b", "stable_player_id": "B01", "team_label": "B", "team_name": "Verisk", "distance_m": 0.6},
            ],
        }],
    }}


class PassUpstreamRootCauseAuditTests(unittest.TestCase):
    def test_trace_separates_contact_and_event_and_resolves_source_link(self) -> None:
        indexes = _indexes()
        before = copy.deepcopy(indexes)
        trace = build_upstream_trace("source", 10.0, indexes, "event-1", expected_team="Verisk")
        self.assertEqual(trace["selected_player"]["stable_subject_id"], "slot-a")
        self.assertEqual(trace["contact_candidate"]["candidate_id"], "contact-1")
        self.assertEqual(trace["event_candidate"]["event_id"], "event-1")
        self.assertEqual(trace["event_candidate"]["source_candidate_id"], "contact-1")
        self.assertTrue(trace["contact_event_divergence"]["link_resolved"])
        self.assertIn("min_distance_m", trace["contact_candidate"]["evidence_summary"])
        self.assertEqual(trace["pass_candidates"][0]["candidate_id"], "pass-1")
        self.assertEqual(trace["effective_ball"]["artifact"], "resolved_ball_tracks.json")
        self.assertEqual(indexes, before)

    def test_event_inheritance_is_not_independent_contact_evidence(self) -> None:
        indexes = _indexes()
        indexes["source"]["events"]["event-1"]["team_label"] = "B"
        indexes["source"]["events"]["event-1"]["team_name"] = "Verisk"
        trace = build_upstream_trace("source", 10.0, indexes, "event-1", expected_team="Corgi")
        self.assertTrue(trace["contact_event_divergence"]["has_divergence"])
        trace["possession"]["closer_expected_team_players"] = []
        self.assertEqual(_team_error_root_cause("Corgi", trace), "UNKNOWN")

    def test_contact_player_error_requires_local_production_alternative(self) -> None:
        trace = build_upstream_trace("source", 10.0, _indexes(), "event-1", expected_team="Verisk")
        self.assertEqual(_team_error_root_cause("Verisk", trace), "WRONG_POSSESSION_OWNER")
        trace["possession"]["closer_expected_team_players"] = []
        self.assertEqual(_team_error_root_cause("Verisk", trace), "UNKNOWN")

    def test_explicit_operator_team_decision_is_authoritative_in_trace(self) -> None:
        trace = build_upstream_trace("source", 10.0, _indexes(operator_team="Verisk"), "event-1", expected_team="Verisk")
        self.assertEqual(_team_error_root_cause("Verisk", trace), "OPERATOR_DECISION_PROPAGATION_BUG")

    def test_nearby_same_player_skip_is_diagnostic_not_causal(self) -> None:
        indexes = _indexes()
        duplicate = _contact("contact-dup", "slot-a", "A01", "A", 10.15)
        indexes["source"]["contacts"]["contact-dup"] = duplicate
        indexes["source"]["events"] = {"event-1": indexes["source"]["events"]["event-1"], "event-dup": _event("event-dup", duplicate)}
        trace = build_upstream_trace("source", 10.0, indexes, "event-1")
        row = {"root_cause": "UNKNOWN", "actor": {"team": "Corgi"}, "target": {"team": "Verisk"}, "skipped_contact_pairs": [{"source_event_id": "event-1", "target_event_id": "event-dup", "skip_reason": "same_player_consecutive_contacts"}]}
        chain = _structural_pairing_chain(row, "source", indexes, 10.0)
        self.assertFalse(chain["demonstrated"])
        self.assertIn("NEARBY_SAME_PLAYER_CONSECUTIVE_CONTACTS", chain["diagnostic_signals"])
        self.assertEqual(_miss_root_cause(row, trace, chain), "UNKNOWN")

    def test_structural_source_duplicate_receiver_chain_is_pair_construction(self) -> None:
        indexes = _indexes()
        duplicate = _contact("contact-dup", "slot-a", "A01", "A", 10.15)
        receiver = _contact("contact-receiver", "slot-c", "B02", "B", 10.25)
        indexes["source"]["contacts"].update({"contact-dup": duplicate, "contact-receiver": receiver})
        indexes["source"]["events"] = {
            "event-1": indexes["source"]["events"]["event-1"],
            "event-dup": _event("event-dup", duplicate),
            "event-receiver": _event("event-receiver", receiver),
        }
        row = {"root_cause": "UNKNOWN", "actor": {"team": "Corgi"}, "target": {"team": "Verisk"}, "skipped_contact_pairs": [{"source_event_id": "event-1", "target_event_id": "event-dup", "skip_reason": "same_player_consecutive_contacts"}]}
        chain = _structural_pairing_chain(row, "source", indexes, 10.0)
        self.assertTrue(chain["demonstrated"])
        self.assertEqual(chain["chains"][0]["later_distinct_event"]["event_id"], "event-receiver")
        self.assertEqual(_miss_root_cause(row, build_upstream_trace("source", 10.0, indexes, "event-1"), chain), "PASS_PAIR_CONSTRUCTION")

    def test_structural_pairing_must_match_gold_action_plausibility(self) -> None:
        indexes = _indexes()
        duplicate = _contact("contact-dup", "slot-a", "A01", "A", 10.15)
        receiver = _contact("contact-receiver", "slot-c", "B02", "B", 10.25)
        indexes["source"]["events"] = {
            "event-1": indexes["source"]["events"]["event-1"],
            "event-dup": _event("event-dup", duplicate),
            "event-receiver": _event("event-receiver", receiver),
        }
        row = {"root_cause": "UNKNOWN", "actor": {"team": "Verisk"}, "target": {"team": "Verisk"}, "skipped_contact_pairs": [{"source_event_id": "event-1", "target_event_id": "event-dup", "skip_reason": "same_player_consecutive_contacts"}]}
        chain = _structural_pairing_chain(row, "source", indexes, 10.0)
        self.assertFalse(chain["demonstrated"])
        self.assertFalse(chain["chains"][0]["gold_action_plausibility"]["demonstrated"])

    def test_miss_stages_use_effective_ball_evidence_without_claiming_ball_causality(self) -> None:
        ball_trace = {"effective_ball": {"nearby_positions": [{"frame": 1}]}}
        no_ball_trace = {"effective_ball": {"nearby_positions": []}}
        prerequisites = {
            "demonstrates_contact_generation_gap": True,
            "earliest_missing_stage": "CONTACT_GENERATION",
        }
        self.assertEqual(_miss_root_cause({"root_cause": "NO_SOURCE_CONTACT"}, ball_trace, contact_prerequisites=prerequisites), "CONTACT_GENERATION")
        self.assertEqual(_miss_root_cause({"root_cause": "NO_SOURCE_CONTACT"}, no_ball_trace), "UNKNOWN")
        self.assertEqual(_miss_root_cause({"root_cause": "EXCLUDED_BY_RELEASE_POLICY"}, ball_trace), "RELEASE_POLICY")

    def test_semantic_shot_context_is_not_a_root_without_runtime_chain(self) -> None:
        self.assertEqual(_fp_root_cause("SHOT_AS_PASS", {"demonstrated": False}), "UNKNOWN")
        chain = _fp_structural_chain("SHOT_AS_PASS", {"runtime_chain": {"kind": "shot_rebound", "source_contact": "event-1", "target_contact": "event-2"}})
        self.assertTrue(chain["demonstrated"])
        self.assertEqual(_fp_root_cause("SHOT_AS_PASS", chain), "SHOT_REBOUND_CHAIN")

    def test_false_positive_sample_coverage_is_deterministic_and_reports_dimensions(self) -> None:
        rows = [{"candidate_ref": f"source:{window}", "candidate_id": window, "window_id": window,
                 "merged_release_time_sec": index, "evaluation_label": "UNMATCHED_PASS_CANDIDATE"}
                for index, window in enumerate(("W1", "W2", "W3", "W4", "W5", "W6"), start=1)]
        first = _deterministic_fp_sample(rows)
        self.assertEqual(first, _deterministic_fp_sample(rows))
        self.assertEqual({row["window_id"] for row in first}, {"W1", "W2", "W3", "W4", "W5", "W6"})
        sample = [
            {"candidate_ref": "source:isolated", "window_id": "W1", "semantic_context": "SHOT_AS_PASS", "evidence": {"duration_sec": 0.3, "confidence": 0.8, "pass_type": "same_team_pass", "cluster": {"membership": "isolated_or_singleton"}}},
            {"candidate_ref": "source:multi", "window_id": "W2", "semantic_context": "INTERVENTION_AS_PASS", "evidence": {"duration_sec": 1.1, "confidence": 0.6, "pass_type": "turnover_or_interception", "cluster": {"membership": "multi_candidate_cluster"}}},
        ]
        clusters = {"source:isolated": {"cluster_size": 1}, "source:multi": {"cluster_size": 2}}
        coverage = _fp_sample_coverage(sample, sample, clusters)
        self.assertEqual(coverage["team_relationship"], {"same_team": 1, "turnover": 1})
        self.assertEqual(coverage["cluster_membership"]["multi_candidate_cluster"], 1)

    def test_singleton_cluster_id_is_not_multi_candidate_and_sample_adds_both_shapes(self) -> None:
        evidence = {"all_candidate_clusters": [
            {"cluster_id": "cluster-one", "size": 1, "composition": "FALSE_ONLY", "candidate_refs": ["source:one"]},
            {"cluster_id": "cluster-two", "size": 2, "composition": "FALSE_ONLY", "candidate_refs": ["source:two", "source:three"]},
        ]}
        clusters = _all_candidate_cluster_index(evidence)
        self.assertEqual(_cluster_membership("source:one", clusters)["membership"], "isolated_or_singleton")
        self.assertEqual(_cluster_membership("source:two", clusters)["membership"], "multi_candidate_cluster")
        rows = [
            {"candidate_ref": "source:one", "candidate_id": "one", "window_id": "W1", "merged_release_time_sec": 1, "evaluation_label": "SHOT_AS_PASS"},
            {"candidate_ref": "source:two", "candidate_id": "two", "window_id": "W2", "merged_release_time_sec": 2, "evaluation_label": "SHOT_AS_PASS"},
        ]
        selected = _deterministic_fp_sample(rows, clusters)
        self.assertEqual(
            {_cluster_membership(row["candidate_ref"], clusters)["membership"] for row in selected},
            {"isolated_or_singleton", "multi_candidate_cluster"},
        )

    def test_real_fp_cluster_population_reports_singletons_and_multi_candidates(self) -> None:
        artifact = Path(__file__).resolve().parents[1] / "benchmarks" / "contact-action-goldset-v1" / "open_play_pass_evidence_audit_v1.json"
        report = json.loads(artifact.read_text(encoding="utf-8"))
        clusters = _all_candidate_cluster_index(report)
        false_rows = [row for row in report["evidence_rows"] if row["evaluation_label"] != "TRUE_PASS_MATCH"]
        membership = [
            _cluster_membership(row["candidate_ref"], clusters)["membership"]
            for row in false_rows
        ]
        self.assertEqual(membership.count("isolated_or_singleton"), 24)
        self.assertEqual(membership.count("multi_candidate_cluster"), 43)

    def test_effective_ball_alone_does_not_prove_contact_generation(self) -> None:
        prerequisites = _contact_generation_prerequisites(
            {"nearby_contact_ids": []},
            {"effective_ball": {"nearby_positions": [{"frame": 1}]}, "nearby_players": [], "possession": {"nearby_owner_frames": []}},
        )
        self.assertEqual(prerequisites["earliest_missing_stage"], "PLAYER_TRACK")
        self.assertFalse(prerequisites["demonstrates_contact_generation_gap"])

    def test_contact_generation_requires_all_upstream_prerequisites(self) -> None:
        prerequisites = _contact_generation_prerequisites(
            {"nearby_contact_ids": []},
            {
                "effective_ball": {"nearby_positions": [{"frame": 1}]},
                "nearby_players": [{"stable_player_id": "A01", "stable_subject_id": "slot-a"}],
                "possession": {"nearby_owner_frames": [{"status": "controlled", "stable_player_id": "A01", "stable_subject_id": "slot-a"}]},
            },
        )
        self.assertTrue(prerequisites["demonstrates_contact_generation_gap"])

    def test_shared_fix_evaluation_supports_multiple_families_and_go_and_no_go(self) -> None:
        no_go = _evaluate_shared_fix_candidates([
            {"root_cause": "PASS_PAIR_CONSTRUCTION", "population": "MISS", "window_id": "W1"},
            {"root_cause": "PASS_PAIR_CONSTRUCTION", "population": "MISS", "window_id": "W2"},
            {"root_cause": "WRONG_CONTACT_PLAYER", "population": "TEAM", "window_id": "W3"},
        ])
        self.assertEqual({row["root_cause"] for row in no_go}, {"PASS_PAIR_CONSTRUCTION", "WRONG_CONTACT_PLAYER"})
        self.assertFalse(any(row["qualifies_for_go"] for row in no_go))
        self.assertEqual(_go_no_go_decision(no_go), "NO_SHARED_FIX_FOUND")
        clear_go = _evaluate_shared_fix_candidates([
            {"root_cause": "SHARED_SYNTHETIC_DEFECT", "population": "TEAM", "window_id": "W1", "engineering_assessment": {"production_layer": "synthetic runtime", "bounded_fix_scope": "one bounded synthetic guard", "gold_independent": True, "regression_risk": "acceptable", "rationale": "synthetic repeated runtime evidence"}},
            {"root_cause": "SHARED_SYNTHETIC_DEFECT", "population": "TEAM", "window_id": "W2", "engineering_assessment": {"production_layer": "synthetic runtime", "bounded_fix_scope": "one bounded synthetic guard", "gold_independent": True, "regression_risk": "acceptable", "rationale": "synthetic repeated runtime evidence"}},
        ])
        self.assertTrue(clear_go[0]["qualifies_for_go"])
        self.assertEqual(_go_no_go_decision(clear_go), "CLEAR_SHARED_UPSTREAM_FIX")

    def test_root_cause_aggregation_is_deterministic_across_populations(self) -> None:
        rows = [{"root_cause": "CONTACT_GENERATION", "window_id": "W1"}, {"root_cause": "UNKNOWN", "window_id": "W2"}]
        first = _root_cause_counts(rows, rows[:1], rows[1:])
        self.assertEqual(first, _root_cause_counts(rows, rows[:1], rows[1:]))
        self.assertEqual(first["total"]["CONTACT_GENERATION"], 2)

    def test_runtime_pass_services_do_not_import_evaluation_or_goldset(self) -> None:
        service = (Path(__file__).resolve().parents[1] / "app" / "services" / "pass_candidates.py").read_text(encoding="utf-8")
        self.assertNotIn("pass_upstream_root_cause_audit", service)
        self.assertNotIn("contact_action_goldset", service)


if __name__ == "__main__":
    unittest.main()
