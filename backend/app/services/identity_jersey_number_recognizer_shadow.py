from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import (
    canonical_digest,
    normalize_jersey_number,
    stable_key,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_recognizer_shadow"
ALGORITHM_VERSION = "0.1.0"
DEFAULT_PARAMETERS: dict[str, Any] = {
    "number_roi_normalized": [0.20, 0.08, 0.80, 0.62],
    "minimum_template_score": 0.72,
    "minimum_score_margin": 0.08,
    "minimum_foreground_ratio": 0.01,
    "maximum_foreground_ratio": 0.38,
}


def build_identity_jersey_number_recognizer_shadow(
    anchor_crops_doc: dict[str, Any],
    roster_doc: dict[str, Any],
    *,
    crop_root: Path,
    reviewed_observations_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recognize roster-constrained numbers from a torso ROI without changing identity."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    roster_numbers = sorted(
        {
            str(row["jersey_number"])
            for row in roster_doc.get("players") or []
            if row.get("jersey_number_trusted")
            and row.get("roster_number_status") == "confirmed"
            and row.get("jersey_number") is not None
        },
        key=lambda value: (len(value), value),
    )
    observations: list[dict[str, Any]] = []
    for subject in anchor_crops_doc.get("cards") or []:
        if not isinstance(subject, dict):
            continue
        for crop in subject.get("anchor_crops") or []:
            if not isinstance(crop, dict) or not crop.get("anchor_crop_id"):
                continue
            observations.append(
                _recognize_crop(
                    crop,
                    subject=subject,
                    crop_root=crop_root,
                    roster_numbers=roster_numbers,
                    parameters=params,
                )
            )
    metrics = _evaluate(observations, reviewed_observations_doc or {})
    states = Counter(str(row.get("state") or "number_unreadable") for row in observations)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "source": {
            "anchor_crops_digest": canonical_digest(anchor_crops_doc),
            "roster_digest": canonical_digest(roster_doc),
            "reviewed_observations_digest": canonical_digest(reviewed_observations_doc or {}),
        },
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_assignments": 0,
            "emits_number_absent": False,
            "whole_frame_ocr": False,
        },
        "summary": {
            "evaluated_crops": len(observations),
            "state_counts": dict(sorted(states.items())),
            "confirmed_numbers": states.get("number_confirmed", 0),
            "unreadable": states.get("number_unreadable", 0),
        },
        "calibration": metrics,
        "observations": observations,
    }


