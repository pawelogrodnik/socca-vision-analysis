from __future__ import annotations

"""Evaluation-only comparison for the v5 weak-boundary shadow experiment."""

from typing import Any, Mapping

from app.services.shot_review_editor import build_review_clusters
from evaluation.shot_candidate_benchmark import benchmark_shot_candidates


SCHEMA_VERSION = "shot-weak-boundary-shadow-evaluation:v1"


def evaluate_weak_boundary_shadow(
    v4_candidates_doc: Mapping[str, Any],
    v5_candidates_doc: Mapping[str, Any],
    goldset_doc: Mapping[str, Any],
    v4_deep_dive_doc: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare v5 to v4 without feeding goldset data into runtime generation."""

    v4_benchmark = benchmark_shot_candidates(v4_candidates_doc, goldset_doc)
    v5_benchmark = benchmark_shot_candidates(v5_candidates_doc, goldset_doc)
    v4_matches = {str(row.get("gold_shot_id") or "") for row in v4_benchmark.get("matches") or [] if isinstance(row, Mapping)}
    v5_matches = {str(row.get("gold_shot_id") or "") for row in v5_benchmark.get("matches") or [] if isinstance(row, Mapping)}
    v4_candidate_keys = {_key(row) for row in v4_candidates_doc.get("candidates") or [] if isinstance(row, Mapping)}
    v5_candidates = [dict(row) for row in v5_candidates_doc.get("candidates") or [] if isinstance(row, Mapping)]
    v5_by_contact = {
        (_text(row.get("source_match_id")), _text(row.get("source_event_id"))): row
        for row in v5_candidates
    }
    v5_diagnostics = {
        (_text(row.get("source_match_id")), _text(row.get("contact_event_id"))): row
        for row in v5_candidates_doc.get("weak_boundary_diagnostics") or []
        if isinstance(row, Mapping)
    }

    weak_cases = []
    for miss in v4_deep_dive_doc.get("misses") or []:
        if not isinstance(miss, Mapping):
            continue
        source_match_id = _text(miss.get("source_match_id"))
        for boundary in miss.get("weak_detected_boundaries") or []:
            if not isinstance(boundary, Mapping):
                continue
            contact_id = _text(boundary.get("contact_event_id"))
            diagnostic = _mapping(v5_diagnostics.get((source_match_id, contact_id)))
            candidate = _mapping(v5_by_contact.get((source_match_id, contact_id)))
            recovered = str(miss.get("gold_shot_id") or "") in v5_matches
            weak_cases.append({
                "canonical_shot_id": miss.get("gold_shot_id"),
                "canonical_timestamp_display": miss.get("timestamp_display"),
                "canonical_logical_timestamp_sec": miss.get("logical_timestamp_sec"),
                "source_match_id": source_match_id,
                "contact_event_id": contact_id,
                "v4_state": "detector_evidence_boundary",
                "v5_reinterpretation": diagnostic.get("weak_boundary_state"),
                "v5_candidate_id": candidate.get("candidate_id"),
                "v5_candidate_timestamp_sec": candidate.get("logical_timestamp_sec"),
                "benchmark_recovered": recovered,
                "reason": _case_reason(diagnostic, candidate, recovered),
            })

    v5_only = []
    for candidate in v5_candidates:
        if _key(candidate) in v4_candidate_keys:
            continue
        diagnostic = _mapping(v5_diagnostics.get((_text(candidate.get("source_match_id")), _text(candidate.get("source_event_id")))))
        v5_only.append({
            "candidate_id": candidate.get("candidate_id"),
            "source_match_id": candidate.get("source_match_id"),
            "source_event_id": candidate.get("source_event_id"),
            "logical_timestamp_sec": candidate.get("logical_timestamp_sec"),
            "suggested_team": candidate.get("suggested_team_name"),
            "suggested_player": candidate.get("suggested_player_id"),
            "confidence": candidate.get("confidence"),
            "reasons": list(candidate.get("reasons") or []),
            "weak_boundary_state": diagnostic.get("weak_boundary_state"),
            "weak_boundary_gap_sec": diagnostic.get("gap_sec"),
            "weak_boundary_spatial_residual_m": diagnostic.get("weak_boundary_spatial_residual_m"),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "v4_policy_version": v4_candidates_doc.get("policy_version"),
        "v5_policy_version": v5_candidates_doc.get("policy_version"),
        "benchmark": {"v4": v4_benchmark["summary"], "v5": v5_benchmark["summary"]},
        "incremental_value_over_v4": {
            "canonical_shots_recovered": sorted(v5_matches - v4_matches),
            "canonical_shots_lost": sorted(v4_matches - v5_matches),
            "v5_only_candidate_count": len(v5_only),
            "additional_review_clusters": _cluster_count(v5_candidates_doc) - _cluster_count(v4_candidates_doc),
            "hard_negative_delta": _number(v5_benchmark["summary"].get("hard_negative_hits")) - _number(v4_benchmark["summary"].get("hard_negative_hits")),
        },
        "weak_boundary_cases": sorted(weak_cases, key=lambda row: (_number(row.get("canonical_logical_timestamp_sec")), str(row.get("contact_event_id") or ""))),
        "v5_only_candidates": sorted(v5_only, key=lambda row: (_number(row.get("logical_timestamp_sec")), str(row.get("candidate_id") or ""))),
        "limitations": [
            "The comparison is evaluation-only; goldset rows never influence v5 candidate generation.",
            "A v5-only candidate is a review hypothesis, not a positive or negative result until operator review.",
        ],
    }


def _case_reason(diagnostic: Mapping[str, Any], candidate: Mapping[str, Any], recovered: bool) -> str:
    state = _text(diagnostic.get("weak_boundary_state"))
    if recovered:
        return "recovered_safely_within_benchmark_tolerance"
    if state != "weak_boundary_reinterpreted":
        return state or "no_v5_weak_boundary_diagnostic"
    if candidate:
        return "candidate_emitted_but_not_matched_to_this_canonical_shot"
    return "reinterpreted_but_later_rejected_by_candidate_stage"


def _cluster_count(document: Mapping[str, Any]) -> int:
    candidates = [dict(row) for row in document.get("candidates") or [] if isinstance(row, Mapping)]
    return len(build_review_clusters(
        candidates,
        candidate_generation_digest=f"evaluation:{document.get('policy_version') or 'unknown'}",
        timeline_span_sec=_number(document.get("timeline_span_sec")),
    ))


def _key(row: Mapping[str, Any]) -> str:
    return str(row.get("candidate_key") or row.get("candidate_id") or "")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
