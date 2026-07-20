from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.identity_fragment_consolidation_goldset import (
    classify_fragment_consolidation_proposal,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_fragment_visual_content_gate"
ALGORITHM_VERSION = "0.1.0"
POLICY_NAME = "strict_identity_plus_visual_content_v1"


def classify_with_visual_content_gate(
    proposal: dict[str, Any],
    pair_evidence: dict[str, Any] | None,
    *,
    strict_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply visual-content evidence as a fail-closed guard around strict P1.9."""
    strict = classify_fragment_consolidation_proposal(
        proposal,
        parameters=strict_parameters,
    )
    quality = str((pair_evidence or {}).get("quality") or "missing")
    content_reasons: list[str] = []
    if pair_evidence is None:
        content_reasons.append("visual_content_evidence_missing")
    elif quality == "invalid_content":
        content_reasons.append("endpoint_not_person")
    elif quality == "unclear":
        content_reasons.append("visual_content_unclear")
    elif quality != "person_content_supported":
        content_reasons.append("visual_content_unavailable")

    strict_passed = bool(strict.get("auto_accept"))
    content_passed = not content_reasons
    auto_accept = strict_passed and content_passed
    reasons = sorted(set((strict.get("reason_codes") or []) + content_reasons))
    return {
        "policy": {
            "name": POLICY_NAME,
            "version": ALGORITHM_VERSION,
            "strict_policy": strict.get("policy") or {},
        },
        "decision": "auto_accept_shadow" if auto_accept else "manual_review",
        "auto_accept": auto_accept,
        "strict_gate_passed": strict_passed,
        "visual_content_gate_passed": content_passed,
        "visual_content_quality": quality,
        "reason_codes": reasons,
        "advisory_only": True,
        "safe_for_production_identity_merge": False,
    }


def evaluate_visual_content_gate(
    goldset: dict[str, Any],
    predictions_by_benchmark: dict[str, dict[str, Any]],
    content_by_benchmark: dict[str, dict[str, Any]],
    *,
    strict_parameters: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate the composed P1.9 + P1.11 gate without promoting identity."""
    prediction_indexes = {
        benchmark_id: {
            str(row.get("proposal_key") or ""): row
            for row in document.get("proposals") or []
            if row.get("proposal_key")
        }
        for benchmark_id, document in predictions_by_benchmark.items()
    }
    content_indexes = {
        benchmark_id: {
            str(row.get("proposal_key") or ""): row
            for row in document.get("pairs") or []
            if row.get("proposal_key")
        }
        for benchmark_id, document in content_by_benchmark.items()
    }

    confusion = _empty_confusion()
    strict_auto_accepts = 0
    gated_auto_accepts = 0
    uncertain_auto_accepts = 0
    missing_predictions: list[dict[str, str]] = []
    missing_content_pairs: list[dict[str, str]] = []
    errors: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    quality_counts: Counter[str] = Counter()
    blocked_strict_counts: Counter[str] = Counter()
    per_benchmark: dict[str, Counter[str]] = {}

    for gold in sorted(
        goldset.get("items") or [],
        key=lambda row: (str(row.get("benchmark_id") or ""), str(row.get("candidate_key") or "")),
    ):
        benchmark_id = str(gold.get("benchmark_id") or "")
        candidate_key = str(gold.get("candidate_key") or "")
        proposal = prediction_indexes.get(benchmark_id, {}).get(candidate_key)
        pair_evidence = content_indexes.get(benchmark_id, {}).get(candidate_key)
        benchmark_counts = per_benchmark.setdefault(benchmark_id, Counter())
        if proposal is None:
            missing_predictions.append(
                {"benchmark_id": benchmark_id, "candidate_key": candidate_key}
            )
            decision = {
                "decision": "manual_review",
                "auto_accept": False,
                "strict_gate_passed": False,
                "visual_content_gate_passed": False,
                "visual_content_quality": "missing",
                "reason_codes": ["missing_prediction"],
            }
        else:
            decision = classify_with_visual_content_gate(
                proposal,
                pair_evidence,
                strict_parameters=strict_parameters,
            )
            if decision["strict_gate_passed"]:
                strict_auto_accepts += 1
                benchmark_counts["strict_auto_accepts"] += 1
            if decision["auto_accept"]:
                gated_auto_accepts += 1
                benchmark_counts["gated_auto_accepts"] += 1
            elif decision["strict_gate_passed"]:
                blocked_strict_counts[str(decision["visual_content_quality"])] += 1

        if pair_evidence is None:
            missing_content_pairs.append(
                {"benchmark_id": benchmark_id, "candidate_key": candidate_key}
            )
        quality = str(decision.get("visual_content_quality") or "missing")
        quality_counts[quality] += 1
        benchmark_counts[f"content_{quality}"] += 1

        expected = gold.get("expected_same_person")
        selected = bool(decision.get("auto_accept"))
        if expected is None:
            if selected:
                uncertain_auto_accepts += 1
                errors.append(_error_row(gold, decision, "uncertain_auto_accept"))
        elif expected and selected:
            confusion["true_positive"] += 1
        elif not expected and selected:
            confusion["false_positive"] += 1
            errors.append(_error_row(gold, decision, "false_merge"))
        elif expected:
            confusion["false_negative"] += 1
        else:
            confusion["true_negative"] += 1

        decisions.append(
            {
                "benchmark_id": benchmark_id,
                "candidate_key": candidate_key,
                "review_status": gold.get("review_status"),
                "expected_same_person": expected,
                **decision,
            }
        )

    metrics = _metrics(confusion)
    gates = {
        "all_predictions_present": not missing_predictions,
        "all_content_pairs_present": not missing_content_pairs,
        "zero_false_merges": confusion["false_positive"] == 0,
        "zero_uncertain_auto_accepts": uncertain_auto_accepts == 0,
        "advisory_only": all(
            row.get("safe_for_production_identity_merge") is not True
            for row in decisions
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "policy": {"name": POLICY_NAME, "version": ALGORITHM_VERSION},
        "goldset": {
            "goldset_id": goldset.get("goldset_id"),
            "version": goldset.get("version"),
            "goldset_digest": goldset.get("goldset_digest"),
        },
        "status": "passed" if all(gates.values()) else "failed",
        "summary": {
            "goldset_items": len(goldset.get("items") or []),
            "strict_auto_accepts": strict_auto_accepts,
            "gated_auto_accepts": gated_auto_accepts,
            "strict_candidates_blocked_by_content": strict_auto_accepts - gated_auto_accepts,
            "blocked_strict_quality_counts": dict(sorted(blocked_strict_counts.items())),
            "uncertain_auto_accepts": uncertain_auto_accepts,
            "content_quality_counts": dict(sorted(quality_counts.items())),
            "confusion": confusion,
            **metrics,
            "per_benchmark": {
                benchmark_id: dict(sorted(counts.items()))
                for benchmark_id, counts in sorted(per_benchmark.items())
            },
        },
        "gates": gates,
        "missing_predictions": missing_predictions,
        "missing_content_pairs": missing_content_pairs,
        "errors": errors,
        "decisions": decisions,
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "auto_accept_is_advisory_only": True,
        },
        "limitations": [
            "Person-content evidence verifies endpoint content, not same-person identity.",
            "Unreviewed, unavailable, and unclear endpoint content always abstains.",
            "The gate cannot increase strict-policy recall; it can only preserve or reduce it.",
        ],
    }


def _error_row(
    gold: dict[str, Any],
    decision: dict[str, Any],
    error_type: str,
) -> dict[str, Any]:
    return {
        "error_type": error_type,
        "benchmark_id": gold.get("benchmark_id"),
        "candidate_key": gold.get("candidate_key"),
        "review_status": gold.get("review_status"),
        "visual_content_quality": decision.get("visual_content_quality"),
        "reason_codes": decision.get("reason_codes") or [],
    }


def _empty_confusion() -> dict[str, int]:
    return {
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
        "true_negative": 0,
    }


def _metrics(confusion: dict[str, int]) -> dict[str, float]:
    true_positive = int(confusion["true_positive"])
    false_positive = int(confusion["false_positive"])
    false_negative = int(confusion["false_negative"])
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "precision": round(true_positive / precision_denominator, 6)
        if precision_denominator
        else 0.0,
        "recall": round(true_positive / recall_denominator, 6)
        if recall_denominator
        else 0.0,
    }
