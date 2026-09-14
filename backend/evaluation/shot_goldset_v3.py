from __future__ import annotations

"""Evaluation-only freezing of canonical Shot Review as goldset v3."""

from typing import Any, Mapping


SCHEMA_VERSION = "shot-goldset:v3"


def build_shot_goldset_v3(
    editorial_document: Mapping[str, Any],
    public_report: Mapping[str, Any],
    *,
    hard_negatives: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project current canonical Shot Review into a minimal immutable fixture.

    The projection deliberately accepts only canonical-shot fields. Review
    revisions, suggestion IDs, confidence, and internal storage metadata are
    excluded from the frozen evaluation truth.
    """

    if str(editorial_document.get("schema_version") or "") != "shot-review-editorial:v1":
        raise ValueError("Expected canonical shot-review editorial state")
    teams = {
        str(row.get("team_id") or ""): str(row.get("team_name") or "")
        for row in public_report.get("teams") or []
        if isinstance(row, Mapping) and row.get("team_id") and row.get("team_name")
    }
    players = {
        str(row.get("player_id") or ""): str(row.get("player_name") or "")
        for row in public_report.get("players") or []
        if isinstance(row, Mapping) and row.get("player_id") and row.get("player_name")
    }
    shots = [_project_shot(row, teams, players) for row in editorial_document.get("canonical_shots") or [] if isinstance(row, Mapping)]
    if len({str(row["id"]) for row in shots}) != len(shots):
        raise ValueError("Canonical Shot Review contains duplicate shot IDs")
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "evaluation_only",
        "source_authority": "canonical_shot_review",
        "shots": sorted(shots, key=lambda row: (float(row["timestamp_sec"]), str(row["id"]))),
        "hard_negatives": [dict(row) for row in hard_negatives],
        "uncertain": [],
    }


def _project_shot(shot: Mapping[str, Any], teams: Mapping[str, str], players: Mapping[str, str]) -> dict[str, Any]:
    shot_id = str(shot.get("shot_id") or "")
    team_id = str(shot.get("team_id") or "")
    if not shot_id or team_id not in teams:
        raise ValueError("Canonical Shot Review row is missing a resolvable shot or team ID")
    time_sec = _number(shot.get("time_sec"))
    player_id = str(shot.get("player_id") or "")
    timestamp_semantics, timestamp_precision = _timestamp_metadata(shot)
    return {
        "id": shot_id,
        "timestamp_sec": time_sec,
        "timestamp_display": _clock(time_sec),
        "timestamp_semantics": timestamp_semantics,
        "timestamp_precision": timestamp_precision,
        "team": teams[team_id],
        "player": players.get(player_id) if player_id else None,
        "outcome": str(shot.get("outcome") or ""),
        "origin": str(shot.get("origin") or ""),
        "provenance": "canonical_shot_review",
    }


def _timestamp_metadata(shot: Mapping[str, Any]) -> tuple[str, str]:
    """Keep canonical authority distinct from the meaning of a stored time.

    Manual Shot Review times are deliberate pre-event playback anchors. An
    accepted suggestion instead retains the detector-derived candidate event
    time that the operator accepted. Neither provenance claim implies that a
    frame-perfect contact annotation exists.
    """

    origin = str(shot.get("origin") or "")
    if origin == "manual":
        return "pre_event_playback_anchor", "approximate"
    if origin == "accepted_suggestion":
        return "candidate_event_anchor", "detector_derived"
    raise ValueError(f"Canonical Shot Review row has an unsupported origin: {origin or '<missing>'}")


def _number(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("Canonical Shot Review row has no numeric timestamp")
    return float(value)


def _clock(time_sec: float) -> str:
    minutes, seconds = divmod(time_sec, 60.0)
    return f"{int(minutes):02}:{seconds:04.1f}"
