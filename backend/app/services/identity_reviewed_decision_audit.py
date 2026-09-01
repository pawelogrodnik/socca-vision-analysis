from __future__ import annotations

"""Append-only Reviewed operator-decision audit and derived benchmark."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
from typing import Any, Mapping
from uuid import uuid4

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_ownership_compact import CompactOwnershipError, decode_pair_runs
from app.services.identity_reviewed_team_attribution_policy import team_evidence_features
from app.services.identity_reviewed_team_attribution_policy import (
    SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
    has_structural_team_attribution_conflict,
    short_track_projection_is_applicable,
    short_track_dominant_team_assignment,
)
from app.services.identity_reviewed_slot_registry import build_reviewed_slot_registry


AUDIT_FILENAME = "review_operator_decision_audit.json"
PENDING_AUDIT_FILENAME = "review_operator_decision_audit_pending.json"
BENCHMARK_FILENAME = "review_decision_benchmark.json"
BACKFILL_REPORT_FILENAME = "review_operator_decision_backfill_report.json"
CALIBRATION_SAMPLES_FILENAME = "review_decision_calibration_samples.jsonl"
AUDIT_SCHEMA_VERSION = "1.0.0"
CALIBRATION_SCHEMA_VERSION = "1.0.0"


def append_operator_decision_audit(
    match_path: Path, *, unit: Mapping[str, Any] | None, payload: Mapping[str, Any],
    required: bool,
) -> dict[str, Any]:
    """Append the before-state and final human choice; never rewrite history."""
    event = prepare_operator_decision_audit_event(
        unit=unit, payload=payload, required=required
    )
    _append_audit_event(match_path, event)
    return event


def prepare_operator_decision_audit_event(
    *,
    unit: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
    required: bool,
    mutation_kind: str = "correction",
) -> dict[str, Any]:
    """Capture a pre-mutation human event without persisting it yet."""
    source = dict(unit or {})
    features = _features_from_unit(source)
    action = str(payload.get("action") or "")
    semantic_result = {
        "action": action,
        "resolution": payload.get("resolution"),
        "team_label": payload.get("team_label"),
        "player_id": payload.get("player_id"),
        "stable_slot_id": payload.get("stable_slot_id") or payload.get("existing_slot_id"),
    }
    previous = source.get("current_decision")
    return {
        "event_id": uuid4().hex,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "EXACT_PERSISTED",
        "mutation": {
            "kind": mutation_kind,
            "semantic": semantic_result,
        },
        "decision_stage": _stage(source, action),
        "required": bool(required),
        "required_reason": list(source.get("reason_codes") or []),
        "scope_policy": source.get("scope_kind"),
        "policy_version": source.get("coverage_policy_version"),
        "source": {
            "candidate_subject_id": source.get("candidate_subject_id") or payload.get("candidate_subject_id"),
            "review_target_id": source.get("review_target_id") or payload.get("review_target_id"),
            "scope_kind": source.get("scope_kind"),
            "continuity_group_id": source.get("continuity_group_id") or payload.get("continuity_group_id"),
            "source_ownership_digest": source.get("source_ownership_digest") or payload.get("source_ownership_digest"),
            "tracklet_ids": list(source.get("tracklet_ids") or []),
            "detected_pair_runs": source.get("detected_pair_runs"),
            "frame_start": source.get("frame_start"),
            "frame_end": source.get("frame_end"),
            "frame_count": source.get("detected_frame_count"),
            "duration_sec": source.get("detected_time_sec"),
            "observation_count": source.get("detected_observation_count"),
        },
        "team_evidence_before": features,
        "reviewed_team_attribution_state": _team_state(source),
        "system_path": {
            "source_team_label": source.get("source_team_label"),
            "effective_team_label": source.get("effective_team_label"),
            "reason_codes": list(source.get("reason_codes") or []),
            "current_decision": source.get("current_decision"),
            "automatic_team_assignment_shadow": source.get("automatic_team_assignment"),
        },
        "operator_result": {
            **semantic_result,
            "replaces_prior_operator_decision": bool(isinstance(previous, Mapping) and previous.get("action")),
        },
    }


def stage_operator_decision_audit(match_path: Path, event: Mapping[str, Any]) -> None:
    """Durably stage an audit event before its canonical human mutation."""
    document = _load_pending_audit(match_path)
    event_id = str(event.get("event_id") or "")
    if event_id and all(str(row.get("event_id") or "") != event_id for row in document["events"]):
        document["events"].append(dict(event))
        write_identity_json_atomic(match_path / PENDING_AUDIT_FILENAME, document)


def commit_staged_operator_decision_audit(match_path: Path, event_id: str) -> dict[str, Any] | None:
    """Promote one staged event only after its canonical mutation is proven."""
    pending = _load_pending_audit(match_path)
    event = next((row for row in pending["events"] if str(row.get("event_id") or "") == event_id), None)
    if not isinstance(event, dict):
        return None
    canonical = _canonical_mutation(match_path, event)
    if canonical is None:
        return None
    event = _with_canonical_operator_result(match_path, event, canonical)
    _append_audit_event(match_path, event)
    pending["events"] = [row for row in pending["events"] if str(row.get("event_id") or "") != event_id]
    write_identity_json_atomic(match_path / PENDING_AUDIT_FILENAME, pending)
    return event


def recover_staged_operator_decision_audits(
    match_path: Path, *, event_id: str | None = None,
) -> int:
    """Complete prior successful mutations whose audit write was interrupted."""
    recovered = 0
    for event in list(_load_pending_audit(match_path)["events"]):
        if event_id is not None and str(event.get("event_id") or "") != event_id:
            continue
        if commit_staged_operator_decision_audit(match_path, str(event.get("event_id") or "")):
            recovered += 1
    return recovered


def discard_staged_operator_decision_audit(match_path: Path, event_id: str) -> None:
    pending = _load_pending_audit(match_path)
    retained = [row for row in pending["events"] if str(row.get("event_id") or "") != event_id]
    if len(retained) != len(pending["events"]):
        pending["events"] = retained
        write_identity_json_atomic(match_path / PENDING_AUDIT_FILENAME, pending)


def _append_audit_event(match_path: Path, event: Mapping[str, Any]) -> None:
    document = _load_audit(match_path)
    event_id = str(event.get("event_id") or "")
    if event_id and any(str(row.get("event_id") or "") == event_id for row in document["events"]):
        return
    document["events"].append(dict(event))
    write_identity_json_atomic(match_path / AUDIT_FILENAME, document)
    # A benchmark can always be regenerated from the append-only event log;
    # never let its failure erase or roll back the human decision record.
    try:
        build_review_decision_benchmark(match_path, document=document)
    except OSError:
        pass


def canonical_mutation_persisted(match_path: Path, event: Mapping[str, Any]) -> bool:
    """Prove a staged event against the current canonical decision stores.

    A pending file is a write-ahead record, not operator truth.  It may only
    become append-only audit history after the matching canonical mutation is
    visible.  This also makes a scan during an unrelated retry safe.
    """
    return _canonical_mutation(match_path, event) is not None


def _canonical_mutation(match_path: Path, event: Mapping[str, Any]) -> dict[str, Any] | None:
    mutation = event.get("mutation") if isinstance(event.get("mutation"), Mapping) else {}
    kind = str(mutation.get("kind") or "correction")
    if kind in {"mixed_resolution", "temporal_split"}:
        return _persisted_split_resolution_match(match_path, event)
    return _persisted_correction_match(match_path, event)


def _persisted_correction_match(match_path: Path, event: Mapping[str, Any]) -> dict[str, Any] | None:
    expected = event.get("operator_result") if isinstance(event.get("operator_result"), Mapping) else {}
    action = str(expected.get("action") or "")
    source = event.get("source") if isinstance(event.get("source"), Mapping) else {}
    if action == "mixed_players":
        return next((
            row for row in _canonical_rows(match_path, "reviewed_identity_mixed_players.json", "cases")
            if _source_matches_event(row, source)
            and str(row.get("original_issue") or "") in {"mixed_players", "inline_temporal_split"}
        ), None)
    for filename, key in (
        ("reviewed_identity_slot_assignments.json", "decisions"),
        ("reviewed_identity_segment_decisions.json", "decisions"),
        ("reviewed_identity_material_continuity_decisions.json", "decisions"),
        ("identity_roster_subject_review_decisions_shadow.json", "decisions"),
    ):
        for row in _canonical_rows(match_path, filename, key):
            if _source_matches_event(row, source) and _semantic_result_matches(row, expected):
                return row
    return None


def _persisted_split_resolution_match(match_path: Path, event: Mapping[str, Any]) -> dict[str, Any] | None:
    source = event.get("source") if isinstance(event.get("source"), Mapping) else {}
    result = event.get("operator_result") if isinstance(event.get("operator_result"), Mapping) else {}
    expected_resolution = str(result.get("resolution") or "split")
    for row in _canonical_rows(match_path, "reviewed_identity_mixed_players.json", "cases"):
        if not _source_matches_event(row, source):
            continue
        status = str(row.get("resolution_status") or "")
        if expected_resolution == "unresolved_complex_mix":
            return row if status == "unresolved_complex_mix" else None
        if expected_resolution == "concurrent_lanes":
            return row if status == "resolved" and str(row.get("resolution_model") or "") == "concurrent_lanes" else None
        if expected_resolution == "split":
            return row if status == "resolved" and str(row.get("resolution_model") or "") != "concurrent_lanes" else None
    return None


def _with_canonical_operator_result(
    match_path: Path, event: Mapping[str, Any], canonical: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a live event to the persisted decision, never browser input."""
    completed = dict(event)
    result = dict(completed.get("operator_result") or {})
    action = str(canonical.get("action") or result.get("action") or "")
    result["action"] = action
    for key in ("team_label", "player_id", "stable_slot_id"):
        if canonical.get(key) is not None:
            result[key] = canonical.get(key)
    result["effective_team_label"] = _effective_team_label(match_path, canonical, action)
    result["exact_source_linkage_proven"] = _canonical_source_linkage_proven(
        event, canonical,
    )
    result["canonical_result_verified"] = True
    completed["operator_result"] = result
    return completed


