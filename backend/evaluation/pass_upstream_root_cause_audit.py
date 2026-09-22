"""Read-only go/no-go audit for upstream automatic-pass failures.

The module deliberately consumes the same regenerated Pass Policy v1 documents
as #170–#173.  It never writes match storage and only joins gold annotations
after candidate generation to decide which existing rows deserve inspection.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from evaluation.open_play_pass_evidence_audit import audit_open_play_pass_evidence
from evaluation.pass_team_attribution_v2 import evaluate_pass_team_attribution_v2


SCHEMA_VERSION = "pass-upstream-root-cause-audit:v1"
TRACE_RADIUS_SEC = 0.75
FP_SAMPLE_LIMITS = {
    "SHOT_AS_PASS": 6,
    "INTERVENTION_AS_PASS": 5,
    "CONTROL_AS_PASS": 2,
    "CONTEST_AS_PASS": 2,
    "AMBIGUOUS_ACTION_AS_PASS": 3,
}
GENERIC_FP_LIMIT = 6


def audit_pass_upstream_root_causes(
    goldset: Mapping[str, Any],
    source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
    global_identity_documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a bounded forensic audit without changing automatic evidence."""

    evidence = audit_open_play_pass_evidence(goldset, source_documents)
    team = evaluate_pass_team_attribution_v2(goldset, source_documents, global_identity_documents)
    indexes = _indexes(source_documents, global_identity_documents)
    windows = _windows(goldset)

    missed = [
        _audit_missed(row, windows, indexes)
        for row in evidence.get("missed_gold_pass_root_causes") or []
        if isinstance(row, Mapping)
    ]
    team_errors = _audit_team_errors(team, indexes)
    fp_population = [
        row for row in evidence.get("evidence_rows") or []
        if isinstance(row, Mapping) and row.get("evaluation_label") != "TRUE_PASS_MATCH"
    ]
    fp_sample = _audit_false_positive_sample(evidence, windows, indexes)
    counts = _root_cause_counts(missed, team_errors, fp_sample)
    trace_rows = _extract_traces(missed, team_errors, fp_sample)
    contact_findings = _contact_findings(missed, team_errors, fp_sample)
    identity_findings = _identity_findings(team_errors)
    ball_findings = _ball_findings(missed)
    candidates = _shared_fix_candidates(missed, team_errors, fp_sample)
    decision = _go_no_go_decision(candidates)

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_layer": {
            "candidate_generation": "current Pass Policy v1 regenerated in memory",
            "gold_use": "post-generation labels and inspection selection only",
            "production_artifacts_written": False,
            "runtime_modules_changed": False,
            "manual_operator_decisions_reinterpreted": False,
        },
        "current_pass_quality": {
            "gold_open_play_passes": evidence["current_error_budget"]["gold_open_play_passes"],
            "automatic_open_play_candidates": len(evidence.get("evidence_rows") or []),
            "matched": evidence["current_error_budget"]["matched"],
            "missed": evidence["current_error_budget"]["missed"],
            "false_positives": evidence["current_error_budget"]["false_positives"],
        },
        "inspected_missed_passes": missed,
        "inspected_team_attribution_errors": team_errors,
        "inspected_false_positive_sample": fp_sample,
        "false_positive_sample_coverage": _fp_sample_coverage(fp_sample, fp_population),
        "upstream_traces": trace_rows,
        "root_cause_counts": counts["total"],
        "root_cause_by_population": counts["by_population"],
        "root_cause_by_window": counts["by_window"],
        "contact_player_association_findings": contact_findings,
        "contact_fragmentation_findings": _fragmentation_findings(evidence, missed, fp_sample),
        "identity_findings": identity_findings,
        "ball_track_findings": ball_findings,
        "shared_fix_candidates": candidates,
        "go_no_go_decision": decision,
        "recommended_follow_up": None,
    }


