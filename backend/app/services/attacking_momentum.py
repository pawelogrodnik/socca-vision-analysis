from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.services.match_phase_config import direction_for_team_at_time

MOMENTUM_SOURCE = "attacking_momentum_v1"

POSITION_BASE_SCORE = 0.10
POSITION_WEIGHT = 0.90
POSITION_EXPONENT = 1.8
PROGRESSION_LOOKBACK_SEC = 1.0
PROGRESSION_LOOKBACK_TOLERANCE_SEC = 0.5
PROGRESSION_MAX_GAP_SEC = 1.5
PROGRESSION_FULL_BONUS_M = 6.0
PROGRESSION_MAX_BONUS = 0.30

COMPLETED_PASS_BASE_BONUS = 0.10
FAILED_PASS_BASE_BONUS = 0.035
PROGRESSIVE_PASS_MAX_BONUS = 0.25
PROGRESSIVE_PASS_FULL_BONUS_M = 10.0
RESTART_SETUP_BASE_BONUS = 0.025

PASS_REVIEW_MULTIPLIERS = {
    "accepted": 1.00,
    "needs_review": 0.70,
    "uncertain": 0.45,
    "rejected": 0.00,
}

MIN_NORMALIZATION_SCALE = 0.15
DOMINANT_TEAM_DEAD_ZONE = 5.0
MIN_POINT_CONFIDENCE = 0.15


