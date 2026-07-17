from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "joint_occlusion_assignment_goldset_evaluator"
ALGORITHM_VERSION = "0.3.0"
LABELED_STATUSES = {"assignment_a", "assignment_b", "partial", "neither"}
SUPPORTED_STATUSES = LABELED_STATUSES | {"uncertain", "pending"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_joint_assignment_goldset(
    reviewed_manifests: list[dict[str, Any]],
    *,
    goldset_id: str,
    version: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if not goldset_id.strip() or not version.strip():
        raise ValueError("goldset_id and version must not be empty")
    items_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    for manifest in reviewed_manifests:
        benchmark = manifest.get("benchmark") or {}
        benchmark_id = str(benchmark.get("benchmark_id") or "").strip()
        if not benchmark_id:
            raise ValueError("Reviewed manifest is missing benchmark.benchmark_id")
        sources.append(
            {
                "benchmark_id": benchmark_id,
                "benchmark_label": benchmark.get("label") or benchmark_id,
                "audit_algorithm": manifest.get("algorithm") or {},
                "assignment_algorithm": (manifest.get("source") or {}).get("assignment_algorithm") or {},
            }
        )
        for item in manifest.get("items") or []:
            case_key = str(item.get("case_key") or "").strip()
            if not case_key:
                raise ValueError(f"Reviewed manifest {benchmark_id} contains an item without case_key")
            review = item.get("manual_review") or {}
            status = str(review.get("status") or "pending")
            if status not in SUPPORTED_STATUSES:
                raise ValueError(f"Unsupported joint review status: {status}")
            expected = status if status in LABELED_STATUSES else None
            explicit = review.get("correct_assignment_id")
            expected_explicit = expected if expected in {"assignment_a", "assignment_b"} else None
            if explicit != expected_explicit:
                raise ValueError(
                    f"Inconsistent joint review for {benchmark_id}/{case_key}: "
                    f"status={status}, correct_assignment_id={explicit}"
                )
            assignments = {
                str(row.get("assignment_id")): row
                for row in item.get("assignments") or []
                if row.get("assignment_id")
            }
            if set(assignments) != {"assignment_a", "assignment_b"}:
                raise ValueError(f"Joint audit {benchmark_id}/{case_key} must contain assignments A and B")
            confirmed_pairs = _confirmed_pair_ids(review.get("confirmed_pairs") or [], assignments)
            if status == "partial" and len(confirmed_pairs) != 1:
                raise ValueError(f"Partial joint review {benchmark_id}/{case_key} must confirm exactly one pair")
            if status != "partial" and confirmed_pairs:
                raise ValueError(f"Only partial joint review may define confirmed_pairs: {benchmark_id}/{case_key}")
            expected_pairs = (
                _assignment_pair_ids(assignments[expected])
                if expected in {"assignment_a", "assignment_b"}
                else confirmed_pairs if expected == "partial"
                else set() if expected == "neither"
                else None
            )
            edge_labels = _edge_labels(assignments, expected_pairs=expected_pairs)
            row = {
                "benchmark_id": benchmark_id,
                "benchmark_label": benchmark.get("label") or benchmark_id,
                "case_key": case_key,
                "team_label": item.get("team_label"),
                "start_frame": (item.get("event") or {}).get("start_frame"),
                "end_frame": (item.get("event") or {}).get("end_frame"),
                "review_status": status,
                "expected_assignment_id": expected,
                "expected_pairs": [
                    {"source_tracklet_id": source, "target_tracklet_id": target}
                    for source, target in sorted(expected_pairs or set())
                ],
                "edge_labels": edge_labels,
                "reviewer": review.get("reviewer"),
                "reviewed_at": review.get("reviewed_at"),
                "notes": review.get("notes") or "",
            }
            compound_key = (benchmark_id, case_key)
            previous = items_by_key.get(compound_key)
            if previous is not None and _stable_item(previous) != _stable_item(row):
                raise ValueError(f"Conflicting joint reviews for {benchmark_id}/{case_key}")
            items_by_key[compound_key] = row
    items = [items_by_key[key] for key in sorted(items_by_key)]
    summary = _summary(items)
    digest_payload = {
        "goldset_id": goldset_id,
        "version": version,
        "items": [_stable_item(row) for row in items],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "goldset_id": goldset_id,
        "version": version,
        "goldset_digest": _sha256(digest_payload),
        "status": "ready" if summary["pending"] == 0 and summary["labeled_cases"] > 0 else "needs_review",
        "summary": summary,
        "sources": sorted(sources, key=lambda row: str(row["benchmark_id"])),
        "items": items,
    }


def evaluate_joint_assignment_goldset(
    goldset: dict[str, Any],
    predictions_by_benchmark: dict[str, dict[str, Any]],
    *,
    min_labeled_cases: int = 8,
    min_accuracy: float = 0.90,
    max_wrong_assignments: int = 0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if min_labeled_cases < 1 or not 0.0 <= min_accuracy <= 1.0:
        raise ValueError("Invalid evaluation thresholds")
    prediction_indexes = {
        benchmark_id: {
            str(row.get("case_key")): row
            for row in document.get("cases") or []
            if row.get("case_key")
        }
        for benchmark_id, document in predictions_by_benchmark.items()
    }
    counters: Counter[str] = Counter()
    edge_confusion = {"true_positive": 0, "false_positive": 0, "false_negative": 0, "true_negative": 0}
    errors: list[dict[str, Any]] = []
    missing_documents: set[str] = set()
    for item in goldset.get("items") or []:
        expected = item.get("expected_assignment_id")
        if expected is None:
            counters["unlabeled"] += 1
            continue
        benchmark_id = str(item.get("benchmark_id") or "")
        if benchmark_id not in prediction_indexes:
            missing_documents.add(benchmark_id)
        prediction = prediction_indexes.get(benchmark_id, {}).get(str(item.get("case_key") or ""))
        decision = (prediction or {}).get("decision") or {}
        predicted = decision.get("recommended_assignment_id")
        predicted_edges = _predicted_edge_set(prediction, predicted)
        expected_edges = {
            (str(pair.get("source_tracklet_id")), str(pair.get("target_tracklet_id")))
            for pair in item.get("expected_pairs") or []
        }
        if expected == "neither":
            matches_expected = predicted is None
        elif expected == "partial":
            matches_expected = predicted == "partial" and predicted_edges == expected_edges
        else:
            matches_expected = predicted == expected
        counters["labeled"] += 1
        if expected == "neither":
            if predicted is None:
                counters["correct"] += 1
            else:
                counters["wrong"] += 1
                counters["false_positive_assignment"] += 1
        elif expected == "partial":
            if predicted == "partial" and predicted_edges == expected_edges:
                counters["correct"] += 1
            elif predicted is None:
                counters["abstained"] += 1
            else:
                counters["wrong"] += 1
                counters["false_positive_assignment"] += 1
        elif predicted == expected:
            counters["correct"] += 1
        elif predicted is None:
            counters["abstained"] += 1
        else:
            counters["wrong"] += 1
        if not matches_expected:
            errors.append(
                {
                    "benchmark_id": benchmark_id,
                    "case_key": item.get("case_key"),
                    "expected_assignment_id": expected,
                    "predicted_assignment_id": predicted,
                    "expected_pairs": item.get("expected_pairs") or [],
                    "predicted_pairs": [
                        {"source_tracklet_id": source, "target_tracklet_id": target}
                        for source, target in sorted(predicted_edges)
                    ],
                }
            )
        for edge in item.get("edge_labels") or []:
            expected_same = edge.get("expected_same_person")
            if expected_same is None:
                continue
            pair = (str(edge.get("source_tracklet_id")), str(edge.get("target_tracklet_id")))
            predicted_same = pair in predicted_edges
            key = (
                "true_positive" if expected_same and predicted_same
                else "false_positive" if not expected_same and predicted_same
                else "false_negative" if expected_same and not predicted_same
                else "true_negative"
            )
            edge_confusion[key] += 1
    labeled = counters["labeled"]
    accuracy = counters["correct"] / labeled if labeled else 0.0
    ready = labeled >= min_labeled_cases and not missing_documents
    gates = {
        "minimum_labeled_cases": labeled >= min_labeled_cases,
        "prediction_documents_present": not missing_documents,
        "accuracy": ready and accuracy >= min_accuracy,
        "wrong_assignments": ready and counters["wrong"] <= max_wrong_assignments,
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
        "status": "not_ready" if not ready else "passed" if all(gates.values()) else "failed",
        "thresholds": {
            "min_labeled_cases": min_labeled_cases,
            "min_accuracy": min_accuracy,
            "max_wrong_assignments": max_wrong_assignments,
        },
        "summary": {
            "labeled_cases": labeled,
            "unlabeled_cases": counters["unlabeled"],
            "correct": counters["correct"],
            "wrong": counters["wrong"],
            "abstained": counters["abstained"],
            "false_positive_assignments": counters["false_positive_assignment"],
            "accuracy": round(accuracy, 6),
            "edge_confusion": edge_confusion,
            "missing_prediction_documents": sorted(missing_documents),
        },
        "gates": gates,
        "errors": errors,
    }


def _edge_labels(
    assignments: dict[str, dict[str, Any]],
    *,
    expected_pairs: set[tuple[str, str]] | None,
) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for assignment in assignments.values():
        for pair in assignment.get("pairs") or []:
            key = (str(pair.get("source_tracklet_id")), str(pair.get("target_tracklet_id")))
            rows[key] = {
                "source_tracklet_id": key[0],
                "target_tracklet_id": key[1],
                "expected_same_person": key in expected_pairs if expected_pairs is not None else None,
            }
    return [rows[key] for key in sorted(rows)]


def _assignment_pair_ids(assignment: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (str(pair.get("source_tracklet_id")), str(pair.get("target_tracklet_id")))
        for pair in assignment.get("pairs") or []
    }


def _confirmed_pair_ids(
    confirmed_pairs: list[dict[str, Any]],
    assignments: dict[str, dict[str, Any]],
) -> set[tuple[str, str]]:
    assignment_a_pairs = (assignments.get("assignment_a") or {}).get("pairs") or []
    source_ids = [str(pair.get("source_tracklet_id")) for pair in assignment_a_pairs]
    target_ids = [str(pair.get("target_tracklet_id")) for pair in assignment_a_pairs]
    aliases = {
        **{f"S{index + 1}": value for index, value in enumerate(source_ids)},
        **{f"T{index + 1}": value for index, value in enumerate(target_ids)},
    }
    valid_pairs = {pair for assignment in assignments.values() for pair in _assignment_pair_ids(assignment)}
    resolved: set[tuple[str, str]] = set()
    for pair in confirmed_pairs:
        source = aliases.get(str(pair.get("source")), str(pair.get("source_tracklet_id") or pair.get("source") or ""))
        target = aliases.get(str(pair.get("target")), str(pair.get("target_tracklet_id") or pair.get("target") or ""))
        candidate = (source, target)
        if candidate not in valid_pairs:
            raise ValueError(f"Confirmed partial pair is not part of the 2x2 assignment: {candidate}")
        resolved.add(candidate)
    return resolved


def _predicted_edge_set(prediction: dict[str, Any] | None, assignment_id: str | None) -> set[tuple[str, str]]:
    decision = (prediction or {}).get("decision") or {}
    if assignment_id == "partial":
        return {
            (str(pair.get("source_tracklet_id")), str(pair.get("target_tracklet_id")))
            for pair in decision.get("recommended_pairs") or []
        }
    assignment = next(
        (
            row
            for row in (prediction or {}).get("assignments") or []
            if str(row.get("assignment_id")) == str(assignment_id)
        ),
        None,
    )
    return {
        (str(pair.get("source_tracklet_id")), str(pair.get("target_tracklet_id")))
        for pair in (assignment or {}).get("pairs") or []
    }


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "items": len(items),
        "labeled_cases": sum(row["expected_assignment_id"] is not None for row in items),
        "assignment_a": sum(row["expected_assignment_id"] == "assignment_a" for row in items),
        "assignment_b": sum(row["expected_assignment_id"] == "assignment_b" for row in items),
        "partial": sum(row["expected_assignment_id"] == "partial" for row in items),
        "neither": sum(row["expected_assignment_id"] == "neither" for row in items),
        "uncertain": sum(row["review_status"] == "uncertain" for row in items),
        "pending": sum(row["review_status"] == "pending" for row in items),
        "positive_edge_labels": sum(
            edge.get("expected_same_person") is True for row in items for edge in row.get("edge_labels") or []
        ),
        "negative_edge_labels": sum(
            edge.get("expected_same_person") is False for row in items for edge in row.get("edge_labels") or []
        ),
    }


def _stable_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark_id": row.get("benchmark_id"),
        "case_key": row.get("case_key"),
        "review_status": row.get("review_status"),
        "expected_assignment_id": row.get("expected_assignment_id"),
        "expected_pairs": row.get("expected_pairs") or [],
        "edge_labels": row.get("edge_labels") or [],
        "notes": row.get("notes") or "",
    }


def _sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
