from __future__ import annotations

import copy
import unittest
from pathlib import Path

from evaluation.pass_team_attribution_v2 import (
    _apply_team_attribution_v2,
    _relationship_outcome,
    evaluate_pass_team_attribution_v2,
    resolve_contact_team_v2,
)


def _window() -> dict:
    return {
        "window_id": "W1", "source_match_id": "source", "merged_start_time_sec": 0.0,
        "merged_end_time_sec": 20.0, "source_start_time_sec": 0.0, "source_end_time_sec": 20.0,
        "source_to_merged_offset_sec": 0.0,
    }


def _gold(*, actor: str = "Corgi", receiver: str = "Corgi") -> dict:
    return {
        "event_id": "gold", "window_id": "W1", "approx_time_sec": 5.0, "timing_tolerance_sec": 1.0,
        "action": "PASS", "outcome": "COMPLETED", "actor": {"team": actor}, "target": {"team": receiver}, "modifiers": [],
    }


def _contact(event_id: str, player: str, subject: str, team: str, *, team_id: str | None = None) -> dict:
    return {
        "event_id": event_id, "stable_player_id": player, "stable_subject_id": subject,
        "team_label": team, "team_id": team_id or f"team-{team.lower()}", "team_name": "Corgi" if team == "A" else "Verisk",
        "start_time_sec": 5.0, "end_time_sec": 5.1,
    }


def _candidate(*, from_team: str = "A", to_team: str = "A") -> dict:
    return {
        "candidate_id": "pass-0001", "start_time_sec": 5.0, "end_time_sec": 5.3,
        "source_event_id": "source-contact", "target_event_id": "target-contact", "from_restart": False,
        "from_stable_player_id": "P1", "from_stable_subject_id": "slot-p1", "from_team_label": from_team,
        "from_team_id": f"team-{from_team.lower()}", "from_team_name": "Corgi" if from_team == "A" else "Verisk",
        "to_stable_player_id": "P2", "to_stable_subject_id": "slot-p2", "to_team_label": to_team,
        "to_team_id": f"team-{to_team.lower()}", "to_team_name": "Corgi" if to_team == "A" else "Verisk",
        "pass_type": "same_team_pass" if from_team == to_team else "turnover_or_interception",
        "outcome": "completed_pass" if from_team == to_team else "failed_pass", "completed": from_team == to_team, "failed": from_team != to_team,
    }


def _identities(*, source_team: str = "A", target_team: str = "A") -> dict:
    return {"slots": [
        {"stable_player_id": "P1", "stable_subject_id": "slot-p1", "team_label": source_team, "team_id": f"team-{source_team.lower()}", "team_name": "Corgi" if source_team == "A" else "Verisk"},
        {"stable_player_id": "P2", "stable_subject_id": "slot-p2", "team_label": target_team, "team_id": f"team-{target_team.lower()}", "team_name": "Corgi" if target_team == "A" else "Verisk"},
    ]}


def _docs(candidate: dict, source: dict, target: dict) -> dict:
    return {"source": {
        "pass_candidates": {"candidates": [candidate]},
        "event_candidates": {"events": [source, target]},
        "contact_candidates": {"candidates": []}, "restart_candidates": {"candidates": []},
        "possession_candidates": {"frames": [
            {"frame": 150, "time_sec": 5.0, "status": "controlled", "stable_player_id": source["stable_player_id"], "stable_subject_id": source["stable_subject_id"], "team_label": source["team_label"]},
            {"frame": 151, "time_sec": 5.033, "status": "controlled", "stable_player_id": target["stable_player_id"], "stable_subject_id": target["stable_subject_id"], "team_label": target["team_label"]},
        ]},
    }}


