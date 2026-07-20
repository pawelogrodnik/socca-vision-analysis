from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_fragment_consolidation_goldset_evaluator"
ALGORITHM_VERSION = "0.1.0"
POLICY_NAME = "strict_v1"

REVIEW_OUTCOMES: dict[str, bool | None] = {
    "confirmed_same": True,
    "confirmed_different": False,
    "uncertain": None,
    "pending": None,
}

DEFAULT_POLICY: dict[str, Any] = {
    "min_gap_frames": 1,
    "max_gap_seconds": 0.7,
    "min_confidence": 0.8,
    "max_endpoint_distance_m": 1.5,
    "max_required_speed_mps": 4.0,
    "min_active_ratio": 0.9,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_identity_fragment_consolidation_goldset(
    reviewed_manifests: list[dict[str, Any]],
    *,
    goldset_id: str,
    version: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compile reviewed P1.8 cards into a deterministic three-state goldset."""
    if not goldset_id.strip() or not version.strip():
        raise ValueError("goldset_id and version must not be empty")

    items_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    for manifest in reviewed_manifests:
        if str(manifest.get("audit_kind") or "") != "fragment_consolidation":
            raise ValueError("Reviewed manifest is not a fragment consolidation audit")
        benchmark = manifest.get("benchmark") or {}
        benchmark_id = str(benchmark.get("benchmark_id") or "").strip()
        benchmark_label = str(benchmark.get("label") or benchmark_id).strip()
        if not benchmark_id:
            raise ValueError("Reviewed manifest is missing benchmark.benchmark_id")
        sources.append(
            {
                "benchmark_id": benchmark_id,
                "benchmark_label": benchmark_label,
                "audit_algorithm": manifest.get("algorithm") or {},
                "consolidation_algorithm": (
                    (manifest.get("source") or {}).get("consolidation_algorithm") or {}
                ),
            }
        )
        for item in manifest.get("items") or []:
            candidate_key = str(item.get("candidate_key") or "").strip()
            if not candidate_key:
                raise ValueError(f"Reviewed manifest {benchmark_id} contains an item without candidate_key")
            review = item.get("manual_review") or {}
            status = str(review.get("status") or "pending")
            if status not in REVIEW_OUTCOMES:
                raise ValueError(f"Unsupported review status: {status}")
            expected = REVIEW_OUTCOMES[status]
            if review.get("same_person") is not None and review.get("same_person") is not expected:
                raise ValueError(
                    f"Inconsistent review for {benchmark_id}/{candidate_key}: "
                    f"status={status}, same_person={review.get('same_person')}"
                )
            source = item.get("source") or {}
            target = item.get("target") or {}
            decision = item.get("decision") or {}
            transition = item.get("transition") or {}
            row = {
                "benchmark_id": benchmark_id,
                "benchmark_label": benchmark_label,
                "candidate_key": candidate_key,
                "source_subject_id": source.get("raw_tracker_id"),
                "target_subject_id": target.get("raw_tracker_id"),
                "source_player_id": source.get("tracklet_id"),
                "target_player_id": target.get("tracklet_id"),
                "review_status": status,
                "expected_same_person": expected,
                "reviewer": review.get("reviewer"),
                "reviewed_at": review.get("reviewed_at"),
                "notes": review.get("notes") or "",
                "audit_context": {
                    "audit_index": item.get("audit_index"),
                    "decision": decision.get("source_quality_class"),
                    "confidence": decision.get("base_confidence"),
                    "gap_sec": transition.get("gap_sec"),
                    "distance_m": decision.get("distance_m"),
                    "required_speed_mps": decision.get("required_speed_mps"),
                    "reason_codes": decision.get("reason_codes") or [],
                },
            }
            compound_key = (benchmark_id, candidate_key)
            previous = items_by_key.get(compound_key)
            if previous is not None and _review_signature(previous) != _review_signature(row):
                raise ValueError(f"Conflicting reviews for {benchmark_id}/{candidate_key}")
            items_by_key[compound_key] = row

    items = [items_by_key[key] for key in sorted(items_by_key)]
    summary = _goldset_summary(items)
    digest_payload = {
        "goldset_id": goldset_id,
        "version": version,
        "items": [_stable_goldset_item(row) for row in items],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "goldset_id": goldset_id,
        "version": version,
        "goldset_digest": _sha256(digest_payload),
        "status": "ready" if summary["pending"] == 0 and summary["labeled"] > 0 else "needs_review",
        "summary": summary,
        "sources": sorted(sources, key=lambda row: row["benchmark_id"]),
        "items": items,
    }


def classify_fragment_consolidation_proposal(
    proposal: dict[str, Any],
    *,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a conservative shadow promotion decision for one P1.8 proposal."""
    params = {**DEFAULT_POLICY, **(parameters or {})}
    reasons: list[str] = []
    if str(proposal.get("decision") or "") != "recommended_review":
        reasons.append("not_recommended_for_review")
    gap_frames = int(proposal.get("gap_frames") or 0)
    gap_seconds = float(proposal.get("gap_seconds") or 0.0)
    if gap_frames < int(params["min_gap_frames"]):
        reasons.append("no_real_temporal_gap")
    if gap_seconds > float(params["max_gap_seconds"]):
        reasons.append("gap_too_long")
    if float(proposal.get("confidence") or 0.0) < float(params["min_confidence"]):
        reasons.append("confidence_too_low")
    distance = proposal.get("endpoint_distance_m")
    if distance is None or float(distance) > float(params["max_endpoint_distance_m"]):
        reasons.append("endpoint_distance_not_safe")
    speed = proposal.get("required_speed_mps")
    if speed is None or float(speed) > float(params["max_required_speed_mps"]):
        reasons.append("required_speed_not_safe")
    for key in ("source_active_ratio", "target_active_ratio"):
        ratio = proposal.get(key)
        if ratio is None or float(ratio) < float(params["min_active_ratio"]):
            reasons.append(f"{key}_too_low")
    source_team = str(proposal.get("source_team_label") or "U")
    target_team = str(proposal.get("target_team_label") or "U")
    if source_team not in {"A", "B"} or source_team != target_team:
        reasons.append("team_not_safe")
    if not str(proposal.get("shared_production_anchor") or ""):
        reasons.append("missing_safe_anchor")
    if proposal.get("reason_codes"):
        reasons.append("proposal_has_reason_codes")
    if int(proposal.get("overlap_frames") or 0) > 0:
        reasons.append("temporal_overlap")
    return {
        "policy": {"name": POLICY_NAME, "version": "0.1.0", "parameters": params},
        "decision": "auto_accept_shadow" if not reasons else "manual_review",
        "auto_accept": not reasons,
        "reason_codes": sorted(set(reasons)),
    }


def evaluate_identity_fragment_consolidation_goldset(
    goldset: dict[str, Any],
    predictions_by_benchmark: dict[str, dict[str, Any]],
    *,
    min_labeled: int = 100,
    min_precision: float = 1.0,
    max_false_merges: int = 0,
    max_uncertain_auto_accepts: int = 0,
    min_auto_accepts_per_benchmark: int = 1,
    policy_parameters: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate strict shadow auto-accept against reviewed same/different/uncertain cards."""
    prediction_indexes = {
        benchmark_id: {
            str(row.get("proposal_key") or ""): row
            for row in document.get("proposals") or []
            if row.get("proposal_key")
        }
        for benchmark_id, document in predictions_by_benchmark.items()
    }
    rows_by_benchmark: dict[str, list[dict[str, Any]]] = {}
    for row in goldset.get("items") or []:
        rows_by_benchmark.setdefault(str(row.get("benchmark_id") or ""), []).append(row)

    totals = _empty_confusion()
    case_reports: list[dict[str, Any]] = []
    missing_documents: list[str] = []
    uncertain_auto_accepts = 0
    for benchmark_id in sorted(rows_by_benchmark):
        prediction_index = prediction_indexes.get(benchmark_id)
        if prediction_index is None:
            missing_documents.append(benchmark_id)
            prediction_index = {}
        report = _evaluate_case(
            benchmark_id,
            rows_by_benchmark[benchmark_id],
            prediction_index,
            policy_parameters=policy_parameters,
        )
        case_reports.append(report)
        uncertain_auto_accepts += int(report["summary"]["uncertain_auto_accepts"])
        for key in totals:
            totals[key] += int(report["confusion"][key])

    metrics = _metrics(totals)
    labeled = sum(totals.values())
    source_recommendation_baseline = _source_recommendation_baseline(goldset.get("items") or [])
    auto_accepts_by_benchmark = {
        row["benchmark_id"]: int(row["summary"]["auto_accepts"])
        for row in case_reports
    }
    gates = {
        "minimum_labeled_examples": labeled >= min_labeled,
        "prediction_documents_present": not missing_documents,
        "precision": metrics["precision"] >= min_precision,
        "false_merges": totals["false_positive"] <= max_false_merges,
        "uncertain_auto_accepts": uncertain_auto_accepts <= max_uncertain_auto_accepts,
        "evidence_in_every_benchmark": bool(case_reports) and all(
            count >= min_auto_accepts_per_benchmark for count in auto_accepts_by_benchmark.values()
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "policy": {
            "name": POLICY_NAME,
            "version": "0.1.0",
            "parameters": {**DEFAULT_POLICY, **(policy_parameters or {})},
        },
        "goldset": {
            "goldset_id": goldset.get("goldset_id"),
            "version": goldset.get("version"),
            "goldset_digest": goldset.get("goldset_digest"),
        },
        "status": "passed" if all(gates.values()) else "failed",
        "thresholds": {
            "min_labeled": min_labeled,
            "min_precision": min_precision,
            "max_false_merges": max_false_merges,
            "max_uncertain_auto_accepts": max_uncertain_auto_accepts,
            "min_auto_accepts_per_benchmark": min_auto_accepts_per_benchmark,
        },
        "summary": {
            "labeled": labeled,
            "unlabeled": sum(int(row["summary"]["unlabeled"]) for row in case_reports),
            "auto_accepts": sum(auto_accepts_by_benchmark.values()),
            "auto_accepts_by_benchmark": auto_accepts_by_benchmark,
            "uncertain_auto_accepts": uncertain_auto_accepts,
            "missing_prediction_documents": missing_documents,
            "confusion": totals,
            **metrics,
        },
        "source_recommendation_baseline": source_recommendation_baseline,
        "gates": gates,
        "cases": case_reports,
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "auto_accept_is_advisory_only": True,
        },
    }


def _source_recommendation_baseline(items: list[dict[str, Any]]) -> dict[str, Any]:
    confusion = _empty_confusion()
    uncertain_selected = 0
    selected = 0
    for row in items:
        predicted = str((row.get("audit_context") or {}).get("decision") or "") == "recommended_review"
        if predicted:
            selected += 1
        expected = row.get("expected_same_person")
        if expected is None:
            if predicted:
                uncertain_selected += 1
            continue
        if expected and predicted:
            confusion["true_positive"] += 1
        elif not expected and predicted:
            confusion["false_positive"] += 1
        elif expected and not predicted:
            confusion["false_negative"] += 1
        else:
            confusion["true_negative"] += 1
    return {
        "selector": "source_decision_is_recommended_review",
        "selected": selected,
        "uncertain_selected": uncertain_selected,
        "confusion": confusion,
        **_metrics(confusion),
    }


def _evaluate_case(
    benchmark_id: str,
    gold_rows: list[dict[str, Any]],
    prediction_index: dict[str, dict[str, Any]],
    *,
    policy_parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    confusion = _empty_confusion()
    errors: list[dict[str, Any]] = []
    auto_accepts = 0
    uncertain_auto_accepts = 0
    unlabeled = 0
    missing_predictions = 0
    for gold in sorted(gold_rows, key=lambda row: str(row.get("candidate_key") or "")):
        key = str(gold.get("candidate_key") or "")
        prediction = prediction_index.get(key)
        if prediction is None:
            missing_predictions += 1
            selected = False
            policy = {"reason_codes": ["missing_prediction"]}
        else:
            policy = classify_fragment_consolidation_proposal(
                prediction,
                parameters=policy_parameters,
            )
            selected = bool(policy["auto_accept"])
        if selected:
            auto_accepts += 1
        expected = gold.get("expected_same_person")
        if expected is None:
            unlabeled += 1
            if selected:
                uncertain_auto_accepts += 1
                errors.append(_error_row(gold, prediction, policy, "uncertain_auto_accept"))
            continue
        if expected and selected:
            confusion["true_positive"] += 1
        elif not expected and selected:
            confusion["false_positive"] += 1
            errors.append(_error_row(gold, prediction, policy, "false_merge"))
        elif expected and not selected:
            confusion["false_negative"] += 1
        else:
            confusion["true_negative"] += 1
    return {
        "benchmark_id": benchmark_id,
        "summary": {
            "labeled": len(gold_rows) - unlabeled,
            "unlabeled": unlabeled,
            "auto_accepts": auto_accepts,
            "uncertain_auto_accepts": uncertain_auto_accepts,
            "missing_predictions": missing_predictions,
        },
        "confusion": confusion,
        "metrics": _metrics(confusion),
        "errors": errors,
    }


def _error_row(
    gold: dict[str, Any],
    prediction: dict[str, Any] | None,
    policy: dict[str, Any],
    error_type: str,
) -> dict[str, Any]:
    return {
        "error_type": error_type,
        "candidate_key": gold.get("candidate_key"),
        "source_player_id": gold.get("source_player_id"),
        "target_player_id": gold.get("target_player_id"),
        "review_status": gold.get("review_status"),
        "policy_reason_codes": policy.get("reason_codes") or [],
        "proposal": {
            "decision": prediction.get("decision") if prediction else None,
            "confidence": prediction.get("confidence") if prediction else None,
            "gap_seconds": prediction.get("gap_seconds") if prediction else None,
            "endpoint_distance_m": prediction.get("endpoint_distance_m") if prediction else None,
        },
    }


def _goldset_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "items": len(items),
        "labeled": sum(row["expected_same_person"] is not None for row in items),
        "confirmed_same": sum(row["expected_same_person"] is True for row in items),
        "confirmed_different": sum(row["expected_same_person"] is False for row in items),
        "uncertain": sum(row["review_status"] == "uncertain" for row in items),
        "pending": sum(row["review_status"] == "pending" for row in items),
    }


def _stable_goldset_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark_id": row.get("benchmark_id"),
        "candidate_key": row.get("candidate_key"),
        "source_subject_id": row.get("source_subject_id"),
        "target_subject_id": row.get("target_subject_id"),
        "review_status": row.get("review_status"),
        "expected_same_person": row.get("expected_same_person"),
        "notes": row.get("notes") or "",
    }


def _review_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("review_status"),
        row.get("expected_same_person"),
        row.get("source_subject_id"),
        row.get("target_subject_id"),
    )


def _empty_confusion() -> dict[str, int]:
    return {"true_positive": 0, "false_positive": 0, "false_negative": 0, "true_negative": 0}


def _metrics(confusion: dict[str, int]) -> dict[str, float]:
    true_positive = int(confusion["true_positive"])
    false_positive = int(confusion["false_positive"])
    false_negative = int(confusion["false_negative"])
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "precision": round(true_positive / precision_denominator, 6) if precision_denominator else 0.0,
        "recall": round(true_positive / recall_denominator, 6) if recall_denominator else 0.0,
    }


def _sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
