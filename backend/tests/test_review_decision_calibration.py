from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.identity_reviewed_decision_audit import (
    AUDIT_FILENAME,
    BENCHMARK_FILENAME,
    CALIBRATION_SAMPLES_FILENAME,
    build_review_decision_calibration_samples,
    build_review_decision_benchmark,
    commit_staged_operator_decision_audit,
    export_review_decision_calibration_artifacts,
    prepare_operator_decision_audit_event,
    recover_staged_operator_decision_audits,
    stage_operator_decision_audit,
)
from app.services.identity_reviewed_slot_review import (
    prepare_reviewed_slot_assignments,
)


def _live_event(
    *,
    event_id: str,
    action: str = "assign_team",
    final: str = "B",
    state: str = "unknown",
    reasons: list[str] | None = None,
    provenance: str = "EXACT_PERSISTED",
    source_digest: str = "digest",
    canonical: bool = True,
    dominant: str = "B",
) -> dict:
    return {
        "event_id": event_id,
        "provenance": provenance,
        "decision_stage": "player_identity" if action == "assign_roster_player" else "team_attribution",
        "required": True,
        "required_reason": reasons if reasons is not None else ["team_attribution_coverage_debt"],
        "reviewed_team_attribution_state": state,
        "source": {
            "candidate_subject_id": f"subject-{event_id}",
            "source_ownership_digest": source_digest,
            "scope_kind": "whole_subject",
            "frame_count": 59,
            "observation_count": 59,
            "duration_sec": 1.967,
        },
        "team_evidence_before": {
            "provenance": "EXACT_PRE_DECISION",
            "A_observations": 4 if dominant == "B" else 55,
            "B_observations": 55 if dominant == "B" else 4,
            "U_observations": 0,
            "known_team_observations": 59,
            "dominant_team": dominant,
            "dominant_ratio": 55 / 59,
            "team_switch_count": 2,
            "longest_A_run": 2 if dominant == "B" else 31,
            "longest_B_run": 31 if dominant == "B" else 2,
            "source_frame_count": 59,
        },
        "system_path": {"current_decision": {}},
        "operator_result": {
            "action": action,
            "effective_team_label": final,
            "canonical_result_verified": canonical,
            "exact_source_linkage_proven": canonical,
        },
    }


