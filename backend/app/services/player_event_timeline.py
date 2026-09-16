from __future__ import annotations

"""Private, production-grade player positions for ball-derived analytics.

The public ``stable_players.json`` intentionally omits dense overlay rows.
Possession and event derivation must instead use this internal timeline, which
has exactly the player rows produced by stabilization.  Older matches can be
reconstructed deterministically from the durable private global-identity
document without detector work.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.services.artifact_lineage import canonical_json_sha256


PLAYER_EVENT_TIMELINE_FILENAME = "stable_players_event_timeline.json"
PLAYER_EVENT_TIMELINE_SCHEMA_VERSION = "stable-player-event-timeline:v1"


class PlayerEventTimelineError(ValueError):
    pass


@dataclass(frozen=True)
class PlayerEventTimeline:
    document: dict[str, Any]
    input_digest: str
    provenance: str
    artifact: str


def write_player_event_timeline(match_dir: Path, stable_players_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Persist the exact private stabilization player document.

    This artifact remains in match storage and is deliberately not a package
    asset.  It preserves ``overlay_positions`` required by production ball
    possession analysis.
    """

    document = dict(stable_players_doc)
    _validate(document, label="stabilization player timeline", require_dense=False)
    payload = {
        "schema_version": PLAYER_EVENT_TIMELINE_SCHEMA_VERSION,
        "player_timeline": document,
    }
    target = match_dir / PLAYER_EVENT_TIMELINE_FILENAME
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(target)
    return payload


def load_player_event_timeline(match_dir: Path) -> PlayerEventTimeline:
    """Return the same trusted player event input used by normal analysis."""

    persisted = match_dir / PLAYER_EVENT_TIMELINE_FILENAME
    if persisted.exists():
        payload = _load(persisted, "player event timeline")
        if str(payload.get("schema_version") or "") != PLAYER_EVENT_TIMELINE_SCHEMA_VERSION:
            raise PlayerEventTimelineError("stable_players_event_timeline.json has an incompatible schema")
        document = payload.get("player_timeline")
        if not isinstance(document, dict):
            raise PlayerEventTimelineError("stable_players_event_timeline.json is missing player_timeline")
        _validate(document, label="player event timeline", require_dense=False)
        return _result(document, "persisted_stabilization_overlay", PLAYER_EVENT_TIMELINE_FILENAME)

    # Legacy source matches predate the dedicated internal artifact.  The
    # private global identity slot document contains the same dense rows that
    # stabilization used before it stripped them from stable_players.json.
    public = _load(match_dir / "stable_players.json", "stable players")
    global_identity = _load(match_dir / "global_identity.json", "global identity")
    slots = global_identity.get("slots")
    if not isinstance(slots, list):
        raise PlayerEventTimelineError("global_identity.json is missing private stable slots")
    by_slot = {
        str(slot.get("stable_subject_id") or slot.get("slot_id") or ""): slot
        for slot in slots
        if isinstance(slot, dict)
    }
    players: list[dict[str, Any]] = []
    for player in public.get("players") or []:
        if not isinstance(player, dict):
            continue
        key = str(player.get("stable_subject_id") or player.get("slot_id") or "")
        slot = by_slot.get(key)
        if slot is None:
            raise PlayerEventTimelineError(f"global_identity.json has no private slot for {key or 'a stable player'}")
        merged = {**player, **slot}
        players.append(merged)
    document = {**public, "players": players}
    _validate(document, label="reconstructed player event timeline", require_dense=False)
    return _result(document, "reconstructed_global_identity_overlay", "global_identity.json")


def player_event_timeline_digest(document: Mapping[str, Any]) -> str:
    _validate(document, label="player event timeline", require_dense=False)
    rows: list[dict[str, Any]] = []
    for player in document.get("players") or []:
        if not isinstance(player, Mapping):
            continue
        positions = []
        for position in player.get("overlay_positions") or player.get("trajectory_m") or []:
            if not isinstance(position, Mapping):
                continue
            point = position.get("pitch_m")
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            positions.append({
                "frame": _integer(position.get("frame")),
                "pitch_m": [_number(point[0]), _number(point[1])],
                "source": str(position.get("source") or "unknown"),
                "status": str(position.get("status") or "unknown"),
            })
        rows.append({
            "stable_subject_id": str(player.get("stable_subject_id") or player.get("slot_id") or ""),
            "team_id": str(player.get("team_id") or ""),
            "positions": sorted(positions, key=lambda row: row["frame"] if row["frame"] is not None else -1),
        })
    return canonical_json_sha256({
        "schema_version": PLAYER_EVENT_TIMELINE_SCHEMA_VERSION,
        "players": sorted(rows, key=lambda row: row["stable_subject_id"]),
    })


def _result(document: dict[str, Any], provenance: str, artifact: str) -> PlayerEventTimeline:
    return PlayerEventTimeline(
        document=document,
        input_digest=player_event_timeline_digest(document),
        provenance=provenance,
        artifact=artifact,
    )


def _validate(document: Mapping[str, Any], *, label: str, require_dense: bool) -> None:
    players = document.get("players")
    if not isinstance(players, list):
        raise PlayerEventTimelineError(f"{label} must contain a players list")
    for player in players:
        if not isinstance(player, Mapping):
            raise PlayerEventTimelineError(f"{label} contains an invalid player")
        if require_dense and not isinstance(player.get("overlay_positions"), list):
            raise PlayerEventTimelineError(f"{label} is missing dense overlay positions")


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlayerEventTimelineError(f"{label} artifact is unreadable") from error
    if not isinstance(value, dict):
        raise PlayerEventTimelineError(f"{label} artifact must be a JSON object")
    return value


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