def _extract_traces(*populations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Store each evidence trace once; population rows retain a stable reference."""

    traces: list[dict[str, Any]] = []
    for row in (item for population in populations for item in population):
        trace = row.pop("upstream_trace")
        trace_id = f"trace-{len(traces) + 1:03d}"
        row["trace_id"] = trace_id
        traces.append({"trace_id": trace_id, **trace})
    return traces


def _indexes(
    documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
    identities: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for source_id, source in documents.items():
        events = _rows_by_id(source, "event_candidates", "events", "event_id")
        contacts = _rows_by_id(source, "contact_candidates", "candidates", "candidate_id")
        passes = _rows_by_id(source, "pass_candidates", "candidates", "candidate_id")
        slots = {
            str(slot.get("stable_subject_id") or slot.get("slot_id") or ""): _slot_public(slot)
            for slot in identities.get(source_id, {}).get("slots") or []
            if isinstance(slot, Mapping) and (slot.get("stable_subject_id") or slot.get("slot_id"))
        }
        output[str(source_id)] = {
            "events": events,
            "contacts": contacts,
            "passes": passes,
            "possession": [dict(row) for row in source.get("possession_candidates", {}).get("frames") or [] if isinstance(row, Mapping)],
            "effective_ball_positions": [
                dict(row) for row in source.get("effective_ball_tracks", {}).get("positions") or [] if isinstance(row, Mapping)
            ],
            "effective_ball_metadata": dict(source.get("effective_ball_metadata") or {}),
            "slots": slots,
        }
    return output


def _rows_by_id(source: Mapping[str, Mapping[str, Any]], document: str, collection: str, identifier: str) -> dict[str, dict[str, Any]]:
    return {
        str(row.get(identifier)): dict(row)
        for row in source.get(document, {}).get(collection) or []
        if isinstance(row, Mapping) and row.get(identifier)
    }


def _slot_public(slot: Mapping[str, Any]) -> dict[str, Any]:
    tracklets = [str(value) for value in slot.get("tracklet_ids") or []]
    raw_tracks = [value for value in slot.get("raw_track_ids") or []]
    return {
        "slot_id": slot.get("slot_id"),
        "stable_subject_id": slot.get("stable_subject_id"),
        "stable_player_id": slot.get("stable_player_id"),
        "team_label": slot.get("team_label"),
        "team_id": slot.get("team_id"),
        "team_name": slot.get("team_name"),
        "tracklet_count": len(tracklets),
        "tracklet_id_samples": tracklets[:12],
        "raw_track_count": len(raw_tracks),
        "raw_track_id_samples": raw_tracks[:12],
        "slot_creation_reason": slot.get("slot_creation_reason"),
        "identity_event_count": len(slot.get("identity_events") or []),
        "risky_link_count": len(slot.get("risky_links") or []),
        "operator_identity_decision": _operator_identity_decision(slot),
    }


def _audit_missed(row: Mapping[str, Any], windows: Mapping[str, Mapping[str, Any]], indexes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    source_id, source_time = _source_time(row, windows)
    trace = build_upstream_trace(source_id, source_time, indexes)
    structural_chain = _structural_pairing_chain(row, source_id, indexes)
    root = _miss_root_cause(row, trace, structural_chain)
    return {
        "population": "MISS",
        "gold_event_id": row.get("gold_event_id"),
        "window_id": row.get("window_id"),
        "merged_time_sec": row.get("gold_time_sec"),
        "source_match_id": source_id,
        "source_time_sec": _round(source_time),
        "prior_evidence_root_cause": row.get("root_cause"),
        "root_cause": root,
        "evidence": {
            "nearby_contact_ids": row.get("nearby_contact_ids"),
            "nearby_pass_candidate_ids": row.get("nearby_pass_candidate_ids"),
            "rejection_reasons": row.get("rejection_reasons"),
            "skipped_contact_pairs": row.get("skipped_contact_pairs"),
            "structural_pairing_chain": structural_chain,
            "effective_ball_frame_count": len(trace["effective_ball"]["nearby_positions"]),
        },
        "upstream_trace": trace,
    }


def _miss_root_cause(
    row: Mapping[str, Any], trace: Mapping[str, Any], structural_chain: Mapping[str, Any] | None = None,
) -> str:
    prior = str(row.get("root_cause") or "")
    if prior == "EXCLUDED_BY_RELEASE_POLICY":
        return "RELEASE_POLICY"
    if prior == "NO_SOURCE_CONTACT":
        return "CONTACT_GENERATION" if trace["effective_ball"]["nearby_positions"] else "UNKNOWN"
    if _mapping(structural_chain).get("demonstrated"):
        return "PASS_PAIR_CONSTRUCTION"
    return "UNKNOWN"


def _structural_pairing_chain(
    row: Mapping[str, Any], source_id: str, indexes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Require an actual event chain, not a skip merely near an approximate anchor."""

    events = _mapping(_mapping(indexes.get(source_id)).get("events"))
    ordered = sorted(events.values(), key=lambda event: _number(event.get("start_time_sec")))
    by_id = {str(event.get("event_id")): event for event in ordered}
    candidates: list[dict[str, Any]] = []
    for skipped in row.get("skipped_contact_pairs") or []:
        if not isinstance(skipped, Mapping):
            continue
        source = _mapping(by_id.get(str(skipped.get("source_event_id") or "")))
        duplicate = _mapping(by_id.get(str(skipped.get("target_event_id") or "")))
        if not source or not duplicate:
            continue
        duplicate_index = next((index for index, event in enumerate(ordered) if event.get("event_id") == duplicate.get("event_id")), -1)
        later = ordered[duplicate_index + 1] if duplicate_index >= 0 and duplicate_index + 1 < len(ordered) else {}
        source_subject = str(source.get("stable_subject_id") or "")
        later_subject = str(later.get("stable_subject_id") or "")
        # A direct source -> duplicate same-owner -> distinct immediate receiver
        # chain is structural. Any other nearby skip remains diagnostic only.
        demonstrated = (
            str(skipped.get("skip_reason") or "") == "same_player_consecutive_contacts"
            and source_subject
            and source_subject == str(duplicate.get("stable_subject_id") or "")
            and later_subject
            and later_subject != source_subject
            and _number(later.get("start_time_sec")) - _number(duplicate.get("end_time_sec")) <= 1.0
        )
        candidates.append({
            "skip_reason": skipped.get("skip_reason"),
            "source_event": _event_public(source),
            "skipped_duplicate_event": _event_public(duplicate),
            "later_distinct_event": _event_public(later),
            "demonstrated": demonstrated,
        })
    return {
        "demonstrated": any(item["demonstrated"] for item in candidates),
        "chains": candidates,
        "diagnostic_signals": [
            f"NEARBY_{str(item.get('skip_reason') or 'SKIP').upper()}" for item in candidates if not item["demonstrated"]
        ],
    }


def _audit_team_errors(report: Mapping[str, Any], indexes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in report.get("error_audit") or []:
        if not isinstance(match, Mapping):
            continue
        for role, contact_key, expected_key, actual_key in (
            ("actor", "source_contact", "gold_actor_team", "v1_actor_team"),
            ("receiver", "target_contact", "gold_receiver_team", "v1_receiver_team"),
        ):
            expected, actual = match.get(expected_key), match.get(actual_key)
            if not expected or expected == actual:
                continue
            source_id = str(match.get("source_match_id") or "")
            contact_evidence = _mapping(match.get(contact_key))
            contact_id = str(contact_evidence.get("event_id") or "")
            actual_contact = _mapping(_mapping(_mapping(indexes.get(source_id)).get("events")).get(contact_id))
            time_sec = _number(actual_contact.get("start_time_sec"))
            trace = build_upstream_trace(source_id, time_sec, indexes, contact_id, expected_team=str(expected))
            root = _team_error_root_cause(str(expected), trace)
            rows.append({
                "population": "TEAM",
                "role": role,
                "gold_event_id": match.get("gold_event_id"),
                "window_id": match.get("window_id"),
                "merged_time_sec": match.get("gold_time_sec"),
                "source_match_id": source_id,
                "source_time_sec": _round(time_sec),
                "expected_team": expected,
                "automatic_team": actual,
                "contact_event_id": contact_id,
                "root_cause": root,
                "evidence": {
                    "stable_player_id": _mapping(trace.get("contact_candidate")).get("stable_player_id"),
                    "stable_subject_id": _mapping(trace.get("contact_candidate")).get("stable_subject_id"),
                    "event_team": contact_evidence.get("event_team"),
                    "canonical_subject_team": contact_evidence.get("canonical_subject_team"),
                    "canonical_player_team": contact_evidence.get("canonical_player_team"),
                    "nearby_possession": contact_evidence.get("nearby_controlled_possession"),
                },
                "upstream_trace": trace,
            })
    return rows


def _team_error_root_cause(expected_team: str, trace: Mapping[str, Any]) -> str:
    """Only claim an owner error when local production geometry demonstrates it."""

    operator = _mapping(_mapping(trace.get("global_identity")).get("operator_identity_decision"))
    event = _mapping(trace.get("event_candidate"))
    if _team_name(operator) == expected_team and _team_name(event) != expected_team:
        return "OPERATOR_DECISION_PROPAGATION_BUG"
    alternatives = trace["possession"].get("closer_expected_team_players") or []
    if alternatives:
        return "WRONG_POSSESSION_OWNER"
    # An event/contact disagreement is evidence of inheritance divergence, not
    # evidence that the contact player was wrong.  The persisted trace has no
    # independent alternative contact candidate to rank here, so it remains
    # deliberately unresolved.
    return "UNKNOWN"


def _audit_false_positive_sample(
    evidence: Mapping[str, Any], windows: Mapping[str, Mapping[str, Any]], indexes: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = [row for row in evidence.get("evidence_rows") or [] if isinstance(row, Mapping) and row.get("evaluation_label") != "TRUE_PASS_MATCH"]
    selected = _deterministic_fp_sample(rows)
    output: list[dict[str, Any]] = []
    for row in selected:
        source_id = str(row.get("source_match_id") or "")
        merged_time = _number(row.get("merged_release_time_sec"))
        offset = _number(_mapping(windows.get(str(row.get("window_id") or ""))).get("source_to_merged_offset_sec"))
        time_sec = merged_time - offset
        trace = build_upstream_trace(source_id, time_sec, indexes, row.get("source_contact_event_id"))
        classification = str(row.get("evaluation_label") or "UNMATCHED_PASS_CANDIDATE")
        structural_chain = _fp_structural_chain(classification, trace)
        output.append({
            "population": "FP_SAMPLE",
            "candidate_ref": row.get("candidate_ref"),
            "candidate_id": row.get("candidate_id"),
            "window_id": row.get("window_id"),
            "source_match_id": source_id,
            "source_time_sec": _round(time_sec),
            "classification": classification,
            "semantic_context": classification,
            "root_cause": _fp_root_cause(classification, structural_chain),
            "evidence": {
                "source_event_id": row.get("source_contact_event_id"),
                "target_event_id": row.get("target_contact_event_id"),
                "duration_sec": _mapping(row.get("runtime_evidence")).get("duration_sec"),
                "confidence": _mapping(row.get("automatic")).get("confidence"),
                "pass_type": _mapping(row.get("automatic")).get("pass_type"),
                "cluster_id": row.get("false_positive_cluster_id"),
                "structural_chain": structural_chain,
            },
            "upstream_trace": trace,
        })
    return output


def _deterministic_fp_sample(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_label: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_label[str(row.get("evaluation_label") or "UNMATCHED_PASS_CANDIDATE")].append(row)
    selected: list[Mapping[str, Any]] = []
    for label, limit in FP_SAMPLE_LIMITS.items():
        selected.extend(_stable_sample(by_label.pop(label, []), limit))
    generic = by_label.pop("UNMATCHED_PASS_CANDIDATE", [])
    selected.extend(_spread_generic_sample(generic, GENERIC_FP_LIMIT))
    for label in sorted(by_label):
        selected.extend(_stable_sample(by_label[label], 1))
    return selected


def _stable_sample(rows: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
    return sorted(rows, key=_sample_key)[:limit]


def _spread_generic_sample(rows: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get("window_id") or "unknown")].append(row)
    buckets = {window: sorted(values, key=_sample_key) for window, values in buckets.items()}
    selected: list[Mapping[str, Any]] = []
    while len(selected) < limit:
        added = False
        for window_id in sorted(buckets):
            if buckets[window_id] and len(selected) < limit:
                selected.append(buckets[window_id].pop(0))
                added = True
        if not added:
            break
    return selected


def _sample_key(row: Mapping[str, Any]) -> tuple[str, float, str]:
    return (str(row.get("window_id") or ""), _number(row.get("merged_release_time_sec")), str(row.get("candidate_ref") or row.get("candidate_id") or ""))


def _fp_root_cause(classification: str, structural_chain: Mapping[str, Any]) -> str:
    if _mapping(structural_chain).get("demonstrated"):
        return str(structural_chain.get("root_cause"))
    return "UNKNOWN"


def _fp_structural_chain(semantic_context: str, trace: Mapping[str, Any]) -> dict[str, Any]:
    """Keep gold-relative semantic context distinct from runtime causality.

    The current persisted pass/contact schema has no shot or intervention link.
    A root cause is therefore promoted only if a future trace supplies an
    explicit runtime chain marker; ordinary proximity never suffices.
    """

    runtime = _mapping(trace.get("runtime_chain"))
    marker = str(runtime.get("kind") or "")
    expected = {
        "SHOT_AS_PASS": ("shot_rebound", "SHOT_REBOUND_CHAIN"),
        "INTERVENTION_AS_PASS": ("intervention", "INTERVENTION_CHAIN"),
        "CONTROL_AS_PASS": ("continued_control", "CONTINUED_CONTROL_FRAGMENTATION"),
    }.get(semantic_context)
    if expected and marker == expected[0] and runtime.get("source_contact") and runtime.get("target_contact"):
        return {"demonstrated": True, "root_cause": expected[1], "runtime_chain": dict(runtime)}
    return {"demonstrated": False, "reason": "semantic_context_without_runtime_chain"}


def build_upstream_trace(
    source_match_id: str,
    source_time_sec: float,
    indexes: Mapping[str, Mapping[str, Any]],
    contact_event_id: Any = None,
    expected_team: str | None = None,
) -> dict[str, Any]:
    """Trace existing production interpretation at one source-timeline time."""

    source = _mapping(indexes.get(source_match_id))
    events = _mapping(source.get("events"))
    selected_event = _mapping(events.get(str(contact_event_id or "")))
    if not selected_event:
        selected_event = _nearest_event(events, source_time_sec)
    contact_id = str(selected_event.get("source_candidate_id") or "")
    selected_contact = _mapping(_mapping(source.get("contacts")).get(contact_id))
    nearby_frames = [
        row for row in source.get("possession") or []
        if abs(_number(row.get("time_sec")) - source_time_sec) <= TRACE_RADIUS_SEC
    ]
    selected_subject = str(selected_contact.get("stable_subject_id") or selected_event.get("stable_subject_id") or "")
    closer_expected = _closer_team_alternatives(selected_contact, nearby_frames, expected_team)
    nearby_events = _nearby_rows(events, source_time_sec, _event_public)
    nearby_passes = _nearby_rows(source.get("passes") or {}, source_time_sec, _pass_public)
    return {
        "source_match_id": source_match_id,
        "source_time_sec": _round(source_time_sec),
        "effective_ball": {
            "artifact": _mapping(source.get("effective_ball_metadata")).get("artifact"),
            "provenance": _mapping(source.get("effective_ball_metadata")).get("provenance"),
            "nearby_positions": _nearby_effective_ball_positions(source, source_time_sec),
        },
        "nearby_players": [
            player for row in nearby_frames[:6] for player in row.get("nearest_players") or []
        ][:12],
        "selected_player": _contact_player_public(selected_contact or selected_event),
        "team_assignment": _team_public(selected_contact or selected_event),
        "stable_identity": _mapping(source.get("slots")).get(selected_subject),
        "global_identity": _mapping(source.get("slots")).get(selected_subject),
        "possession": {
            "nearby_owner_frames": [_owner_frame_public(row) for row in nearby_frames[:12]],
            "closer_expected_team_players": closer_expected,
        },
        "contact_candidate": _contact_public(selected_contact),
        "event_candidate": _event_public(selected_event),
        "contact_event_divergence": _contact_event_divergence(selected_contact, selected_event),
        "pass_candidates": nearby_passes,
        "nearby_contact_events": nearby_events,
    }


def _nearby_effective_ball_positions(source: Mapping[str, Any], time_sec: float) -> list[dict[str, Any]]:
    return [
        {
            key: deepcopy(row.get(key))
            for key in ("frame", "time_sec", "source", "candidate_id", "confidence", "position_m", "position_px", "resolution_source", "interpolated_from")
        }
        for row in source.get("effective_ball_positions") or []
        if abs(_number(row.get("time_sec")) - time_sec) <= TRACE_RADIUS_SEC
    ][:18]


def _contact_event_divergence(contact: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("stable_player_id", "stable_subject_id", "team_label", "team_id", "team_name")
    differences = {
        field: {"contact": contact.get(field), "event": event.get(field)}
        for field in fields
        if contact and event and contact.get(field) != event.get(field)
    }
    return {
        "event_source_candidate_id": event.get("source_candidate_id"),
        "contact_candidate_id": contact.get("candidate_id"),
        "link_resolved": bool(event and contact and event.get("source_candidate_id") == contact.get("candidate_id")),
        "has_divergence": bool(differences),
        "field_differences": differences,
    }


def _closer_team_alternatives(
    contact: Mapping[str, Any], frames: Sequence[Mapping[str, Any]], expected_team: str | None,
) -> list[dict[str, Any]]:
    selected_subject = str(contact.get("stable_subject_id") or "")
    selected_team = str(contact.get("team_label") or "")
    output: list[dict[str, Any]] = []
    for frame in frames:
        owner_distance = _number(frame.get("nearest_distance_m"))
        for player in frame.get("nearest_players") or []:
            if not isinstance(player, Mapping) or str(player.get("stable_subject_id") or "") == selected_subject:
                continue
            player_team = _team_name(player)
            if (
                str(player.get("team_label") or "") != selected_team
                and player_team == expected_team
                and _number(player.get("distance_m")) < owner_distance
            ):
                output.append({
                    "frame": frame.get("frame"), "time_sec": frame.get("time_sec"), "stable_subject_id": player.get("stable_subject_id"),
                    "team_label": player.get("team_label"), "distance_m": player.get("distance_m"), "selected_distance_m": frame.get("nearest_distance_m"),
                })
    return output[:12]


def _nearest_event(rows: Mapping[str, Mapping[str, Any]], time_sec: float) -> Mapping[str, Any]:
    if not rows:
        return {}
    return min(
        rows.values(),
        key=lambda row: (
            abs(_number(row.get("start_time_sec")) - time_sec),
            _number(row.get("start_time_sec")),
            str(row.get("event_id") or ""),
        ),
    )


def _nearby_rows(
    rows: Mapping[str, Mapping[str, Any]], time_sec: float, serializer: Any,
) -> list[dict[str, Any]]:
    return [
        serializer(row)
        for row in rows.values()
        if abs(_number(row.get("start_time_sec") or row.get("release_time_sec")) - time_sec) <= TRACE_RADIUS_SEC
    ][:12]


def _contact_public(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if not row:
        return None
    evidence = _mapping(row.get("evidence"))
    # The persisted contact candidate stores most contact-quality fields at the
    # top level; event candidates keep a copy under ``evidence``.  Expose both
    # shapes without treating the event copy as an independent observation.
    return {
        **{key: deepcopy(row.get(key)) for key in (
            "event_id", "candidate_id", "candidate_key", "start_frame", "end_frame", "start_time_sec", "end_time_sec", "release_time_sec",
            "stable_player_id", "stable_subject_id", "team_label", "team_id", "team_name", "source_event_id", "target_event_id",
            "confidence", "mean_confidence", "review_status", "review_source", "auto_review", "source",
        )},
        "evidence_summary": {
            key: deepcopy(row.get(key, evidence.get(key)))
            for key in ("frames", "detected_ball_frames", "detected_player_frames", "mean_distance_m", "min_distance_m", "player_source_counts")
            if key in row or key in evidence
        },
        "positions": {
            key: deepcopy(row.get(key, evidence.get(key)))
            for key in ("start_ball_position_m", "end_ball_position_m", "start_player_position_m", "end_player_position_m")
            if key in row or key in evidence
        },
    }


def _event_public(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        key: deepcopy(row.get(key))
        for key in (
            "event_id", "source_candidate_id", "source_candidate_key", "start_time_sec", "end_time_sec",
            "stable_player_id", "stable_subject_id", "team_label", "team_id", "team_name", "review_status", "confidence", "source",
        )
    }


def _pass_public(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        key: deepcopy(row.get(key))
        for key in (
            "candidate_id", "candidate_key", "source_event_id", "target_event_id", "source_candidate_id", "target_candidate_id",
            "start_time_sec", "end_time_sec", "duration_sec", "pass_type", "outcome", "confidence", "review_status",
        )
    }


def _contact_player_public(row: Mapping[str, Any]) -> dict[str, Any] | None:
    value = _contact_public(row)
    if value is None:
        return None
    return {key: value.get(key) for key in ("stable_player_id", "stable_subject_id", "team_label", "team_id", "team_name")}


def _team_public(row: Mapping[str, Any]) -> dict[str, Any] | None:
    value = _contact_player_public(row)
    return value


def _owner_frame_public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{key: deepcopy(row.get(key)) for key in (
            "frame", "time_sec", "status", "stable_player_id", "stable_subject_id", "team_label", "team_id", "team_name", "nearest_distance_m",
        )},
        "nearest_player_summary": [
            {key: player.get(key) for key in ("stable_player_id", "stable_subject_id", "team_label", "distance_m")}
            for player in row.get("nearest_players") or []
            if isinstance(player, Mapping)
        ][:3],
    }


def _root_cause_counts(
    missed: Sequence[Mapping[str, Any]], team: Sequence[Mapping[str, Any]], fp: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    populations = {"MISS": missed, "TEAM": team, "FP_SAMPLE": fp}
    by_population = {
        population: dict(sorted(Counter(str(row.get("root_cause")) for row in rows).items()))
        for population, rows in populations.items()
    }
    total = Counter()
    by_window: dict[str, Counter[str]] = defaultdict(Counter)
    for rows in populations.values():
        for row in rows:
            cause = str(row.get("root_cause"))
            total[cause] += 1
            by_window[str(row.get("window_id") or "unknown")][cause] += 1
    return {
        "total": dict(sorted(total.items(), key=lambda item: (-item[1], item[0]))),
        "by_population": by_population,
        "by_window": {window: dict(sorted(values.items())) for window, values in sorted(by_window.items())},
    }


def _contact_findings(missed: Sequence[Mapping[str, Any]], team: Sequence[Mapping[str, Any]], fp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "wrong_possession_owner_demonstrated": sum(row.get("root_cause") == "WRONG_POSSESSION_OWNER" for row in team),
        "misses_with_explicit_pair_skip": sum(row.get("root_cause") == "PASS_PAIR_CONSTRUCTION" for row in missed),
        "fp_samples_with_contact_fragmentation": sum(row.get("root_cause") == "CONTACT_FRAGMENTATION" for row in fp),
        "conclusion": "No contact-player selector defect is asserted unless a closer opposite-team production candidate is present in the trace.",
    }


def _fragmentation_findings(evidence: Mapping[str, Any], missed: Sequence[Mapping[str, Any]], fp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary = _mapping(evidence.get("all_candidate_cluster_summary"))
    return {
        "multi_candidate_clusters": summary.get("multi_candidate_clusters"),
        "true_only": summary.get("TRUE_ONLY"),
        "false_only": summary.get("FALSE_ONLY"),
        "mixed": summary.get("MIXED"),
        "miss_pair_construction_count": sum(row.get("root_cause") == "PASS_PAIR_CONSTRUCTION" for row in missed),
        "fp_fragmentation_asserted": sum(row.get("root_cause") == "CONTACT_FRAGMENTATION" for row in fp),
        "conclusion": "Cluster density remains non-selective because mixed clusters contain genuine passes; no suppression recommendation follows.",
    }


def _identity_findings(team: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    causes = Counter(str(row.get("root_cause")) for row in team)
    return {
        "team_error_count": len(team),
        "wrong_possession_owner_demonstrated": causes["WRONG_POSSESSION_OWNER"],
        "unresolved_first_divergence": causes["UNKNOWN"],
        "operator_decision_contradiction": causes["OPERATOR_DECISION_PROPAGATION_BUG"],
        "conclusion": "Where contact, canonical slot and local possession agree on a team that conflicts with gold, this audit cannot distinguish a wrong physical-player association from an upstream identity slot without new human identity evidence.",
    }


def _ball_findings(missed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    with_effective = sum(bool(_mapping(row.get("evidence")).get("effective_ball_frame_count")) for row in missed)
    return {
        "misses_with_nearby_authoritative_effective_ball_positions": with_effective,
        "misses_without_nearby_authoritative_effective_ball_positions": len(missed) - with_effective,
        "conclusion": "The audit records actual effective-ball provenance and nearby positions, but does not infer BALL_TRACK_NOT_CAUSAL from their presence alone. No detector change is proposed.",
    }


def _shared_fix_candidates(
    missed: Sequence[Mapping[str, Any]], team: Sequence[Mapping[str, Any]], fp: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate every demonstrated family with the same descriptive contract."""

    records = [*missed, *team, *fp]
    return _evaluate_shared_fix_candidates(records)


def _evaluate_shared_fix_candidates(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    families: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        cause = str(row.get("root_cause") or "UNKNOWN")
        families[cause].append(row)
    result: list[dict[str, Any]] = []
    for cause, rows in sorted(families.items()):
        populations = sorted({str(row.get("population") or "unknown") for row in rows})
        windows = sorted({str(row.get("window_id") or "unknown") for row in rows})
        layer, scope, risk = _fix_family_assessment(cause)
        repeated = len(rows) > 1
        qualifies = bool(scope and risk == "acceptable" and repeated)
        result.append({
            "root_cause": cause,
            "count": len(rows),
            "affected_populations": populations,
            "affected_windows": windows,
            "production_layer": layer,
            "bounded_fix_scope": scope,
            "gold_independent": True,
            "regression_risk": risk,
            "support_character": "repeated demonstrated evidence" if repeated else "single demonstrated instance",
            "qualifies_for_go": qualifies,
            "decision": "candidate" if qualifies else "rejected",
            "rejection_reason": _fix_rejection_reason(scope, risk, repeated),
        })
    return result


def _fix_family_assessment(cause: str) -> tuple[str, str | None, str]:
    assessments = {
        "OPERATOR_DECISION_PROPAGATION_BUG": ("identity downstream consumption", "propagate existing durable operator team decision", "acceptable"),
        "WRONG_CONTACT_PLAYER": ("contact candidate generation", None, "high"),
        "WRONG_POSSESSION_OWNER": ("ball possession owner selection", None, "high"),
        "CONTACT_GENERATION": ("ball possession/contact generation", None, "high"),
        "PASS_PAIR_CONSTRUCTION": ("pass candidate pairing", None, "high"),
        "RELEASE_POLICY": ("pass release policy", None, "high"),
        "CONTACT_FRAGMENTATION": ("contact segmentation", None, "high"),
        "SHOT_REBOUND_CHAIN": ("football action semantics", None, "high"),
        "INTERVENTION_CHAIN": ("football action semantics", None, "high"),
        "CONTINUED_CONTROL_FRAGMENTATION": ("contact segmentation", None, "high"),
        "UNKNOWN": ("undetermined", None, "unknown"),
    }
    return assessments.get(cause, ("unknown", None, "high"))


def _fix_rejection_reason(scope: str | None, risk: str, repeated: bool) -> str | None:
    if scope is None:
        return "No demonstrated bounded general production fix; retain this family as diagnostic evidence only."
    if risk != "acceptable":
        return "A bounded change is not currently safe because its known true-positive regression risk is not acceptable."
    if not repeated:
        return "One isolated instance is insufficient to establish a shared production defect."
    return None


def _go_no_go_decision(candidates: Sequence[Mapping[str, Any]]) -> str:
    return "CLEAR_SHARED_UPSTREAM_FIX" if any(row.get("qualifies_for_go") for row in candidates) else "NO_SHARED_FIX_FOUND"


def _fp_sample_coverage(
    rows: Sequence[Mapping[str, Any]], population_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    durations = Counter(_duration_bucket(_number(_mapping(row.get("evidence")).get("duration_sec"))) for row in rows)
    classifications = Counter(str(row.get("semantic_context") or "unknown") for row in rows)
    evidence_rows = [_mapping(row.get("evidence")) for row in rows]
    pass_types = Counter(str(evidence.get("pass_type") or "unavailable") for evidence in evidence_rows)
    relationships = Counter(
        "turnover" if evidence.get("pass_type") == "turnover_or_interception"
        else "same_team" if evidence.get("pass_type") in {"same_team_pass", "completed_pass"}
        else "unavailable"
        for evidence in evidence_rows
    )
    confidence = Counter(_confidence_bucket(_number(evidence.get("confidence"))) for evidence in evidence_rows)
    population_cluster_membership = Counter(
        "multi_candidate_cluster" if row.get("false_positive_cluster_id") else "isolated_or_unclustered"
        for row in population_rows
    )
    return {
        "sample_count": len(rows),
        "windows": sorted({str(row.get("window_id") or "unknown") for row in rows}),
        "semantic_context": dict(sorted(classifications.items())),
        "duration_buckets_sec": dict(sorted(durations.items())),
        "pass_type": dict(sorted(pass_types.items())),
        "team_relationship": dict(sorted(relationships.items())),
        "cluster_membership": dict(sorted(Counter(
            "multi_candidate_cluster" if _mapping(row.get("evidence")).get("cluster_id") else "isolated_or_unclustered" for row in rows
        ).items())),
        "population_cluster_membership": dict(sorted(population_cluster_membership.items())),
        "confidence_buckets": dict(sorted(confidence.items())),
    }


def _duration_bucket(duration: float) -> str:
    if duration < 0.5:
        return "short_<0.5"
    if duration < 1.5:
        return "medium_0.5_to_<1.5"
    return "long_>=1.5"


def _confidence_bucket(confidence: float) -> str:
    if confidence <= 0:
        return "unavailable"
    if confidence < 0.5:
        return "low_<0.5"
    if confidence < 0.75:
        return "medium_0.5_to_<0.75"
    return "high_>=0.75"


def render_pass_upstream_root_cause_audit_markdown(report: Mapping[str, Any]) -> str:
    quality = _mapping(report.get("current_pass_quality"))
    counts = _mapping(report.get("root_cause_counts"))
    by_population = _mapping(report.get("root_cause_by_population"))
    lines = [
        "# Pass Upstream Root-Cause Audit", "", "## Executive summary", "",
        "This is a read-only GO / NO-GO audit. It regenerates current Pass Policy v1 evidence, then uses the goldset only to label and select errors for inspection.",
        f"- Decision: **{report.get('go_no_go_decision')}**.",
        f"- Recommendation: **{'PARK AUTOMATIC PASS DEVELOPMENT' if report.get('go_no_go_decision') == 'NO_SHARED_FIX_FOUND' else 'one bounded follow-up PR'}**.", "",
        "## Current pass quality", "",
        f"- Gold / automatic / matched / missed / false positives: **{quality.get('gold_open_play_passes')} / {quality.get('automatic_open_play_candidates')} / {quality.get('matched')} / {quality.get('missed')} / {quality.get('false_positives')}**.",
        "", "## Missed-pass root causes", "",
    ]
    lines.extend(_root_rows(report.get("inspected_missed_passes") or []))
    lines.extend(["", "## Team-attribution upstream root causes", ""])
    lines.extend(_root_rows(report.get("inspected_team_attribution_errors") or []))
    lines.extend(["", "## False-positive sample root causes", ""])
    lines.append("- Deterministic sample: category caps (shot 6, intervention 5, control 2, contest 2, ambiguous 3) plus a W1–W6 round-robin generic sample capped at 6.")
    lines.extend(_root_rows(report.get("inspected_false_positive_sample") or []))
    lines.extend(["", "## Diagnostic / semantic context", ""])
    lines.append("- Semantic context and nearby skipped-contact signals are diagnostic only. They are not counted as demonstrated root causes without a structural runtime chain.")
    lines.append(f"- False-positive sample coverage: `{report.get('false_positive_sample_coverage')}`")
    miss_signals = sorted({
        signal
        for row in report.get("inspected_missed_passes") or []
        for signal in (_mapping(_mapping(row.get("evidence")).get("structural_pairing_chain")).get("diagnostic_signals") or [])
    })
    lines.append(f"- Miss diagnostic signals: `{miss_signals or ['none']}`")
    lines.extend(["", "## Cross-population error families", "", "| Root cause | MISS | TEAM | FP sample | Total |", "| --- | ---: | ---: | ---: | ---: |"])
    causes = sorted(counts, key=lambda cause: (-int(counts[cause]), cause))
    for cause in causes:
        lines.append(f"| {cause} | {_mapping(by_population.get('MISS')).get(cause, 0)} | {_mapping(by_population.get('TEAM')).get(cause, 0)} | {_mapping(by_population.get('FP_SAMPLE')).get(cause, 0)} | {counts[cause]} |")
    lines.extend(["", "## Contact-player association findings", "", f"- `{report.get('contact_player_association_findings')}`", "", "## Contact-fragmentation findings", "", f"- `{report.get('contact_fragmentation_findings')}`", "", "## Identity findings", "", f"- `{report.get('identity_findings')}`", "", "## Ball-track findings", "", f"- `{report.get('ball_track_findings')}`", "", "## Shared-fix evaluation", ""])
    candidates = report.get("shared_fix_candidates") or []
    if candidates:
        lines.extend(f"- `{candidate}`" for candidate in candidates)
    else:
        lines.append("- None. No measured cause has a bounded, gold-independent production fix with acceptable true-positive risk.")
    lines.extend(["", "## Go / No-Go", "", f"**{report.get('go_no_go_decision')}**."])
    if report.get("go_no_go_decision") == "NO_SHARED_FIX_FOUND":
        lines.append("**RECOMMENDATION: PARK AUTOMATIC PASS DEVELOPMENT.** The audit found heterogeneous failures and no safe shared production change; existing experimental artifacts remain retained.")
    return "\n".join(lines) + "\n"


def _root_rows(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return ["- None."]
    return [
        f"- `{row.get('gold_event_id') or row.get('candidate_id')}` · {row.get('window_id')} · **{row.get('root_cause')}** · source `{row.get('source_match_id')}` @ `{row.get('source_time_sec')}`s"
        for row in rows
    ]


def _windows(goldset: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(row.get("window_id")): row for row in goldset.get("windows") or [] if isinstance(row, Mapping)}


def _source_time(row: Mapping[str, Any], windows: Mapping[str, Mapping[str, Any]]) -> tuple[str, float]:
    window = _mapping(windows.get(str(row.get("window_id") or "")))
    return str(window.get("source_match_id") or ""), _number(row.get("gold_time_sec")) - _number(window.get("source_to_merged_offset_sec"))


def _team_name(team: Mapping[str, Any]) -> str | None:
    label = str(team.get("team_label") or "")
    return {"A": "Corgi", "B": "Verisk"}.get(label, str(team.get("team_name") or "") or None)


def _operator_identity_decision(slot: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract an explicit existing operator decision without reinterpreting it."""

    for key in ("operator_identity_decision", "manual_identity_decision", "reviewed_identity_decision"):
        value = slot.get(key)
        if isinstance(value, Mapping):
            return {field: deepcopy(value.get(field)) for field in ("team_label", "team_id", "team_name", "source", "decision_id")}
    for event in slot.get("identity_events") or []:
        if not isinstance(event, Mapping):
            continue
        source = str(event.get("source") or event.get("reason") or "").lower()
        if "operator" in source or "manual" in source or "review" in source:
            return {field: deepcopy(event.get(field)) for field in ("team_label", "team_id", "team_name", "source", "decision_id")}
    return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _round(value: float) -> float:
    return round(float(value), 4)