def _canonical_source_linkage_proven(
    event: Mapping[str, Any], canonical: Mapping[str, Any],
) -> bool:
    """Require a persisted canonical source digest for calibration truth.

    Older whole-subject rows may still be valid audit history without this
    field. They must not be reclassified as exact calibration ground truth.
    """
    source = event.get("source") if isinstance(event.get("source"), Mapping) else {}
    event_digest = str(source.get("source_ownership_digest") or "")
    canonical_source = (
        canonical.get("source")
        if isinstance(canonical.get("source"), Mapping)
        else canonical
    )
    canonical_digest = str(
        canonical.get("source_ownership_digest")
        or canonical.get("source_subject_digest")
        or canonical_source.get("source_ownership_digest")
        or canonical_source.get("source_subject_digest")
        or ""
    )
    return bool(event_digest and canonical_digest and event_digest == canonical_digest)


def _effective_team_label(
    match_path: Path, canonical: Mapping[str, Any], action: str,
) -> str | None:
    # Terminal semantics take precedence over stale incidental team fields.
    if action == "team_unknown":
        return "U"
    if action in {"referee", "false_detection"}:
        return action
    if action == "assign_roster_player":
        player_id = str(canonical.get("player_id") or "")
        try:
            match = json.loads((match_path / "match.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            match = {}
        for index, team in enumerate(match.get("teams") or []):
            label = "A" if index == 0 else "B" if index == 1 else None
            if label and any(str(player.get("id") or "") == player_id for player in team.get("players") or []):
                return label
        return None
    if action in {"assign_existing_slot", "create_new_stable_player"}:
        slot_id = str(canonical.get("stable_slot_id") or "")
        slot = build_reviewed_slot_registry(match_path).get(slot_id)
        team = str((slot or {}).get("team_label") or "").upper()
        return team if team in {"A", "B"} else None
    if action == "assign_team":
        team = str(canonical.get("team_label") or "").upper()
        return team if team in {"A", "B"} else None
    return None


def _canonical_rows(match_path: Path, filename: str, key: str) -> list[dict[str, Any]]:
    try:
        document = json.loads((match_path / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in document.get(key) or [] if isinstance(row, Mapping)]


def _source_matches_event(row: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    row_source = row.get("source") if isinstance(row.get("source"), Mapping) else row
    event_subject = str(source.get("candidate_subject_id") or "")
    event_target = str(source.get("review_target_id") or "")
    event_group = str(source.get("continuity_group_id") or "")
    event_digest = str(source.get("source_ownership_digest") or "")
    if event_subject and str(row.get("candidate_subject_id") or row_source.get("candidate_subject_id") or "") != event_subject:
        return False
    if event_target and str(row.get("review_target_id") or row_source.get("review_target_id") or row.get("case_id") or "") != event_target:
        return False
    if event_group and str(row.get("continuity_group_id") or row_source.get("continuity_group_id") or "") != event_group:
        return False
    if event_digest:
        row_digest = str(
            row.get("source_ownership_digest")
            or row.get("source_subject_digest")
            or row_source.get("source_ownership_digest")
            or ""
        )
        # Whole-subject slot stores intentionally predate exact-source digest
        # persistence; their canonical key is the unique subject decision.
        if row_digest and row_digest != event_digest:
            return False
    return bool(event_subject or event_target or event_group)


def _semantic_result_matches(row: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    action = str(expected.get("action") or "")
    row_action = str(row.get("action") or "")
    if action and row_action != action:
        return False
    # Roster players and existing slots derive team from their canonical
    # identity key.  A newly created player has no client-known slot yet, so
    # its selected canonical team remains part of the semantic decision.
    keys = ["team_label", "player_id", "stable_slot_id"]
    if action in {"assign_roster_player", "assign_existing_slot"}:
        keys.remove("team_label")
    for key in keys:
        expected_value = expected.get(key)
        if expected_value is None or expected_value == "":
            continue
        row_value = row.get(key)
        if row_value is None and key == "stable_slot_id":
            row_value = row.get("existing_slot_id")
        if str(row_value or "") != str(expected_value):
            return False
    return True


def build_review_decision_benchmark(
    match_path: Path,
    *,
    document: dict[str, Any] | None = None,
    historical_provenance: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    events = list((document or _load_audit(match_path)).get("events") or [])
    durations = [float(((event.get("source") or {}).get("duration_sec") or 0)) for event in events]
    observations = [int(((event.get("source") or {}).get("observation_count") or 0)) for event in events]
    actions = Counter(str(((event.get("operator_result") or {}).get("action") or "unknown")) for event in events)
    stages = Counter(str(event.get("decision_stage") or "unknown") for event in events)
    team_events = [event for event in events if event.get("decision_stage") == "team_attribution"]
    samples = build_review_decision_calibration_samples(
        document={"events": events}, match_id=match_path.name,
    )
    dominant = [sample for sample in samples if sample["team_evidence_before"]["dominant_team"] in {"A", "B"}]
    agreements = sum(_sample_outcome(sample) == "agreed" for sample in dominant)
    overrides = sum(_sample_outcome(sample) == "overrode" for sample in dominant)
    benchmark = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": {
            "total_operator_decisions": len(events),
            "mandatory_decisions": sum(event.get("required") is True for event in events),
            "optional_decisions": sum(event.get("required") is False for event in events),
            "requiredness_unavailable": sum(event.get("required") is None for event in events),
            "decision_counts_by_stage": dict(sorted(stages.items())),
            "decision_counts_by_action": dict(sorted(actions.items())),
            "operator_result_distribution": _operator_result_distribution(events),
            "average_source_duration_sec": _mean(durations),
            "median_source_duration_sec": _median(durations),
            "average_observation_count": _mean(observations),
            "median_observation_count": _median(observations),
        },
        "team_attribution": {
            "operator_decisions": len(team_events),
            "eligible_calibration_samples": len(samples),
            "eligible_dominant_signal_cases": len(dominant),
            "dominant_upstream_signal_cases": len(dominant),
            "operator_agreed_with_dominant_signal": agreements,
            "operator_overrode_dominant_signal": overrides,
            "operator_non_binary_final_disposition": sum(
                _sample_outcome(sample) == "non_binary" for sample in dominant
            ),
            "agreement_by_dominant_ratio": _bucket_counts(dominant, _ratio_bucket),
            "agreement_by_source_length": _bucket_counts(dominant, _length_bucket),
            "agreement_by_switch_count": _bucket_counts(dominant, _switch_bucket),
            "agreement_by_minority_run": _bucket_counts(dominant, _minority_run_bucket),
            "short_high_dominance": _short_high(dominant),
        },
        "decision_paths": dict(sorted(Counter(_path(event) for event in events).items())),
    }
    if historical_provenance is not None:
        benchmark["historical_backfill"] = {
            "exact_source_linkable_count": int(historical_provenance.get("exact_source_linkable_count") or 0),
            "reconstructed_team_feature_count": int(historical_provenance.get("reconstructed_team_feature_count") or 0),
            "unavailable_team_feature_count": int(historical_provenance.get("unavailable_team_feature_count") or 0),
        }
        benchmark["team_attribution"]["unavailable_team_features"] = int(
            historical_provenance.get("unavailable_team_feature_count") or 0
        )
    write_identity_json_atomic(match_path / BENCHMARK_FILENAME, benchmark)
    return benchmark


def build_review_decision_calibration_samples(
    match_path: Path | None = None,
    *,
    document: Mapping[str, Any] | None = None,
    match_id: str | None = None,
) -> list[dict[str, Any]]:
    """Project one exact live human decision into at most one compact sample."""
    if document is None:
        if match_path is None:
            raise ValueError("match_path or document is required")
        document = _load_audit(match_path)
    resolved_match_id = match_id or (match_path.name if match_path is not None else "")
    return [
        sample
        for event in document.get("events") or []
        if isinstance(event, Mapping)
        for sample in [_calibration_sample(event, resolved_match_id)]
        if sample is not None
    ]


def export_review_decision_calibration_artifacts(
    match_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Write only the small, Git-safe benchmark and JSONL calibration output."""
    try:
        match = json.loads((match_path / "match.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        match = {}
    match_id = str(match.get("id") or match_path.name)
    output = output_root / match_id
    output.mkdir(parents=True, exist_ok=True)
    audit = _load_audit(match_path)
    # Older matches have no live before-click audit.  Keep their benchmark
    # provenance honest through the existing read-only backfill, while their
    # calibration export remains empty rather than fabricated from history.
    if audit["events"]:
        benchmark = build_review_decision_benchmark(match_path, document=audit)
    else:
        backfill_review_decision_audit(match_path)
        benchmark = json.loads(
            (match_path / BENCHMARK_FILENAME).read_text(encoding="utf-8")
        )
    samples = build_review_decision_calibration_samples(document=audit, match_id=match_id)
    write_identity_json_atomic(
        output / "match.json",
        {"match_id": match_id, "title": match.get("title") or match_id},
    )
    write_identity_json_atomic(output / BENCHMARK_FILENAME, benchmark)
    _write_jsonl_atomic(output / CALIBRATION_SAMPLES_FILENAME, samples)
    return {
        "match_id": match_id,
        "output_path": str(output),
        "calibration_samples": len(samples),
    }


def _calibration_sample(event: Mapping[str, Any], match_id: str) -> dict[str, Any] | None:
    source = event.get("source") if isinstance(event.get("source"), Mapping) else {}
    evidence = event.get("team_evidence_before") if isinstance(event.get("team_evidence_before"), Mapping) else {}
    result = event.get("operator_result") if isinstance(event.get("operator_result"), Mapping) else {}
    if not _eligible_calibration_event(event, source, evidence, result):
        return None
    dominant = str(evidence.get("dominant_team") or "").upper()
    features = {
        key: evidence.get(key)
        for key in (
            "A_observations", "B_observations", "U_observations",
            "known_team_observations", "dominant_team", "dominant_ratio",
            "team_switch_count", "longest_A_run", "longest_B_run",
        )
    }
    minority = "B" if dominant == "A" else "A" if dominant == "B" else None
    features["longest_minority_run"] = int(
        evidence.get(f"longest_{minority}_run") or 0
    ) if minority else 0
    structural = _is_structural_event(event)
    contradiction = _is_operator_contradiction(event)
    would_auto = (
        short_track_projection_is_applicable(
            source.get("scope_kind"),
            excluded_from_projection=_has_existing_operator_decision(event),
            structural_conflict=structural,
            operator_contradiction=contradiction,
        )
        and short_track_dominant_team_assignment(
            evidence,
            structural_conflict=structural,
            operator_contradiction=contradiction,
        ) is not None
    )
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "match_id": match_id,
        "event_id": str(event.get("event_id") or ""),
        "provenance": str(event.get("provenance") or ""),
        "source": {
            "scope_kind": source.get("scope_kind"),
            "source_frame_count": int(evidence.get("source_frame_count") or source.get("frame_count") or 0),
            "observation_count": int(source.get("observation_count") or 0),
            "duration_sec": source.get("duration_sec"),
        },
        "team_evidence_before": features,
        "review_context": {
            "required": event.get("required"),
            "structural_conflict": structural,
            "operator_contradiction": contradiction,
        },
        "operator_result": {
            "action": result.get("action"),
            "effective_team_label": result.get("effective_team_label"),
        },
        "automatic_policy": {
            "policy_version": SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
            "would_auto_assign": would_auto,
        },
    }


def _eligible_calibration_event(
    event: Mapping[str, Any], source: Mapping[str, Any], evidence: Mapping[str, Any],
    result: Mapping[str, Any],
) -> bool:
    if str(event.get("provenance") or "") != "EXACT_PERSISTED":
        return False
    if not bool(result.get("canonical_result_verified")):
        return False
    if not bool(result.get("exact_source_linkage_proven")):
        return False
    if str(evidence.get("provenance") or "") != "EXACT_PRE_DECISION":
        return False
    if not str(source.get("source_ownership_digest") or ""):
        return False
    if not str(source.get("candidate_subject_id") or source.get("review_target_id") or ""):
        return False
    if str(result.get("effective_team_label") or "") not in {"A", "B", "U", "referee", "false_detection"}:
        return False
    return _represented_team_attribution_question(event, evidence)


def _represented_team_attribution_question(event: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    state = str(event.get("reviewed_team_attribution_state") or "")
    # A diagnostic reason or noisy upstream A/B votes do not turn an already
    # certain player-identity card into a team-calibration question.  The
    # frozen Review snapshot is the authority for what the operator was asked
    # to decide at click time.
    return state in {"unknown", "cross_team"}


def _is_structural_event(event: Mapping[str, Any]) -> bool:
    source = event.get("source") if isinstance(event.get("source"), Mapping) else {}
    return has_structural_team_attribution_conflict({
        "scope_kind": source.get("scope_kind"),
        "reason_codes": event.get("required_reason") or [],
    })


def _is_operator_contradiction(event: Mapping[str, Any]) -> bool:
    path = event.get("system_path") if isinstance(event.get("system_path"), Mapping) else {}
    current = path.get("current_decision") if isinstance(path.get("current_decision"), Mapping) else {}
    return bool(current.get("operator_contradiction"))


def _has_existing_operator_decision(event: Mapping[str, Any]) -> bool:
    """Mirror the snapshot builder's exclusion of already reviewed subjects."""
    path = event.get("system_path") if isinstance(event.get("system_path"), Mapping) else {}
    current = path.get("current_decision") if isinstance(path.get("current_decision"), Mapping) else {}
    return bool(current.get("action"))


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)


def backfill_review_decision_audit(match_path: Path) -> dict[str, Any]:
    """Read legacy stores without changing decisions; emit honest provenance."""
    decisions: list[dict[str, Any]] = []
    unavailable = 0
    reconstructed = 0
    reconstruction_context = _reconstruction_context(match_path)
    for filename in (
        "reviewed_identity_slot_assignments.json",
        "reviewed_identity_segment_decisions.json",
        "reviewed_identity_material_continuity_decisions.json",
        "reviewed_identity_mixed_players.json",
    ):
        path = match_path / filename
        if not path.exists():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            unavailable += 1
            continue
        rows = document.get("decisions") or document.get("cases") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            exact_pairs = _exact_source_pairs(row)
            features = _reconstruct_team_features(row, reconstruction_context, exact_pairs)
            feature_provenance = "RECONSTRUCTED" if features is not None else "UNAVAILABLE"
            reconstructed += int(features is not None)
            decisions.append({
                "source_file": filename,
                "provenance": "EXACT_PERSISTED",
                "decision_record_provenance": "EXACT_PERSISTED",
                "decision": row,
                "exact_source_linkage": exact_pairs is not None and _current_source_digest_matches(row, reconstruction_context, exact_pairs),
                "team_features_provenance": feature_provenance,
                "team_features": features,
            })
    report = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "decisions_recovered": len(decisions),
        "exact_source_linkage": sum(bool(row["exact_source_linkage"]) for row in decisions),
        "reconstructed_team_features": reconstructed,
        "unavailable_records": unavailable,
        "records": decisions,
    }
    write_identity_json_atomic(match_path / BACKFILL_REPORT_FILENAME, report)
    historical_events = [_historical_event(record) for record in decisions]
    build_review_decision_benchmark(
        match_path,
        document={"schema_version": AUDIT_SCHEMA_VERSION, "events": historical_events},
        historical_provenance={
            "exact_source_linkable_count": report["exact_source_linkage"],
            "reconstructed_team_feature_count": report["reconstructed_team_features"],
            "unavailable_team_feature_count": sum(
                1 for record in decisions
                if record["team_features_provenance"] == "UNAVAILABLE"
            ),
        },
    )
    return report


def _reconstruction_context(match_path: Path) -> dict[str, Any] | None:
    try:
        tracklets_document = json.loads((match_path / "tracklets.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        candidates = json.loads((match_path / "identity_candidate_shadow.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        candidates = {}
    subjects = {
        str(row.get("candidate_subject_id") or ""): {str(value) for value in row.get("tracklet_ids") or []}
        for row in candidates.get("subjects") or [] if isinstance(row, Mapping)
    }
    observations = {
        str(tracklet.get("tracklet_id") or ""): [
            {"tracklet_id": str(tracklet.get("tracklet_id") or ""), "frame": position.get("frame"), "team_label": tracklet.get("team_label")}
            for position in tracklet.get("positions_m") or [] if isinstance(position, Mapping)
        ]
        for tracklet in tracklets_document.get("tracklets") or [] if isinstance(tracklet, Mapping)
    }
    return {"subjects": subjects, "observations": observations}


def _exact_source_pairs(decision: Mapping[str, Any]) -> list[tuple[str, int]] | None:
    """Return persisted exact ownership only; never infer a whole subject."""
    source = decision.get("source") if isinstance(decision.get("source"), Mapping) else decision
    raw_pairs = source.get("detected_pairs") or source.get("observation_pairs")
    if isinstance(raw_pairs, list):
        pairs = sorted({
            (str(row[0]), int(row[1]))
            for row in raw_pairs
            if isinstance(row, (list, tuple)) and len(row) >= 2
        })
    elif isinstance(source.get("detected_pair_runs"), dict):
        try:
            pairs = decode_pair_runs(source["detected_pair_runs"])
        except CompactOwnershipError:
            return None
    else:
        return None
    return pairs or None


def _current_source_digest_matches(
    decision: Mapping[str, Any], context: Mapping[str, Any] | None,
    pairs: list[tuple[str, int]] | None,
) -> bool:
    if context is None or not pairs:
        return False
    source = decision.get("source") if isinstance(decision.get("source"), Mapping) else decision
    digest = str(source.get("source_ownership_digest") or decision.get("source_ownership_digest") or "")
    candidate_subject_id = str(source.get("candidate_subject_id") or decision.get("candidate_subject_id") or "")
    if not digest or not candidate_subject_id:
        return False
    tracklet_ids = sorted({tracklet_id for tracklet_id, _frame in pairs})
    observations = [{"tracklet_id": tracklet_id, "frame": frame} for tracklet_id, frame in pairs]
    return digest in {
        canonical_digest({"candidate_subject_id": candidate_subject_id, "pairs": pairs}),
        canonical_digest({
            "candidate_subject_id": candidate_subject_id,
            "tracklet_ids": tracklet_ids,
            "observations": observations,
        }),
    }


def _reconstruct_team_features(
    decision: Mapping[str, Any], context: Mapping[str, Any] | None,
    pairs: list[tuple[str, int]] | None,
) -> dict[str, Any] | None:
    """Recompute only immutable upstream label features; never relabel history."""
    if context is None:
        return None
    if not _current_source_digest_matches(decision, context, pairs):
        return None
    assert pairs is not None
    requested = set(pairs)
    observations = [
        row for tracklet_id, frame in requested
        for row in (context.get("observations") or {}).get(tracklet_id, [])
        if int(row.get("frame") if row.get("frame") is not None else -1) == frame
    ]
    return team_evidence_features(observations) if len(observations) == len(requested) else None


def _load_audit(match_path: Path) -> dict[str, Any]:
    path = match_path / AUDIT_FILENAME
    if not path.exists():
        return {"schema_version": AUDIT_SCHEMA_VERSION, "events": []}
    document = json.loads(path.read_text(encoding="utf-8"))
    return document if isinstance(document, dict) and isinstance(document.get("events"), list) else {"schema_version": AUDIT_SCHEMA_VERSION, "events": []}


def _load_pending_audit(match_path: Path) -> dict[str, Any]:
    path = match_path / PENDING_AUDIT_FILENAME
    if not path.exists():
        return {"schema_version": AUDIT_SCHEMA_VERSION, "events": []}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": AUDIT_SCHEMA_VERSION, "events": []}
    return document if isinstance(document, dict) and isinstance(document.get("events"), list) else {"schema_version": AUDIT_SCHEMA_VERSION, "events": []}


def _features_from_unit(unit: Mapping[str, Any]) -> dict[str, Any]:
    features = unit.get("team_attribution_features")
    source_identity = bool(
        str(unit.get("source_ownership_digest") or "")
        and str(unit.get("candidate_subject_id") or unit.get("review_target_id") or "")
    )
    if isinstance(features, Mapping) and source_identity:
        return {"provenance": "EXACT_PRE_DECISION", **dict(features)}
    return {"provenance": "UNAVAILABLE", **team_evidence_features([])}


def _stage(unit: Mapping[str, Any], action: str) -> str:
    if action in {"temporal_split", "split", "concurrent_lanes"}:
        return "temporal_split"
    if action == "mixed_players" or str(unit.get("scope_kind") or "") == "mixed":
        return "mixed"
    if action in {"referee", "false_detection", "team_unknown", "assign_team"}:
        return "team_attribution"
    return "player_identity"


def _team_state(unit: Mapping[str, Any]) -> str:
    persisted = str(unit.get("reviewed_team_attribution_state") or "")
    if persisted in {"unknown", "cross_team", "certain_A", "certain_B"}:
        return persisted
    labels = {str(value).upper() for value in unit.get("detected_team_labels") or []}
    return "cross_team" if labels >= {"A", "B"} else "certain" if labels & {"A", "B"} else "unknown"


def _mean(values: list[float | int]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _median(values: list[float | int]) -> float:
    return round(float(median(values)), 3) if values else 0.0


def _ratio_bucket(event: dict[str, Any]) -> str:
    value = float(((event.get("team_evidence_before") or {}).get("dominant_ratio") or 0))
    for lower, label in ((.95, "95-100%"), (.90, "90-95%"), (.85, "85-90%"), (.80, "80-85%"), (.70, "70-80%"), (.60, "60-70%")):
        if value >= lower:
            return label
    return "<60%"


def _length_bucket(event: dict[str, Any]) -> str:
    value = int(
        ((event.get("team_evidence_before") or {}).get("source_frame_count")
        or (event.get("source") or {}).get("source_frame_count")
        or (event.get("source") or {}).get("frame_count")
        or 0)
    )
    for maximum, label in ((30, "<=30"), (60, "<=60"), (100, "<=100"), (200, "<=200")):
        if value <= maximum:
            return label
    return ">200"


def _switch_bucket(event: dict[str, Any]) -> str:
    value = int(((event.get("team_evidence_before") or {}).get("team_switch_count") or 0))
    if value == 0:
        return "0"
    if value <= 2:
        return "1-2"
    if value <= 6:
        return "3-6"
    return ">6"


def _minority_run_bucket(event: dict[str, Any]) -> str:
    evidence = event.get("team_evidence_before") or {}
    dominant = str(evidence.get("dominant_team") or "")
    minority = "B" if dominant == "A" else "A" if dominant == "B" else ""
    value = int(evidence.get("longest_minority_run") or evidence.get(f"longest_{minority}_run") or 0)
    if value == 0:
        return "0"
    if value <= 2:
        return "1-2"
    if value <= 8:
        return "3-8"
    return ">8"


def _bucket_counts(events: list[dict[str, Any]], bucket) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for event in events:
        key = bucket(event)
        row = result.setdefault(key, {"total": 0, "agreed": 0, "overrode": 0, "non_binary": 0})
        row["total"] += 1
        row[_sample_outcome(event)] += 1
    return result


def _short_high(events: list[dict[str, Any]]) -> dict[str, int]:
    selected = [event for event in events if _length_bucket(event) in {"<=30", "<=60", "<=100", "<=200"} and float((event.get("team_evidence_before") or {}).get("dominant_ratio") or 0) >= .90]
    agreed = sum(_sample_outcome(event) == "agreed" for event in selected)
    overrides = sum(_sample_outcome(event) == "overrode" for event in selected)
    return {"total": len(selected), "agreed": agreed, "overrode": overrides, "non_binary": len(selected) - agreed - overrides}


def _sample_outcome(sample: Mapping[str, Any]) -> str:
    evidence = sample.get("team_evidence_before") if isinstance(sample.get("team_evidence_before"), Mapping) else {}
    result = sample.get("operator_result") if isinstance(sample.get("operator_result"), Mapping) else {}
    dominant = str(evidence.get("dominant_team") or "")
    final = str(result.get("effective_team_label") or "")
    if final not in {"A", "B"}:
        return "non_binary"
    return "agreed" if final == dominant else "overrode"


def _path(event: dict[str, Any]) -> str:
    state = str(event.get("reviewed_team_attribution_state") or "unknown")
    action = str((event.get("operator_result") or {}).get("action") or "unknown")
    return f"{state}->{action}"


def _operator_result_distribution(events: list[dict[str, Any]]) -> dict[str, int]:
    """Report operator choices without inventing unavailable upstream features."""
    counts = Counter({
        "team_A": 0,
        "team_B": 0,
        "unknown": 0,
        "referee": 0,
        "false_detection": 0,
        "other": 0,
    })
    for event in events:
        result = event.get("operator_result") or {}
        action = str(result.get("action") or "")
        effective = str(result.get("effective_team_label") or result.get("team_label") or "").upper()
        if action == "referee" or effective == "REFEREE":
            counts["referee"] += 1
        elif action == "false_detection" or effective == "FALSE_DETECTION":
            counts["false_detection"] += 1
        elif effective == "A":
            counts["team_A"] += 1
        elif effective == "B":
            counts["team_B"] += 1
        elif action in {"team_unknown", "unresolved"} or effective == "U":
            counts["unknown"] += 1
        else:
            counts["other"] += 1
    return dict(counts)


def _historical_event(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project a persisted decision into an auditable historical benchmark row.

    This projection deliberately carries only exact persisted source fields. It
    never promotes a legacy whole-subject decision into synthetic evidence.
    """
    decision = record.get("decision") if isinstance(record.get("decision"), Mapping) else {}
    source = decision.get("source") if isinstance(decision.get("source"), Mapping) else decision
    action = str(decision.get("action") or decision.get("resolution") or decision.get("decision") or "unknown")
    features = record.get("team_features") if isinstance(record.get("team_features"), Mapping) else {
        "provenance": "UNAVAILABLE",
        **team_evidence_features([]),
    }
    scope_kind = str(source.get("scope_kind") or "")
    stage = _historical_stage(decision, action)
    return {
        "event_id": f"historical:{record.get('source_file')}:{canonical_digest(decision)}",
        "provenance": "HISTORICAL_BACKFILL",
        "history_availability": "recovered_persisted_decision_record",
        "decision_stage": stage,
        # Legacy stores preserve current canonical state, not the queue
        # classification at the original operator click.
        "required": None,
        "scope_policy": scope_kind or None,
        "source": {
            "candidate_subject_id": source.get("candidate_subject_id"),
            "review_target_id": source.get("review_target_id"),
            "scope_kind": scope_kind or None,
            "source_ownership_digest": source.get("source_ownership_digest"),
            "observation_count": source.get("detected_observation_count") or source.get("observation_count"),
            "duration_sec": source.get("detected_time_sec") or source.get("duration_sec"),
        },
        "team_evidence_before": dict(features),
        "operator_result": {
            "action": action,
            "resolution": decision.get("resolution"),
            "team_label": decision.get("team_label"),
            "player_id": decision.get("player_id"),
            "effective_team_label": _historical_effective_team_label(decision, action),
        },
    }


def _historical_effective_team_label(decision: Mapping[str, Any], action: str) -> str | None:
    """Use only final semantic fields that legacy canonical rows actually kept."""
    if action == "team_unknown":
        return "U"
    if action in {"referee", "false_detection"}:
        return action
    if action in {
        "assign_team", "assign_roster_player", "assign_existing_slot",
        "create_new_stable_player",
    }:
        label = str(decision.get("team_label") or "").upper()
        return label if label in {"A", "B"} else None
    return None


def _historical_stage(decision: Mapping[str, Any], action: str) -> str:
    if action in {"assign_team", "team_unknown", "referee", "false_detection"}:
        return "team_attribution"
    if action in {
        "assign_roster_player",
        "assign_existing_slot",
        "create_new_stable_player",
    }:
        return "player_identity"
    if action in {"mixed_players", "unresolved_complex_mix"}:
        return "mixed"
    if (
        str(decision.get("original_issue") or "") == "inline_temporal_split"
        or str(decision.get("resolution_model") or "") == "concurrent_lanes"
        or str(decision.get("resolution_status") or "") in {
            "resolved",
            "unresolved_complex_mix",
        }
    ):
        return "temporal_split"
    # A canonical child in the segment file can be an ordinary correction.
    # Preserve its scope separately and do not infer a parent split event.
    return "unknown"
