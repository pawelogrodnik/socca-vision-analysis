from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_stitching_goldset_evaluator"
ALGORITHM_VERSION = "0.1.0"

REVIEW_OUTCOMES: dict[str, bool | None] = {
    "confirmed_same": True,
    "confirmed_different": False,
    "uncertain": None,
    "pending": None,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_identity_stitching_goldset(
    reviewed_manifests: list[dict[str, Any]],
    *,
    goldset_id: str,
    version: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compile reviewed visual-audit manifests into a deterministic benchmark set."""
    if not goldset_id.strip():
        raise ValueError("goldset_id must not be empty")
    if not version.strip():
        raise ValueError("version must not be empty")

    items_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    for manifest in reviewed_manifests:
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
                "stitching_algorithm": ((manifest.get("source") or {}).get("stitching_algorithm") or {}),
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
            explicit_value = review.get("same_person")
            if explicit_value is not None and explicit_value is not expected:
                raise ValueError(
                    f"Inconsistent review for {benchmark_id}/{candidate_key}: "
                    f"status={status}, same_person={explicit_value}"
                )
            source = item.get("source") or {}
            target = item.get("target") or {}
            decision = item.get("decision") or {}
            row = {
                "benchmark_id": benchmark_id,
                "benchmark_label": benchmark_label,
                "candidate_key": candidate_key,
                "source_tracklet_id": source.get("tracklet_id"),
                "target_tracklet_id": target.get("tracklet_id"),
                "review_status": status,
                "expected_same_person": expected,
                "reviewer": review.get("reviewer"),
                "reviewed_at": review.get("reviewed_at"),
                "notes": review.get("notes") or "",
                "audit_context": {
                    "audit_index": item.get("audit_index"),
                    "current_identity_relation": decision.get("current_identity_relation"),
                    "source_stable_subject_ids": decision.get("source_stable_subject_ids") or [],
                    "target_stable_subject_ids": decision.get("target_stable_subject_ids") or [],
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
        "sources": sorted(sources, key=lambda row: (row["benchmark_id"], row["benchmark_label"])),
        "items": items,
    }


def evaluate_identity_stitching_goldset(
    goldset: dict[str, Any],
    predictions_by_benchmark: dict[str, dict[str, Any]],
    *,
    min_precision: float = 0.95,
    min_recall: float = 0.0,
    min_labeled: int = 10,
    max_false_positives: int | None = 0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate shadow recommendations against reviewed same/different labels."""
    if not 0.0 <= min_precision <= 1.0 or not 0.0 <= min_recall <= 1.0:
        raise ValueError("Precision and recall thresholds must be between 0 and 1")
    if min_labeled < 1:
        raise ValueError("min_labeled must be at least 1")

    prediction_indexes = {
        benchmark_id: {
            str(row.get("candidate_key")): row
            for row in document.get("candidate_edges") or []
            if row.get("candidate_key")
        }
        for benchmark_id, document in predictions_by_benchmark.items()
    }
    cases: dict[str, list[dict[str, Any]]] = {}
    for row in goldset.get("items") or []:
        cases.setdefault(str(row.get("benchmark_id") or ""), []).append(row)

    case_reports: list[dict[str, Any]] = []
    totals = _empty_confusion()
    missing_prediction_documents: list[str] = []
    for benchmark_id in sorted(cases):
        prediction_index = prediction_indexes.get(benchmark_id)
        if prediction_index is None:
            missing_prediction_documents.append(benchmark_id)
            prediction_index = {}
        report = _evaluate_case(benchmark_id, cases[benchmark_id], prediction_index)
        case_reports.append(report)
        for key in totals:
            totals[key] += int(report["confusion"][key])

    metrics = _metrics(totals)
    labeled = totals["true_positive"] + totals["false_positive"] + totals["false_negative"] + totals["true_negative"]
    readiness_reasons: list[str] = []
    if labeled < min_labeled:
        readiness_reasons.append("insufficient_labeled_examples")
    if missing_prediction_documents:
        readiness_reasons.append("missing_prediction_documents")
    ready = not readiness_reasons
    gates = {
        "minimum_labeled_examples": labeled >= min_labeled,
        "prediction_documents_present": not missing_prediction_documents,
        "precision": ready and metrics["precision"] >= min_precision,
        "recall": ready and metrics["recall"] >= min_recall,
        "false_positives": ready and (
            max_false_positives is None or totals["false_positive"] <= max_false_positives
        ),
    }
    status = "not_ready" if not ready else "passed" if all(gates.values()) else "failed"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "goldset": {
            "goldset_id": goldset.get("goldset_id"),
            "version": goldset.get("version"),
            "goldset_digest": goldset.get("goldset_digest"),
        },
        "status": status,
        "readiness_reasons": readiness_reasons,
        "thresholds": {
            "min_precision": min_precision,
            "min_recall": min_recall,
            "min_labeled": min_labeled,
            "max_false_positives": max_false_positives,
        },
        "summary": {
            "labeled": labeled,
            "unlabeled": sum(int(row["summary"]["unlabeled"]) for row in case_reports),
            "missing_prediction_documents": missing_prediction_documents,
            "confusion": totals,
            **metrics,
        },
        "gates": gates,
        "cases": case_reports,
    }


def _evaluate_case(
    benchmark_id: str,
    gold_rows: list[dict[str, Any]],
    prediction_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    confusion = _empty_confusion()
    errors: list[dict[str, Any]] = []
    unlabeled = 0
    for gold in sorted(gold_rows, key=lambda row: str(row.get("candidate_key") or "")):
        expected = gold.get("expected_same_person")
        if expected is None:
            unlabeled += 1
            continue
        candidate_key = str(gold.get("candidate_key") or "")
        prediction = prediction_index.get(candidate_key)
        predicted = bool(prediction and prediction.get("recommended"))
        if expected and predicted:
            confusion["true_positive"] += 1
        elif not expected and predicted:
            confusion["false_positive"] += 1
            errors.append(_error_row(gold, prediction, "false_positive"))
        elif expected and not predicted:
            confusion["false_negative"] += 1
            errors.append(_error_row(gold, prediction, "false_negative"))
        else:
            confusion["true_negative"] += 1
    return {
        "benchmark_id": benchmark_id,
        "summary": {"labeled": len(gold_rows) - unlabeled, "unlabeled": unlabeled},
        "confusion": confusion,
        "metrics": _metrics(confusion),
        "errors": errors,
    }


def _error_row(
    gold: dict[str, Any],
    prediction: dict[str, Any] | None,
    error_type: str,
) -> dict[str, Any]:
    return {
        "error_type": error_type,
        "candidate_key": gold.get("candidate_key"),
        "source_tracklet_id": gold.get("source_tracklet_id"),
        "target_tracklet_id": gold.get("target_tracklet_id"),
        "expected_same_person": gold.get("expected_same_person"),
        "predicted_recommended": bool(prediction and prediction.get("recommended")),
        "cost": prediction.get("cost") if prediction else None,
        "base_confidence": prediction.get("base_confidence") if prediction else None,
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
        "source_tracklet_id": row.get("source_tracklet_id"),
        "target_tracklet_id": row.get("target_tracklet_id"),
        "review_status": row.get("review_status"),
        "expected_same_person": row.get("expected_same_person"),
        "notes": row.get("notes") or "",
    }


def _review_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("review_status"),
        row.get("expected_same_person"),
        row.get("source_tracklet_id"),
        row.get("target_tracklet_id"),
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
