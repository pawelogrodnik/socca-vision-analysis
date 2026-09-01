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


AUDIT_FILENAME = "review_operator_decision_audit.json"
BENCHMARK_FILENAME = "review_decision_benchmark.json"
BACKFILL_REPORT_FILENAME = "review_operator_decision_backfill_report.json"
AUDIT_SCHEMA_VERSION = "1.0.0"


def append_operator_decision_audit(
    match_path: Path, *, unit: Mapping[str, Any] | None, payload: Mapping[str, Any],
    required: bool,
) -> dict[str, Any]:
    """Append the before-state and final human choice; never rewrite history."""
    document = _load_audit(match_path)
    source = dict(unit or {})
    features = _features_from_unit(source)
    action = str(payload.get("action") or "")
    previous = source.get("current_decision")
    event = {
        "event_id": uuid4().hex,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "EXACT_PERSISTED",
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
            "automatic_team_assignment_shadow": source.get("automatic_team_assignment"),
        },
        "operator_result": {
            "action": action,
            "resolution": payload.get("resolution"),
            "team_label": payload.get("team_label"),
            "player_id": payload.get("player_id"),
            "replaces_prior_operator_decision": bool(isinstance(previous, Mapping) and previous.get("action")),
        },
    }
    document["events"].append(event)
    write_identity_json_atomic(match_path / AUDIT_FILENAME, document)
    build_review_decision_benchmark(match_path, document=document)
    return event


def build_review_decision_benchmark(match_path: Path, *, document: dict[str, Any] | None = None) -> dict[str, Any]:
    events = list((document or _load_audit(match_path)).get("events") or [])
    durations = [float(((event.get("source") or {}).get("duration_sec") or 0)) for event in events]
    observations = [int(((event.get("source") or {}).get("observation_count") or 0)) for event in events]
    actions = Counter(str(((event.get("operator_result") or {}).get("action") or "unknown")) for event in events)
    stages = Counter(str(event.get("decision_stage") or "unknown") for event in events)
    team_events = [event for event in events if event.get("decision_stage") == "team_attribution"]
    dominant = [event for event in team_events if ((event.get("team_evidence_before") or {}).get("dominant_team") in {"A", "B"})]
    matched = sum(1 for event in dominant if str((event.get("operator_result") or {}).get("team_label") or "") == (event.get("team_evidence_before") or {}).get("dominant_team"))
    benchmark = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": {
            "total_operator_decisions": len(events),
            "mandatory_decisions": sum(bool(event.get("required")) for event in events),
            "optional_decisions": sum(not bool(event.get("required")) for event in events),
            "decision_counts_by_stage": dict(sorted(stages.items())),
            "decision_counts_by_action": dict(sorted(actions.items())),
            "average_source_duration_sec": _mean(durations),
            "median_source_duration_sec": _median(durations),
            "average_observation_count": _mean(observations),
            "median_observation_count": _median(observations),
        },
        "team_attribution": {
            "operator_decisions": len(team_events),
            "dominant_upstream_signal_cases": len(dominant),
            "operator_agreed_with_dominant_signal": matched,
            "operator_overrode_dominant_signal": len(dominant) - matched,
            "agreement_by_dominant_ratio": _bucket_counts(dominant, _ratio_bucket),
            "agreement_by_source_length": _bucket_counts(dominant, _length_bucket),
            "short_high_dominance": _short_high(dominant),
        },
        "decision_paths": dict(sorted(Counter(_path(event) for event in events).items())),
    }
    write_identity_json_atomic(match_path / BENCHMARK_FILENAME, benchmark)
    return benchmark


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


def _features_from_unit(unit: Mapping[str, Any]) -> dict[str, Any]:
    features = unit.get("team_attribution_features")
    if isinstance(features, dict):
        return dict(features)
    return {"provenance": "UNAVAILABLE", **team_evidence_features([])}


def _stage(unit: Mapping[str, Any], action: str) -> str:
    if action == "mixed_players" or str(unit.get("scope_kind") or "") == "mixed":
        return "mixed"
    if action in {"referee", "false_detection", "team_unknown", "assign_team"}:
        return "team_attribution"
    return "player_identity"


def _team_state(unit: Mapping[str, Any]) -> str:
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
    value = int(((event.get("team_evidence_before") or {}).get("source_frame_count") or 0))
    for maximum, label in ((30, "<=30"), (60, "<=60"), (100, "<=100"), (200, "<=200")):
        if value <= maximum:
            return label
    return ">200"


def _bucket_counts(events: list[dict[str, Any]], bucket) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for event in events:
        key = bucket(event)
        row = result.setdefault(key, {"total": 0, "agreed": 0, "overrode": 0})
        row["total"] += 1
        agreed = str((event.get("operator_result") or {}).get("team_label") or "") == (event.get("team_evidence_before") or {}).get("dominant_team")
        row["agreed" if agreed else "overrode"] += 1
    return result


def _short_high(events: list[dict[str, Any]]) -> dict[str, int]:
    selected = [event for event in events if int((event.get("team_evidence_before") or {}).get("source_frame_count") or 0) <= 200 and float((event.get("team_evidence_before") or {}).get("dominant_ratio") or 0) >= .90]
    agreed = sum(str((event.get("operator_result") or {}).get("team_label") or "") == (event.get("team_evidence_before") or {}).get("dominant_team") for event in selected)
    return {"total": len(selected), "agreed": agreed, "overrode": len(selected) - agreed}


def _path(event: dict[str, Any]) -> str:
    state = str(event.get("reviewed_team_attribution_state") or "unknown")
    action = str((event.get("operator_result") or {}).get("action") or "unknown")
    return f"{state}->{action}"