def _recognize_crop(
    crop: dict[str, Any],
    *,
    subject: dict[str, Any],
    crop_root: Path,
    roster_numbers: list[str],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    base = {
        "recognition_key": stable_key("jersey-recognition", {"anchor_crop_id": crop["anchor_crop_id"]}),
        "anchor_crop_id": crop["anchor_crop_id"],
        "candidate_subject_id": subject.get("candidate_subject_id"),
        "tracklet_id": crop.get("tracklet_id"),
        "frame": crop.get("frame"),
        "team_label": subject.get("team_label"),
        "state": "number_unreadable",
        "number": None,
        "confidence": 0.0,
        "number_panel_visible": False,
        "reason_codes": [],
    }
    artifact = Path(str(crop.get("artifact") or ""))
    if artifact.is_absolute() or ".." in artifact.parts or not artifact.parts:
        base["reason_codes"] = ["invalid_crop_artifact"]
        return base
    try:
        import cv2
        import numpy as np
    except ImportError:
        base["reason_codes"] = ["opencv_unavailable"]
        return base
    image = cv2.imread(str(crop_root / artifact))
    if image is None:
        base["reason_codes"] = ["crop_unavailable"]
        return base
    height, width = image.shape[:2]
    x1n, y1n, x2n, y2n = parameters["number_roi_normalized"]
    x1, y1 = max(0, int(width * x1n)), max(0, int(height * y1n))
    x2, y2 = min(width, int(width * x2n)), min(height, int(height * y2n))
    roi = image[y1:y2, x1:x2]
    if roi.size == 0:
        base["reason_codes"] = ["empty_number_roi"]
        return base
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    contrast = float(np.percentile(gray, 90) - np.percentile(gray, 10))
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.resize(mask, (64, 64), interpolation=cv2.INTER_NEAREST)
    mask[:4, :] = 0
    mask[-4:, :] = 0
    mask[:, :4] = 0
    mask[:, -4:] = 0
    foreground_ratio = float(np.count_nonzero(mask) / mask.size)
    panel_visible = contrast >= 20 and parameters["minimum_foreground_ratio"] <= foreground_ratio <= parameters["maximum_foreground_ratio"]
    base["number_panel_visible"] = panel_visible
    base["roi_diagnostics"] = {
        "contrast": round(contrast, 3),
        "foreground_ratio": round(foreground_ratio, 4),
    }
    if not panel_visible or not roster_numbers:
        base["reason_codes"] = ["number_panel_not_reliably_visible" if not panel_visible else "roster_numbers_missing"]
        return base
    scores = sorted(
        ((_template_score(mask, value), value) for value in roster_numbers),
        reverse=True,
    )
    best_score, best_number = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    margin = best_score - second_score
    base["candidate_scores"] = [
        {"number": number, "score": round(score, 4)} for score, number in scores[:3]
    ]
    base["confidence"] = round(max(0.0, min(1.0, best_score * (0.5 + margin))), 4)
    if best_score >= parameters["minimum_template_score"] and margin >= parameters["minimum_score_margin"]:
        base["state"] = "number_confirmed"
        base["number"] = best_number
        base["reason_codes"] = ["constrained_number_roi_template_consensus"]
    else:
        base["reason_codes"] = ["recognizer_below_conservative_threshold"]
    return base


def _template_score(mask: Any, number: str) -> float:
    import cv2
    import numpy as np

    best = 0.0
    observed = mask > 0
    for scale in (1.2, 1.4, 1.6):
        for thickness in (2, 3):
            template = np.zeros((64, 64), dtype=np.uint8)
            size = cv2.getTextSize(number, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0]
            origin = (max(0, (64 - size[0]) // 2), min(60, (64 + size[1]) // 2))
            cv2.putText(template, number, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, 255, thickness, cv2.LINE_AA)
            candidate = template > 64
            union = np.count_nonzero(observed | candidate)
            if union:
                best = max(best, float(np.count_nonzero(observed & candidate) / union))
    return best


def _evaluate(observations: list[dict[str, Any]], reviewed_doc: dict[str, Any]) -> dict[str, Any]:
    reviewed = {
        str(row.get("anchor_crop_id")): row
        for row in reviewed_doc.get("observations") or []
        if isinstance(row, dict) and row.get("anchor_crop_id")
    }
    compared = 0
    readable_true = readable_false = number_correct = number_incorrect = 0
    false_number_on_plain_shirt = 0
    for row in observations:
        gold = reviewed.get(str(row.get("anchor_crop_id")))
        if not gold:
            continue
        compared += 1
        predicted = row.get("state") == "number_confirmed"
        expected = gold.get("state") == "number_confirmed"
        if predicted and expected:
            readable_true += 1
            if normalize_jersey_number(row.get("number")) == normalize_jersey_number(gold.get("number")):
                number_correct += 1
            else:
                number_incorrect += 1
        elif predicted:
            readable_false += 1
            if gold.get("state") == "number_absent":
                false_number_on_plain_shirt += 1
    precision_denominator = readable_true + readable_false
    accuracy_denominator = number_correct + number_incorrect
    return {
        "reviewed_crops": compared,
        "readability_precision": round(readable_true / precision_denominator, 4) if precision_denominator else None,
        "number_accuracy": round(number_correct / accuracy_denominator, 4) if accuracy_denominator else None,
        "false_number_on_plain_shirt": false_number_on_plain_shirt,
        "numbered_player_false_positives": readable_false,
        "calibration_status": "measured" if compared else "needs_reviewed_fixture",
    }
