from __future__ import annotations

"""Shadow-only active-play selector over persisted ball and player evidence.

The selector deliberately lives in ``evaluation``.  Runtime ball tracking does
not import it and production continues to use ``ball-selection:v1``.
"""

from collections import Counter
from typing import Any, Mapping

from app.services.ball_tracking import (
    DEFAULT_MAX_INTERPOLATION_GAP_SEC,
    DEFAULT_MAX_INTERPOLATION_SPEED_MPS,
    DEFAULT_RECOVERY_SEGMENT_MIN_DETECTIONS,
    DEFAULT_RECOVERY_SEGMENT_MIN_DURATION_SEC,
    build_ball_positions,
    filter_recovery_ball_segments,
    select_ball_detections,
)


POLICY_VERSION = "ball-selection:v3-active-play-shadow"
TRUSTED_CONFIDENCE = 0.35
STRONG_CONFIDENCE = 0.55
MAX_CONTINUITY_SPEED_MPS = 42.0
MAX_REACQUIRE_SPEED_MPS = 55.0
LOOKAHEAD_FRAMES = 3
PLAYER_RADIUS_M = 6.0


def build_player_activity_context(raw_tracks: list[Mapping[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Return upstream player observations keyed by frame, without identity labels."""

    by_frame: dict[int, list[dict[str, Any]]] = {}
    for track in raw_tracks:
        if not isinstance(track, Mapping):
            continue
        previous: dict[str, Any] | None = None
        for row in sorted((item for item in track.get("positions") or [] if isinstance(item, Mapping)), key=lambda item: int(item.get("frame") or 0)):
            point = _point(row.get("pitch_m"))
            if point is None:
                continue
            frame = int(row.get("frame") or 0)
            previous_point = _point(previous.get("pitch_m")) if previous is not None else None
            by_frame.setdefault(frame, []).append(
                {
                    "position_m": point,
                    "previous_position_m": previous_point,
                    "previous_frame": int(previous.get("frame") or 0) if previous is not None else None,
                }
            )
            previous = dict(row)
    return by_frame


def select_active_play_shadow(
    frames: list[Mapping[str, Any]],
    *,
    fps: float,
    player_context_by_frame: Mapping[int, list[Mapping[str, Any]]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Select bounded, corroborated candidates without changing confidence.

    A low-confidence row is selected only when short future evidence supports
    it and no stronger plausible candidate competes.  Reacquisition permits a
    brief deflection discontinuity only for a strong, multi-frame-supported
    candidate.  Player context breaks otherwise close competing hypotheses.
    """

    ordered = sorted((dict(frame) for frame in frames if isinstance(frame, Mapping)), key=lambda item: int(item.get("frame") or 0))
    baseline_selected = select_ball_detections(
        ordered,
        fps=fps,
        max_link_speed_mps=22.0,
        min_start_conf=0.08,
        policy_version="ball-selection:v1",
    )
    selected: dict[int, dict[str, Any]] = {}
    last_trusted: dict[str, Any] | None = None
    diagnostics: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    context = player_context_by_frame or {}
    for index, frame in enumerate(ordered):
        frame_number = int(frame.get("frame") or 0)
        candidates = sorted((dict(item) for item in frame.get("candidates") or [] if isinstance(item, Mapping)), key=lambda item: str(item.get("candidate_id") or ""))
        if not candidates:
            diagnostics["no_candidates"] += 1
            continue
        choices = _rank_choices(
            ordered,
            index,
            candidates,
            last_trusted=last_trusted,
            fps=fps,
            players=context.get(frame_number) or [],
        )
        if not choices:
            diagnostics["no_bounded_choice"] += 1
            continue
        choice = choices[0]
        baseline = baseline_selected.get(frame_number)
        if baseline is not None and str(baseline.get("candidate_id") or "") != str(choice["candidate"].get("candidate_id") or ""):
            baseline_active_score = _active_play_score(baseline, context.get(frame_number) or [])
            has_material_active_play_advantage = choice["active_play_score"] >= baseline_active_score + 0.20
            baseline_is_weaker = float(baseline.get("confidence") or 0.0) < TRUSTED_CONFIDENCE and choice["confidence"] >= STRONG_CONFIDENCE
            baseline_is_materially_stronger = float(baseline.get("confidence") or 0.0) >= choice["confidence"] + 0.12
            if baseline_is_materially_stronger and not has_material_active_play_advantage and not baseline_is_weaker:
                choice = {
                    "candidate": dict(baseline),
                    "reason": "baseline_preserved_without_active_play_margin",
                    "supported_low_confidence": False,
                    "active_play_score": baseline_active_score,
                    "forward_support_frames": _forward_support(ordered, index, baseline, fps),
                    "speed_mps": _speed(last_trusted, baseline, fps),
                    "score": 0.0,
                }
                diagnostics["baseline_preserved"] += 1
        candidate = choice["candidate"]
        selected_row = {
            **candidate,
            "selection_reason": choice["reason"],
            "supported_low_confidence": bool(choice["supported_low_confidence"]),
            "selection_details": {
                "active_play_score": round(choice["active_play_score"], 4),
                "forward_support_frames": int(choice["forward_support_frames"]),
                "continuity_speed_mps": round(choice["speed_mps"], 3) if choice["speed_mps"] is not None else None,
                "selection_score": round(choice["score"], 4),
            },
        }
        selected[frame_number] = selected_row
        if selected_row["supported_low_confidence"]:
            diagnostics["supported_low_confidence"] += 1
        if choice["reason"] == "deflection_reacquire":
            diagnostics["deflection_reacquires"] += 1
        if len(candidates) > 1:
            diagnostics["multi_candidate_decisions"] += 1
            if len(examples) < 100:
                examples.append({"frame": frame_number, "selected_candidate_id": candidate.get("candidate_id"), "reason": choice["reason"]})
        if float(selected_row.get("confidence") or 0.0) >= TRUSTED_CONFIDENCE:
            last_trusted = selected_row
    return selected, {
        "policy_version": POLICY_VERSION,
        "supported_low_confidence_rows": diagnostics["supported_low_confidence"],
        "deflection_reacquires": diagnostics["deflection_reacquires"],
        "multi_candidate_frame_decisions": diagnostics["multi_candidate_decisions"],
        "rejected_unbounded_frames": diagnostics["no_bounded_choice"],
        "multi_candidate_examples": examples,
    }


def build_active_play_shadow_tracks_document(
    frames: list[Mapping[str, Any]],
    *,
    processed_frames: list[int],
    fps: float,
    parameters: Mapping[str, Any],
    player_context_by_frame: Mapping[int, list[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    selected, diagnostics = select_active_play_shadow(frames, fps=fps, player_context_by_frame=player_context_by_frame)
    supported_low_confidence_frames = {
        frame for frame, row in selected.items() if bool(row.get("supported_low_confidence"))
    }
    filtered = filter_recovery_ball_segments(
        selected,
        fps=fps,
        min_detections=int(parameters.get("recovery_segment_min_detections") or DEFAULT_RECOVERY_SEGMENT_MIN_DETECTIONS),
        min_duration_sec=float(parameters.get("recovery_segment_min_duration_sec") or DEFAULT_RECOVERY_SEGMENT_MIN_DURATION_SEC),
    )
    positions, interpolation_gaps = build_ball_positions(
        filtered,
        processed_frames=processed_frames,
        fps=fps,
        max_interpolation_gap_sec=float(parameters.get("max_interpolation_gap_sec") or DEFAULT_MAX_INTERPOLATION_GAP_SEC),
        max_interpolation_speed_mps=float(parameters.get("max_interpolation_speed_mps") or DEFAULT_MAX_INTERPOLATION_SPEED_MPS),
    )
    for position in positions:
        if int(position.get("frame") or -1) in supported_low_confidence_frames:
            position["supported_low_confidence"] = True
    sources = Counter(str(row.get("source") or "unknown") for row in positions)
    detected_confidence = [float(row.get("confidence") or 0.0) for row in positions if row.get("source") == "detected"]
    return {
        "schema_version": "ball-tracks-active-play-shadow:v1",
        "evaluation_only": True,
        "source": parameters.get("detector"),
        "track_id": "ball-main-shadow-v3",
        "status_semantics": "detected_interpolated_unknown",
        "parameters": {**dict(parameters), "ball_selection_policy": POLICY_VERSION},
        "positions": positions,
        "interpolation_gaps": interpolation_gaps,
        "summary": {
            "processed_frames": len(positions),
            "detected_frames": sources["detected"],
            "interpolated_frames": sources["interpolated"],
            "unknown_frames": sources["unknown"],
            "detected_coverage": _ratio(sources["detected"], len(positions)),
            "interpolated_coverage": _ratio(sources["interpolated"], len(positions)),
            "known_coverage": _ratio(sources["detected"] + sources["interpolated"], len(positions)),
            "mean_detected_confidence": round(sum(detected_confidence) / len(detected_confidence), 4) if detected_confidence else None,
            "interpolation_gaps": len(interpolation_gaps),
            "ball_segment_count": _segment_count(filtered, fps),
            "supported_low_confidence_frames": diagnostics["supported_low_confidence_rows"],
        },
        "selection_diagnostics": {"method": "active_play_bounded_shadow_v3", **diagnostics},
    }


def _rank_choices(
    ordered: list[dict[str, Any]],
    index: int,
    candidates: list[dict[str, Any]],
    *,
    last_trusted: Mapping[str, Any] | None,
    fps: float,
    players: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for candidate in candidates:
        confidence = float(candidate.get("confidence") or 0.0)
        # A supported low-confidence selection is useful evidence, but cannot
        # become the sole geometric anchor for subsequent continuity checks.
        speed = _speed(last_trusted, candidate, fps)
        support = _forward_support(ordered, index, candidate, fps)
        plausible = speed is None or speed <= MAX_CONTINUITY_SPEED_MPS
        reacquire = speed is not None and speed <= MAX_REACQUIRE_SPEED_MPS and confidence >= STRONG_CONFIDENCE and support >= 2
        if not plausible and not reacquire:
            continue
        active_score = _active_play_score(candidate, players)
        raw.append({"candidate": candidate, "confidence": confidence, "speed_mps": speed, "forward_support_frames": support, "active_play_score": active_score, "reacquire": reacquire})
    stronger_plausible = any(item["confidence"] >= TRUSTED_CONFIDENCE for item in raw)
    choices: list[dict[str, Any]] = []
    for item in raw:
        supported_low = item["confidence"] < TRUSTED_CONFIDENCE and item["forward_support_frames"] >= 2 and not stronger_plausible
        if item["confidence"] < TRUSTED_CONFIDENCE and not supported_low:
            continue
        speed_cost = min((item["speed_mps"] or 0.0) / MAX_REACQUIRE_SPEED_MPS, 1.0)
        score = item["confidence"] * 0.45 + item["active_play_score"] * 0.35 + min(item["forward_support_frames"], 3) / 3 * 0.14 - speed_cost * 0.06
        choices.append({**item, "supported_low_confidence": supported_low, "reason": "deflection_reacquire" if item["reacquire"] else ("supported_low_confidence" if supported_low else "active_play_continuation"), "score": score})
    return sorted(choices, key=lambda item: (-item["score"], -item["confidence"], str(item["candidate"].get("candidate_id") or "")))


def _forward_support(frames: list[dict[str, Any]], index: int, candidate: Mapping[str, Any], fps: float) -> int:
    support = 0
    origin_frame = int(candidate.get("frame") or 0)
    for future in frames[index + 1 : index + 1 + LOOKAHEAD_FRAMES]:
        future_frame = int(future.get("frame") or 0)
        dt = max((future_frame - origin_frame) / max(fps, 0.001), 1.0 / max(fps, 0.001))
        if any(_distance(candidate.get("position_m"), item.get("position_m")) is not None and _distance(candidate.get("position_m"), item.get("position_m")) / dt <= MAX_REACQUIRE_SPEED_MPS and float(item.get("confidence") or 0.0) >= TRUSTED_CONFIDENCE for item in future.get("candidates") or [] if isinstance(item, Mapping)):
            support += 1
    return support


def _active_play_score(candidate: Mapping[str, Any], players: list[Mapping[str, Any]]) -> float:
    point = _point(candidate.get("position_m"))
    if point is None or not players:
        return 0.0
    distances = [_distance(point, player.get("position_m")) for player in players]
    valid = [distance for distance in distances if distance is not None]
    if not valid:
        return 0.0
    nearest = min(valid)
    nearby = sum(1 for distance in valid if distance <= PLAYER_RADIUS_M)
    approaching = 0
    for player in players:
        prior = _distance(point, player.get("previous_position_m"))
        current = _distance(point, player.get("position_m"))
        if prior is not None and current is not None and prior > current:
            approaching += 1
    return min(1.0, max(0.0, (1.0 - nearest / (PLAYER_RADIUS_M * 2)) * 0.55 + min(nearby, 3) * 0.1 + min(approaching, 2) * 0.075))


def _speed(last: Mapping[str, Any] | None, candidate: Mapping[str, Any], fps: float) -> float | None:
    if last is None:
        return None
    distance = _distance(last.get("position_m"), candidate.get("position_m"))
    if distance is None:
        return None
    frames = max(1, int(candidate.get("frame") or 0) - int(last.get("frame") or 0))
    return distance / (frames / max(fps, 0.001))


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    return [float(value[0]), float(value[1])]


def _distance(left: Any, right: Any) -> float | None:
    a, b = _point(left), _point(right)
    if a is None or b is None:
        return None
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _ratio(value: int, total: int) -> float:
    return round(value / total, 4) if total else 0.0


def _segment_count(selected: Mapping[int, Mapping[str, Any]], fps: float) -> int:
    count = 0
    last: int | None = None
    maximum_gap = max(1, int(round(DEFAULT_MAX_INTERPOLATION_GAP_SEC * fps)))
    for frame in sorted(selected):
        if last is None or frame > last + maximum_gap:
            count += 1
        last = frame
    return count