def build_attacking_momentum_document(
    possession_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None,
    *,
    pitch_width_m: float,
    pitch_length_m: float,
    pass_candidates_doc: dict[str, Any] | None = None,
    restart_candidates_doc: dict[str, Any] | None = None,
    bin_sec: float = 5.0,
    smoothing_window_sec: float = 30.0,
) -> dict[str, Any]:
    safe_bin_sec = max(0.1, float(bin_sec))
    safe_window_sec = max(safe_bin_sec, float(smoothing_window_sec))
    frames = _sorted_frames(possession_candidates_doc)
    passes = _candidate_rows(pass_candidates_doc)
    restarts = _candidate_rows(restart_candidates_doc)
    max_time_sec = max(
        [_time_sec(frame) for frame in frames]
        + [_event_time_sec(candidate) for candidate in passes]
        + [_event_time_sec(candidate) for candidate in restarts]
        + [0.0]
    )

    if not frames and not passes and not restarts:
        return _empty_document(
            bin_sec=safe_bin_sec,
            smoothing_window_sec=safe_window_sec,
            warning="No possession, pass or restart candidates were available.",
        )

    bin_count = max(1, int(math.ceil(max_time_sec / safe_bin_sec)))
    bins = [_new_bin(index, safe_bin_sec) for index in range(bin_count)]
    previous_controlled: dict[str, list[tuple[float, float, str]]] = {"A": [], "B": []}
    scored_controlled_frames = 0
    known_possession_frames = 0
    interpolated_scored_frames = 0

    for frame in frames:
        time_sec = _time_sec(frame)
        bucket = bins[_bin_index(time_sec, safe_bin_sec, bin_count)]
        bucket["all_samples"] += 1
        status = str(frame.get("status") or "unknown")
        if status != "unknown":
            known_possession_frames += 1
        if status != "controlled" or frame.get("reason") == "fly_through_no_close_control":
            continue
        team_label = _team_label(frame.get("team_label"))
        if team_label is None:
            continue
        bucket[f"team_{team_label.lower()}_controlled_samples"] += 1
        confidence = _clamp01(_number(frame.get("confidence"), _number(frame.get("ball_confidence"), 0.0)))
        bucket["controlled_confidence_sum"] += confidence
        bucket["controlled_confidence_samples"] += 1
        phase = direction_for_team_at_time(match_phase_config_doc, team_label, time_sec)
        attack_direction = str(phase.get("attack_direction") or "unknown")
        position = frame.get("ball_position_m")
        attack_progress = normalized_attack_progress(
            position,
            attack_direction,
            pitch_width_m,
            pitch_length_m,
        )
        if attack_progress is None:
            continue
        bucket["direction_samples"] += 1
        progression_bonus = _progression_bonus(
            previous_controlled[team_label],
            current_time_sec=time_sec,
            current_progress=attack_progress,
            attack_direction=attack_direction,
            axis_length_m=_attack_axis_length(attack_direction, pitch_width_m, pitch_length_m),
        )
        previous_controlled[team_label].append((time_sec, attack_progress, attack_direction))
        _trim_progress_history(previous_controlled[team_label], time_sec)
        position_score = POSITION_BASE_SCORE + POSITION_WEIGHT * (attack_progress**POSITION_EXPONENT)
        frame_pressure = confidence * (position_score + progression_bonus)
        bucket[f"team_{team_label.lower()}_frame_score_sum"] += frame_pressure
        scored_controlled_frames += 1
        if frame.get("ball_source") != "detected" or frame.get("nearest_player_source") not in {None, "detected"}:
            interpolated_scored_frames += 1

    pass_counts = _apply_pass_bonuses(bins, passes, safe_bin_sec)
    restart_counts = _apply_restart_setup_bonuses(bins, restarts, passes, safe_bin_sec)
    raw_points = [_finalize_raw_bin(bucket) for bucket in bins]
    _apply_causal_smoothing(raw_points, safe_bin_sec, safe_window_sec)
    normalization_scale = _normalization_scale(raw_points)
    points = [_public_point(point, normalization_scale) for point in raw_points]

    total_frames = len(frames)
    controlled_frames = sum(
        int(point["team_a_controlled_samples"]) + int(point["team_b_controlled_samples"])
        for point in points
    )
    direction_samples = sum(int(point["direction_samples"]) for point in raw_points)
    known_coverage = _ratio(known_possession_frames, total_frames)
    controlled_coverage = _ratio(controlled_frames, total_frames)
    direction_coverage = _ratio(direction_samples, controlled_frames)
    interpolated_share = _ratio(interpolated_scored_frames, scored_controlled_frames)
    needs_review = bool((match_phase_config_doc or {}).get("summary", {}).get("needs_review"))
    quality = _quality(
        known_coverage=known_coverage,
        controlled_coverage=controlled_coverage,
        direction_coverage=direction_coverage,
        scored_frames=scored_controlled_frames,
        needs_review=needs_review,
        interpolated_share=interpolated_share,
    )
    warnings = _warnings(
        known_coverage=known_coverage,
        controlled_coverage=controlled_coverage,
        direction_coverage=direction_coverage,
        needs_review=needs_review,
        interpolated_share=interpolated_share,
        has_pass_candidates=bool(passes),
    )
    team_a_pressure = sum(float(point["team_a_raw"]) for point in points)
    team_b_pressure = sum(float(point["team_b_raw"]) for point in points)
    total_pressure = team_a_pressure + team_b_pressure
    return {
        "schema_version": "0.2.0",
        "generated_at": _now_iso(),
        "source": MOMENTUM_SOURCE,
        "status": "completed",
        "experimental": True,
        "semantics": "relative_attacking_pressure_estimate_not_official_stat",
        "parameters": _parameters(safe_bin_sec, safe_window_sec),
        "summary": {
            "points": len(points),
            "duration_sec": round(max_time_sec, 3),
            "known_possession_coverage": known_coverage,
            "controlled_coverage": controlled_coverage,
            "direction_coverage": direction_coverage,
            "scored_controlled_frames": scored_controlled_frames,
            **pass_counts,
            **restart_counts,
            "normalization_scale": round(normalization_scale, 6),
            "team_a_pressure_share": _ratio_float(team_a_pressure, total_pressure),
            "team_b_pressure_share": _ratio_float(team_b_pressure, total_pressure),
            "team_a_peak": round(max(0.0, max((float(point["signed_score"]) for point in points), default=0.0)), 3),
            "team_b_peak": round(min(0.0, min((float(point["signed_score"]) for point in points), default=0.0)), 3),
            "interpolated_scored_share": interpolated_share,
            "quality": quality,
        },
        "points": points,
        "warnings": warnings,
        "notes": [
            "Momentum is relative and normalized within this match.",
            "Values from different matches are not directly comparable in v1.",
            "Possession, passes and momentum remain experimental candidate layers.",
        ],
    }


