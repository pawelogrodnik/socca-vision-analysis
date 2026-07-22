from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import (
    canonical_digest,
    normalize_jersey_number,
    stable_key,
)


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_jersey_number_recognizer_shadow"
ALGORITHM_VERSION = "0.2.0"
DEFAULT_PARAMETERS: dict[str, Any] = {
    "number_roi_normalized": [0.05, 0.00, 0.95, 0.62],
    "minimum_template_score": 0.72,
    "minimum_score_margin": 0.08,
    "minimum_foreground_ratio": 0.008,
    "maximum_foreground_ratio": 0.50,
    "minimum_roi_contrast": 20.0,
    "minimum_roi_edge_ratio": 0.015,
    "minimum_episode_votes": 2,
    "maximum_episode_gap_frames": 5,
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
    _apply_temporal_episode_consensus(observations, params)
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
            "single_frame_confirmation_allowed": False,
            "whole_person_appearance_used_as_number_label": False,
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
        "recognition_method": "constrained_number_roi_template",
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
    person = _extract_person_crop(image, crop)
    height, width = person.shape[:2]
    x1n, y1n, x2n, y2n = parameters["number_roi_normalized"]
    x1, y1 = max(0, int(width * x1n)), max(0, int(height * y1n))
    x2, y2 = min(width, int(width * x2n)), min(height, int(height * y2n))
    roi = person[y1:y2, x1:x2]
    if roi.size == 0:
        base["reason_codes"] = ["empty_number_roi"]
        return base
    panel = _extract_number_panel(roi, parameters)
    mask = panel["digit_mask"]
    panel_visible = bool(panel["visible"])
    base["number_panel_visible"] = panel_visible
    base["roi_diagnostics"] = {
        **panel["diagnostics"],
        "person_crop_shape": [int(height), int(width)],
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
        base["candidate_number"] = best_number
        base["candidate_confidence"] = base["confidence"]
        base["candidate_method"] = "constrained_number_roi_template"
    if base.get("candidate_number") is None:
        base["reason_codes"] = ["recognizer_below_conservative_threshold"]
    else:
        base["reason_codes"] = ["awaiting_temporal_episode_consensus"]
    return base


def _extract_person_crop(image: Any, crop: dict[str, Any]) -> Any:
    """Remove the selection padding added around an anchor crop."""
    height, width = image.shape[:2]
    bbox = crop.get("bbox_xyxy")
    if isinstance(bbox, list) and len(bbox) == 4:
        person_width = max(1, min(width, int(round(float(bbox[2]) - float(bbox[0])))))
        person_height = max(1, min(height, int(round(float(bbox[3]) - float(bbox[1])))))
    else:
        # Anchor crops use 30% horizontal and 20% vertical context on each side.
        person_width = max(1, int(round(width / 1.6)))
        person_height = max(1, int(round(height / 1.4)))
    x1 = max(0, (width - person_width) // 2)
    y1 = max(0, (height - person_height) // 2)
    return image[y1:y1 + person_height, x1:x1 + person_width]


def _extract_number_panel(roi: Any, parameters: dict[str, Any]) -> dict[str, Any]:
    import cv2
    import numpy as np

    height, width = roi.shape[:2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    value_floor = max(105, int(np.percentile(hsv[:, :, 2], 58)))
    white = ((hsv[:, :, 1] <= 75) & (hsv[:, :, 2] >= value_floor)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    component = _select_jersey_component(white)
    if component is None:
        return _empty_panel_result(gray, "white_jersey_component_missing")
    x, y, component_width, component_height, component_area, component_mask = component
    points = cv2.findNonZero(component_mask)
    hull_mask = np.zeros_like(component_mask)
    if points is not None:
        cv2.fillConvexPoly(hull_mask, cv2.convexHull(points), 255)
    hull_mask = cv2.erode(hull_mask, np.ones((3, 3), np.uint8))

    white_values = gray[component_mask > 0]
    white_level = float(np.median(white_values)) if white_values.size else 0.0
    dark_threshold = max(35.0, white_level - 36.0)
    digit_mask = ((gray < dark_threshold) & (hull_mask > 0)).astype(np.uint8) * 255
    digit_mask = cv2.morphologyEx(digit_mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    digit_mask = _remove_tiny_components(digit_mask)

    panel_gray = gray[y:y + component_height, x:x + component_width]
    panel_digits = digit_mask[y:y + component_height, x:x + component_width]
    contrast = float(np.percentile(panel_gray, 98) - np.percentile(panel_gray, 2))
    edge_ratio = float(np.count_nonzero(cv2.Canny(panel_gray, 45, 130)) / max(1, panel_gray.size))
    foreground_ratio = float(np.count_nonzero(panel_digits) / max(1, panel_digits.size))
    visible = (
        component_area >= max(12, int(height * width * 0.025))
        and component_height >= max(5, int(height * 0.18))
        and contrast >= float(parameters["minimum_roi_contrast"])
        and edge_ratio >= float(parameters["minimum_roi_edge_ratio"])
        and float(parameters["minimum_foreground_ratio"])
        <= foreground_ratio
        <= float(parameters["maximum_foreground_ratio"])
    )
    normalized = _normalize_digit_mask(panel_digits)
    return {
        "visible": visible,
        "digit_mask": normalized,
        "diagnostics": {
            "panel_method": "bright_jersey_component_dark_print_v1",
            "panel_bbox": [int(x), int(y), int(component_width), int(component_height)],
            "panel_area": int(component_area),
            "white_level": round(white_level, 3),
            "dark_threshold": round(dark_threshold, 3),
            "contrast": round(contrast, 3),
            "foreground_ratio": round(foreground_ratio, 4),
            "edge_ratio": round(edge_ratio, 4),
        },
    }


def _select_jersey_component(mask: Any) -> tuple[int, int, int, int, int, Any] | None:
    import cv2
    import numpy as np

    height, width = mask.shape[:2]
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    candidates: list[tuple[float, int]] = []
    for label in range(1, count):
        x, y, component_width, component_height, area = (int(value) for value in stats[label])
        center_x, center_y = centroids[label]
        if area < max(8, int(height * width * 0.018)):
            continue
        if component_height < max(4, int(height * 0.14)) or center_y > height * 0.78:
            continue
        center_penalty = abs(center_x - width / 2) / max(1.0, width / 2)
        score = area * (1.2 - min(0.8, center_penalty)) * (1.0 + component_height / max(1, height))
        candidates.append((score, label))
    if not candidates:
        return None
    label = max(candidates)[1]
    x, y, component_width, component_height, area = (int(value) for value in stats[label])
    return x, y, component_width, component_height, area, (labels == label).astype(np.uint8) * 255


def _remove_tiny_components(mask: Any) -> Any:
    import cv2
    import numpy as np

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= 2:
            cleaned[labels == label] = 255
    return cleaned


def _normalize_digit_mask(mask: Any) -> Any:
    import cv2
    import numpy as np

    points = cv2.findNonZero(mask)
    canvas = np.zeros((64, 64), dtype=np.uint8)
    if points is None:
        return canvas
    x, y, width, height = cv2.boundingRect(points)
    content = mask[y:y + height, x:x + width]
    scale = min(52 / max(1, width), 52 / max(1, height))
    resized = cv2.resize(
        content,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_NEAREST,
    )
    y1 = (64 - resized.shape[0]) // 2
    x1 = (64 - resized.shape[1]) // 2
    canvas[y1:y1 + resized.shape[0], x1:x1 + resized.shape[1]] = resized
    return canvas


def _empty_panel_result(gray: Any, reason: str) -> dict[str, Any]:
    import numpy as np

    return {
        "visible": False,
        "digit_mask": np.zeros((64, 64), dtype=np.uint8),
        "diagnostics": {
            "panel_method": "bright_jersey_component_dark_print_v1",
            "panel_failure_reason": reason,
            "contrast": round(float(np.percentile(gray, 98) - np.percentile(gray, 2)), 3),
            "foreground_ratio": 0.0,
            "edge_ratio": 0.0,
        },
    }


def _apply_temporal_episode_consensus(
    observations: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        groups[(
            str(row.get("candidate_subject_id") or ""),
            str(row.get("tracklet_id") or ""),
            str(row.get("team_label") or "U"),
        )].append(row)
    maximum_gap = int(parameters["maximum_episode_gap_frames"])
    minimum_votes = int(parameters["minimum_episode_votes"])
    for group_key, rows in groups.items():
        episode: list[dict[str, Any]] = []
        previous_frame: int | None = None
        episode_index = 0
        for row in sorted(rows, key=lambda value: int(value.get("frame") or 0)) + [None]:
            frame = int(row.get("frame") or 0) if row else None
            if row is not None and (previous_frame is None or frame - previous_frame <= maximum_gap):
                episode.append(row)
                previous_frame = frame
                continue
            if episode:
                episode_index += 1
                _finalize_episode(
                    episode,
                    group_key=group_key,
                    episode_index=episode_index,
                    minimum_votes=minimum_votes,
                )
            episode = [row] if row is not None else []
            previous_frame = frame


def _finalize_episode(
    rows: list[dict[str, Any]],
    *,
    group_key: tuple[str, str, str],
    episode_index: int,
    minimum_votes: int,
) -> None:
    episode_id = stable_key(
        "jersey-visibility-episode",
        {
            "subject": group_key[0],
            "tracklet": group_key[1],
            "team": group_key[2],
            "index": episode_index,
            "start_frame": min(int(row.get("frame") or 0) for row in rows),
        },
    )
    votes = Counter(
        str(row["candidate_number"])
        for row in rows
        if row.get("candidate_number") is not None and row.get("number_panel_visible")
    )
    number, support = votes.most_common(1)[0] if votes else (None, 0)
    competing = sum(value for key, value in votes.items() if key != number)
    accepted = bool(number is not None and support >= minimum_votes and competing == 0)
    for row in rows:
        row["visibility_episode_id"] = episode_id
        row["episode_diagnostics"] = {
            "supporting_votes": support,
            "competing_votes": competing,
            "observations": len(rows),
        }
        if accepted and str(row.get("candidate_number")) == number:
            row["state"] = "number_confirmed"
            row["number"] = number
            row["confidence"] = round(float(row.get("candidate_confidence") or 0.0), 4)
            row["reason_codes"] = ["multi_frame_number_shape_episode_consensus"]
        elif row.get("candidate_number") is not None:
            row["reason_codes"] = ["insufficient_temporal_episode_consensus"]


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
    readable_true = readable_false = readable_missed = number_correct = number_incorrect = 0
    panel_true = panel_false = panel_missed = 0
    false_number_on_plain_shirt = 0
    for row in observations:
        gold = reviewed.get(str(row.get("anchor_crop_id")))
        if not gold:
            continue
        compared += 1
        predicted = row.get("state") == "number_confirmed"
        expected = gold.get("state") == "number_confirmed"
        panel_predicted = bool(row.get("number_panel_visible"))
        panel_expected = bool(gold.get("number_panel_visible")) or expected
        if panel_predicted and panel_expected:
            panel_true += 1
        elif panel_predicted:
            panel_false += 1
        elif panel_expected:
            panel_missed += 1
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
        elif expected:
            readable_missed += 1
    precision_denominator = readable_true + readable_false
    recall_denominator = readable_true + readable_missed
    accuracy_denominator = number_correct + number_incorrect
    panel_precision_denominator = panel_true + panel_false
    panel_recall_denominator = panel_true + panel_missed
    return {
        "reviewed_crops": compared,
        "number_panel_precision": round(panel_true / panel_precision_denominator, 4)
        if panel_precision_denominator else None,
        "number_panel_recall": round(panel_true / panel_recall_denominator, 4)
        if panel_recall_denominator else None,
        "number_panel_false_negatives": panel_missed,
        "readability_precision": round(readable_true / precision_denominator, 4) if precision_denominator else None,
        "readability_recall": round(readable_true / recall_denominator, 4) if recall_denominator else None,
        "readable_false_negatives": readable_missed,
        "number_accuracy": round(number_correct / accuracy_denominator, 4) if accuracy_denominator else None,
        "false_number_on_plain_shirt": false_number_on_plain_shirt,
        "numbered_player_false_positives": readable_false,
        "calibration_status": "measured" if compared else "needs_reviewed_fixture",
    }
