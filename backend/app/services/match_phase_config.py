from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.pass_candidates import write_pass_candidate_artifacts

MATCH_PHASE_SOURCE = "match_phase_config_v1"
ATTACK_DIRECTIONS = {"towards_y_min", "towards_y_max", "towards_x_min", "towards_x_max", "unknown"}
DEFAULT_TEAM_A_FIRST_HALF_DIRECTION = "towards_y_min"
DEFAULT_TEAM_B_FIRST_HALF_DIRECTION = "towards_y_max"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_default_match_phase_config(
    meta: dict[str, Any],
    *,
    team_a_first_half_direction: str = DEFAULT_TEAM_A_FIRST_HALF_DIRECTION,
) -> dict[str, Any]:
    duration_sec = _video_duration_sec(meta)
    team_a_direction = _normalize_direction(team_a_first_half_direction)
    team_b_direction = _opposite_direction(team_a_direction)
    period = _period(
        period_id="full_video",
        label="Full video / first half context",
        start_time_sec=0.0,
        end_time_sec=duration_sec,
        team_a_direction=team_a_direction,
        team_b_direction=team_b_direction,
        source="default_single_period",
    )
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "updated_at": now_iso(),
        "source": MATCH_PHASE_SOURCE,
        "coordinate_system": "pitch_m_origin_top_left_y_down",
        "direction_axis": "pitch_y",
        "default_team_a_first_half_direction": team_a_direction,
        "default_team_b_first_half_direction": team_b_direction,
        "halves_switch_sides": True,
        "second_half_start_time_sec": None,
        "periods": [period],
        "summary": {
            "periods": 1,
            "has_second_half": False,
            "needs_review": True,
        },
        "notes": [
            "Default assumes Team A attacks towards lower pitch y in the first period and Team B the opposite direction.",
            "Set second_half_start_time_sec when the video contains a side switch.",
        ],
    }


def build_two_half_match_phase_config(
    meta: dict[str, Any],
    *,
    second_half_start_time_sec: float,
    first_half_start_time_sec: float = 0.0,
    first_half_end_time_sec: float | None = None,
    second_half_end_time_sec: float | None = None,
    team_a_first_half_direction: str = DEFAULT_TEAM_A_FIRST_HALF_DIRECTION,
) -> dict[str, Any]:
    duration_sec = _video_duration_sec(meta)
    second_start = _clamp_time(second_half_start_time_sec, duration_sec)
    first_start = _clamp_time(first_half_start_time_sec, duration_sec)
    first_end = _clamp_time(first_half_end_time_sec if first_half_end_time_sec is not None else second_start, duration_sec)
    second_end = _clamp_time(second_half_end_time_sec if second_half_end_time_sec is not None else duration_sec, duration_sec)
    team_a_first = _normalize_direction(team_a_first_half_direction)
    team_b_first = _opposite_direction(team_a_first)
    team_a_second = _opposite_direction(team_a_first)
    team_b_second = _opposite_direction(team_b_first)
    periods = [
        _period(
            period_id="first_half",
            label="First half",
            start_time_sec=first_start,
            end_time_sec=first_end,
            team_a_direction=team_a_first,
            team_b_direction=team_b_first,
            source="configured_half",
        ),
        _period(
            period_id="second_half",
            label="Second half",
            start_time_sec=second_start,
            end_time_sec=second_end,
            team_a_direction=team_a_second,
            team_b_direction=team_b_second,
            source="configured_half_side_switch",
        ),
    ]
    return normalize_match_phase_config(
        {
            "schema_version": "0.1.0",
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source": MATCH_PHASE_SOURCE,
            "coordinate_system": "pitch_m_origin_top_left_y_down",
            "direction_axis": "pitch_y",
            "default_team_a_first_half_direction": team_a_first,
            "default_team_b_first_half_direction": team_b_first,
            "halves_switch_sides": True,
            "second_half_start_time_sec": round(second_start, 3),
            "periods": periods,
            "notes": [
                "Directions are switched automatically in the second half.",
                "Pass direction/progressive labels are candidate-only until review/model validation.",
            ],
        },
        meta,
    )


