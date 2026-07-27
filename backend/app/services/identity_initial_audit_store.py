from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

from app.services.identity_initial_audit import (
    AUDIT_DIRECTORY,
    SELECTION_FILENAME,
    build_initial_identity_audit_document,
)
from app.services.identity_jersey_number_common import canonical_digest


SCHEMA_VERSION = "0.1.0"
MODE = "initial_identity_audit_operator_seeds"
SEEDS_FILENAME = "identity_operator_seeds.json"
ALLOWED_ACTIONS = {
    "assign_roster_player",
    "team_a_unknown",
    "team_b_unknown",
    "referee",
    "false_detection",
    "skip",
}
TELEMETRY_EVENT_TYPES = {
    "session_started",
    "frame_shown",
    "crop_clicked",
    "action",
    "session_finished",
}
MAX_ACTIVE_DELTA_SECONDS = 30.0
PRODUCTION_IDENTITY_FILENAMES = (
    "global_identity.json",
    "stable_players.json",
    "tracklets.json",
    "tracks.json",
)


class InitialIdentityAuditConflictError(ValueError):
    pass


class InitialIdentityAuditStaleError(ValueError):
    pass


def load_initial_identity_audit_seeds(
    match_path: Path,
    match_document: dict[str, Any],
) -> dict[str, Any]:
    selection = _load_selection(match_path)
    source = _source_descriptor(selection)
    stored = _load_optional_json(match_path / SEEDS_FILENAME)
    if stored is None:
        return _public_document(
            _empty_document(
                source,
                production_snapshot=_production_identity_snapshot(
                    match_path,
                    match_document,
                ),
            ),
            status="empty",
            decisions_fresh=True,
        )

    decisions_fresh = _stored_source_matches(stored, source)
    return _public_document(
        stored,
        status="fresh" if decisions_fresh else "stale",
        decisions_fresh=decisions_fresh,
    )


def save_initial_identity_audit_seeds(
    match_path: Path,
    match_document: dict[str, Any],
    updates: list[dict[str, Any]],
    *,
    telemetry_events: list[dict[str, Any]] | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    selection = _load_selection(match_path)
    source = _source_descriptor(selection)
    observation_index = _observation_index(selection, match_document)
    roster_index = _roster_index(match_document)
    team_index = _team_index(match_document)
    seed_path = match_path / SEEDS_FILENAME
    existing = _load_optional_json(seed_path)
    if existing is not None and not _stored_source_matches(existing, source):
        if existing.get("decisions") or existing.get("operator_telemetry", {}).get(
            "events"
        ):
            raise InitialIdentityAuditStaleError(
                "Initial Identity Audit selection changed. Reopen the audit "
                "before saving new decisions."
            )
        existing = None

    production_before = _production_identity_snapshot(match_path, match_document)
    document = (
        _normalized_existing_document(existing, source, production_before)
        if existing is not None
        else _empty_document(source, production_snapshot=production_before)
    )
    timestamp = updated_at or datetime.now(timezone.utc).isoformat()
    decisions_by_key = {
        str(row.get("observation_key")): dict(row)
        for row in document.get("decisions") or []
        if isinstance(row, dict) and row.get("observation_key")
    }
    processed_update_ids = {
        str(value)
        for value in (
            document.get("operator_telemetry", {}).get("processed_update_ids")
            or []
        )
        if value
    }

    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("Each Initial Identity Audit update must be an object")
        update_id = str(update.get("update_id") or "")
        if update_id and update_id in processed_update_ids:
            continue
        observation_key = str(update.get("observation_key") or "")
        observation = observation_index.get(observation_key)
        if observation is None:
            raise ValueError(
                f"Unknown Initial Identity Audit observation: "
                f"{observation_key or '<missing>'}"
            )
        action = str(update.get("action") or "")
        if action == "clear":
            decisions_by_key.pop(observation_key, None)
        else:
            decisions_by_key[observation_key] = _build_decision(
                observation,
                action=action,
                player_id=update.get("player_id"),
                roster_index=roster_index,
                team_index=team_index,
                timestamp=timestamp,
            )
        if update_id:
            processed_update_ids.add(update_id)

    decisions = sorted(
        decisions_by_key.values(),
        key=lambda row: (
            int(row.get("frame_number") or 0),
            int(row.get("display_order") or 0),
            str(row.get("observation_key") or ""),
        ),
    )
    _validate_same_frame_player_conflicts(decisions)

    telemetry = _merge_telemetry(
        document.get("operator_telemetry"),
        telemetry_events or [],
        observation_index=observation_index,
        decisions=decisions,
    )
    document.update(
        {
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "source": source,
            "decisions": decisions,
            "operator_telemetry": {
                **telemetry,
                "processed_update_ids": sorted(processed_update_ids),
            },
            "production_identity_snapshot": production_before,
            "updated_at": timestamp,
            "safety": {
                "observation_level_seeds_only": True,
                "production_identity_untouched": True,
                "candidate_identity_untouched": True,
                "downstream_rebuild_triggered": False,
                "yolo_not_required": True,
            },
        }
    )
    _write_atomic(seed_path, document)

    production_after = _production_identity_snapshot(match_path, match_document)
    if production_after != production_before:
        raise RuntimeError(
            "Production identity artifacts changed while saving operator seeds"
        )
    return _public_document(
        document,
        status="fresh",
        decisions_fresh=True,
    )


def _load_selection(match_path: Path) -> dict[str, Any]:
    selection_path = match_path / AUDIT_DIRECTORY / SELECTION_FILENAME
    if not selection_path.exists():
        raise FileNotFoundError(
            "Initial Identity Audit selection is missing. Open the audit first."
        )
    selection = _load_json(selection_path)
    if not isinstance(selection.get("selected_frames"), list):
        raise ValueError("Initial Identity Audit selection is invalid")
    return selection


def _source_descriptor(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_digest": str(
            selection.get("selection_digest") or canonical_digest(selection)
        ),
        "selection_artifact_digest": canonical_digest(selection),
        "selection_schema_version": selection.get("schema_version"),
        "analysis_run_id": (selection.get("source") or {}).get(
            "analysis_run_id"
        ),
    }


