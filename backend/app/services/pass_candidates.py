from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PASS_SOURCE = "ball_contact_events_to_pass_candidates_v1"
PASS_EVENT_STATUSES = {"accepted", "uncertain"}
PASS_REVIEW_STATUSES = {"needs_review", "accepted", "rejected", "uncertain"}
MIN_PASS_GAP_SEC = 0.05
MAX_PASS_GAP_SEC = 5.0
FORWARD_PASS_MIN_PROGRESS_M = 1.5
PROGRESSIVE_PASS_MIN_PROGRESS_M = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_pass_candidates_document(
    event_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contact_events = _sorted_contact_events(event_candidates_doc)
    candidates: list[dict[str, Any]] = []
    skipped_reasons: Counter[str] = Counter()

    for source_event, target_event in zip(contact_events, contact_events[1:]):
        reason = _skip_reason(source_event, target_event)
        if reason:
            skipped_reasons[reason] += 1
            continue
        pass_type = _pass_type(source_event, target_event)
        start_position_m = _event_end_position_m(source_event)
        end_position_m = _event_start_position_m(target_event)
        displacement_m = _displacement_m(start_position_m, end_position_m)
        phase = _direction_for_team_at_time(
            match_phase_config_doc,
            source_event.get("team_label"),
            source_event.get("end_time_sec") if source_event.get("end_time_sec") is not None else source_event.get("start_time_sec"),
        )
        forward_progress_m = _forward_progress_m(start_position_m, end_position_m, phase["attack_direction"])
        direction = _pass_direction(forward_progress_m)
        auto_review_status = _pass_auto_review_status(source_event, target_event)
        review_status = _initial_pass_review_status(auto_review_status)
        candidate_id = f"pass-{len(candidates) + 1:04d}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "event_type": "pass_candidate",
                "pass_type": pass_type,
                "source": PASS_SOURCE,
                "source_event_id": source_event.get("event_id"),
                "target_event_id": target_event.get("event_id"),
                "source_candidate_id": source_event.get("source_candidate_id"),
                "target_candidate_id": target_event.get("source_candidate_id"),
                "from_stable_player_id": source_event.get("stable_player_id"),
                "from_stable_subject_id": source_event.get("stable_subject_id"),
                "from_team_label": source_event.get("team_label"),
                "from_team_id": source_event.get("team_id"),
                "from_team_name": source_event.get("team_name"),
                "to_stable_player_id": target_event.get("stable_player_id"),
                "to_stable_subject_id": target_event.get("stable_subject_id"),
                "to_team_label": target_event.get("team_label"),
                "to_team_id": target_event.get("team_id"),
                "to_team_name": target_event.get("team_name"),
                "start_frame": source_event.get("end_frame"),
                "end_frame": target_event.get("start_frame"),
                "start_time_sec": source_event.get("end_time_sec"),
                "end_time_sec": target_event.get("start_time_sec"),
                "duration_sec": _round(_time_gap_sec(source_event, target_event), 3),
                "start_position_m": start_position_m,
                "end_position_m": end_position_m,
                "displacement_m": displacement_m,
                "distance_m": _distance_m(start_position_m, end_position_m),
                "match_phase_period_id": phase["period_id"],
                "attack_direction": phase["attack_direction"],
                "direction_source": phase["direction_source"],
                "forward_progress_m": forward_progress_m,
                "direction": direction,
                "is_progressive": forward_progress_m is not None and forward_progress_m >= PROGRESSIVE_PASS_MIN_PROGRESS_M,
                "confidence": _pass_confidence(source_event, target_event, pass_type),
                "auto_review_status": auto_review_status,
                "review_status": review_status,
                "review_source": "generated",
                "review_notes": "",
                "final_stat_eligible": False,
                "source_event_review_statuses": [
                    source_event.get("review_status"),
                    target_event.get("review_status"),
                ],
                "notes": [
                    "Candidate only. Do not count as final pass statistic until pass model/review is implemented.",
                ],
            }
        )

    return {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": PASS_SOURCE,
        "experimental": True,
        "candidate_semantics": "pass_candidates_from_consecutive_ball_contacts_not_final_stats",
        "parameters": {
            "min_pass_gap_sec": MIN_PASS_GAP_SEC,
            "max_pass_gap_sec": MAX_PASS_GAP_SEC,
            "forward_pass_min_progress_m": FORWARD_PASS_MIN_PROGRESS_M,
            "progressive_pass_min_progress_m": PROGRESSIVE_PASS_MIN_PROGRESS_M,
            "allowed_source_event_statuses": sorted(PASS_EVENT_STATUSES),
        },
        "summary": _pass_summary(contact_events, candidates, skipped_reasons),
        "candidates": candidates,
    }