class PassTeamAttributionV2Tests(unittest.TestCase):
    def test_canonical_stable_subject_overrides_conflicting_event_team(self) -> None:
        identity = {"by_subject": {"slot-p1": {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"}}, "by_player": {"P1": {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"}}}
        result = resolve_contact_team_v2(_contact("c", "P1", "slot-p1", "B"), identity)
        self.assertEqual(result["team_label"], "A")
        self.assertEqual(result["attribution_source"], "canonical_stable_subject")
        self.assertIn("event_team_disagrees_with_canonical_identity", result["conflicting_sources"])

    def test_event_team_is_fallback_when_no_canonical_identity_exists(self) -> None:
        result = resolve_contact_team_v2(_contact("c", "P1", "slot-p1", "B"), {"by_subject": {}, "by_player": {}})
        self.assertEqual(result["team_label"], "B")
        self.assertEqual(result["attribution_source"], "event_team_fallback")

    def test_conflicting_strong_canonical_sources_fail_closed_to_unknown(self) -> None:
        identity = {"by_subject": {"slot-p1": {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"}}, "by_player": {"P1": {"team_label": "B", "team_id": "team-b", "team_name": "Verisk"}}}
        result = resolve_contact_team_v2(_contact("c", "P1", "slot-p1", "A"), identity)
        self.assertIsNone(result["team_label"])
        self.assertEqual(result["attribution_source"], "CONFLICTING_CANONICAL_IDENTITY")

    def test_source_and_target_are_resolved_independently_and_drive_relationship(self) -> None:
        candidate = _candidate(from_team="B", to_team="B")
        candidate["source_match_id"] = "source"
        contacts = {"source": {"source-contact": _contact("source-contact", "P1", "slot-p1", "B"), "target-contact": _contact("target-contact", "P2", "slot-p2", "B")}}
        identities = {"source": {"by_subject": {"slot-p1": {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"}, "slot-p2": {"team_label": "B", "team_id": "team-b", "team_name": "Verisk"}}, "by_player": {}}}
        resolved = _apply_team_attribution_v2(candidate, contacts, identities)
        self.assertEqual((resolved["from_team_label"], resolved["to_team_label"]), ("A", "B"))
        self.assertEqual((resolved["pass_type"], resolved["outcome"]), ("turnover_or_interception", "failed_pass"))

    def test_relationship_semantics_cover_same_different_and_unknown(self) -> None:
        a = {"team_label": "A", "team_id": "team-a"}
        b = {"team_label": "B", "team_id": "team-b"}
        self.assertEqual(_relationship_outcome(a, a), ("same_team_pass", "completed_pass"))
        self.assertEqual(_relationship_outcome(a, b), ("turnover_or_interception", "failed_pass"))
        self.assertEqual(_relationship_outcome(a, {"team_label": None}), ("unknown_team_pass", "unknown_pass_attempt"))

    def test_evaluation_preserves_candidate_set_and_does_not_use_gold_for_resolution(self) -> None:
        candidate = _candidate(from_team="B", to_team="B")
        docs = _docs(candidate, _contact("source-contact", "P1", "slot-p1", "B"), _contact("target-contact", "P2", "slot-p2", "B"))
        report = evaluate_pass_team_attribution_v2({"windows": [_window()], "game_state_intervals": [], "events": [_gold()]}, docs, {"source": _identities(source_team="A", target_team="A")})
        self.assertTrue(report["candidate_set"]["candidate_refs_identical"])
        self.assertEqual(report["candidate_set"]["v1_count"], report["candidate_set"]["v2_count"])
        self.assertEqual(report["matched_team_attribution"]["v2"]["actor_accuracy"], 1.0)
        self.assertEqual(candidate["from_team_label"], "B")

    def test_error_audit_includes_nearby_production_possession_evidence(self) -> None:
        candidate = _candidate(from_team="B", to_team="B")
        docs = _docs(candidate, _contact("source-contact", "P1", "slot-p1", "B"), _contact("target-contact", "P2", "slot-p2", "B"))
        report = evaluate_pass_team_attribution_v2(
            {"windows": [_window()], "game_state_intervals": [], "events": [_gold()]},
            docs,
            {"source": _identities(source_team="B", target_team="B")},
        )
        audit = report["error_audit"]
        self.assertEqual(len(audit), 1)
        source_possession = audit[0]["source_contact"]["nearby_controlled_possession"]
        target_possession = audit[0]["target_contact"]["nearby_controlled_possession"]
        self.assertEqual(source_possession["team_label_counts"], {"B": 1})
        self.assertEqual(target_possession["team_label_counts"], {"B": 1})

    def test_v1_correct_to_v2_wrong_is_counted(self) -> None:
        candidate = _candidate()
        docs = _docs(candidate, _contact("source-contact", "P1", "slot-p1", "A"), _contact("target-contact", "P2", "slot-p2", "A"))
        report = evaluate_pass_team_attribution_v2({"windows": [_window()], "game_state_intervals": [], "events": [_gold()]}, docs, {"source": _identities(source_team="B", target_team="B")})
        self.assertEqual(report["regression_matrix"]["actor"]["V1_correct_TO_V2_wrong"], 1)

    def test_no_runtime_pass_service_imports_evaluation_or_goldset(self) -> None:
        service = (Path(__file__).resolve().parents[1] / "app" / "services" / "pass_candidates.py").read_text(encoding="utf-8")
        self.assertNotIn("pass_team_attribution_v2", service)
        self.assertNotIn("contact_action_goldset", service)

    def test_attribution_is_deterministic_and_input_candidates_are_not_mutated(self) -> None:
        candidate = _candidate()
        before = copy.deepcopy(candidate)
        contacts = {"source": {"source-contact": _contact("source-contact", "P1", "slot-p1", "A"), "target-contact": _contact("target-contact", "P2", "slot-p2", "A")}}
        identities = {"source": {"by_subject": {"slot-p1": {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"}, "slot-p2": {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"}}, "by_player": {}}}
        self.assertEqual(_apply_team_attribution_v2(candidate, contacts, identities), _apply_team_attribution_v2(candidate, contacts, identities))
        self.assertEqual(candidate, before)


if __name__ == "__main__":
    unittest.main()