def _stored_source_matches(
    document: dict[str, Any],
    source: dict[str, Any],
) -> bool:
    stored = document.get("source") or {}
    return (
        stored.get("selection_digest") == source.get("selection_digest")
        and stored.get("selection_artifact_digest")
        == source.get("selection_artifact_digest")
    )


def _observation_index(
    selection: dict[str, Any],
    match_document: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    public_document = build_initial_identity_audit_document(
        selection,
        match_document,
    )
    selected_rows = (selection.get("selected_frames") or [])[:10]
    index: dict[str, dict[str, Any]] = {}
    for frame, selection_row in zip(
        public_document.get("frames") or [],
        selected_rows,
    ):
        capture_domain = selection_row.get("capture_domain")
        for observation in frame.get("observations") or []:
            key = str(observation.get("observation_key") or "")
            if not key:
                continue
            index[key] = {
                **observation,
                "audit_frame_key": frame.get("audit_frame_key"),
                "frame_number": int(frame.get("frame_number") or 0),
                "time_sec": float(frame.get("time_sec") or 0.0),
                "capture_domain": capture_domain,
            }
    return index


def _roster_index(
    match_document: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for team in _operator_roster(match_document):
        for player in team.get("players") or []:
            index[str(player["player_id"])] = {
                **player,
                "team_label": team.get("team_label"),
                "team_id": team.get("team_id"),
                "team_name": team.get("team_name"),
            }
    return index


def _team_index(
    match_document: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(team.get("team_label") or "U"): {
            "team_label": team.get("team_label"),
            "team_id": team.get("team_id"),
            "team_name": team.get("team_name"),
        }
        for team in _operator_roster(match_document)
    }


def _operator_roster(
    match_document: dict[str, Any],
) -> list[dict[str, Any]]:
    document = build_initial_identity_audit_document(
        {"selected_frames": [], "video": {}},
        match_document,
    )
    return [
        dict(team)
        for team in document.get("roster") or []
        if isinstance(team, dict)
    ]


def _build_decision(
    observation: dict[str, Any],
    *,
    action: str,
    player_id: Any,
    roster_index: dict[str, dict[str, Any]],
    team_index: dict[str, dict[str, Any]],
    timestamp: str,
) -> dict[str, Any]:
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported Initial Identity Audit action: {action}")

    assigned_player: dict[str, Any] | None = None
    assigned_team: dict[str, Any] | None = None
    if action == "assign_roster_player":
        normalized_player_id = str(player_id or "")
        assigned_player = roster_index.get(normalized_player_id)
        if assigned_player is None:
            raise ValueError(
                f"Unknown roster player_id: {normalized_player_id or '<missing>'}"
            )
        assigned_team = {
            "team_label": assigned_player.get("team_label"),
            "team_id": assigned_player.get("team_id"),
            "team_name": assigned_player.get("team_name"),
        }
    elif player_id not in (None, ""):
        raise ValueError(f"player_id is not allowed for action {action}")
    elif action in {"team_a_unknown", "team_b_unknown"}:
        team_label = "A" if action == "team_a_unknown" else "B"
        team_row = team_index.get(team_label)
        assigned_team = {
            "team_label": team_label,
            "team_id": team_row.get("team_id") if team_row else None,
            "team_name": (
                team_row.get("team_name") if team_row else f"Team {team_label}"
            ),
        }

    automatic_team = str(observation.get("team_label") or "U")
    assigned_team_label = (
        str(assigned_team.get("team_label"))
        if assigned_team is not None
        else None
    )
    return {
        "observation_key": observation["observation_key"],
        "audit_frame_key": observation.get("audit_frame_key"),
        "frame_number": observation.get("frame_number"),
        "time_sec": observation.get("time_sec"),
        "bbox_xyxy": observation.get("bbox_xyxy"),
        "display_order": observation.get("display_order"),
        "action": action,
        "automatic_team_label": automatic_team,
        "assigned_team": assigned_team,
        "assigned_player": (
            {
                "player_id": assigned_player.get("player_id"),
                "player_name": assigned_player.get("player_name"),
                "player_number": assigned_player.get("player_number"),
                "player_role": assigned_player.get("player_role"),
            }
            if assigned_player is not None
            else None
        ),
        "team_assignment_corrected": bool(
            assigned_team_label in {"A", "B"}
            and assigned_team_label != automatic_team
        ),
        "provenance": observation.get("provenance"),
        "capture_domain": observation.get("capture_domain"),
        "updated_at": timestamp,
    }


def _validate_same_frame_player_conflicts(
    decisions: list[dict[str, Any]],
) -> None:
    claimed: dict[tuple[int, str], str] = {}
    for decision in decisions:
        player = decision.get("assigned_player") or {}
        player_id = str(player.get("player_id") or "")
        if not player_id:
            continue
        key = (int(decision.get("frame_number") or 0), player_id)
        observation_key = str(decision.get("observation_key") or "")
        previous = claimed.get(key)
        if previous and previous != observation_key:
            raise InitialIdentityAuditConflictError(
                f"Player {player_id} cannot be assigned to two observations "
                f"in frame {key[0]}"
            )
        claimed[key] = observation_key


def _merge_telemetry(
    stored: Any,
    events: list[dict[str, Any]],
    *,
    observation_index: dict[str, dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    state = stored if isinstance(stored, dict) else {}
    stored_events = [
        dict(row)
        for row in state.get("events") or []
        if isinstance(row, dict) and row.get("event_id")
    ]
    event_ids = {str(row["event_id"]) for row in stored_events}
    for event in events:
        normalized = _normalize_telemetry_event(
            event,
            observation_index=observation_index,
        )
        if normalized["event_id"] in event_ids:
            continue
        stored_events.append(normalized)
        event_ids.add(normalized["event_id"])

    frames_shown = {
        str(row.get("audit_frame_key"))
        for row in stored_events
        if row.get("event_type") == "frame_shown"
        and row.get("audit_frame_key")
    }
    unique_players = {
        str((row.get("assigned_player") or {}).get("player_id"))
        for row in decisions
        if (row.get("assigned_player") or {}).get("player_id")
    }
    metrics = {
        "audit_frames_shown": len(frames_shown),
        "audit_crops_clicked": sum(
            row.get("event_type") == "crop_clicked"
            for row in stored_events
        ),
        "audit_actions": sum(
            row.get("event_type") == "action"
            for row in stored_events
        ),
        "active_operator_seconds": round(
            sum(float(row.get("active_delta_seconds") or 0.0) for row in stored_events),
            3,
        ),
        "unique_players_seeded": len(unique_players),
        "team_assignments_corrected": sum(
            bool(row.get("team_assignment_corrected"))
            for row in decisions
        ),
        "false_detections_marked": sum(
            row.get("action") == "false_detection"
            for row in decisions
        ),
    }
    return {
        "events": stored_events,
        "metrics": metrics,
        "processed_update_ids": list(state.get("processed_update_ids") or []),
    }


def _normalize_telemetry_event(
    event: Any,
    *,
    observation_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("Each telemetry event must be an object")
    event_type = str(event.get("event_type") or "")
    if event_type not in TELEMETRY_EVENT_TYPES:
        raise ValueError(f"Unsupported telemetry event type: {event_type}")
    event_id = str(event.get("event_id") or "")
    session_id = str(event.get("session_id") or "")
    if not event_id or not session_id:
        raise ValueError("telemetry event_id and session_id are required")
    observation_key = str(event.get("observation_key") or "") or None
    observation = (
        observation_index.get(observation_key)
        if observation_key is not None
        else None
    )
    if observation_key is not None and observation is None:
        raise ValueError(f"Unknown telemetry observation: {observation_key}")
    audit_frame_key = (
        observation.get("audit_frame_key")
        if observation is not None
        else str(event.get("audit_frame_key") or "") or None
    )
    try:
        active_delta = float(event.get("active_delta_seconds") or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("active_delta_seconds must be numeric") from exc
    return {
        "event_id": event_id,
        "session_id": session_id,
        "event_type": event_type,
        "audit_frame_key": audit_frame_key,
        "observation_key": observation_key,
        "active_delta_seconds": round(
            max(0.0, min(MAX_ACTIVE_DELTA_SECONDS, active_delta)),
            3,
        ),
        "occurred_at": str(
            event.get("occurred_at") or datetime.now(timezone.utc).isoformat()
        ),
    }


def _empty_document(
    source: dict[str, Any],
    *,
    production_snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "source": source,
        "decisions": [],
        "operator_telemetry": {
            "events": [],
            "metrics": _empty_metrics(),
            "processed_update_ids": [],
        },
        "production_identity_snapshot": production_snapshot,
        "updated_at": None,
        "safety": {
            "observation_level_seeds_only": True,
            "production_identity_untouched": True,
            "candidate_identity_untouched": True,
            "downstream_rebuild_triggered": False,
            "yolo_not_required": True,
        },
    }


def _normalized_existing_document(
    existing: dict[str, Any],
    source: dict[str, Any],
    production_snapshot: dict[str, Any],
) -> dict[str, Any]:
    document = _empty_document(
        source,
        production_snapshot=production_snapshot,
    )
    document.update(existing)
    document["source"] = source
    return document


def _empty_metrics() -> dict[str, int | float]:
    return {
        "audit_frames_shown": 0,
        "audit_crops_clicked": 0,
        "audit_actions": 0,
        "active_operator_seconds": 0.0,
        "unique_players_seeded": 0,
        "team_assignments_corrected": 0,
        "false_detections_marked": 0,
    }


def _public_document(
    document: dict[str, Any],
    *,
    status: str,
    decisions_fresh: bool,
) -> dict[str, Any]:
    decisions = []
    if decisions_fresh:
        for row in document.get("decisions") or []:
            decisions.append(
                {
                    "observation_key": row.get("observation_key"),
                    "action": row.get("action"),
                    "assigned_team": row.get("assigned_team"),
                    "assigned_player": row.get("assigned_player"),
                    "team_assignment_corrected": bool(
                        row.get("team_assignment_corrected")
                    ),
                    "updated_at": row.get("updated_at"),
                }
            )
    telemetry = document.get("operator_telemetry") or {}
    return {
        "schema_version": document.get("schema_version") or SCHEMA_VERSION,
        "mode": MODE,
        "status": status,
        "decisions_fresh": decisions_fresh,
        "source_selection_digest": (document.get("source") or {}).get(
            "selection_digest"
        ),
        "decisions": decisions,
        "operator_telemetry": {
            "metrics": telemetry.get("metrics") or _empty_metrics(),
        },
        "safety": document.get("safety") or {},
        "updated_at": document.get("updated_at"),
    }


def _production_identity_snapshot(
    match_path: Path,
    match_document: dict[str, Any],
) -> dict[str, Any]:
    files = []
    for filename in PRODUCTION_IDENTITY_FILENAMES:
        candidate = _first_artifact_candidate(
            match_path,
            match_document,
            filename,
        )
        if candidate is None:
            continue
        files.append(
            {
                "artifact": str(candidate.relative_to(match_path)),
                "sha256": _file_sha256(candidate),
            }
        )
    return {
        "files": files,
        "snapshot_digest": canonical_digest(files),
    }


def _first_artifact_candidate(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> Path | None:
    candidates = [match_path / filename]
    latest_run_id = str(match_document.get("latest_analysis_run_id") or "")
    runs = [
        row
        for row in match_document.get("analysis_runs") or []
        if isinstance(row, dict)
    ]
    runs.sort(
        key=lambda row: (
            str(row.get("run_id") or "") != latest_run_id,
            str(row.get("generated_at") or ""),
        )
    )
    for row in runs:
        run_directory = str(row.get("run_directory") or "")
        if run_directory:
            candidates.append(match_path / run_directory / filename)
    return next((path for path in candidates if path.exists()), None)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    return _load_json(path) if path.exists() else None


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def find_identity_artifact(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> Path | None:
    """Find a root or analysis-run identity artifact using canonical precedence."""
    return _first_artifact_candidate(match_path, match_document, filename)


def production_identity_snapshot(
    match_path: Path,
    match_document: dict[str, Any],
) -> dict[str, Any]:
    """Return immutable hashes used by shadow rebuild safety checks."""
    return _production_identity_snapshot(match_path, match_document)


def write_identity_json_atomic(
    path: Path,
    document: dict[str, Any],
) -> None:
    """Persist a derived identity document without exposing partial JSON."""
    _write_atomic(path, document)


def load_identity_json(path: Path) -> dict[str, Any]:
    return _load_json(path)