def normalized_attack_progress(
    position_m: Any,
    attack_direction: str,
    pitch_width_m: float,
    pitch_length_m: float,
) -> float | None:
    if not _valid_pair(position_m):
        return None
    x, y = float(position_m[0]), float(position_m[1])
    if attack_direction == "towards_y_min":
        return 1.0 - _clamp01(y / max(float(pitch_length_m), 0.001))
    if attack_direction == "towards_y_max":
        return _clamp01(y / max(float(pitch_length_m), 0.001))
    if attack_direction == "towards_x_min":
        return 1.0 - _clamp01(x / max(float(pitch_width_m), 0.001))
    if attack_direction == "towards_x_max":
        return _clamp01(x / max(float(pitch_width_m), 0.001))
    return None


def _new_bin(index: int, bin_sec: float) -> dict[str, Any]:
    start = index * bin_sec
    return {
        "index": index,
        "time_sec": start + bin_sec / 2.0,
        "start_time_sec": start,
        "end_time_sec": start + bin_sec,
        "all_samples": 0,
        "team_a_controlled_samples": 0,
        "team_b_controlled_samples": 0,
        "team_a_frame_score_sum": 0.0,
        "team_b_frame_score_sum": 0.0,
        "team_a_event_bonus": 0.0,
        "team_b_event_bonus": 0.0,
        "controlled_confidence_sum": 0.0,
        "controlled_confidence_samples": 0,
        "direction_samples": 0,
        "evidence": {
            "completed_passes": 0,
            "failed_passes": 0,
            "progressive_passes": 0,
            "restart_passes": 0,
            "restart_setup_bonuses": 0,
        },
    }


def _progression_bonus(
    history: list[tuple[float, float, str]],
    *,
    current_time_sec: float,
    current_progress: float,
    attack_direction: str,
    axis_length_m: float,
) -> float:
    candidates = [
        row
        for row in history
        if row[2] == attack_direction
        and 0.0 < current_time_sec - row[0] <= PROGRESSION_MAX_GAP_SEC
        and abs((current_time_sec - row[0]) - PROGRESSION_LOOKBACK_SEC) <= PROGRESSION_LOOKBACK_TOLERANCE_SEC
    ]
    if not candidates:
        return 0.0
    previous_time, previous_progress, _ = min(
        candidates,
        key=lambda row: abs((current_time_sec - row[0]) - PROGRESSION_LOOKBACK_SEC),
    )
    if current_time_sec <= previous_time:
        return 0.0
    progress_m = (current_progress - previous_progress) * max(axis_length_m, 0.001)
    return _clamp01(progress_m / PROGRESSION_FULL_BONUS_M) * PROGRESSION_MAX_BONUS


def _apply_pass_bonuses(
    bins: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    bin_sec: float,
) -> dict[str, int]:
    counts = {
        "pass_attempts_used": 0,
        "completed_passes_used": 0,
        "failed_passes_used": 0,
        "excluded_non_pass_ignored": 0,
        "restart_passes_used": 0,
    }
    for candidate in candidates:
        outcome = str(candidate.get("outcome") or "unknown_pass_attempt")
        if outcome == "excluded_non_pass":
            counts["excluded_non_pass_ignored"] += 1
            continue
        if outcome not in {"completed_pass", "failed_pass"}:
            continue
        review_status = str(candidate.get("review_status") or "needs_review")
        multiplier = PASS_REVIEW_MULTIPLIERS.get(review_status, PASS_REVIEW_MULTIPLIERS["needs_review"])
        if multiplier <= 0.0:
            continue
        team_label = _team_label(candidate.get("count_for_team_label") or candidate.get("from_team_label"))
        if team_label is None:
            continue
        base_bonus = COMPLETED_PASS_BASE_BONUS if outcome == "completed_pass" else FAILED_PASS_BASE_BONUS
        progress_m = max(0.0, _number(candidate.get("forward_progress_m"), 0.0))
        progressive_bonus = 0.0
        if bool(candidate.get("is_progressive")) or progress_m > 0.0:
            progressive_bonus = min(
                PROGRESSIVE_PASS_MAX_BONUS,
                progress_m / PROGRESSIVE_PASS_FULL_BONUS_M * PROGRESSIVE_PASS_MAX_BONUS,
            )
        bonus = (base_bonus + progressive_bonus) * _clamp01(_number(candidate.get("confidence"), 0.0)) * multiplier
        bucket = bins[_bin_index(_event_time_sec(candidate), bin_sec, len(bins))]
        bucket[f"team_{team_label.lower()}_event_bonus"] += bonus
        bucket["evidence"]["completed_passes" if outcome == "completed_pass" else "failed_passes"] += 1
        if progressive_bonus > 0.0:
            bucket["evidence"]["progressive_passes"] += 1
        if candidate.get("from_restart"):
            bucket["evidence"]["restart_passes"] += 1
            counts["restart_passes_used"] += 1
        counts["pass_attempts_used"] += 1
        counts["completed_passes_used" if outcome == "completed_pass" else "failed_passes_used"] += 1
    return counts