def load_match_phase_config(match_path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    path = match_path / "match_phase_config.json"
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("match_phase_config.json must be a JSON object")
        return normalize_match_phase_config(document, meta)
    document = build_default_match_phase_config(meta)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document


def save_match_phase_config(match_path: Path, meta: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("second_half_start_time_sec") is not None:
        document = build_two_half_match_phase_config(
            meta,
            second_half_start_time_sec=float(payload.get("second_half_start_time_sec")),
            first_half_start_time_sec=float(payload.get("first_half_start_time_sec") or 0.0),
            first_half_end_time_sec=_optional_float(payload.get("first_half_end_time_sec")),
            second_half_end_time_sec=_optional_float(payload.get("second_half_end_time_sec")),
            team_a_first_half_direction=str(
                payload.get("team_a_first_half_direction") or DEFAULT_TEAM_A_FIRST_HALF_DIRECTION
            ),
        )
    elif payload.get("team_a_first_half_direction") is not None and not payload.get("periods"):
        document = build_default_match_phase_config(
            meta,
            team_a_first_half_direction=str(payload.get("team_a_first_half_direction")),
        )
    else:
        document = normalize_match_phase_config(dict(payload), meta)
    document["updated_at"] = now_iso()
    (match_path / "match_phase_config.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    _refresh_pass_candidates(match_path, document)
    return document


def direction_for_team_at_time(config: dict[str, Any] | None, team_label: Any, time_sec: Any) -> dict[str, Any]:
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
                "attack_direction": _normalize_direction(directions.get(label)),
                "direction_source": period.get("direction_source") or period.get("source") or "match_phase_config",
            }
    return {"period_id": None, "attack_direction": "unknown", "direction_source": "outside_configured_periods"}


def normalize_match_phase_config(document: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    duration_sec = _video_duration_sec(meta)
    periods = document.get("periods") if isinstance(document.get("periods"), list) else []
    normalized_periods = []
    for index, raw_period in enumerate(periods):
        if not isinstance(raw_period, dict):
            continue
        directions = raw_period.get("team_attack_directions") if isinstance(raw_period.get("team_attack_directions"), dict) else {}
        team_a = _normalize_direction(directions.get("A") or raw_period.get("team_a_direction"))
        team_b = _normalize_direction(directions.get("B") or raw_period.get("team_b_direction") or _opposite_direction(team_a))
        normalized_periods.append(
            _period(
                period_id=str(raw_period.get("period_id") or f"period_{index + 1}"),
                label=str(raw_period.get("label") or f"Period {index + 1}"),
                start_time_sec=_clamp_time(raw_period.get("start_time_sec"), duration_sec),
                end_time_sec=_clamp_time(raw_period.get("end_time_sec"), duration_sec),
                team_a_direction=team_a,
                team_b_direction=team_b,
                source=str(raw_period.get("direction_source") or raw_period.get("source") or "configured_period"),
            )
        )
    if not normalized_periods:
        return build_default_match_phase_config(meta)
    normalized_periods.sort(key=lambda item: float(item.get("start_time_sec") or 0.0))
    has_second_half = len(normalized_periods) >= 2
    document["schema_version"] = str(document.get("schema_version") or "0.1.0")
    document["source"] = str(document.get("source") or MATCH_PHASE_SOURCE)
    document["coordinate_system"] = str(document.get("coordinate_system") or "pitch_m_origin_top_left_y_down")
    document["direction_axis"] = str(document.get("direction_axis") or "pitch_y")
    document["periods"] = normalized_periods
    document["summary"] = {
        "periods": len(normalized_periods),
        "has_second_half": has_second_half,
        "needs_review": bool(document.get("summary", {}).get("needs_review", False)),
    }
    return document


def _period(
    *,
    period_id: str,
    label: str,
    start_time_sec: float,
    end_time_sec: float | None,
    team_a_direction: str,
    team_b_direction: str,
    source: str,
) -> dict[str, Any]:
    return {
        "period_id": period_id,
        "label": label,
        "start_time_sec": round(float(start_time_sec), 3),
        "end_time_sec": round(float(end_time_sec), 3) if end_time_sec is not None else None,
        "team_attack_directions": {
            "A": _normalize_direction(team_a_direction),
            "B": _normalize_direction(team_b_direction),
        },
        "direction_source": source,
    }


def _video_duration_sec(meta: dict[str, Any]) -> float | None:
    video = meta.get("video") if isinstance(meta.get("video"), dict) else {}
    value = video.get("duration_sec")
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _clamp_time(value: Any, duration_sec: float | None) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    numeric = max(0.0, numeric)
    if duration_sec is not None:
        numeric = min(numeric, duration_sec)
    return round(numeric, 3)


def _normalize_direction(value: Any) -> str:
    direction = str(value or "unknown")
    if direction not in ATTACK_DIRECTIONS:
        return "unknown"
    return direction


def _opposite_direction(direction: str) -> str:
    return {
        "towards_y_min": "towards_y_max",
        "towards_y_max": "towards_y_min",
        "towards_x_min": "towards_x_max",
        "towards_x_max": "towards_x_min",
    }.get(direction, "unknown")


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _refresh_pass_candidates(match_path: Path, match_phase_config: dict[str, Any]) -> None:
    event_path = match_path / "event_candidates.json"
    if not event_path.exists():
        return
    event_candidates = json.loads(event_path.read_text(encoding="utf-8"))
    if isinstance(event_candidates, dict):
        write_pass_candidate_artifacts(match_path, event_candidates, match_phase_config)
