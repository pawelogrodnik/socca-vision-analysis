from __future__ import annotations

"""Evaluation-only operator-semantic overlay for temporal shot benchmarks."""

from collections import Counter
from typing import Any, Mapping

from evaluation.shot_candidate_benchmark import benchmark_shot_candidates


SCHEMA_VERSION = "shot-semantic-benchmark:v1"
TRUE_SHOT_CLASSES = frozenset({"TRUE_SHOT_EXISTING_CANONICAL", "TRUE_SHOT_MISSING_CANONICAL"})


def evaluate_semantic_shot_benchmark(
    candidates_document: Mapping[str, Any],
    goldset_document: Mapping[str, Any],
    audit_document: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep temporal matching intact and expose bounded operator truth beside it.

    Only audited candidate IDs can be semantically validated. An unaudited
    temporal match remains explicitly unaudited rather than being promoted to
    a semantic success by timestamp proximity alone.
    """

    if str(audit_document.get("schema_version") or "") != "shot-v5-weak-boundary-operator-audit:v1":
        raise ValueError("Expected the frozen v5 weak-boundary operator audit")
    temporal = benchmark_shot_candidates(candidates_document, goldset_document)
    audits = _audits_by_candidate(audit_document)
    candidates = {_text(row.get("candidate_id")): row for row in candidates_document.get("candidates") or [] if isinstance(row, Mapping)}
    matches_by_candidate = {_text(row.get("candidate_id")): row for row in temporal.get("matches") or [] if isinstance(row, Mapping)}
    enriched_matches = [_enrich_temporal_match(row, audits.get(_text(row.get("candidate_id")))) for row in temporal.get("matches") or [] if isinstance(row, Mapping)]
    audit_table = [_audit_row(audit, candidates.get(candidate_id), matches_by_candidate.get(candidate_id)) for candidate_id, audit in sorted(audits.items(), key=lambda item: (_number(item[1].get("candidate_timestamp_sec")), item[0]))]
    semantic_summary = _semantic_summary(enriched_matches, temporal)

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "goldset_schema_version": goldset_document.get("schema_version"),
        "candidate_policy_version": candidates_document.get("policy_version"),
        "temporal_benchmark": temporal,
        "semantic_summary": semantic_summary,
        "semantic_matches": enriched_matches,
        "audited_v5_candidate_table": audit_table,
        "temporal_false_positive_matches": [
            _false_positive_row(row)
            for row in enriched_matches
            if row["semantic_match_status"] == "validated_false"
        ],
        "limitations": [
            "Temporal matching remains the existing candidate benchmark and uses its unchanged tolerance.",
            "Only frozen operator-audited candidate IDs receive a semantic verdict; all other temporal matches remain unaudited.",
            "The operator audit is evaluation truth and never influences candidate generation or canonical Shot Review state.",
        ],
    }


def compare_semantic_policy_increment(
    v4_report: Mapping[str, Any],
    v5_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe v5 value over v4 without converting audit truth into policy."""

    v4_temporal = _matched_gold_ids(v4_report)
    v5_temporal = _matched_gold_ids(v5_report)
    v4_semantic = _validated_true_gold_ids(v4_report)
    v5_semantic = _validated_true_gold_ids(v5_report)
    false_temporal = [
        row for row in v5_report.get("temporal_false_positive_matches") or []
        if isinstance(row, Mapping) and _text(row.get("gold_shot_id")) not in v4_temporal
    ]
    return {
        "temporal_incremental_recoveries": sorted(v5_temporal - v4_temporal),
        "semantic_validated_incremental_recoveries": sorted(v5_semantic - v4_semantic),
        "false_temporal_incremental_recoveries": false_temporal,
    }


def _audits_by_candidate(audit_document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    audits = {
        _text(row.get("candidate_id")): row
        for row in audit_document.get("audits") or []
        if isinstance(row, Mapping) and _text(row.get("candidate_id"))
    }
    if len(audits) != len([row for row in audit_document.get("audits") or [] if isinstance(row, Mapping)]):
        raise ValueError("Operator audit contains a missing or duplicate candidate ID")
    return audits


def _enrich_temporal_match(temporal_match: Mapping[str, Any], audit: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(temporal_match)
    if audit is None:
        result.update({"temporal_match": True, "semantic_match_status": "unaudited", "semantic_validated_match": None})
        return result
    operator_class = _text(audit.get("operator_class"))
    expected_shot_id = _text(audit.get("canonical_shot_id"))
    same_action = operator_class in TRUE_SHOT_CLASSES and bool(expected_shot_id) and expected_shot_id == _text(temporal_match.get("gold_shot_id"))
    result.update({
        "temporal_match": True,
        "operator_class": operator_class,
        "operator_notes": audit.get("notes"),
        "identity_followup_required": bool(audit.get("identity_followup_required")),
        "semantic_expected_canonical_shot_id": expected_shot_id or None,
        "semantic_match_status": "validated_true" if same_action else "validated_false",
        "semantic_validated_match": same_action,
    })
    return result


def _audit_row(audit: Mapping[str, Any], candidate: Mapping[str, Any] | None, temporal_match: Mapping[str, Any] | None) -> dict[str, Any]:
    operator_class = _text(audit.get("operator_class"))
    expected_shot_id = _text(audit.get("canonical_shot_id"))
    matched_gold_id = _text(temporal_match.get("gold_shot_id")) if temporal_match else ""
    semantic_match = operator_class in TRUE_SHOT_CLASSES and bool(expected_shot_id) and expected_shot_id == matched_gold_id
    return {
        "candidate_id": audit.get("candidate_id"),
        "candidate_timestamp_sec": _number(audit.get("candidate_timestamp_sec")),
        "candidate_present_in_policy": candidate is not None,
        "operator_class": operator_class,
        "temporal_match": temporal_match is not None,
        "temporal_canonical_shot_id": matched_gold_id or None,
        "temporal_canonical_timestamp_sec": temporal_match.get("gold_timestamp_sec") if temporal_match else None,
        "semantic_shot_match": semantic_match if temporal_match else False,
        "semantic_match_status": "validated_true" if semantic_match else "validated_false",
        "expected_canonical_shot_id": expected_shot_id or None,
        "identity_followup_required": bool(audit.get("identity_followup_required")),
        "notes": audit.get("notes"),
    }


def _semantic_summary(matches: list[Mapping[str, Any]], temporal: Mapping[str, Any]) -> dict[str, Any]:
    gold_count = _number(_mapping(temporal.get("summary")).get("gold_shots"))
    status_counts = Counter(_text(row.get("semantic_match_status")) for row in matches)
    by_origin = _semantic_breakdown(matches, _mapping(temporal.get("origin_recall")), "gold_origin")
    by_team = _semantic_breakdown(matches, _mapping(temporal.get("team_recall")), "gold_team")
    by_outcome = _semantic_breakdown(matches, _mapping(temporal.get("outcome_recall")), "gold_outcome")
    return {
        "gold_shots": gold_count,
        "temporal_matches": len(matches),
        "validated_true": status_counts["validated_true"],
        "validated_false": status_counts["validated_false"],
        "unaudited_temporal_matches": status_counts["unaudited"],
        "semantic_validated_recall_lower_bound": _ratio(status_counts["validated_true"], gold_count),
        "manual_origin": by_origin,
        "team": by_team,
        "outcome": by_outcome,
    }


def _semantic_breakdown(
    matches: list[Mapping[str, Any]],
    temporal_breakdown: Mapping[str, Any],
    field: str,
) -> dict[str, dict[str, int | float]]:
    values = sorted(set(temporal_breakdown) | {_text(row.get(field)) for row in matches if _text(row.get(field))})
    return {
        value: {
            "gold": _number(_mapping(temporal_breakdown.get(value)).get("gold")),
            "temporal_matches": sum(_text(row.get(field)) == value for row in matches),
            "validated_true": sum(_text(row.get(field)) == value and row.get("semantic_match_status") == "validated_true" for row in matches),
            "validated_false": sum(_text(row.get(field)) == value and row.get("semantic_match_status") == "validated_false" for row in matches),
            "unaudited": sum(_text(row.get(field)) == value and row.get("semantic_match_status") == "unaudited" for row in matches),
            "semantic_validated_recall_lower_bound": _ratio(
                sum(_text(row.get(field)) == value and row.get("semantic_match_status") == "validated_true" for row in matches),
                _number(_mapping(temporal_breakdown.get(value)).get("gold")),
            ),
        }
        for value in values
    }


def _false_positive_row(match: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": match.get("candidate_id"),
        "candidate_timestamp_sec": match.get("candidate_timestamp_sec"),
        "canonical_shot_id": match.get("gold_shot_id"),
        "canonical_timestamp_sec": match.get("gold_timestamp_sec"),
        "signed_timing_error_sec": match.get("signed_timing_error_sec"),
        "operator_class": match.get("operator_class"),
        "reason": "operator_audit_identifies_a_different_football_action",
    }


def _matched_gold_ids(report: Mapping[str, Any]) -> set[str]:
    return {_text(row.get("gold_shot_id")) for row in _mapping(report.get("temporal_benchmark")).get("matches") or [] if isinstance(row, Mapping)}


def _validated_true_gold_ids(report: Mapping[str, Any]) -> set[str]:
    return {_text(row.get("gold_shot_id")) for row in report.get("semantic_matches") or [] if isinstance(row, Mapping) and row.get("semantic_match_status") == "validated_true"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _ratio(numerator: int, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
