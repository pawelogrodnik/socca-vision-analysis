from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_decision_audit import (
    AUDIT_FILENAME,
    BACKFILL_REPORT_FILENAME,
    BENCHMARK_FILENAME,
    PENDING_AUDIT_FILENAME,
    append_operator_decision_audit,
    backfill_review_decision_audit,
    commit_staged_operator_decision_audit,
    prepare_operator_decision_audit_event,
    recover_staged_operator_decision_audits,
    stage_operator_decision_audit,
)
from app.services.identity_reviewed_team_attribution_policy import (
    SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
    TEAM_ATTRIBUTION_POLICY_FILENAME,
    persist_automatic_team_assignments,
    short_track_dominant_team_assignment,
    team_evidence_features,
)
from app.services.identity_jersey_number_common import canonical_digest


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
            self.assertFalse((path / AUDIT_FILENAME).exists())

    def test_weak_or_sustained_opposing_signal_is_not_auto_assigned(self) -> None:
        weak = team_evidence_features(
            [{"frame": frame, "tracklet_id": "t", "team_label": "A" if frame < 16 else "B"} for frame in range(40)]
        )
        sustained = team_evidence_features(
            [{"frame": frame, "tracklet_id": "t", "team_label": "A" if frame < 10 else "B"} for frame in range(90)]
        )
        self.assertIsNone(short_track_dominant_team_assignment(weak))
        self.assertIsNone(short_track_dominant_team_assignment(sustained))

    def test_highly_alternating_votes_are_not_treated_as_isolated_noise(self) -> None:
        # B still has a high vote ratio, but the repeated A/B switches are a
        # temporal-structure hazard rather than a safe short-track majority.
        labels = ["A" if frame in {5, 15, 25, 35, 45, 55, 65} else "B" for frame in range(80)]
        features = team_evidence_features(
            [{"frame": frame, "tracklet_id": "t", "team_label": label} for frame, label in enumerate(labels)]
        )
        self.assertGreater(features["dominant_ratio"], .85)
        self.assertGreater(features["team_switch_count"], 6)
        self.assertIsNone(short_track_dominant_team_assignment(features))

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
            # The direct append helper deliberately bypasses the durable
            # mutation commit.  It remains useful for the legacy audit
            # benchmark, but cannot manufacture an exact calibration label.
            self.assertEqual(benchmark["team_attribution"]["eligible_calibration_samples"], 0)
            self.assertEqual(benchmark["team_attribution"]["operator_agreed_with_dominant_signal"], 0)
            self.assertEqual(
                benchmark["overall"]["operator_result_distribution"],
                {"team_A": 0, "team_B": 1, "unknown": 1, "referee": 0, "false_detection": 0, "other": 0},
            )

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
            benchmark = json.loads((path / BENCHMARK_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(benchmark["overall"]["total_operator_decisions"], 1)
            self.assertEqual(benchmark["overall"]["mandatory_decisions"], 0)
            self.assertEqual(benchmark["overall"]["optional_decisions"], 0)
            self.assertEqual(benchmark["overall"]["requiredness_unavailable"], 1)
            self.assertEqual(benchmark["overall"]["operator_result_distribution"]["unknown"], 1)
            self.assertEqual(benchmark["team_attribution"]["eligible_dominant_signal_cases"], 0)
            self.assertEqual(benchmark["team_attribution"]["unavailable_team_features"], 1)

    def test_backfill_uses_only_proven_exact_segment_frames(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            positions = [{"frame": frame} for frame in range(100)]
            (path / "tracklets.json").write_text(json.dumps({"tracklets": [{"tracklet_id": "t", "team_label": "B", "positions_m": positions}]}), encoding="utf-8")
            (path / "identity_candidate_shadow.json").write_text(json.dumps({"subjects": [{"candidate_subject_id": "s", "tracklet_ids": ["t"]}]}), encoding="utf-8")
            pairs = [["t", frame] for frame in range(30)]
            digest = canonical_digest({"candidate_subject_id": "s", "tracklet_ids": ["t"], "observations": [{"tracklet_id": "t", "frame": frame} for frame in range(30)]})
            decision = {"candidate_subject_id": "s", "action": "assign_team", "team_label": "B", "source": {"candidate_subject_id": "s", "source_ownership_digest": digest, "detected_pairs": pairs}}
            (path / "reviewed_identity_segment_decisions.json").write_text(json.dumps({"decisions": [decision]}), encoding="utf-8")
            report = backfill_review_decision_audit(path)
            record = report["records"][0]
            self.assertTrue(record["exact_source_linkage"])
            self.assertEqual(record["team_features"]["B_observations"], 30)
            benchmark = json.loads((path / BENCHMARK_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(benchmark["historical_backfill"]["exact_source_linkable_count"], 1)
            self.assertEqual(benchmark["historical_backfill"]["reconstructed_team_feature_count"], 1)
            self.assertEqual(benchmark["overall"]["decision_counts_by_stage"], {"team_attribution": 1})

    def test_recovery_promotes_only_pending_events_with_matching_canonical_decision(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            valid = prepare_operator_decision_audit_event(
                unit={"candidate_subject_id": "a"},
                payload={"candidate_subject_id": "a", "action": "assign_team", "team_label": "A"},
                required=True,
            )
            orphan = prepare_operator_decision_audit_event(
                unit={"candidate_subject_id": "b"},
                payload={"candidate_subject_id": "b", "action": "assign_team", "team_label": "B"},
                required=True,
            )
            stage_operator_decision_audit(path, valid)
            stage_operator_decision_audit(path, orphan)
            (path / "reviewed_identity_slot_assignments.json").write_text(
                json.dumps({"decisions": [{"candidate_subject_id": "a", "action": "assign_team", "team_label": "A"}]}),
                encoding="utf-8",
            )

            self.assertEqual(recover_staged_operator_decision_audits(path), 1)
            audit = json.loads((path / AUDIT_FILENAME).read_text(encoding="utf-8"))
            pending = json.loads((path / PENDING_AUDIT_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual([event["source"]["candidate_subject_id"] for event in audit["events"]], ["a"])
            self.assertEqual([event["source"]["candidate_subject_id"] for event in pending["events"]], ["b"])

    def test_recovery_deduplicates_after_audit_promotion_before_pending_cleanup(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            event = prepare_operator_decision_audit_event(
                unit={"candidate_subject_id": "a"},
                payload={"candidate_subject_id": "a", "action": "assign_team", "team_label": "A"},
                required=True,
            )
            stage_operator_decision_audit(path, event)
            (path / "reviewed_identity_slot_assignments.json").write_text(
                json.dumps({"decisions": [{"candidate_subject_id": "a", "action": "assign_team", "team_label": "A"}]}),
                encoding="utf-8",
            )
            from app.services.identity_initial_audit_store import write_identity_json_atomic as real_write

            def fail_only_pending(destination: Path, document: dict) -> None:
                if destination.name == PENDING_AUDIT_FILENAME:
                    raise OSError("injected pending cleanup failure")
                real_write(destination, document)

            with patch(
                "app.services.identity_reviewed_decision_audit.write_identity_json_atomic",
                side_effect=fail_only_pending,
            ):
                with self.assertRaises(OSError):
                    commit_staged_operator_decision_audit(path, event["event_id"])

            self.assertEqual(recover_staged_operator_decision_audits(path), 1)
            audit = json.loads((path / AUDIT_FILENAME).read_text(encoding="utf-8"))
            pending = json.loads((path / PENDING_AUDIT_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(len(audit["events"]), 1)
            self.assertEqual(pending["events"], [])


if __name__ == "__main__":
    unittest.main()
