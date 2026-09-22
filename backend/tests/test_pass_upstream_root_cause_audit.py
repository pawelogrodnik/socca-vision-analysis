from __future__ import annotations

import copy
import unittest
from pathlib import Path

from evaluation.pass_upstream_root_cause_audit import (
    _deterministic_fp_sample,
    _miss_root_cause,
    _root_cause_counts,
    _team_error_root_cause,
    build_upstream_trace,
)


def _indexes(*, operator_team: str | None = None) -> dict:
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
        "events": {
            "event-1": {
                "event_id": "event-1", "start_time_sec": 10.0, "end_time_sec": 10.1,
                "stable_player_id": "A01", "stable_subject_id": "slot-a", "team_label": "A", "team_name": "Corgi",
                "evidence": {"frames": 3, "min_distance_m": 0.8},
            },
        },
        "passes": {"pass-1": {"candidate_id": "pass-1", "start_time_sec": 10.1, "source_event_id": "event-1", "target_event_id": "event-2"}},
        "slots": {"slot-a": slot},
        "possession": [
            {
                "frame": 300, "time_sec": 10.0, "ball_source": "detected", "ball_confidence": 0.8,
                "ball_position_m": [10.0, 20.0], "status": "controlled", "stable_subject_id": "slot-a",
                "stable_player_id": "A01", "team_label": "A", "nearest_distance_m": 1.0,
                "nearest_players": [
                    {"stable_subject_id": "slot-a", "stable_player_id": "A01", "team_label": "A", "distance_m": 1.0},
                    {"stable_subject_id": "slot-b", "stable_player_id": "B01", "team_label": "B", "team_name": "Verisk", "distance_m": 0.6},
                ],
            },
        ],
    }}


class PassUpstreamRootCauseAuditTests(unittest.TestCase):
    def test_trace_preserves_existing_ids_times_and_layers(self) -> None:
        indexes = _indexes()
        before = copy.deepcopy(indexes)
        trace = build_upstream_trace("source", 10.0, indexes, "event-1", expected_team="Verisk")
        self.assertEqual(trace["source_match_id"], "source")
        self.assertEqual(trace["source_time_sec"], 10.0)
        self.assertEqual(trace["selected_player"]["stable_subject_id"], "slot-a")
        self.assertEqual(trace["global_identity"]["tracklet_id_samples"], ["12:1"])
        self.assertEqual(trace["contact"]["event_id"], "event-1")
        self.assertEqual(trace["pass"][0]["candidate_id"], "pass-1")
        self.assertEqual(indexes, before)

    def test_contact_player_error_is_distinct_from_possession_owner_error(self) -> None:
        trace = build_upstream_trace("source", 10.0, _indexes(), "event-1", expected_team="Verisk")
        contact = {"event_team": {"team_label": "A", "team_name": "Corgi"}, "canonical_subject_team": {"team_label": "A", "team_name": "Corgi"}}
        self.assertEqual(_team_error_root_cause("Verisk", contact, trace), "WRONG_POSSESSION_OWNER")
        no_local_alternative = copy.deepcopy(trace)
        no_local_alternative["possession"]["closer_expected_team_players"] = []
        self.assertEqual(_team_error_root_cause("Verisk", contact, no_local_alternative), "UNKNOWN")

    def test_explicit_operator_team_decision_is_authoritative_in_trace(self) -> None:
        trace = build_upstream_trace("source", 10.0, _indexes(operator_team="Verisk"), "event-1", expected_team="Verisk")
        contact = {"event_team": {"team_label": "A", "team_name": "Corgi"}, "canonical_subject_team": {"team_label": "A", "team_name": "Corgi"}}
        self.assertEqual(_team_error_root_cause("Verisk", contact, trace), "OPERATOR_DECISION_PROPAGATION_BUG")

    def test_miss_stages_require_concrete_evidence(self) -> None:
        ball_trace = {"ball": {"nearby_frames": [{"frame": 1}]}}
        no_ball_trace = {"ball": {"nearby_frames": []}}
        self.assertEqual(_miss_root_cause({"root_cause": "NO_SOURCE_CONTACT"}, ball_trace), "CONTACT_GENERATION")
        self.assertEqual(_miss_root_cause({"root_cause": "NO_SOURCE_CONTACT"}, no_ball_trace), "BALL_TRACK")
        self.assertEqual(_miss_root_cause({"root_cause": "EXCLUDED_BY_RELEASE_POLICY"}, ball_trace), "RELEASE_POLICY")
        self.assertEqual(_miss_root_cause({"root_cause": "UNKNOWN"}, ball_trace), "UNKNOWN")

    def test_false_positive_sample_is_deterministic_and_spans_windows(self) -> None:
        rows = [
            {"candidate_ref": f"source:{window}-{index}", "candidate_id": f"{window}-{index}", "window_id": window,
             "merged_release_time_sec": index, "evaluation_label": "UNMATCHED_PASS_CANDIDATE"}
            for index, window in enumerate(("W1", "W2", "W3", "W4", "W5", "W6"), start=1)
        ]
        first = _deterministic_fp_sample(rows)
        second = _deterministic_fp_sample(rows)
        self.assertEqual(first, second)
        self.assertEqual({row["window_id"] for row in first}, {"W1", "W2", "W3", "W4", "W5", "W6"})

    def test_root_cause_aggregation_is_deterministic_across_populations(self) -> None:
        rows = [{"root_cause": "CONTACT_GENERATION", "window_id": "W1"}, {"root_cause": "UNKNOWN", "window_id": "W2"}]
        first = _root_cause_counts(rows, rows[:1], rows[1:])
        second = _root_cause_counts(rows, rows[:1], rows[1:])
        self.assertEqual(first, second)
        self.assertEqual(first["total"]["CONTACT_GENERATION"], 2)

    def test_runtime_pass_services_do_not_import_evaluation_or_goldset(self) -> None:
        service = (Path(__file__).resolve().parents[1] / "app" / "services" / "pass_candidates.py").read_text(encoding="utf-8")
        self.assertNotIn("pass_upstream_root_cause_audit", service)
        self.assertNotIn("contact_action_goldset", service)


if __name__ == "__main__":
    unittest.main()