def _apply_restart_setup_bonuses(
    bins: list[dict[str, Any]],
    restarts: list[dict[str, Any]],
    passes: list[dict[str, Any]],
    bin_sec: float,
) -> dict[str, int]:
    matched_restart_ids = {
        str(candidate.get("restart_candidate_id"))
        for candidate in passes
        if candidate.get("from_restart") and candidate.get("restart_candidate_id")
    }
    used = 0
    for restart in restarts:
        candidate_id = str(restart.get("candidate_id") or "")
        if candidate_id and candidate_id in matched_restart_ids:
            continue
        team_label = _team_label(restart.get("actor_team_label") or restart.get("team_label"))
        if team_label is None or str(restart.get("review_status") or "needs_review") == "rejected":
            continue
        confidence = _clamp01(_number(restart.get("confidence"), 0.0))
        if confidence <= 0.0:
            continue
        bucket = bins[_bin_index(_event_time_sec(restart), bin_sec, len(bins))]
        bucket[f"team_{team_label.lower()}_event_bonus"] += RESTART_SETUP_BASE_BONUS * confidence
        bucket["evidence"]["restart_setup_bonuses"] += 1
        used += 1
    return {"restart_setup_bonuses": used}


def _finalize_raw_bin(bucket: dict[str, Any]) -> dict[str, Any]:
    all_samples = max(int(bucket["all_samples"]), 1)
    team_a_positional = float(bucket["team_a_frame_score_sum"]) / all_samples
    team_b_positional = float(bucket["team_b_frame_score_sum"]) / all_samples
    team_a_raw = team_a_positional + float(bucket["team_a_event_bonus"])
    team_b_raw = team_b_positional + float(bucket["team_b_event_bonus"])
    controlled_samples = int(bucket["team_a_controlled_samples"]) + int(bucket["team_b_controlled_samples"])
    mean_confidence = float(bucket["controlled_confidence_sum"]) / max(int(bucket["controlled_confidence_samples"]), 1)
    controlled_coverage = controlled_samples / max(int(bucket["all_samples"]), 1)
    direction_coverage = int(bucket["direction_samples"]) / max(controlled_samples, 1)
    return {
        **bucket,
        "team_a_positional_raw": team_a_positional,
        "team_b_positional_raw": team_b_positional,
        "team_a_raw": team_a_raw,
        "team_b_raw": team_b_raw,
        "signed_raw": team_a_raw - team_b_raw,
        "controlled_coverage": controlled_coverage,
        "direction_coverage": direction_coverage,
        "mean_confidence": mean_confidence,
    }


def _apply_causal_smoothing(points: list[dict[str, Any]], bin_sec: float, window_sec: float) -> None:
    window_bins = max(1, round(window_sec / bin_sec))
    alpha = 2.0 / (window_bins + 1.0)
    previous = 0.0
    for point in points:
        current = float(point["signed_raw"])
        smoothed = alpha * current + (1.0 - alpha) * previous
        point["smoothed_signed_raw"] = smoothed
        previous = smoothed


def _normalization_scale(points: list[dict[str, Any]]) -> float:
    values = sorted(abs(float(point["smoothed_signed_raw"])) for point in points if abs(float(point["smoothed_signed_raw"])) > 1e-12)
    return max(_percentile(values, 95.0), MIN_NORMALIZATION_SCALE)


