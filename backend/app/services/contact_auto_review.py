from __future__ import annotations

from collections import Counter
from typing import Any

AUTO_REVIEW_SOURCE = "auto_contact_review_v1"

MIN_ACCEPT_CONFIDENCE = 0.44
MIN_ACCEPT_DETECTED_BALL_FRAMES = 2
MAX_ACCEPT_MIN_DISTANCE_M = 0.9
MAX_ACCEPT_MEAN_DISTANCE_M = 1.25
MAX_ACCEPT_INTERPOLATED_RATIO = 0.75

MIN_UNCERTAIN_CONFIDENCE = 0.28
MAX_UNCERTAIN_MIN_DISTANCE_M = 1.8
MAX_UNCERTAIN_MEAN_DISTANCE_M = 2.2
MAX_REJECT_MIN_DISTANCE_M = 2.6
MAX_FLY_THROUGH_CLOSE_DISTANCE_M = 0.95
MIN_FLY_THROUGH_MEAN_SPEED_MPS = 7.5
MIN_FLY_THROUGH_STRAIGHTNESS = 0.85
MIN_FLY_THROUGH_PATH_DISTANCE_M = 1.5


def auto_review_contact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    detected_ball_frames = int(candidate.get("detected_ball_frames") or 0)
    detected_player_frames = int(candidate.get("detected_player_frames") or 0)
    interpolated_player_frames = int(candidate.get("interpolated_player_frames") or 0)
    total_player_frames = max(detected_player_frames + interpolated_player_frames, 1)
    interpolated_ratio = interpolated_player_frames / total_player_frames
    mean_confidence = float(candidate.get("mean_confidence") or 0.0)
    min_distance = _float_or_none(candidate.get("min_distance_m"))
    mean_distance = _float_or_none(candidate.get("mean_distance_m"))
    mean_ball_speed = _float_or_none(candidate.get("mean_ball_speed_mps"))
    ball_straightness = _float_or_none(candidate.get("ball_path_straightness"))
    ball_path_distance = _float_or_none(candidate.get("ball_path_distance_m"))
    stable_player_id = candidate.get("stable_player_id")

    reject_reasons: list[str] = []
    if not stable_player_id:
        reject_reasons.append("missing_stable_player_id")
    if detected_ball_frames <= 0:
        reject_reasons.append("no_detected_ball_frames")
    if min_distance is not None and min_distance > MAX_REJECT_MIN_DISTANCE_M:
        reject_reasons.append("ball_too_far_from_player")
    if _looks_like_fast_fly_through(min_distance, mean_ball_speed, ball_straightness, ball_path_distance):
        reject_reasons.append("fly_through_without_close_control")
    if mean_confidence < 0.18:
        reject_reasons.append("very_low_confidence")

    if reject_reasons:
        return _decision("rejected", mean_confidence, reject_reasons)

    accept_reasons: list[str] = []
    if mean_confidence >= MIN_ACCEPT_CONFIDENCE:
        accept_reasons.append("confidence_ok")
    if detected_ball_frames >= MIN_ACCEPT_DETECTED_BALL_FRAMES:
        accept_reasons.append("multi_frame_ball_detection")
    if min_distance is not None and min_distance <= MAX_ACCEPT_MIN_DISTANCE_M:
        accept_reasons.append("close_min_distance")
    if mean_distance is not None and mean_distance <= MAX_ACCEPT_MEAN_DISTANCE_M:
        accept_reasons.append("close_mean_distance")
    if interpolated_ratio <= MAX_ACCEPT_INTERPOLATED_RATIO:
        accept_reasons.append("player_position_not_over_interpolated")

    if len(accept_reasons) >= 5:
        return _decision("accepted", mean_confidence, accept_reasons)

    uncertain_reasons: list[str] = []
    if mean_confidence >= MIN_UNCERTAIN_CONFIDENCE:
        uncertain_reasons.append("usable_confidence")
    if min_distance is not None and min_distance <= MAX_UNCERTAIN_MIN_DISTANCE_M:
        uncertain_reasons.append("plausible_min_distance")
    if mean_distance is not None and mean_distance <= MAX_UNCERTAIN_MEAN_DISTANCE_M:
        uncertain_reasons.append("plausible_mean_distance")
    if detected_ball_frames > 0:
        uncertain_reasons.append("has_detected_ball")

    if len(uncertain_reasons) >= 3:
        return _decision("uncertain", mean_confidence, uncertain_reasons)

    return _decision("rejected", mean_confidence, ["insufficient_contact_evidence"])