def build_pass_review_report(pass_candidates_doc: dict[str, Any]) -> dict[str, Any]:
    summary = dict(pass_candidates_doc.get("summary") or {})
    warnings: list[str] = []
    if int(summary.get("pass_candidates") or 0) == 0:
        warnings.append("No pass candidates were generated from reviewed ball-contact events.")
    if int(summary.get("turnover_or_interception_candidates") or 0) > 0:
        warnings.append("Some candidate sequences switch team and should be treated as turnover/interception candidates.")
    if int(summary.get("needs_review_pass_candidates") or 0) > 0:
        warnings.append("Some pass candidates still need review before they can be used in final pass stats.")
    warnings.append("Only accepted same-team pass candidates are eligible for final pass stats.")
    return {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": PASS_SOURCE,
        "experimental": True,
        "summary": summary,
        "warnings": warnings,
        "notes": [
            "This layer is fully automatic and intentionally conservative.",
            "final_stat_eligible is true only for accepted same-team pass candidates.",
        ],
    }


def build_pass_candidate_artifacts(
    event_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pass_candidates = build_pass_candidates_document(event_candidates_doc, match_phase_config_doc)
    pass_review_report = build_pass_review_report(pass_candidates)
    return {
        "pass_candidates": pass_candidates,
        "pass_review_report": pass_review_report,
        "artifacts": {
            "pass_candidates": "pass_candidates.json",
            "pass_review_report": "pass_review_report.json",
        },
    }


def write_pass_candidate_artifacts(
    match_path: Path,
    event_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_reviews = _load_existing_pass_reviews(match_path)
    artifacts = build_pass_candidate_artifacts(event_candidates_doc, match_phase_config_doc)
    _apply_existing_pass_reviews(artifacts["pass_candidates"], existing_reviews)
    artifacts["pass_review_report"] = build_pass_review_report(artifacts["pass_candidates"])
    (match_path / "pass_candidates.json").write_text(
        json.dumps(artifacts["pass_candidates"], indent=2),
        encoding="utf-8",
    )
    (match_path / "pass_review_report.json").write_text(
        json.dumps(artifacts["pass_review_report"], indent=2),
        encoding="utf-8",
    )
    return artifacts


def _sorted_contact_events(event_candidates_doc: dict[str, Any]) -> list[dict[str, Any]]:
    events = [
        event
        for event in event_candidates_doc.get("events") or []
        if isinstance(event, dict)
        and event.get("event_type") == "ball_contact"
        and str(event.get("review_status") or "needs_review") in PASS_EVENT_STATUSES
    ]
    return sorted(events, key=lambda event: (float(event.get("start_time_sec") or 0.0), int(event.get("start_frame") or 0)))


def _skip_reason(source_event: dict[str, Any], target_event: dict[str, Any]) -> str | None:
    if not source_event.get("stable_player_id") or not target_event.get("stable_player_id"):
        return "missing_stable_player"
    if source_event.get("stable_player_id") == target_event.get("stable_player_id"):
        return "same_player_consecutive_contacts"
    gap = _time_gap_sec(source_event, target_event)
    if gap < MIN_PASS_GAP_SEC:
        return "gap_too_short"
    if gap > MAX_PASS_GAP_SEC:
        return "gap_too_long"
    if not _valid_pair(_event_end_position_m(source_event)) or not _valid_pair(_event_start_position_m(target_event)):
        return "missing_position"
    return None


def _pass_type(source_event: dict[str, Any], target_event: dict[str, Any]) -> str:
    source_team = source_event.get("team_id") or source_event.get("team_label")
    target_team = target_event.get("team_id") or target_event.get("team_label")
    if not source_team or not target_team:
        return "unknown_team_pass"
    if source_team == target_team:
        return "same_team_pass"
    return "turnover_or_interception"


def _pass_auto_review_status(source_event: dict[str, Any], target_event: dict[str, Any]) -> str:
    statuses = {str(source_event.get("review_status") or "needs_review"), str(target_event.get("review_status") or "needs_review")}
    if statuses == {"accepted"}:
        return "strong_candidate"
    if "uncertain" in statuses:
        return "uncertain"
    return "candidate"


def _initial_pass_review_status(auto_review_status: str) -> str:
    if auto_review_status == "uncertain":
        return "uncertain"
    return "needs_review"


def _pass_confidence(source_event: dict[str, Any], target_event: dict[str, Any], pass_type: str) -> float:
    source_confidence = float(source_event.get("confidence") or 0.0)
    target_confidence = float(target_event.get("confidence") or 0.0)
    confidence = min(source_confidence, target_confidence)
    if pass_type == "turnover_or_interception":
        confidence *= 0.75
    if _pass_auto_review_status(source_event, target_event) == "uncertain":
        confidence *= 0.7
    return _round(confidence, 4)


def normalize_pass_review_status(value: Any) -> str:
    status = str(value or "needs_review")
    if status in PASS_REVIEW_STATUSES:
        return status
    if status == "uncertain":
        return "uncertain"
    return "needs_review"


def update_pass_candidate_summary(document: dict[str, Any]) -> None:
    candidates = [candidate for candidate in document.get("candidates") or [] if isinstance(candidate, dict)]
    summary = dict(document.get("summary") or {})
    source_events = int(summary.get("source_contact_events") or 0)
    skipped_reasons = Counter(summary.get("skipped_reasons") if isinstance(summary.get("skipped_reasons"), dict) else {})
    summary.update(_pass_summary([{}] * source_events, candidates, skipped_reasons))
    document["summary"] = summary


def is_final_stat_pass_candidate(candidate: dict[str, Any]) -> bool:
    return (
        normalize_pass_review_status(candidate.get("review_status")) == "accepted"
        and str(candidate.get("pass_type") or "") == "same_team_pass"
    )


def normalize_pass_candidate_review_fields(candidate: dict[str, Any]) -> None:
    raw_status = str(candidate.get("review_status") or "needs_review")
    if raw_status in {"strong_candidate", "candidate"}:
        candidate.setdefault("auto_review_status", raw_status)
        candidate["review_status"] = "needs_review"
    else:
        candidate["review_status"] = normalize_pass_review_status(raw_status)
    candidate.setdefault("review_source", "generated")
    candidate.setdefault("review_notes", "")
    candidate["final_stat_eligible"] = is_final_stat_pass_candidate(candidate)


def _pass_summary(
    contact_events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    skipped_reasons: Counter[str],
) -> dict[str, Any]:
    for candidate in candidates:
        normalize_pass_candidate_review_fields(candidate)
    type_counts = Counter(str(candidate.get("pass_type") or "unknown") for candidate in candidates)
    review_counts = Counter(normalize_pass_review_status(candidate.get("review_status")) for candidate in candidates)
    auto_review_counts = Counter(str(candidate.get("auto_review_status") or "unknown") for candidate in candidates)
    direction_counts = Counter(str(candidate.get("direction") or "unknown") for candidate in candidates)
    final_candidates = [candidate for candidate in candidates if is_final_stat_pass_candidate(candidate)]
    return {
        "source_contact_events": len(contact_events),
        "pass_candidates": len(candidates),
        "pass_type_counts": dict(sorted(type_counts.items())),
        "review_status_counts": dict(sorted(review_counts.items())),
        "auto_review_status_counts": dict(sorted(auto_review_counts.items())),
        "same_team_pass_candidates": type_counts.get("same_team_pass", 0),
        "turnover_or_interception_candidates": type_counts.get("turnover_or_interception", 0),
        "unknown_team_pass_candidates": type_counts.get("unknown_team_pass", 0),
        "direction_counts": dict(sorted(direction_counts.items())),
        "forward_pass_candidates": direction_counts.get("forward", 0),
        "backward_pass_candidates": direction_counts.get("backward", 0),
        "lateral_pass_candidates": direction_counts.get("lateral", 0),
        "unknown_direction_pass_candidates": direction_counts.get("unknown", 0),
        "progressive_pass_candidates": sum(1 for candidate in candidates if candidate.get("is_progressive")),
        "skipped_sequences": sum(skipped_reasons.values()),
        "skipped_reasons": dict(sorted(skipped_reasons.items())),
        "accepted_pass_candidates": review_counts.get("accepted", 0),
        "rejected_pass_candidates": review_counts.get("rejected", 0),
        "uncertain_pass_candidates": review_counts.get("uncertain", 0),
        "needs_review_pass_candidates": review_counts.get("needs_review", 0),
        "final_stat_passes": len(final_candidates),
        "final_forward_passes": sum(1 for candidate in final_candidates if candidate.get("direction") == "forward"),
        "final_progressive_passes": sum(1 for candidate in final_candidates if candidate.get("is_progressive")),
        "candidates_with_positions": sum(
            1 for candidate in candidates if candidate.get("start_position_m") and candidate.get("end_position_m")
        ),
        "candidates_with_direction": sum(
            1 for candidate in candidates if candidate.get("direction") not in {None, "unknown"}
        ),
    }


def _load_existing_pass_reviews(match_path: Path) -> dict[tuple[Any, Any], dict[str, Any]]:
    path = match_path / "pass_candidates.json"
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(document, dict):
        return {}
    reviews: dict[tuple[Any, Any], dict[str, Any]] = {}
    for candidate in document.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        key = _candidate_pair_key(candidate)
        if key == (None, None):
            continue
        reviews[key] = candidate
    return reviews


def _apply_existing_pass_reviews(document: dict[str, Any], existing_reviews: dict[tuple[Any, Any], dict[str, Any]]) -> None:
    if not existing_reviews:
        update_pass_candidate_summary(document)
        return
    for candidate in document.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        existing = existing_reviews.get(_candidate_pair_key(candidate))
        if not existing:
            continue
        for key in ["review_status", "review_source", "review_notes", "reviewed_at"]:
            if key in existing:
                candidate[key] = existing.get(key)
    update_pass_candidate_summary(document)


def _candidate_pair_key(candidate: dict[str, Any]) -> tuple[Any, Any]:
    return (candidate.get("source_event_id"), candidate.get("target_event_id"))


def _direction_for_team_at_time(
    config: dict[str, Any] | None,
    team_label: Any,
    time_sec: Any,
) -> dict[str, Any]:
    if not config:
        return {"period_id": None, "attack_direction": "unknown", "direction_source": "missing_match_phase_config"}
    label = str(team_label or "").upper()
    time_value = float(time_sec or 0.0)
    for period in config.get("periods") or []:
        if not isinstance(period, dict):
            continue
        start = float(period.get("start_time_sec") or 0.0)
        end = period.get("end_time_sec")
        end_value = float(end) if end is not None else float("inf")
        if start <= time_value <= end_value:
            directions = period.get("team_attack_directions") if isinstance(period.get("team_attack_directions"), dict) else {}
            return {
                "period_id": period.get("period_id"),
                "attack_direction": _normalize_attack_direction(directions.get(label)),
                "direction_source": period.get("direction_source") or "match_phase_config",
            }
    return {"period_id": None, "attack_direction": "unknown", "direction_source": "outside_configured_periods"}


def _normalize_attack_direction(value: Any) -> str:
    direction = str(value or "unknown")
    allowed = {"towards_y_min", "towards_y_max", "towards_x_min", "towards_x_max", "unknown"}
    return direction if direction in allowed else "unknown"


def _forward_progress_m(start: Any, end: Any, attack_direction: str) -> float | None:
    if not _valid_pair(start) or not _valid_pair(end):
        return None
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    if attack_direction == "towards_y_min":
        return _round(sy - ey, 3)
    if attack_direction == "towards_y_max":
        return _round(ey - sy, 3)
    if attack_direction == "towards_x_min":
        return _round(sx - ex, 3)
    if attack_direction == "towards_x_max":
        return _round(ex - sx, 3)
    return None


def _pass_direction(forward_progress_m: float | None) -> str:
    if forward_progress_m is None:
        return "unknown"
    if forward_progress_m >= FORWARD_PASS_MIN_PROGRESS_M:
        return "forward"
    if forward_progress_m <= -FORWARD_PASS_MIN_PROGRESS_M:
        return "backward"
    return "lateral"


def _time_gap_sec(source_event: dict[str, Any], target_event: dict[str, Any]) -> float:
    return float(target_event.get("start_time_sec") or 0.0) - float(source_event.get("end_time_sec") or 0.0)


def _event_start_position_m(event: dict[str, Any]) -> list[float] | None:
    return _rounded_pair(event.get("start_position_m"))


def _event_end_position_m(event: dict[str, Any]) -> list[float] | None:
    return _rounded_pair(event.get("end_position_m"))


def _rounded_pair(value: Any) -> list[float] | None:
    if not _valid_pair(value):
        return None
    return [_round(float(value[0]), 3), _round(float(value[1]), 3)]


def _valid_pair(value: Any) -> bool:
    return isinstance(value, list | tuple) and len(value) == 2 and value[0] is not None and value[1] is not None


def _distance_m(start: Any, end: Any) -> float | None:
    if not _valid_pair(start) or not _valid_pair(end):
        return None
    dx, dy = _displacement_m(start, end) or [0.0, 0.0]
    return _round((dx**2 + dy**2) ** 0.5, 3)


def _displacement_m(start: Any, end: Any) -> list[float] | None:
    if not _valid_pair(start) or not _valid_pair(end):
        return None
    return [_round(float(end[0]) - float(start[0]), 3), _round(float(end[1]) - float(start[1]), 3)]


def _round(value: float, digits: int) -> float:
    return round(float(value), digits)
