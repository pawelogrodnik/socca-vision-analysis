from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest, normalize_jersey_number


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_jersey_number_closed_set_diagnostic_v1"
ALGORITHM_VERSION = "1.1.0"
DEFAULT_PARAMETERS = {
    "feature_width": 24,
    "feature_height": 32,
    "minimum_similarity": 0.58,
    "minimum_margin": 0.025,
    "minimum_train_samples_per_number": 1,
}


def train_identity_jersey_number_learned_baseline(
    dataset_doc: dict[str, Any],
    *,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Train a deterministic real-crop centroid baseline in shadow mode."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    vectors_by_number: dict[str, list[Any]] = defaultdict(list)
    readable_vectors: list[Any] = []
    absent_vectors: list[Any] = []
    unavailable: list[str] = []

    for sample in dataset_doc.get("samples") or []:
        if not isinstance(sample, dict) or sample.get("split") != "train":
            continue
        vector = extract_jersey_number_feature_from_sample(sample, params)
        if vector is None:
            unavailable.append(str(sample.get("sample_key") or ""))
            continue
        state = str(sample.get("label_state") or "")
        if state == "number_confirmed":
            number = normalize_jersey_number(sample.get("number"))
            if number is not None:
                vectors_by_number[number].append(vector)
                readable_vectors.append(vector)
        elif state == "number_absent":
            absent_vectors.append(vector)

    prototypes = {
        number: _mean_vector(vectors)
        for number, vectors in sorted(vectors_by_number.items())
        if len(vectors) >= int(params["minimum_train_samples_per_number"])
    }
    candidate_vocabulary = sorted(prototypes, key=lambda value: (len(value), value))
    candidate_vocabulary_digest = canonical_digest(candidate_vocabulary)
    validation_rows = _validation_predictions(
        dataset_doc,
        prototypes=prototypes,
        readable_centroid=_mean_vector(readable_vectors) if readable_vectors else None,
        absent_centroid=_mean_vector(absent_vectors) if absent_vectors else None,
        parameters=params,
    )
    calibration = _calibrate_thresholds(validation_rows, params)
    source_matches = sorted(
        {
            str(row.get("source_match_key"))
            for row in dataset_doc.get("samples") or []
            if row.get("source_match_key")
        }
    )
    split_contract = dataset_doc.get("split_contract") or {}
    reason_codes = ["shadow_baseline_not_production_validated"]
    if not bool(split_contract.get("production_eligible")) or len(source_matches) < 3:
        reason_codes.append("insufficient_independent_source_matches")
    model_payload = {
        "prototypes": prototypes,
        "readable_centroid": _mean_vector(readable_vectors) if readable_vectors else None,
        "absent_centroid": _mean_vector(absent_vectors) if absent_vectors else None,
        "calibration": calibration,
        "parameters": params,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
            "capabilities": {
                "diagnostic_only": True,
                "production_activation_eligible": False,
                "recognition_mode": "closed_set_diagnostic_v1",
            },
        },
        "model_digest": canonical_digest(model_payload),
        "source": {
            "dataset_digest": dataset_doc.get("dataset_digest") or canonical_digest(dataset_doc),
            "dataset_version": dataset_doc.get("dataset_version"),
            "source_match_keys": source_matches,
        },
        "production_gate": {
            "eligible": False,
            "reason_codes": reason_codes,
        },
        "summary": {
            "trained_numbers": candidate_vocabulary,
            "number_samples": {
                number: len(vectors) for number, vectors in sorted(vectors_by_number.items())
            },
            "readable_samples": len(readable_vectors),
            "absent_samples": len(absent_vectors),
            "unavailable_train_artifacts": len(unavailable),
            "validation_samples": len(validation_rows),
        },
        "feature_contract": {
            "name": "normalized_gray_gradient_torso_v1",
            "dimension": len(next(iter(prototypes.values()), [])),
            "uses_real_annotated_crops": True,
            "uses_synthetic_font_templates": False,
        },
        "calibration": calibration,
        "candidate_vocabulary": candidate_vocabulary,
        "candidate_vocabulary_digest": candidate_vocabulary_digest,
        "candidate_vocabulary_source": "train_prototypes_only",
        "prototypes": prototypes,
        "readable_centroid": model_payload["readable_centroid"],
        "absent_centroid": model_payload["absent_centroid"],
    }