def _public_point(point: dict[str, Any], scale: float) -> dict[str, Any]:
    signed_score = max(-100.0, min(100.0, float(point["smoothed_signed_raw"]) / max(scale, 1e-9) * 100.0))
    point_confidence = _clamp01(
        float(point["mean_confidence"])
        * float(point["direction_coverage"])
        * math.sqrt(max(0.0, float(point["controlled_coverage"])))
    )
    dominant = None
    if abs(signed_score) >= DOMINANT_TEAM_DEAD_ZONE and point_confidence >= MIN_POINT_CONFIDENCE:
        dominant = "A" if signed_score > 0 else "B"
    return {
        "index": int(point["index"]),
        "time_sec": round(float(point["time_sec"]), 3),
        "start_time_sec": round(float(point["start_time_sec"]), 3),
        "end_time_sec": round(float(point["end_time_sec"]), 3),
        "all_samples": int(point["all_samples"]),
        "team_a_controlled_samples": int(point["team_a_controlled_samples"]),
        "team_b_controlled_samples": int(point["team_b_controlled_samples"]),
        "team_a_positional_raw": round(float(point["team_a_positional_raw"]), 6),
        "team_b_positional_raw": round(float(point["team_b_positional_raw"]), 6),
        "team_a_event_bonus": round(float(point["team_a_event_bonus"]), 6),
        "team_b_event_bonus": round(float(point["team_b_event_bonus"]), 6),
        "team_a_raw": round(float(point["team_a_raw"]), 6),
        "team_b_raw": round(float(point["team_b_raw"]), 6),
        "signed_raw": round(float(point["signed_raw"]), 6),
        "smoothed_signed_raw": round(float(point["smoothed_signed_raw"]), 6),
        "signed_score": round(signed_score, 3),
        "team_a_value": round(max(0.0, signed_score), 3),
        "team_b_value": round(min(0.0, signed_score), 3),
        "dominant_team_label": dominant,
        "confidence": round(point_confidence, 4),
        "controlled_coverage": round(float(point["controlled_coverage"]), 4),
        "direction_coverage": round(float(point["direction_coverage"]), 4),
        "intensity": round(abs(signed_score) / 100.0, 4),
        "evidence": dict(point["evidence"]),
    }


def _quality(
    *,
    known_coverage: float,
    controlled_coverage: float,
    direction_coverage: float,
    scored_frames: int,
    needs_review: bool,
    interpolated_share: float,
) -> str:
    high = (
        known_coverage >= 0.75
        and controlled_coverage >= 0.35
        and direction_coverage >= 0.95
        and scored_frames >= 30
        and interpolated_share < 0.7
    )
    if high and not needs_review:
        return "high"
    medium = (
        known_coverage >= 0.50
        and controlled_coverage >= 0.20
        and direction_coverage >= 0.80
        and scored_frames >= 10
    )
    return "medium" if medium else "low"


def _warnings(
    *,
    known_coverage: float,
    controlled_coverage: float,
    direction_coverage: float,
    needs_review: bool,
    interpolated_share: float,
    has_pass_candidates: bool,
) -> list[str]:
    warnings: list[str] = []
    if known_coverage < 0.50:
        warnings.append("Known possession coverage is below 50%.")
    if controlled_coverage < 0.20:
        warnings.append("Controlled possession coverage is too low for a stable momentum signal.")
    if direction_coverage < 0.95:
        warnings.append("Attack direction is unknown for part of the match.")
    if needs_review:
        warnings.append("Match phase direction still uses an unconfirmed default.")
    if not has_pass_candidates:
        warnings.append("Pass candidates were missing; momentum used positional possession only.")
    if interpolated_share >= 0.50:
        warnings.append("A high share of scored samples uses interpolated positions.")
    return warnings


def _parameters(bin_sec: float, smoothing_window_sec: float) -> dict[str, Any]:
    return {
        "bin_sec": bin_sec,
        "smoothing_window_sec": smoothing_window_sec,
        "smoothing_method": "causal_ema",
        "normalization": "robust_abs_p95_with_floor",
        "position_base_score": POSITION_BASE_SCORE,
        "position_weight": POSITION_WEIGHT,
        "position_exponent": POSITION_EXPONENT,
        "progression_lookback_sec": PROGRESSION_LOOKBACK_SEC,
        "progression_max_gap_sec": PROGRESSION_MAX_GAP_SEC,
        "progression_full_bonus_m": PROGRESSION_FULL_BONUS_M,
        "progression_max_bonus": PROGRESSION_MAX_BONUS,
        "completed_pass_base_bonus": COMPLETED_PASS_BASE_BONUS,
        "failed_pass_base_bonus": FAILED_PASS_BASE_BONUS,
        "progressive_pass_max_bonus": PROGRESSIVE_PASS_MAX_BONUS,
        "restart_setup_base_bonus": RESTART_SETUP_BASE_BONUS,
        "minimum_normalization_scale": MIN_NORMALIZATION_SCALE,
        "dominant_team_dead_zone": DOMINANT_TEAM_DEAD_ZONE,
        "uses_possession_candidates": True,
        "uses_pass_outcomes": True,
        "uses_restart_candidates": True,
        "camera_motion_reapplied": False,
    }


