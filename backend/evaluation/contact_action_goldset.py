"""Validation helpers for the evaluation-only Contact / Action Goldset v1."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


SCHEMA_VERSION = "contact-action-goldset:v1"
PRIMARY_ACTIONS = frozenset({"PASS", "RESTART", "INTERVENTION", "CONTROL", "CONTEST", "GK_COLLECTION", "OTHER"})
OUTCOMES = frozenset({"COMPLETED", "INTERCEPTED", "MISCONTROLLED", "OUT", "FOUL", "UNKNOWN"})
MODIFIERS = frozenset(
    {
        "LONG",
        "ONE_TOUCH",
        "HEADER",
        "THROUGH_BALL",
        "CLEARANCE",
        "BLOCK",
        "DEFLECTION",
        "TACKLE",
        "DRIBBLE",
        "AMBIGUOUS",
        "ATTEMPTED_CONTACT",
    }
)
GAME_STATES = frozenset({"NOT_IN_PLAY", "GK_HOLD"})
TIMING_CONFIDENCES = frozenset({"high", "medium", "low"})
ANNOTATION_CONFIDENCES = TIMING_CONFIDENCES


def validate_contact_action_goldset(
    document: Mapping[str, Any],
    canonical_shots: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Return deterministic validation errors for an evaluation-only goldset."""

    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be contact-action-goldset:v1")

    windows = list(document.get("windows") or [])
    window_by_id: dict[str, Mapping[str, Any]] = {}
    previous_end: float | None = None
    for window in sorted(windows, key=lambda item: _number(item.get("merged_start_time_sec"), -1.0)):
        window_id = _text(window.get("window_id"))
        start = _number(window.get("merged_start_time_sec"), -1.0)
        end = _number(window.get("merged_end_time_sec"), -1.0)
        source_start = _number(window.get("source_start_time_sec"), -1.0)
        source_end = _number(window.get("source_end_time_sec"), -1.0)
        offset = _number(window.get("source_to_merged_offset_sec"), float("nan"))
        if not window_id or window_id in window_by_id:
            errors.append(f"window id is missing or duplicated: {window_id or '<missing>'}")
        else:
            window_by_id[window_id] = window
        if not start < end:
            errors.append(f"{window_id}: merged range is invalid")
        if not source_start < source_end:
            errors.append(f"{window_id}: source range is invalid")
        if previous_end is not None and start < previous_end:
            errors.append(f"{window_id}: merged windows overlap")
        previous_end = max(previous_end or end, end)
        if abs((source_start + offset) - start) > 0.001 or abs((source_end + offset) - end) > 0.001:
            errors.append(f"{window_id}: merged/source time mapping is inconsistent")

    canonical_by_id = {_text(shot.get("id") or shot.get("shot_id")): shot for shot in canonical_shots}
    event_ids: set[str] = set()
    event_order: list[tuple[float, str]] = []
    for event in document.get("events") or []:
        event_id = _text(event.get("event_id"))
        window_id = _text(event.get("window_id"))
        approx = _number(event.get("approx_time_sec"), float("nan"))
        if not event_id or event_id in event_ids:
            errors.append(f"event id is missing or duplicated: {event_id or '<missing>'}")
        event_ids.add(event_id)
        event_order.append((approx, event_id))
        window = window_by_id.get(window_id)
        if window is None:
            errors.append(f"{event_id}: unknown window {window_id}")
            continue
        if not _is_finite(approx):
            errors.append(f"{event_id}: approx_time_sec is required")
        elif not _event_is_near_window(event, window, approx):
            errors.append(f"{event_id}: timestamp is outside its window")
        aligned = event.get("aligned_time_sec")
        if aligned is not None and not _is_finite(_number(aligned, float("nan"))):
            errors.append(f"{event_id}: aligned_time_sec must be numeric or null")
        if _text(event.get("timing_confidence")) not in TIMING_CONFIDENCES:
            errors.append(f"{event_id}: invalid timing_confidence")
        if not isinstance(event.get("manual_note"), str) or not event["manual_note"].strip():
            errors.append(f"{event_id}: manual_note is required")

        if event.get("event_type") == "SHOT_REFERENCE":
            _validate_shot_reference(event, canonical_by_id, errors)
            continue
        action = _text(event.get("action"))
        if action not in PRIMARY_ACTIONS:
            errors.append(f"{event_id}: invalid action {action}")
        if _text(event.get("annotation_confidence")) not in ANNOTATION_CONFIDENCES:
            errors.append(f"{event_id}: invalid annotation_confidence")
        outcome = event.get("outcome")
        if outcome is not None and _text(outcome) not in OUTCOMES:
            errors.append(f"{event_id}: invalid outcome {outcome}")
        for modifier in event.get("modifiers") or []:
            if _text(modifier) not in MODIFIERS:
                errors.append(f"{event_id}: invalid modifier {modifier}")

    if event_order != sorted(event_order, key=lambda item: (item[0], item[1])):
        errors.append("events must be deterministically ordered by approx_time_sec and event_id")

    for interval in document.get("game_state_intervals") or []:
        _validate_interval(interval, window_by_id, errors)
    for note in document.get("identity_notes") or []:
        if _text(note.get("window_id")) not in window_by_id:
            errors.append("identity note has unknown window")
        if not _is_finite(_number(note.get("approx_time_sec"), float("nan"))):
            errors.append("identity note requires approx_time_sec")
    return errors