def predict_identity_jersey_number_learned(
    image: Any,
    model_doc: dict[str, Any],
    *,
    candidate_numbers: list[str],
    artifact_kind: str = "torso_crop",
    bbox_xyxy: list[float] | None = None,
) -> dict[str, Any]:
    params = {**DEFAULT_PARAMETERS, **((model_doc.get("algorithm") or {}).get("parameters") or {})}
    vector = extract_jersey_number_feature(
        image,
        parameters=params,
        artifact_kind=artifact_kind,
        bbox_xyxy=bbox_xyxy,
    )
    prototypes = model_doc.get("prototypes") or {}
    normalized_candidates = [
        number
        for value in candidate_numbers
        if (number := normalize_jersey_number(value)) is not None and number in prototypes
    ]
    scores = sorted(
        (
            (_cosine(vector, prototypes[number]), number)
            for number in sorted(set(normalized_candidates))
        ),
        reverse=True,
    )
    readable_score = _readability_score(
        vector,
        model_doc.get("readable_centroid"),
        model_doc.get("absent_centroid"),
    )
    calibration = model_doc.get("calibration") or {}
    similarity_threshold = float(
        calibration.get("minimum_similarity") or params["minimum_similarity"]
    )
    margin_threshold = float(calibration.get("minimum_margin") or params["minimum_margin"])
    best_score, best_number = scores[0] if scores else (0.0, None)
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    margin = best_score - second_score
    calibrated = _calibrated_confidence(
        best_score=best_score,
        margin=margin,
        readable_score=readable_score,
        minimum_similarity=similarity_threshold,
        minimum_margin=margin_threshold,
    )
    accepted = bool(
        best_number is not None
        and best_score >= similarity_threshold
        and margin >= margin_threshold
        and readable_score >= 0.5
    )
    return {
        "candidate_number": best_number if accepted else None,
        "raw_candidate_number": best_number,
        "raw_similarity": round(best_score, 6),
        "raw_margin": round(margin, 6),
        "readability_score": round(readable_score, 6),
        "calibrated_confidence": round(calibrated, 6),
        "confidence_tier": (
            "high" if calibrated >= 0.85 else "medium" if calibrated >= 0.65 else "low"
        ),
        "accepted": accepted,
        "candidate_scores": [
            {"number": number, "score": round(score, 6)} for score, number in scores[:5]
        ],
        "thresholds": {
            "minimum_similarity": similarity_threshold,
            "minimum_margin": margin_threshold,
            "minimum_readability": 0.5,
        },
    }


def extract_jersey_number_feature_from_sample(
    sample: dict[str, Any],
    parameters: dict[str, Any] | None = None,
) -> Any | None:
    try:
        import cv2
    except ImportError:
        return None
    path = Path(str(sample.get("artifact_root") or "")) / str(sample.get("artifact") or "")
    image = cv2.imread(str(path))
    if image is None:
        return None
    return extract_jersey_number_feature(
        image,
        parameters={**DEFAULT_PARAMETERS, **(parameters or {})},
        artifact_kind=str(sample.get("artifact_kind") or "torso_crop"),
        bbox_xyxy=sample.get("bbox_xyxy"),
    )


def extract_jersey_number_feature(
    image: Any,
    *,
    parameters: dict[str, Any],
    artifact_kind: str,
    bbox_xyxy: list[float] | None,
) -> Any:
    import cv2
    import numpy as np

    person = image
    if artifact_kind == "anchor_crop":
        from app.services.identity_jersey_number_recognizer_shadow import _extract_person_crop

        person = _extract_person_crop(image, {"bbox_xyxy": bbox_xyxy})
    height, width = person.shape[:2]
    torso = person[
        0:max(1, int(height * 0.72)),
        max(0, int(width * 0.04)):max(1, int(width * 0.96)),
    ]
    gray = cv2.cvtColor(torso, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(
        gray,
        (int(parameters["feature_width"]), int(parameters["feature_height"])),
        interpolation=cv2.INTER_AREA,
    )
    gray = cv2.equalizeHist(gray).astype(np.float32) / 255.0
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gradient_x, gradient_y)
    compact_gray = cv2.resize(gray, (12, 16), interpolation=cv2.INTER_AREA)
    compact_gradient = cv2.resize(magnitude, (12, 16), interpolation=cv2.INTER_AREA)
    vector = np.concatenate((compact_gray.reshape(-1), compact_gradient.reshape(-1)))
    norm = float(np.linalg.norm(vector))
    return (vector / norm if norm > 0 else vector).astype(float).tolist()


