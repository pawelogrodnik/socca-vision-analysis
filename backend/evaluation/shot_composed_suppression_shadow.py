from __future__ import annotations

"""Evaluation-only report for composing v5 with unchanged v3 suppression."""

from typing import Any, Mapping

from evaluation.shot_candidate_operator_review import evaluate_operator_review_five_way
from evaluation.shot_semantic_benchmark import compare_semantic_policy_increment, evaluate_semantic_shot_benchmark


SCHEMA_VERSION = "shot-composed-suppression-shadow-evaluation:v1"
POLICY_KEYS = ("v2", "v3", "v4", "v5", "v6")


def evaluate_composed_suppression_shadow(
    documents: Mapping[str, Mapping[str, Any]],
    goldset_document: Mapping[str, Any],
    audit_document: Mapping[str, Any],
    editorial_document: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare frozen evaluation truth without exposing it to generation."""

    if set(documents) != set(POLICY_KEYS):
        raise ValueError(f"Expected exactly these policy documents: {', '.join(POLICY_KEYS)}")
    semantic_reports = {
        key: evaluate_semantic_shot_benchmark(documents[key], goldset_document, audit_document, editorial_document)
        for key in POLICY_KEYS
    }
    historical = evaluate_operator_review_five_way(
        documents["v2"], documents["v3"], documents["v4"], documents["v5"], documents["v6"], editorial_document,
    )
    for key, report in semantic_reports.items():
        report["review_clusters"] = historical["policies"][key]["review_clusters"]

    v5_ids = _candidate_ids(documents["v5"])
    v6_ids = _candidate_ids(documents["v6"])
    v6_suppressed = {
        _text(row.get("candidate_id")): dict(row)
        for row in documents["v6"].get("suppressed_candidate_diagnostics") or []
        if isinstance(row, Mapping)
    }
    audit_rows = [dict(row) for row in audit_document.get("audits") or [] if isinstance(row, Mapping)]
    v5_audited_family = [_v5_audit_row(row, v5_ids, v6_ids, v6_suppressed, semantic_reports["v5"]) for row in audit_rows]
    v5_anchor_true = _validated_true_ids(semantic_reports["v5"])
    v6_anchor_true = _validated_true_ids(semantic_reports["v6"])
    v5_benchmark = semantic_reports["v5"]["temporal_benchmark"]["summary"]
    v6_benchmark = semantic_reports["v6"]["temporal_benchmark"]["summary"]
    v5_summary = semantic_reports["v5"]["anchor_aware_semantic_summary"]
    v6_summary = semantic_reports["v6"]["anchor_aware_semantic_summary"]

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "policy_versions": {key: documents[key].get("policy_version") for key in POLICY_KEYS},
        "policies": semantic_reports,
        "historical_operator_review": historical,
        "v5_to_v6": {
            "semantic_increment": compare_semantic_policy_increment(semantic_reports["v5"], semantic_reports["v6"]),
            "delta": {
                "raw_candidates": len(documents["v6"].get("candidates") or []) - len(documents["v5"].get("candidates") or []),
                "review_clusters": historical["policies"]["v6"]["review_clusters"] - historical["policies"]["v5"]["review_clusters"],
                "validated_true": _number(v6_summary.get("validated_true")) - _number(v5_summary.get("validated_true")),
                "validated_false": _number(v6_summary.get("validated_false")) - _number(v5_summary.get("validated_false")),
                "hard_negative_hits": _number(v6_benchmark.get("hard_negative_hits")) - _number(v5_benchmark.get("hard_negative_hits")),
                "historical_accepted_kept": historical["policies"]["v6"]["accepted_operator_shots_kept"] - historical["policies"]["v5"]["accepted_operator_shots_kept"],
                "historical_rejected_surviving": historical["policies"]["v6"]["rejected_reviewed_candidates_surviving"] - historical["policies"]["v5"]["rejected_reviewed_candidates_surviving"],
            },
            "validated_true_retained_from_v5": sorted(v5_anchor_true & v6_anchor_true),
            "validated_true_lost_from_v5": sorted(v5_anchor_true - v6_anchor_true),
            "suppressed_candidate_count": len(v6_suppressed),
            "suppressed_reason_distribution": dict(sorted(_count_by_reason(v6_suppressed.values()).items())),
            "terminal_goal_approach_protected_candidates": sorted(
                candidate_id
                for candidate_id in v5_ids & v6_ids
                if "terminal_goal_approach" in _reasons(_candidate_by_id(documents["v6"], candidate_id))
            ),
        },
        "v5_only_audited_family": sorted(v5_audited_family, key=lambda row: (_number(row.get("candidate_timestamp_sec")), _text(row.get("candidate_id")))),
        "limitations": [
            "This report joins frozen and durable operator truth only after runtime candidate generation is complete.",
            "No goldset, audit, or editorial label is passed to the v6 generator.",
        ],
    }


def _v5_audit_row(
    audit: Mapping[str, Any],
    v5_ids: set[str],
    v6_ids: set[str],
    suppressed: Mapping[str, Mapping[str, Any]],
    v5_report: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_id = _text(audit.get("candidate_id"))
    semantic = next((row for row in v5_report.get("audited_v5_candidate_table") or [] if _text(row.get("candidate_id")) == candidate_id), {})
    suppression = suppressed.get(candidate_id, {})
    return {
        "candidate_id": candidate_id,
        "candidate_timestamp_sec": audit.get("candidate_timestamp_sec"),
        "operator_class": audit.get("operator_class"),
        "present_in_v5": candidate_id in v5_ids,
        "present_in_v6": candidate_id in v6_ids,
        "suppression_reason": suppression.get("suppression_reason"),
        "canonical_shot_id": audit.get("canonical_shot_id"),
        "semantic_verdict": semantic.get("semantic_match_status"),
    }


def _candidate_ids(document: Mapping[str, Any]) -> set[str]:
    return {
        _text(row.get("candidate_id"))
        for row in document.get("candidates") or []
        if isinstance(row, Mapping) and _text(row.get("candidate_id"))
    }


def _candidate_by_id(document: Mapping[str, Any], candidate_id: str) -> Mapping[str, Any]:
    return next((row for row in document.get("candidates") or [] if isinstance(row, Mapping) and _text(row.get("candidate_id")) == candidate_id), {})


def _validated_true_ids(report: Mapping[str, Any]) -> set[str]:
    return {
        _text(row.get("gold_shot_id"))
        for row in report.get("anchor_aware_semantic_matches") or []
        if isinstance(row, Mapping) and row.get("semantic_match_status") == "validated_true"
    }


def _count_by_reason(rows: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        reason = _text(row.get("suppression_reason")) or "unknown"
        result[reason] = result.get(reason, 0) + 1
    return result


def _reasons(candidate: Mapping[str, Any]) -> set[str]:
    return {str(reason) for reason in candidate.get("reasons") or []}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