def summarize_contact_action_goldset(document: Mapping[str, Any]) -> dict[str, int]:
    """Return presentation-only counts; this function performs no evaluation."""

    action_counts = Counter(
        _text(event.get("action"))
        for event in document.get("events") or []
        if event.get("event_type") != "SHOT_REFERENCE"
    )
    return {
        "windows": len(document.get("windows") or []),
        "events": sum(action_counts.values()),
        "pass": action_counts["PASS"],
        "restart": action_counts["RESTART"],
        "intervention": action_counts["INTERVENTION"],
        "control": action_counts["CONTROL"],
        "contest": action_counts["CONTEST"],
        "gk_collection": action_counts["GK_COLLECTION"],
        "other": action_counts["OTHER"],
        "game_state_intervals": len(document.get("game_state_intervals") or []),
        "identity_notes": len(document.get("identity_notes") or []),
        "canonical_shots": sum(event.get("event_type") == "SHOT_REFERENCE" for event in document.get("events") or []),
        "ambiguous_events": sum("AMBIGUOUS" in (event.get("modifiers") or []) for event in document.get("events") or []),
        "possible_missing_manual_events": len(document.get("possible_missing_manual_events") or []),
    }


def _validate_shot_reference(event: Mapping[str, Any], canonical_by_id: Mapping[str, Mapping[str, Any]], errors: list[str]) -> None:
    event_id = _text(event.get("event_id"))
    shot = canonical_by_id.get(_text(event.get("shot_id")))
    if shot is None:
        errors.append(f"{event_id}: shot reference is not canonical")
        return
    expected_time = _number(shot.get("timestamp_sec") or shot.get("time_sec"), float("nan"))
    if abs(_number(event.get("canonical_time_sec"), float("nan")) - expected_time) > 0.001:
        errors.append(f"{event_id}: canonical shot time differs from source")
    for field in ("outcome", "team"):
        expected = shot.get(field)
        if field == "team":
            expected = shot.get("team") or shot.get("team_id")
        if event.get(field) != expected:
            errors.append(f"{event_id}: canonical shot {field} differs from source")


def _validate_interval(interval: Mapping[str, Any], windows: Mapping[str, Mapping[str, Any]], errors: list[str]) -> None:
    window = windows.get(_text(interval.get("window_id")))
    if window is None:
        errors.append("game-state interval has unknown window")
        return
    start = _number(interval.get("start_time_sec"), float("nan"))
    end = _number(interval.get("end_time_sec"), float("nan"))
    if not start < end:
        errors.append("game-state interval has invalid range")
    if _text(interval.get("state")) not in GAME_STATES:
        errors.append("game-state interval has invalid state")
    if start < _number(window.get("merged_start_time_sec"), 0.0) - 0.001 or end > _number(window.get("merged_end_time_sec"), 0.0) + 0.001:
        errors.append("game-state interval is outside its window")


def _event_is_near_window(event: Mapping[str, Any], window: Mapping[str, Any], timestamp: float) -> bool:
    start = _number(window.get("merged_start_time_sec"), 0.0)
    end = _number(window.get("merged_end_time_sec"), 0.0)
    if start <= timestamp <= end:
        return True
    return bool(event.get("context_only")) and start - 2.0 <= timestamp <= end + 30.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _is_finite(value: float) -> bool:
    return value != float("inf") and value != float("-inf") and value == value
