from __future__ import annotations

import json
import unittest
from pathlib import Path

from evaluation.open_play_pass_v2 import (
    _in_active_play,
    derive_active_play_mask,
    evaluate_open_play_pass_policy_comparison,
)


def _window() -> dict:
    return {
        "window_id": "W1", "merged_start_time_sec": 0.0, "merged_end_time_sec": 20.0,
        "source_match_id": "source", "source_start_time_sec": 0.0, "source_end_time_sec": 20.0,
        "source_to_merged_offset_sec": 0.0,
    }


def _event(event_id: str, time_sec: float, *, action: str = "PASS", context_only: bool = False, modifiers: list[str] | None = None) -> dict:
    return {
        "event_id": event_id, "window_id": "W1", "approx_time_sec": time_sec,
        "timing_tolerance_sec": 1.0, "action": action, "outcome": "COMPLETED",
        "actor": {"team": "Corgi"}, "target": {"team": "Corgi"},
        "context_only": context_only, "modifiers": modifiers or [],
    }


def _candidate(candidate_id: str, time_sec: float, *, restart: bool = False, outcome: str = "completed_pass") -> dict:
    return {
        "candidate_id": candidate_id, "start_time_sec": time_sec, "end_time_sec": time_sec + 0.2,
        "outcome": outcome, "from_restart": restart, "from_team_name": "Corgi", "to_team_name": "Corgi",
        "rejection_reasons": [],
    }


def _docs(candidates: list[dict]) -> dict:
    return {"source": {
        "pass_candidates": {"candidates": candidates},
        "contact_candidates": {"candidates": []},
        "event_candidates": {"events": []},
        "restart_candidates": {"candidates": []},
    }}


class OpenPlayPassV2Tests(unittest.TestCase):
    def test_mask_excludes_not_in_play_gk_hold_and_restart_but_keeps_normal_play_afterward(self) -> None:
        goldset = {
            "windows": [_window()],
            "game_state_intervals": [
                {"window_id": "W1", "start_time_sec": 0, "end_time_sec": 3, "state": "NOT_IN_PLAY"},
                {"window_id": "W1", "start_time_sec": 8, "end_time_sec": 10, "state": "GK_HOLD"},
            ],
            "events": [_event("restart", 5, action="RESTART")],
        }
        mask = derive_active_play_mask(goldset)
        self.assertEqual(mask["windows"]["W1"]["active_play_intervals"], [
            {"start_time_sec": 3.0, "end_time_sec": 4.0},
            {"start_time_sec": 6.0, "end_time_sec": 8.0},
            {"start_time_sec": 10.0, "end_time_sec": 20.0},
        ])
        self.assertEqual(mask["windows"]["W1"]["active_play_scored_duration_sec"], 13.0)

    def test_boundary_contract_excludes_interval_start_and_includes_its_end(self) -> None:
        goldset = {
            "windows": [_window()],
            "game_state_intervals": [{"window_id": "W1", "start_time_sec": 0, "end_time_sec": 3, "state": "NOT_IN_PLAY"}],
            "events": [_event("restart", 5, action="RESTART")],
        }
        mask = derive_active_play_mask(goldset)
        # NOT_IN_PLAY [0, 3): 3 is the first active timestamp.
        self.assertFalse(_in_active_play(0.0, "W1", mask))
        self.assertTrue(_in_active_play(3.0, "W1", mask))
        # RESTART [4, 6): the restart start and interior are excluded, 6 is active.
        self.assertFalse(_in_active_play(4.0, "W1", mask))
        self.assertFalse(_in_active_play(5.0, "W1", mask))
        self.assertTrue(_in_active_play(6.0, "W1", mask))

    def test_final_active_interval_includes_selected_window_end(self) -> None:
        mask = derive_active_play_mask({"windows": [_window()], "game_state_intervals": [], "events": []})
        self.assertTrue(_in_active_play(20.0, "W1", mask))

    def test_primary_scope_excludes_non_active_restart_context_only_and_ambiguous(self) -> None:
        goldset = {
            "windows": [_window()],
            "game_state_intervals": [{"window_id": "W1", "start_time_sec": 0, "end_time_sec": 3, "state": "NOT_IN_PLAY"}],
            "events": [
                _event("inactive", 2), _event("restart", 5, action="RESTART"), _event("normal", 7),
                _event("context", 11, context_only=True), _event("ambiguous", 12, modifiers=["AMBIGUOUS"]),
            ],
        }
        source = _docs([
            _candidate("inactive", 2), _candidate("restart", 5, restart=True), _candidate("normal", 7),
            _candidate("after-restart", 7.5), _candidate("context", 11), _candidate("ambiguous", 12),
        ])
        report = evaluate_open_play_pass_policy_comparison(goldset, source, source)
        self.assertEqual(report["primary"]["gold"]["attempts"], 1)
        self.assertEqual(report["primary"]["v1"]["aggregate"]["attempts"], 4)
        self.assertEqual(report["primary"]["v1"]["event_metrics"]["matched_pass_attempts"], 1)
        self.assertEqual(report["primary"]["v1"]["event_metrics"]["unmatched_pass_candidates"], 3)

    def test_same_mask_is_deterministic_for_v1_and_v2(self) -> None:
        goldset = {"windows": [_window()], "game_state_intervals": [], "events": [_event("pass", 7)]}
        source = _docs([_candidate("pass", 7)])
        first = evaluate_open_play_pass_policy_comparison(goldset, source, source)
        second = evaluate_open_play_pass_policy_comparison(goldset, source, source)
        self.assertEqual(first["active_play_mask"], second["active_play_mask"])
        self.assertEqual(first["active_play_mask"], derive_active_play_mask(goldset))

    def test_mask_is_evaluation_only_and_not_imported_by_pass_generation(self) -> None:
        service = (Path(__file__).resolve().parents[1] / "app" / "services" / "pass_candidates.py").read_text(encoding="utf-8")
        self.assertNotIn("contact_action_goldset", service)
        self.assertNotIn("open_play_pass_v2", service)

    def test_real_goldset_mask_is_valid_and_deterministic(self) -> None:
        goldset = json.loads((Path(__file__).parent / "fixtures" / "contact_action_goldset_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(derive_active_play_mask(goldset), derive_active_play_mask(goldset))
        self.assertEqual(set(derive_active_play_mask(goldset)["windows"]), {"W1", "W2", "W3", "W4", "W5", "W6"})

    def test_real_goldset_includes_restart_end_and_selected_window_end_passes(self) -> None:
        goldset = json.loads((Path(__file__).parent / "fixtures" / "contact_action_goldset_v1.json").read_text(encoding="utf-8"))
        report = evaluate_open_play_pass_policy_comparison(goldset, {}, {})
        active_event_ids = {
            row["event_id"]
            for row in goldset["events"]
            if row.get("action") == "PASS"
            and _in_active_play(float(row["approx_time_sec"]), str(row["window_id"]), report["active_play_mask"])
        }
        self.assertIn("W1-E013", active_event_ids)
        self.assertIn("W2-E019", active_event_ids)
        self.assertEqual(report["primary"]["gold"]["attempts"], 48)


if __name__ == "__main__":
    unittest.main()
