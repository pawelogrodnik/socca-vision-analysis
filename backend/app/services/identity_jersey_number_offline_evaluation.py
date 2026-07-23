from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_jersey_number_learned import (
    predict_identity_jersey_number_learned,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_offline_evaluation"
ALGORITHM_VERSION = "1.1.0"
DEFAULT_PARAMETERS = {
    "minimum_episode_support": 2,
    "minimum_subject_episode_support": 2,
}


def evaluate_identity_jersey_number_learned(
    dataset_doc: dict[str, Any],
    model_doc: dict[str, Any],
    *,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate crop, visibility-episode and subject consensus without changing identity."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    team_candidates = _team_candidate_numbers(dataset_doc)
    predictions = [
        prediction
        for sample in dataset_doc.get("samples") or []
        if isinstance(sample, dict) and sample.get("split") in {"validation", "heldout"}
        if (
            prediction := _predict_sample(
                sample,
                model_doc=model_doc,
                candidate_numbers=team_candidates.get(str(sample.get("team_label") or "U"), []),
            )
        )
        is not None
    ]
    episode_rows = _episode_predictions(
        predictions,
        minimum_support=int(params["minimum_episode_support"]),
    )
    subject_rows = _subject_predictions(
        episode_rows,
        minimum_support=int(params["minimum_subject_episode_support"]),
    )
    production_reasons: list[str] = []
    split_contract = dataset_doc.get("split_contract") or {}
    if not split_contract.get("production_eligible"):
        production_reasons.append("dataset_split_not_production_eligible")
    if any(
        row["split"] == "heldout" and row["result"] == "wrong_confirmed_number"
        for row in episode_rows
    ):
        production_reasons.append("heldout_false_confirmed_episode_read")
    if int((model_doc.get("production_gate") or {}).get("eligible") or 0) != 1:
        production_reasons.append("learned_model_not_production_eligible")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_offline_evaluation",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "source": {
            "dataset_digest": dataset_doc.get("dataset_digest") or canonical_digest(dataset_doc),
            "model_digest": model_doc.get("model_digest") or canonical_digest(model_doc),
        },
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_assignments": 0,
        },
        "production_gate": {
            "eligible": not production_reasons,
            "reason_codes": sorted(set(production_reasons)),
        },
        "crop_metrics": _crop_metrics(predictions),
        "episode_metrics": _episode_metrics(episode_rows),
        "subject_metrics": _subject_metrics(subject_rows),
        "slice_metrics": {
            "by_view": _slice_metrics(predictions, "view"),
            "by_split": _slice_metrics(predictions, "split"),
            "by_number_length": _slice_metrics(predictions, "number_length"),
            "by_team": _slice_metrics(predictions, "team_label"),
        },
        "predictions": predictions,
        "episodes": episode_rows,
        "subjects": subject_rows,
    }


def _predict_sample(
    sample: dict[str, Any],
    *,
    model_doc: dict[str, Any],
    candidate_numbers: list[str],
) -> dict[str, Any] | None:
    try:
        import cv2
    except ImportError:
        return None
    path = Path(str(sample.get("artifact_root") or "")) / str(sample.get("artifact") or "")
    image = cv2.imread(str(path))
    if image is None:
        return None
    prediction = predict_identity_jersey_number_learned(
        image,
        model_doc,
        candidate_numbers=candidate_numbers,
        artifact_kind=str(sample.get("artifact_kind") or "torso_crop"),
        bbox_xyxy=sample.get("bbox_xyxy"),
    )
    expected_state = str(sample.get("label_state") or "number_unreadable")
    expected_number = str(sample.get("number")) if sample.get("number") is not None else None
    predicted_number = prediction.get("candidate_number")
    result = _result(expected_state, expected_number, predicted_number)
    readability_threshold = float(
        (prediction.get("thresholds") or {}).get("minimum_readability") or 0.5
    )
    readability_score = float(prediction.get("readability_score") or 0.0)
    return {
        "sample_key": sample.get("sample_key"),
        "anchor_crop_id": sample.get("anchor_crop_id"),
        "source_match_key": sample.get("source_match_key"),
        "source_video_key": sample.get("source_video_key"),
        "candidate_subject_id": sample.get("candidate_subject_id"),
        "tracklet_id": sample.get("tracklet_id"),
        "visibility_episode_id": sample.get("visibility_episode_id"),
        "frame": sample.get("frame"),
        "team_label": sample.get("team_label"),
        "split": sample.get("split"),
        "view": sample.get("view"),
        "number_length": len(expected_number) if expected_number else 0,
        "expected_state": expected_state,
        "expected_number": expected_number,
        "expected_number_panel_visible": bool(sample.get("number_panel_visible")),
        "expected_clean_jersey_visible": bool(sample.get("clean_jersey_visible")),
        "predicted_number": predicted_number,
        "predicted_number_panel_visible": readability_score >= readability_threshold,
        "predicted_readable_number": predicted_number is not None,
        "result": result,
        "accepted": bool(prediction.get("accepted")),
        "calibrated_confidence": prediction.get("calibrated_confidence"),
        "confidence_tier": prediction.get("confidence_tier"),
        "raw_similarity": prediction.get("raw_similarity"),
        "raw_margin": prediction.get("raw_margin"),
        "readability_score": readability_score,
    }


