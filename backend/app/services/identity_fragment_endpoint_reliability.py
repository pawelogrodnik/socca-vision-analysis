from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
import statistics
from typing import Any


ALGORITHM_NAME = "identity_fragment_endpoint_reliability"
ALGORITHM_VERSION = "0.1.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "context_observations": 10,
    "min_context_observations": 3,
    "min_confidence": 0.2,
    "review_area_ratio_min": 0.12,
    "review_area_ratio_max": 3.0,
    "review_aspect_ratio_min": 0.2,
    "review_aspect_ratio_max": 3.0,
    "max_local_speed_mps": 16.0,
}


def assess_fragment_endpoint_reliability(
    player: dict[str, Any],
    *,
    at_end: bool,
    fps: float,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe endpoint observation quality without changing identity decisions."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    positions = sorted(
        (
            row
            for row in player.get("overlay_positions") or []
            if str(row.get("source") or "detected") == "detected"
        ),
        key=lambda row: int(row.get("frame") or 0),
    )
    if not positions:
        return _result(
            quality="invalid",
            reasons=["missing_detected_endpoint"],
            endpoint=None,
            context=[],
            metrics={},
            params=params,
        )

    endpoint_index = len(positions) - 1 if at_end else 0
    endpoint = positions[endpoint_index]
    context_limit = max(0, int(params["context_observations"]))
    if at_end:
        context = positions[max(0, endpoint_index - context_limit) : endpoint_index]
    else:
        context = positions[endpoint_index + 1 : endpoint_index + 1 + context_limit]

    reasons: list[str] = []
    bbox = _bbox(endpoint)
    pitch = _pitch(endpoint)
    if bbox is None:
        reasons.append("invalid_endpoint_bbox")
    if pitch is None:
        reasons.append("missing_endpoint_pitch")
    if str(endpoint.get("quality_class") or "") in {"noise", "duplicate"}:
        reasons.append("endpoint_quality_class_not_safe")
    if str(endpoint.get("play_area_status") or "") == "outside_play":
        reasons.append("endpoint_outside_play_area")

    confidence = _optional_float(endpoint.get("confidence"))
    if confidence is None or confidence < float(params["min_confidence"]):
        reasons.append("endpoint_confidence_too_low")

    context_bboxes = [value for row in context if (value := _bbox(row)) is not None]
    context_pitches = [row for row in context if _pitch(row) is not None]
    area_ratio = _ratio_to_median(_bbox_area(bbox), [_bbox_area(value) for value in context_bboxes])
    aspect_ratio = _ratio_to_median(_bbox_aspect(bbox), [_bbox_aspect(value) for value in context_bboxes])
    local_speed = _local_speed_mps(endpoint, context_pitches, at_end=at_end, fps=fps)

    if len(context) < int(params["min_context_observations"]):
        reasons.append("insufficient_local_context")
    if area_ratio is not None and not (
        float(params["review_area_ratio_min"])
        <= area_ratio
        <= float(params["review_area_ratio_max"])
    ):
        reasons.append("endpoint_area_inconsistent_with_context")
    if aspect_ratio is not None and not (
        float(params["review_aspect_ratio_min"])
        <= aspect_ratio
        <= float(params["review_aspect_ratio_max"])
    ):
        reasons.append("endpoint_aspect_inconsistent_with_context")
    if local_speed is not None and local_speed > float(params["max_local_speed_mps"]):
        reasons.append("endpoint_motion_inconsistent_with_context")

    invalid_reasons = {"missing_detected_endpoint", "invalid_endpoint_bbox", "missing_endpoint_pitch"}
    if invalid_reasons.intersection(reasons):
        quality = "invalid"
    elif reasons:
        quality = "review"
    else:
        quality = "locally_consistent"

    metrics = {
        "context_observations": len(context),
        "context_bbox_observations": len(context_bboxes),
        "context_pitch_observations": len(context_pitches),
        "area_ratio_to_context_median": _rounded(area_ratio),
        "aspect_ratio_to_context_median": _rounded(aspect_ratio),
        "local_speed_mps": _rounded(local_speed),
        "footpoint_reliable": bool(endpoint.get("footpoint_reliable")),
        "appearance_reliable": bool(endpoint.get("appearance_reliable")),
        "visual_content_verified": False,
    }
    return _result(
        quality=quality,
        reasons=reasons,
        endpoint=endpoint,
        context=context,
        metrics=metrics,
        params=params,
    )