def apply_auto_contact_review(
    contact_candidates_doc: dict[str, Any],
    *,
    preserve_manual: bool = True,
) -> dict[str, Any]:
    candidates = contact_candidates_doc.get("candidates") or []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if preserve_manual and candidate.get("review_source") == "manual":
            continue
        if preserve_manual and _looks_like_legacy_manual_review(candidate):
            candidate.setdefault("review_source", "manual_legacy")
            continue
        review = auto_review_contact_candidate(candidate)
        candidate["review_status"] = review["review_status"]
        candidate["status"] = review["review_status"]
        candidate["review_source"] = AUTO_REVIEW_SOURCE
        candidate["auto_review"] = review
    _update_auto_summary(contact_candidates_doc)
    return contact_candidates_doc


def _looks_like_legacy_manual_review(candidate: dict[str, Any]) -> bool:
    status = str(candidate.get("review_status") or candidate.get("status") or "needs_review")
    return status in {"accepted", "rejected", "uncertain"} and not candidate.get("auto_review")


def _update_auto_summary(document: dict[str, Any]) -> None:
    candidates = [
        candidate
        for candidate in document.get("candidates") or []
        if isinstance(candidate, dict)
    ]
    review_counts = Counter(str(candidate.get("review_status") or candidate.get("status") or "needs_review") for candidate in candidates)
    review_source_counts = Counter(str(candidate.get("review_source") or "unknown") for candidate in candidates)
    summary = dict(document.get("summary") or {})
    summary.update(
        {
            "review_counts": dict(sorted(review_counts.items())),
            "review_source_counts": dict(sorted(review_source_counts.items())),
            "auto_reviewed_candidates": sum(
                1
                for candidate in candidates
                if str(candidate.get("review_source") or "").startswith("auto_contact_review")
            ),
            "manual_reviewed_candidates": review_source_counts.get("manual", 0) + review_source_counts.get("manual_legacy", 0),
            "accepted_candidates": review_counts.get("accepted", 0),
            "rejected_candidates": review_counts.get("rejected", 0),
            "uncertain_candidates": review_counts.get("uncertain", 0),
            "needs_review_candidates": review_counts.get("needs_review", 0),
        }
    )
    document["summary"] = summary


def _decision(review_status: str, score: float, reasons: list[str]) -> dict[str, Any]:
    return {
        "review_status": review_status,
        "source": AUTO_REVIEW_SOURCE,
        "score": round(score, 4),
        "reasons": reasons,
        "thresholds": {
            "min_accept_confidence": MIN_ACCEPT_CONFIDENCE,
            "min_accept_detected_ball_frames": MIN_ACCEPT_DETECTED_BALL_FRAMES,
            "max_accept_min_distance_m": MAX_ACCEPT_MIN_DISTANCE_M,
            "max_accept_mean_distance_m": MAX_ACCEPT_MEAN_DISTANCE_M,
            "max_accept_interpolated_ratio": MAX_ACCEPT_INTERPOLATED_RATIO,
            "min_uncertain_confidence": MIN_UNCERTAIN_CONFIDENCE,
            "max_uncertain_min_distance_m": MAX_UNCERTAIN_MIN_DISTANCE_M,
            "max_uncertain_mean_distance_m": MAX_UNCERTAIN_MEAN_DISTANCE_M,
            "max_reject_min_distance_m": MAX_REJECT_MIN_DISTANCE_M,
            "max_fly_through_close_distance_m": MAX_FLY_THROUGH_CLOSE_DISTANCE_M,
            "min_fly_through_mean_speed_mps": MIN_FLY_THROUGH_MEAN_SPEED_MPS,
            "min_fly_through_straightness": MIN_FLY_THROUGH_STRAIGHTNESS,
            "min_fly_through_path_distance_m": MIN_FLY_THROUGH_PATH_DISTANCE_M,
        },
    }


def _looks_like_fast_fly_through(
    min_distance: float | None,
    mean_ball_speed: float | None,
    ball_straightness: float | None,
    ball_path_distance: float | None,
) -> bool:
    return bool(
        min_distance is not None
        and min_distance > MAX_FLY_THROUGH_CLOSE_DISTANCE_M
        and mean_ball_speed is not None
        and mean_ball_speed >= MIN_FLY_THROUGH_MEAN_SPEED_MPS
        and ball_straightness is not None
        and ball_straightness >= MIN_FLY_THROUGH_STRAIGHTNESS
        and ball_path_distance is not None
        and ball_path_distance >= MIN_FLY_THROUGH_PATH_DISTANCE_M
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