def _result(
    expected_state: str,
    expected_number: str | None,
    predicted_number: str | None,
) -> str:
    if predicted_number is None:
        return "missed_number" if expected_state == "number_confirmed" else "correct_abstention"
    if expected_state != "number_confirmed":
        return (
            "false_number_on_plain_shirt"
            if expected_state == "number_absent"
            else "false_number_on_unreadable"
        )
    return "correct_number" if predicted_number == expected_number else "wrong_confirmed_number"


def _episode_predictions(
    predictions: list[dict[str, Any]],
    *,
    minimum_support: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        grouped[str(row.get("visibility_episode_id") or row["sample_key"])].append(row)
    episodes: list[dict[str, Any]] = []
    for episode_id, rows in sorted(grouped.items()):
        expected_votes = Counter(
            str(row["expected_number"])
            for row in rows
            if row["expected_state"] == "number_confirmed" and row["expected_number"] is not None
        )
        predicted_weights: dict[str, float] = defaultdict(float)
        predicted_support: Counter[str] = Counter()
        for row in rows:
            if row["predicted_number"] is None:
                continue
            number = str(row["predicted_number"])
            predicted_support[number] += 1
            predicted_weights[number] += float(row.get("calibrated_confidence") or 0.0)
        ranked = sorted(
            predicted_weights,
            key=lambda number: (predicted_weights[number], predicted_support[number], number),
            reverse=True,
        )
        predicted_number = ranked[0] if ranked and predicted_support[ranked[0]] >= minimum_support else None
        expected_number = expected_votes.most_common(1)[0][0] if expected_votes else None
        expected_state = (
            "number_confirmed"
            if expected_number is not None
            else "number_absent"
            if any(row["expected_state"] == "number_absent" for row in rows)
            else "number_unreadable"
        )
        episodes.append(
            {
                "visibility_episode_id": episode_id,
                "source_match_key": rows[0].get("source_match_key"),
                "source_video_key": rows[0].get("source_video_key"),
                "candidate_subject_id": rows[0].get("candidate_subject_id"),
                "tracklet_id": rows[0].get("tracklet_id"),
                "team_label": rows[0].get("team_label"),
                "split": rows[0].get("split"),
                "start_frame": min(int(row.get("frame") or 0) for row in rows),
                "end_frame": max(int(row.get("frame") or 0) for row in rows),
                "observations": len(rows),
                "expected_state": expected_state,
                "expected_number": expected_number,
                "predicted_number": predicted_number,
                "support": predicted_support.get(str(predicted_number), 0),
                "competing_numbers": max(0, len(predicted_support) - (1 if predicted_number else 0)),
                "result": _result(expected_state, expected_number, predicted_number),
            }
        )
    return episodes


def _subject_predictions(
    episodes: list[dict[str, Any]],
    *,
    minimum_support: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in episodes:
        grouped[(str(row.get("source_match_key")), str(row.get("candidate_subject_id")))].append(row)
    subjects: list[dict[str, Any]] = []
    for (match_key, subject_id), rows in sorted(grouped.items()):
        expected = Counter(
            str(row["expected_number"]) for row in rows if row.get("expected_number") is not None
        )
        predicted = Counter(
            str(row["predicted_number"]) for row in rows if row.get("predicted_number") is not None
        )
        expected_number = expected.most_common(1)[0][0] if expected else None
        predicted_number, support = predicted.most_common(1)[0] if predicted else (None, 0)
        if support < minimum_support:
            predicted_number = None
        subjects.append(
            {
                "source_match_key": match_key,
                "candidate_subject_id": subject_id,
                "team_label": rows[0].get("team_label"),
                "visibility_episodes": len(rows),
                "expected_number": expected_number,
                "predicted_number": predicted_number,
                "independent_episode_support": support,
                "competing_numbers": max(0, len(predicted) - (1 if predicted_number else 0)),
                "result": _result(
                    "number_confirmed" if expected_number is not None else "number_unreadable",
                    expected_number,
                    predicted_number,
                ),
            }
        )
    return subjects


def _crop_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _metrics(rows, unit="crop")


def _episode_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = _metrics(rows, unit="visibility_episode")
    metrics["competing_number_episodes"] = sum(int(row["competing_numbers"] > 0) for row in rows)
    return metrics


def _subject_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = _metrics(rows, unit="subject")
    metrics["subjects_with_strong_consensus"] = sum(
        row.get("predicted_number") is not None for row in rows
    )
    metrics["subjects_with_competing_numbers"] = sum(
        int(row["competing_numbers"] > 0) for row in rows
    )
    return metrics


def _metrics(rows: list[dict[str, Any]], *, unit: str) -> dict[str, Any]:
    counts = Counter(str(row.get("result")) for row in rows)
    expected_positive = sum(row.get("expected_number") is not None for row in rows)
    predicted_positive = sum(row.get("predicted_number") is not None for row in rows)
    correct = counts["correct_number"]
    false_reads = (
        counts["wrong_confirmed_number"]
        + counts["false_number_on_plain_shirt"]
        + counts["false_number_on_unreadable"]
    )
    metrics = {
        "unit": unit,
        "reviewed": len(rows),
        "expected_readable": expected_positive,
        "predicted_readable": predicted_positive,
        "correct_reads": correct,
        "wrong_reads": false_reads,
        "missed_reads": counts["missed_number"],
        "precision": round(correct / predicted_positive, 4) if predicted_positive else None,
        "recall": round(correct / expected_positive, 4) if expected_positive else None,
        "false_confirmed_reads_total": false_reads,
        "false_confirmed_reads_numbered_player": counts["wrong_confirmed_number"],
        "false_confirmed_reads_plain_shirt": counts["false_number_on_plain_shirt"],
        "false_confirmed_reads_unreadable": counts["false_number_on_unreadable"],
        "result_counts": dict(sorted(counts.items())),
    }
    if unit == "crop":
        metrics["number_panel_detection"] = _binary_metrics(
            rows,
            expected_field="expected_number_panel_visible",
            predicted_field="predicted_number_panel_visible",
        )
        metrics["readable_number_detection"] = _binary_metrics(
            rows,
            expected=lambda row: row.get("expected_state") == "number_confirmed",
            predicted_field="predicted_readable_number",
        )
        plain_shirts = [
            row for row in rows if row.get("expected_state") == "number_absent"
        ]
        unreadable = [
            row for row in rows if row.get("expected_state") == "number_unreadable"
        ]
        metrics["plain_shirt_hallucination_rate"] = _rate(
            sum(row.get("predicted_number") is not None for row in plain_shirts),
            len(plain_shirts),
        )
        metrics["unreadable_false_read_rate"] = _rate(
            sum(row.get("predicted_number") is not None for row in unreadable),
            len(unreadable),
        )
    return metrics


def _binary_metrics(
    rows: list[dict[str, Any]],
    *,
    expected_field: str | None = None,
    expected: Any = None,
    predicted_field: str,
) -> dict[str, Any]:
    true_positive = false_positive = false_negative = true_negative = 0
    for row in rows:
        expected_value = bool(
            expected(row) if callable(expected) else row.get(str(expected_field))
        )
        predicted_value = bool(row.get(predicted_field))
        if expected_value and predicted_value:
            true_positive += 1
        elif predicted_value:
            false_positive += 1
        elif expected_value:
            false_negative += 1
        else:
            true_negative += 1
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": _rate(true_positive, true_positive + false_positive),
        "recall": _rate(true_positive, true_positive + false_negative),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _slice_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    return {key: _metrics(values, unit="crop") for key, values in sorted(grouped.items())}


def _team_candidate_numbers(dataset_doc: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for row in dataset_doc.get("samples") or []:
        if row.get("label_state") == "number_confirmed" and row.get("number") is not None:
            result[str(row.get("team_label") or "U")].add(str(row["number"]))
    return {
        team: sorted(numbers, key=lambda value: (len(value), value))
        for team, numbers in sorted(result.items())
    }