def _validation_predictions(
    dataset_doc: dict[str, Any],
    *,
    prototypes: dict[str, list[float]],
    readable_centroid: list[float] | None,
    absent_centroid: list[float] | None,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    model = {
        "algorithm": {"parameters": parameters},
        "prototypes": prototypes,
        "readable_centroid": readable_centroid,
        "absent_centroid": absent_centroid,
        "calibration": {},
    }
    rows: list[dict[str, Any]] = []
    candidates = list(prototypes)
    for sample in dataset_doc.get("samples") or []:
        if not isinstance(sample, dict) or sample.get("split") != "validation":
            continue
        try:
            import cv2
        except ImportError:
            break
        path = Path(str(sample.get("artifact_root") or "")) / str(sample.get("artifact") or "")
        image = cv2.imread(str(path))
        if image is None:
            continue
        prediction = predict_identity_jersey_number_learned(
            image,
            model,
            candidate_numbers=candidates,
            artifact_kind=str(sample.get("artifact_kind") or "torso_crop"),
            bbox_xyxy=sample.get("bbox_xyxy"),
        )
        rows.append(
            {
                "expected_state": sample.get("label_state"),
                "expected_number": sample.get("number"),
                **prediction,
            }
        )
    return rows


def _calibrate_thresholds(
    rows: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    if not rows:
        return {
            "status": "insufficient_validation_data",
            "minimum_similarity": parameters["minimum_similarity"],
            "minimum_margin": parameters["minimum_margin"],
            "validation_samples": 0,
        }
    positives = [row for row in rows if row["expected_state"] == "number_confirmed"]
    negatives = [row for row in rows if row["expected_state"] == "number_absent"]
    candidate_thresholds = [round(value / 100, 2) for value in range(45, 91, 2)]
    best = None
    for similarity in candidate_thresholds:
        for margin in (0.0, 0.015, 0.025, 0.04, 0.06, 0.08):
            true_positive = sum(
                row["raw_similarity"] >= similarity
                and row["raw_margin"] >= margin
                and row["readability_score"] >= 0.5
                and row["raw_candidate_number"] == row["expected_number"]
                for row in positives
            )
            false_positive = sum(
                row["raw_similarity"] >= similarity
                and row["raw_margin"] >= margin
                and row["readability_score"] >= 0.5
                for row in negatives
            )
            score = (false_positive == 0, true_positive, similarity, margin)
            if best is None or score > best[0]:
                best = (score, similarity, margin, true_positive, false_positive)
    assert best is not None
    return {
        "status": "measured_single_match_fallback",
        "minimum_similarity": best[1],
        "minimum_margin": best[2],
        "validation_samples": len(rows),
        "validation_numbered_samples": len(positives),
        "validation_plain_shirt_samples": len(negatives),
        "number_correct_at_threshold": best[3],
        "plain_shirt_false_reads_at_threshold": best[4],
        "production_calibrated": False,
    }


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    import numpy as np

    value = np.asarray(vectors, dtype=np.float32).mean(axis=0)
    norm = float(np.linalg.norm(value))
    return (value / norm if norm > 0 else value).astype(float).tolist()


def _cosine(left: list[float], right: list[float]) -> float:
    import numpy as np

    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator else 0.0


def _readability_score(
    vector: list[float],
    readable_centroid: list[float] | None,
    absent_centroid: list[float] | None,
) -> float:
    if not readable_centroid:
        return 0.0
    positive = _cosine(vector, readable_centroid)
    negative = _cosine(vector, absent_centroid) if absent_centroid else 0.0
    return max(0.0, min(1.0, 0.5 + (positive - negative) * 2.0))


def _calibrated_confidence(
    *,
    best_score: float,
    margin: float,
    readable_score: float,
    minimum_similarity: float,
    minimum_margin: float,
) -> float:
    similarity_component = max(
        0.0,
        min(1.0, (best_score - minimum_similarity) / max(0.01, 1.0 - minimum_similarity)),
    )
    margin_component = max(
        0.0,
        min(1.0, margin / max(0.02, minimum_margin * 3.0)),
    )
    return similarity_component * 0.55 + margin_component * 0.25 + readable_score * 0.20