def _empty_document(*, bin_sec: float, smoothing_window_sec: float, warning: str) -> dict[str, Any]:
    return {
        "schema_version": "0.2.0",
        "generated_at": _now_iso(),
        "source": MOMENTUM_SOURCE,
        "status": "completed",
        "experimental": True,
        "semantics": "relative_attacking_pressure_estimate_not_official_stat",
        "parameters": _parameters(bin_sec, smoothing_window_sec),
        "summary": {
            "points": 0,
            "duration_sec": 0.0,
            "known_possession_coverage": 0.0,
            "controlled_coverage": 0.0,
            "direction_coverage": 0.0,
            "scored_controlled_frames": 0,
            "pass_attempts_used": 0,
            "completed_passes_used": 0,
            "failed_passes_used": 0,
            "excluded_non_pass_ignored": 0,
            "restart_passes_used": 0,
            "restart_setup_bonuses": 0,
            "normalization_scale": MIN_NORMALIZATION_SCALE,
            "team_a_pressure_share": 0.0,
            "team_b_pressure_share": 0.0,
            "team_a_peak": 0.0,
            "team_b_peak": 0.0,
            "interpolated_scored_share": 0.0,
            "quality": "low",
        },
        "points": [],
        "warnings": [warning],
        "notes": ["Momentum could not be estimated without candidate data."],
    }


def _sorted_frames(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows = document.get("frames") if isinstance(document.get("frames"), list) else []
    return sorted((row for row in rows if isinstance(row, dict)), key=lambda row: (_time_sec(row), int(row.get("frame") or 0)))


def _candidate_rows(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = document.get("candidates") if isinstance(document, dict) and isinstance(document.get("candidates"), list) else []
    return sorted((row for row in rows if isinstance(row, dict)), key=lambda row: (_event_time_sec(row), str(row.get("candidate_id") or "")))


def _trim_progress_history(history: list[tuple[float, float, str]], current_time_sec: float) -> None:
    cutoff = current_time_sec - PROGRESSION_MAX_GAP_SEC
    while history and history[0][0] < cutoff:
        history.pop(0)


def _attack_axis_length(direction: str, pitch_width_m: float, pitch_length_m: float) -> float:
    return float(pitch_width_m) if direction in {"towards_x_min", "towards_x_max"} else float(pitch_length_m)


def _event_time_sec(candidate: dict[str, Any]) -> float:
    for key in ("end_time_sec", "start_time_sec", "time_sec"):
        if candidate.get(key) is not None:
            return max(0.0, _number(candidate.get(key), 0.0))
    return 0.0


def _time_sec(frame: dict[str, Any]) -> float:
    return max(0.0, _number(frame.get("time_sec"), 0.0))


def _bin_index(time_sec: float, bin_sec: float, bin_count: int) -> int:
    return min(max(0, int(math.floor(max(0.0, time_sec) / bin_sec))), max(0, bin_count - 1))


def _team_label(value: Any) -> str | None:
    label = str(value or "").upper()
    return label if label in {"A", "B"} else None


def _valid_pair(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 2 and value[0] is not None and value[1] is not None


def _number(value: Any, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    return numeric if math.isfinite(numeric) else fallback


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _ratio(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator > 0 else 0.0


def _ratio_float(numerator: float, denominator: float) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator > 0 else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    rank = (len(values) - 1) * _clamp01(percentile / 100.0)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return float(values[lower])
    fraction = rank - lower
    return float(values[lower]) * (1.0 - fraction) + float(values[upper]) * fraction


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
