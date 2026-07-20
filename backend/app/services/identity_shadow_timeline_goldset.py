from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_shadow_timeline_goldset_evaluator"
ALGORITHM_VERSION = "0.1.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_shadow_timeline_goldset(
    reviewed_manifests: list[dict[str, Any]],
    *,
    goldset_id: str,
    version: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if not goldset_id.strip() or not version.strip():
        raise ValueError("goldset_id and version must not be empty")
    items: dict[tuple[str, str], dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    for manifest in reviewed_manifests:
        benchmark = manifest.get("benchmark") or {}
        benchmark_id = str(benchmark.get("benchmark_id") or "").strip()
        if not benchmark_id:
            raise ValueError("Reviewed manifest is missing benchmark.benchmark_id")
        sources.append({"benchmark_id": benchmark_id, "label": benchmark.get("label")})
        for item in manifest.get("items") or []:
            audit_key = str(item.get("audit_key") or "").strip()
            if not audit_key:
                raise ValueError(f"Reviewed manifest {benchmark_id} contains an item without audit_key")
            review = item.get("manual_review") or {}
            identity = str(review.get("identity_continuity") or "pending")
            state_assessment = str(review.get("state_assessment") or "pending")
            expected_identity = (
                True if identity == "same_person"
                else False if identity == "different_people"
                else None
            )
            original_status = (item.get("timeline_state") or {}).get("status")
            expected_state = (
                original_status if state_assessment == "correct"
                else state_assessment.removeprefix("should_be_")
                if state_assessment.startswith("should_be_")
                else None
            )
            row = {
                "benchmark_id": benchmark_id,
                "audit_key": audit_key,
                "audit_kind": item.get("audit_kind"),
                "shadow_subject_id": item.get("shadow_subject_id"),
                "start_frame": (item.get("timeline_state") or {}).get("start_frame"),
                "end_frame": (item.get("timeline_state") or {}).get("end_frame"),
                "expected_same_person": expected_identity,
                "expected_state": expected_state,
                "reviewer": review.get("reviewer"),
                "notes": review.get("notes") or "",
            }
            key = (benchmark_id, audit_key)
            previous = items.get(key)
            if previous is not None and _stable_item(previous) != _stable_item(row):
                raise ValueError(f"Conflicting shadow timeline reviews for {benchmark_id}/{audit_key}")
            items[key] = row
    rows = [items[key] for key in sorted(items)]
    digest_payload = {
        "goldset_id": goldset_id,
        "version": version,
        "items": [_stable_item(row) for row in rows],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "goldset_id": goldset_id,
        "version": version,
        "goldset_digest": _sha256(digest_payload),
        "summary": {
            "items": len(rows),
            "identity_labeled": sum(row["expected_same_person"] is not None for row in rows),
            "state_labeled": sum(row["expected_state"] is not None for row in rows),
        },
        "sources": sorted(sources, key=lambda row: str(row["benchmark_id"])),
        "items": rows,
    }


def evaluate_shadow_timeline_goldset(
    goldset: dict[str, Any],
    predictions_by_benchmark: dict[str, dict[str, Any]],
    *,
    min_state_accuracy: float = 0.75,
    max_identity_false_positives: int = 0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    prediction_indexes = {
        benchmark_id: _prediction_index(document)
        for benchmark_id, document in predictions_by_benchmark.items()
    }
    counters: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []
    missing_documents: set[str] = set()
    for item in goldset.get("items") or []:
        benchmark_id = str(item.get("benchmark_id") or "")
        if benchmark_id not in prediction_indexes:
            missing_documents.add(benchmark_id)
        prediction = _find_prediction(item, prediction_indexes.get(benchmark_id) or {})
        expected_identity = item.get("expected_same_person")
        expected_state = item.get("expected_state")
        continuity = (prediction or {}).get("identity_continuity_status", "supported")
        if expected_identity is not None:
            counters["identity_labeled"] += 1
            if continuity == "uncertain":
                counters["identity_abstained"] += 1
            elif bool(expected_identity):
                counters["identity_correct" if continuity == "supported" else "identity_false_negative"] += 1
            else:
                counters["identity_false_positive" if continuity == "supported" else "identity_correct"] += 1
        if expected_state is not None and expected_identity is not False:
            counters["state_labeled"] += 1
            if prediction and prediction.get("status") == expected_state:
                counters["state_correct"] += 1
            else:
                counters["state_wrong"] += 1
        if prediction is None or (
            expected_state is not None
            and expected_identity is not False
            and prediction.get("status") != expected_state
        ) or (expected_identity is False and continuity == "supported"):
            errors.append(
                {
                    "benchmark_id": benchmark_id,
                    "audit_key": item.get("audit_key"),
                    "expected_same_person": expected_identity,
                    "predicted_identity_continuity": continuity if prediction else None,
                    "expected_state": expected_state,
                    "predicted_state": (prediction or {}).get("status"),
                }
            )
    state_accuracy = (
        counters["state_correct"] / counters["state_labeled"]
        if counters["state_labeled"]
        else 0.0
    )
    gates = {
        "prediction_documents_present": not missing_documents,
        "state_accuracy": state_accuracy >= min_state_accuracy,
        "identity_false_positives": counters["identity_false_positive"] <= max_identity_false_positives,
    }
    summary_counters = {
        key: int(counters[key])
        for key in (
            "identity_labeled",
            "identity_correct",
            "identity_abstained",
            "identity_false_positive",
            "identity_false_negative",
            "state_labeled",
            "state_correct",
            "state_wrong",
        )
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "goldset": {
            "goldset_id": goldset.get("goldset_id"),
            "version": goldset.get("version"),
            "goldset_digest": goldset.get("goldset_digest"),
        },
        "status": "passed" if all(gates.values()) else "failed",
        "thresholds": {
            "min_state_accuracy": min_state_accuracy,
            "max_identity_false_positives": max_identity_false_positives,
        },
        "summary": {
            **summary_counters,
            "state_accuracy": round(state_accuracy, 6),
            "missing_prediction_documents": sorted(missing_documents),
        },
        "gates": gates,
        "errors": errors,
    }


def _prediction_index(document: dict[str, Any]) -> dict[str, Any]:
    events = {
        str(row.get("event_key")): row
        for row in document.get("transition_events") or []
        if row.get("event_key")
    }
    runs = {
        (
            str(subject.get("shadow_subject_id") or ""),
            int(run.get("start_frame") or 0),
            int(run.get("end_frame") or 0),
        ): run
        for subject in document.get("subjects") or []
        for run in subject.get("state_runs") or []
        if run.get("status") != "detected"
    }
    return {"events": events, "runs": runs}


def _find_prediction(item: dict[str, Any], index: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("audit_kind") == "accepted_transition":
        return (index.get("events") or {}).get(str(item.get("audit_key") or ""))
    key = (
        str(item.get("shadow_subject_id") or ""),
        int(item.get("start_frame") or 0),
        int(item.get("end_frame") or 0),
    )
    return (index.get("runs") or {}).get(key)


def _stable_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"reviewer", "notes"}
    }


def _sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