class ReviewDecisionCalibrationTests(unittest.TestCase):
    def test_calibrates_exact_pre_decision_team_question_once_per_human_decision(self) -> None:
        agrees = _live_event(event_id="agree")
        overrides = _live_event(event_id="override", action="assign_roster_player", final="A")
        certain_player = _live_event(
            event_id="certain-player",
            action="assign_roster_player",
            final="B",
            state="certain_A",
            reasons=[],
        )
        samples = build_review_decision_calibration_samples(
            document={"events": [agrees, overrides, certain_player]}, match_id="match",
        )
        self.assertEqual([sample["event_id"] for sample in samples], ["agree", "override"])
        self.assertEqual(samples[0]["operator_result"]["effective_team_label"], "B")
        self.assertEqual(samples[1]["operator_result"]["effective_team_label"], "A")
        self.assertTrue(samples[0]["automatic_policy"]["would_auto_assign"])
        self.assertEqual(samples[0]["team_evidence_before"]["longest_minority_run"], 2)

    def test_terminal_dispositions_are_calibration_samples_but_not_binary_agreement(self) -> None:
        events = [
            _live_event(event_id="unknown", action="team_unknown", final="U"),
            _live_event(event_id="referee", action="referee", final="referee"),
            _live_event(event_id="false", action="false_detection", final="false_detection"),
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory)
            benchmark = build_review_decision_benchmark(path, document={"events": events})
        team = benchmark["team_attribution"]
        self.assertEqual(team["eligible_calibration_samples"], 3)
        self.assertEqual(team["eligible_dominant_signal_cases"], 3)
        self.assertEqual(team["operator_agreed_with_dominant_signal"], 0)
        self.assertEqual(team["operator_overrode_dominant_signal"], 0)
        self.assertEqual(team["operator_non_binary_final_disposition"], 3)

    def test_history_stale_or_unproven_events_never_become_calibration_labels(self) -> None:
        history = _live_event(event_id="history", provenance="HISTORICAL_BACKFILL")
        stale = _live_event(event_id="stale", source_digest="", canonical=False)
        unavailable = _live_event(event_id="unavailable")
        unavailable["team_evidence_before"]["provenance"] = "UNAVAILABLE"
        automatic = _live_event(event_id="automatic", provenance="AUTOMATIC_POLICY")
        samples = build_review_decision_calibration_samples(
            document={"events": [history, stale, unavailable, automatic]}, match_id="match",
        )
        self.assertEqual(samples, [])

    def test_canonical_commit_overrides_client_team_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "match.json").write_text(json.dumps({
                "teams": [
                    {"players": [{"id": "corgi-player"}]},
                    {"players": [{"id": "verisk-player"}]},
                ],
            }), encoding="utf-8")
            unit = {
                "candidate_subject_id": "subject",
                "source_ownership_digest": "current-digest",
                "detected_team_labels": ["A", "B"],
                "team_attribution_features": _live_event(event_id="features")["team_evidence_before"],
                "reason_codes": ["team_attribution_coverage_debt"],
            }
            event = prepare_operator_decision_audit_event(
                unit=unit,
                payload={
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "current-digest",
                    "action": "assign_roster_player",
                    # Browser input is deliberately wrong; canonical player wins.
                    "player_id": "verisk-player",
                    "team_label": "A",
                },
                required=True,
            )
            stage_operator_decision_audit(path, event)
            (path / "reviewed_identity_slot_assignments.json").write_text(json.dumps({
                "decisions": [{
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "current-digest",
                    "action": "assign_roster_player",
                    "player_id": "verisk-player",
                }],
            }), encoding="utf-8")
            committed = commit_staged_operator_decision_audit(path, event["event_id"])
            self.assertEqual(committed["operator_result"]["effective_team_label"], "B")
            self.assertIsNone(commit_staged_operator_decision_audit(path, event["event_id"]))
            audit = json.loads((path / AUDIT_FILENAME).read_text(encoding="utf-8"))
            samples = build_review_decision_calibration_samples(path)
            self.assertEqual(len(audit["events"]), 1)
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]["operator_result"]["effective_team_label"], "B")

    def test_new_whole_subject_store_persists_authoritative_source_digest(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "tracklets.json").write_text(json.dumps({
                "tracklets": [{"tracklet_id": "track", "team_label": "B", "positions_m": []}],
            }), encoding="utf-8")
            document = prepare_reviewed_slot_assignments(
                path,
                {"subjects": [{"candidate_subject_id": "subject", "tracklet_ids": ["track"]}]},
                [{"candidate_subject_id": "subject", "action": "assign_team", "team_label": "B"}],
                authoritative_source_ownership_digest="D1",
            )
            self.assertEqual(
                document["decisions"][0]["source_ownership_digest"], "D1",
            )

    def test_legacy_no_digest_row_stays_audit_history_not_calibration_truth(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            event = prepare_operator_decision_audit_event(
                unit={
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "D1",
                    "team_attribution_features": _live_event(event_id="features")["team_evidence_before"],
                    "reason_codes": ["team_attribution_coverage_debt"],
                },
                payload={"candidate_subject_id": "subject", "source_ownership_digest": "D1", "action": "assign_team", "team_label": "B"},
                required=True,
            )
            stage_operator_decision_audit(path, event)
            # This is the legacy whole-subject storage shape: semantic state
            # can be recovered, but it does not prove which exact source won.
            (path / "reviewed_identity_slot_assignments.json").write_text(json.dumps({
                "decisions": [{"candidate_subject_id": "subject", "action": "assign_team", "team_label": "B"}],
            }), encoding="utf-8")
            self.assertEqual(recover_staged_operator_decision_audits(path), 1)
            audit = json.loads((path / AUDIT_FILENAME).read_text(encoding="utf-8"))
            self.assertFalse(audit["events"][0]["operator_result"]["exact_source_linkage_proven"])
            self.assertEqual(build_review_decision_calibration_samples(path), [])

    def test_canonical_result_derives_slots_new_players_and_terminal_actions(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "match.json").write_text(json.dumps({
                "teams": [
                    {"players": [{"id": "corgi-player"}]},
                    {"players": [{"id": "verisk-player"}]},
                ],
            }), encoding="utf-8")
            (path / "global_identity.json").write_text(json.dumps({
                "slots": [{"slot_id": "A01"}],
            }), encoding="utf-8")
            specifications = [
                ("team", {"action": "assign_team", "team_label": "B"}, {"action": "assign_team", "team_label": "B"}, "B"),
                ("roster", {"action": "assign_roster_player", "player_id": "verisk-player", "team_label": "A"}, {"action": "assign_roster_player", "player_id": "verisk-player"}, "B"),
                ("slot", {"action": "assign_existing_slot", "stable_slot_id": "A01", "team_label": "B"}, {"action": "assign_existing_slot", "stable_slot_id": "A01"}, "A"),
                ("new", {"action": "create_new_stable_player", "team_label": "B"}, {"action": "create_new_stable_player", "team_label": "B", "stable_slot_id": "B02"}, "B"),
                ("unknown", {"action": "team_unknown"}, {"action": "team_unknown", "team_label": "A"}, "U"),
                ("referee", {"action": "referee"}, {"action": "referee", "team_label": "A"}, "referee"),
                ("false", {"action": "false_detection"}, {"action": "false_detection", "team_label": "B"}, "false_detection"),
            ]
            events = []
            decisions = []
            features = _live_event(event_id="features")["team_evidence_before"]
            for name, payload, canonical, _expected in specifications:
                candidate = f"subject-{name}"
                digest = f"digest-{name}"
                event = prepare_operator_decision_audit_event(
                    unit={
                        "candidate_subject_id": candidate,
                        "source_ownership_digest": digest,
                        "detected_team_labels": ["A", "B"],
                        "team_attribution_features": features,
                        "reason_codes": ["team_attribution_coverage_debt"],
                    },
                    payload={"candidate_subject_id": candidate, "source_ownership_digest": digest, **payload},
                    required=True,
                )
                stage_operator_decision_audit(path, event)
                events.append(event)
                decisions.append({
                    "candidate_subject_id": candidate,
                    "source_ownership_digest": digest,
                    **canonical,
                })
            (path / "reviewed_identity_slot_assignments.json").write_text(json.dumps({
                "reviewed_slots": [{"stable_slot_id": "B02"}],
                "decisions": decisions,
            }), encoding="utf-8")
            results = [commit_staged_operator_decision_audit(path, event["event_id"]) for event in events]
            self.assertEqual(
                [row["operator_result"]["effective_team_label"] for row in results],
                [expected for _name, _payload, _canonical, expected in specifications],
            )
            self.assertEqual(results[1]["decision_stage"], "player_identity")
            samples = build_review_decision_calibration_samples(path)
            self.assertEqual(len(samples), len(specifications))

    def test_stale_canonical_source_digest_cannot_promote_or_calibrate(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            event = prepare_operator_decision_audit_event(
                unit={
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "current",
                    "team_attribution_features": _live_event(event_id="features")["team_evidence_before"],
                    "reason_codes": ["team_attribution_coverage_debt"],
                },
                payload={"candidate_subject_id": "subject", "source_ownership_digest": "current", "action": "assign_team", "team_label": "B"},
                required=True,
            )
            stage_operator_decision_audit(path, event)
            (path / "reviewed_identity_slot_assignments.json").write_text(json.dumps({
                "decisions": [{
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "stale",
                    "action": "assign_team",
                    "team_label": "B",
                }],
            }), encoding="utf-8")
            self.assertIsNone(commit_staged_operator_decision_audit(path, event["event_id"]))
            self.assertEqual(build_review_decision_calibration_samples(path), [])

    def test_create_new_player_recovery_requires_the_requested_team(self) -> None:
        features = _live_event(event_id="features")["team_evidence_before"]
        with TemporaryDirectory() as directory:
            path = Path(directory)
            event = prepare_operator_decision_audit_event(
                unit={
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "D1",
                    "detected_team_labels": ["A", "B"],
                    "team_attribution_features": features,
                    "reason_codes": ["team_attribution_coverage_debt"],
                },
                payload={
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "D1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                },
                required=True,
            )
            stage_operator_decision_audit(path, event)
            # A later decision for the same exact source chose Team B.  It
            # cannot prove the interrupted Team A click ever persisted.
            (path / "reviewed_identity_slot_assignments.json").write_text(json.dumps({
                "reviewed_slots": [{"stable_slot_id": "B01"}],
                "decisions": [{
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "D1",
                    "action": "create_new_stable_player",
                    "team_label": "B",
                    "stable_slot_id": "B01",
                }],
            }), encoding="utf-8")
            self.assertEqual(recover_staged_operator_decision_audits(path), 0)
            self.assertFalse((path / AUDIT_FILENAME).exists())
            self.assertEqual(build_review_decision_calibration_samples(path), [])

    def test_create_new_player_recovery_accepts_matching_team_and_server_slot(self) -> None:
        features = _live_event(event_id="features")["team_evidence_before"]
        with TemporaryDirectory() as directory:
            path = Path(directory)
            event = prepare_operator_decision_audit_event(
                unit={
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "D1",
                    "detected_team_labels": ["A", "B"],
                    "team_attribution_features": features,
                    "reason_codes": ["team_attribution_coverage_debt"],
                },
                payload={
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "D1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                },
                required=True,
            )
            stage_operator_decision_audit(path, event)
            (path / "reviewed_identity_slot_assignments.json").write_text(json.dumps({
                "reviewed_slots": [{"stable_slot_id": "A01"}],
                "decisions": [{
                    "candidate_subject_id": "subject",
                    "source_ownership_digest": "D1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                    "stable_slot_id": "A01",
                }],
            }), encoding="utf-8")
            self.assertEqual(recover_staged_operator_decision_audits(path), 1)
            audit = json.loads((path / AUDIT_FILENAME).read_text(encoding="utf-8"))
            self.assertTrue(audit["events"][0]["operator_result"]["exact_source_linkage_proven"])
            self.assertEqual(len(build_review_decision_calibration_samples(path)), 1)

    def test_would_auto_assign_uses_real_production_scope_and_safety_gates(self) -> None:
        safe = _live_event(event_id="safe")
        material = _live_event(event_id="material")
        material["source"]["scope_kind"] = "material_continuity"
        segment = _live_event(event_id="segment")
        segment["source"]["scope_kind"] = "segment"
        structural = _live_event(event_id="structural")
        structural["required_reason"].append("duplicate_exact_ownership")
        contradictory = _live_event(event_id="contradictory")
        contradictory["system_path"]["current_decision"] = {"operator_contradiction": True}
        prior_team_decision = _live_event(event_id="prior-team")
        prior_team_decision["system_path"]["current_decision"] = {"action": "assign_team", "team_label": "B"}
        replacement_roster_decision = _live_event(
            event_id="replacement-roster",
            action="assign_roster_player",
            final="B",
        )
        replacement_roster_decision["system_path"]["current_decision"] = {"action": "assign_team", "team_label": "B"}
        replacement_roster_decision["operator_result"]["replaces_prior_operator_decision"] = True
        samples = build_review_decision_calibration_samples(
            document={"events": [
                safe,
                prior_team_decision,
                replacement_roster_decision,
                material,
                segment,
                structural,
                contradictory,
            ]},
            match_id="match",
        )
        self.assertEqual(
            [sample["automatic_policy"]["would_auto_assign"] for sample in samples],
            [True, False, False, False, False, False, False],
        )

    def test_export_is_compact_and_history_exports_zero_labels(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            match_path = root / "matches" / "legacy"
            match_path.mkdir(parents=True)
            (match_path / "match.json").write_text(
                json.dumps({"id": "legacy", "title": "Legacy match"}), encoding="utf-8",
            )
            # A legacy canonical decision has no before-click event, so its
            # compact calibration JSONL must remain empty.
            (match_path / "reviewed_identity_slot_assignments.json").write_text(
                json.dumps({"decisions": [{"candidate_subject_id": "s", "action": "team_unknown"}]}),
                encoding="utf-8",
            )
            result = export_review_decision_calibration_artifacts(match_path, root / "out")
            output = root / "out" / "legacy"
            self.assertEqual(result["calibration_samples"], 0)
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["match.json", BENCHMARK_FILENAME, CALIBRATION_SAMPLES_FILENAME],
            )
            self.assertEqual((output / CALIBRATION_SAMPLES_FILENAME).read_text(encoding="utf-8"), "")
            benchmark = json.loads((output / BENCHMARK_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(benchmark["overall"]["total_operator_decisions"], 1)

    def test_export_writes_one_compact_jsonl_line_for_one_live_decision(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            match_path = root / "matches" / "live"
            match_path.mkdir(parents=True)
            (match_path / "match.json").write_text(
                json.dumps({"id": "live", "title": "Live match"}), encoding="utf-8",
            )
            (match_path / AUDIT_FILENAME).write_text(json.dumps({
                "schema_version": "1.0.0",
                "events": [_live_event(event_id="one-live-event")],
            }), encoding="utf-8")
            result = export_review_decision_calibration_artifacts(match_path, root / "out")
            rows = [
                json.loads(line)
                for line in (root / "out" / "live" / CALIBRATION_SAMPLES_FILENAME).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(result["calibration_samples"], 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_id"], "one-live-event")
            self.assertEqual(rows[0]["source"]["observation_count"], 59)
            self.assertEqual(rows[0]["operator_result"]["effective_team_label"], "B")


if __name__ == "__main__":
    unittest.main()
