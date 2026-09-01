from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.identity_reviewed_decision_audit import (
    AUDIT_FILENAME,
    BACKFILL_REPORT_FILENAME,
    BENCHMARK_FILENAME,
    append_operator_decision_audit,
    backfill_review_decision_audit,
)
from app.services.identity_reviewed_team_attribution_policy import (
    SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
    TEAM_ATTRIBUTION_POLICY_FILENAME,
    persist_automatic_team_assignments,
    short_track_dominant_team_assignment,
    team_evidence_features,
)


class TeamAttributionPolicyTests(unittest.TestCase):
    def test_short_track_with_isolated_noise_gets_versioned_dominant_assignment(self) -> None:
        features = team_evidence_features(
            [{"frame": frame, "tracklet_id": "t", "team_label": "A" if frame in {5, 17} else "B"} for frame in range(40)], fps=30
        )
        assignment = short_track_dominant_team_assignment(features)
        self.assertEqual(assignment["team_label"], "B")
        self.assertEqual(assignment["provenance"], SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION)
        with TemporaryDirectory() as directory:
            path = Path(directory)
            persist_automatic_team_assignments(path, [{"candidate_subject_id": "s", "tracklet_ids": ["t"], "automatic_team_assignment": assignment}])
            persisted = json.loads((path / TEAM_ATTRIBUTION_POLICY_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(persisted["assignments"][0]["assignment"]["provenance"], SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION)

    def test_weak_or_sustained_opposing_signal_is_not_auto_assigned(self) -> None:
        weak = team_evidence_features(
            [{"frame": frame, "tracklet_id": "t", "team_label": "A" if frame < 16 else "B"} for frame in range(40)]
        )
        sustained = team_evidence_features(
            [{"frame": frame, "tracklet_id": "t", "team_label": "A" if frame < 10 else "B"} for frame in range(90)]
        )
        self.assertIsNone(short_track_dominant_team_assignment(weak))
        self.assertIsNone(short_track_dominant_team_assignment(sustained))

    def test_audit_is_append_only_and_benchmark_reports_dominant_agreement(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            unit = {
                "candidate_subject_id": "s", "tracklet_ids": ["t"], "frame_start": 1,
                "frame_end": 20, "detected_frame_count": 20, "detected_time_sec": .667,
                "detected_observation_count": 20, "detected_team_labels": ["A", "B"],
                "team_attribution_features": {"A_observations": 1, "B_observations": 19, "U_observations": 0, "dominant_team": "B", "dominant_ratio": .95, "team_switch_count": 2, "longest_A_run": 1, "longest_B_run": 10, "source_frame_count": 20},
                "reason_codes": ["team_attribution_coverage_debt"],
            }
            append_operator_decision_audit(path, unit=unit, payload={"candidate_subject_id": "s", "action": "assign_team", "team_label": "B"}, required=True)
            unit["current_decision"] = {"action": "assign_team", "team_label": "B"}
            append_operator_decision_audit(path, unit=unit, payload={"candidate_subject_id": "s", "action": "team_unknown"}, required=False)
            audit = json.loads((path / AUDIT_FILENAME).read_text(encoding="utf-8"))
            benchmark = json.loads((path / BENCHMARK_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(len(audit["events"]), 2)
            self.assertTrue(audit["events"][1]["operator_result"]["replaces_prior_operator_decision"])
            self.assertEqual(benchmark["team_attribution"]["operator_agreed_with_dominant_signal"], 1)

    def test_backfill_never_mutates_legacy_decision_and_marks_exact_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            legacy = {"decisions": [{"candidate_subject_id": "s", "action": "team_unknown"}]}
            (path / "reviewed_identity_slot_assignments.json").write_text(json.dumps(legacy), encoding="utf-8")
            report = backfill_review_decision_audit(path)
            self.assertEqual(report["decisions_recovered"], 1)
            self.assertEqual(report["records"][0]["provenance"], "EXACT_PERSISTED")
            self.assertEqual(json.loads((path / "reviewed_identity_slot_assignments.json").read_text(encoding="utf-8")), legacy)
            self.assertTrue((path / BACKFILL_REPORT_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