def summarize_endpoint_pair(
    source: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    qualities = [str(source.get("quality") or "invalid"), str(target.get("quality") or "invalid")]
    if "invalid" in qualities:
        quality = "invalid"
    elif "review" in qualities:
        quality = "review"
    else:
        quality = "locally_consistent"
    reasons = sorted(
        set(str(value) for row in (source, target) for value in row.get("reason_codes") or [])
    )
    return {
        "quality": quality,
        "source_quality": qualities[0],
        "target_quality": qualities[1],
        "reason_codes": reasons,
        "safe_for_automatic_identity_merge": False,
        "visual_content_verified": False,
    }


def evaluate_fragment_endpoint_reliability(
    goldset: dict[str, Any],
    predictions_by_benchmark: dict[str, dict[str, Any]],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compare endpoint quality with manual identity labels without treating it as ReID."""
    prediction_indexes = {
        benchmark_id: {
            str(row.get("proposal_key") or ""): row
            for row in document.get("proposals") or []
            if row.get("proposal_key")
        }
        for benchmark_id, document in predictions_by_benchmark.items()
    }
    counts: Counter[tuple[str, str]] = Counter()
    missing: list[dict[str, str]] = []
    advisory_violations: list[dict[str, str]] = []
    for item in goldset.get("items") or []:
        benchmark_id = str(item.get("benchmark_id") or "")
        candidate_key = str(item.get("candidate_key") or "")
        proposal = prediction_indexes.get(benchmark_id, {}).get(candidate_key)
        if proposal is None:
            missing.append({"benchmark_id": benchmark_id, "candidate_key": candidate_key})
            continue
        summary = proposal.get("endpoint_reliability") or {}
        review_status = str(item.get("review_status") or "pending")
        quality = str(summary.get("quality") or "missing")
        counts[(review_status, quality)] += 1
        if bool(summary.get("safe_for_automatic_identity_merge")):
            advisory_violations.append(
                {"benchmark_id": benchmark_id, "candidate_key": candidate_key}
            )

    matrix = {
        review_status: {
            quality: counts[(review_status, quality)]
            for quality in ("locally_consistent", "review", "invalid", "missing")
        }
        for review_status in ("confirmed_same", "confirmed_different", "uncertain", "pending")
    }
    gates = {
        "all_goldset_items_resolved": not missing,
        "endpoint_quality_is_advisory_only": not advisory_violations,
    }
    return {
        "schema_version": "0.1.0",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "status": "passed" if all(gates.values()) else "failed",
        "summary": {
            "goldset_items": len(goldset.get("items") or []),
            "resolved_items": len(goldset.get("items") or []) - len(missing),
            "matrix": matrix,
        },
        "gates": gates,
        "missing_predictions": missing,
        "advisory_violations": advisory_violations,
        "limitations": [
            "Locally consistent endpoints can still contain a ball, a partial body, or background.",
            "Endpoint reliability is observation-quality evidence, not same-person evidence.",
            "The report must not be used to authorize an identity merge.",
        ],
    }


def _result(
    *,
    quality: str,
    reasons: list[str],
    endpoint: dict[str, Any] | None,
    context: list[dict[str, Any]],
    metrics: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    frames = [int(row.get("frame") or 0) for row in context]
    return {
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "quality": quality,
        "endpoint_frame": int(endpoint.get("frame") or 0) if endpoint else None,
        "context_frame_range": [min(frames), max(frames)] if frames else None,
        "reason_codes": sorted(set(reasons)),
        "metrics": metrics,
        "advisory_only": True,
    }


def _bbox(row: dict[str, Any]) -> list[float] | None:
    value = row.get("bbox_xyxy")
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    bbox = [float(component) for component in value[:4]]
    if not all(math.isfinite(component) for component in bbox):
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _pitch(row: dict[str, Any]) -> list[float] | None:
    value = row.get("pitch_m")
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    pitch = [float(value[0]), float(value[1])]
    if not all(math.isfinite(component) for component in pitch):
        return None
    return pitch


def _bbox_area(bbox: list[float] | None) -> float | None:
    if bbox is None:
        return None
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def _bbox_aspect(bbox: list[float] | None) -> float | None:
    if bbox is None:
        return None
    return (bbox[3] - bbox[1]) / max(bbox[2] - bbox[0], 1e-6)


def _ratio_to_median(value: float | None, context: list[float | None]) -> float | None:
    safe_context = [float(item) for item in context if item is not None and float(item) > 0]
    if value is None or value <= 0 or not safe_context:
        return None
    return float(value) / statistics.median(safe_context)


def _local_speed_mps(
    endpoint: dict[str, Any],
    context: list[dict[str, Any]],
    *,
    at_end: bool,
    fps: float,
) -> float | None:
    endpoint_pitch = _pitch(endpoint)
    if endpoint_pitch is None or not context:
        return None
    nearest = max(context, key=lambda row: int(row.get("frame") or 0)) if at_end else min(
        context,
        key=lambda row: int(row.get("frame") or 0),
    )
    nearest_pitch = _pitch(nearest)
    if nearest_pitch is None:
        return None
    frame_delta = abs(int(endpoint.get("frame") or 0) - int(nearest.get("frame") or 0))
    if frame_delta <= 0:
        return None
    seconds = frame_delta / max(float(fps), 1e-6)
    return math.hypot(
        endpoint_pitch[0] - nearest_pitch[0],
        endpoint_pitch[1] - nearest_pitch[1],
    ) / seconds


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _rounded(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
