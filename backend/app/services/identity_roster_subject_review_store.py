from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
import uuid


REVIEW_ARTIFACT_FILENAME = "identity_roster_subject_review_shadow.json"
REVIEW_DECISIONS_FILENAME = "identity_roster_subject_review_decisions_shadow.json"
PERSISTED_DECISIONS = {
    "confirm_recommended_player",
    "assign_roster_player",
    "mark_unresolved",
}


def load_identity_roster_subject_review(
    path: Path,
    *,
    match_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = _load_object(path / REVIEW_ARTIFACT_FILENAME)
    artifact_digest = identity_review_artifact_digest(artifact)
    decisions_doc = _load_optional_decisions(path / REVIEW_DECISIONS_FILENAME)
    decisions_fresh = decisions_doc.get("source_artifact_digest") == artifact_digest
    stored_by_key = {
        str(row.get("review_card_key")): row
        for row in decisions_doc.get("decisions") or []
        if isinstance(row, dict) and row.get("review_card_key")
    }
    cards: list[dict[str, Any]] = []
    applied = 0
    stale = 0
    for source_card in artifact.get("cards") or []:
        card = dict(source_card)
        card["operator_roster_options"] = _operator_roster_options(card, match_doc)
        card["decision_contract"] = _effective_decision_contract(card)
        card["allowed_actions"] = _effective_allowed_actions(card)
        stored = stored_by_key.get(str(card.get("review_card_key") or ""))
        if stored and decisions_fresh:
            card["operator_decision"] = stored
            applied += 1
        else:
            card["operator_decision"] = None
            if stored:
                stale += 1
        cards.append(card)
    return {
        "schema_version": "0.1.0",
        "mode": "shadow_operator_review",
        "source_artifact_digest": artifact_digest,
        "decisions_fresh": decisions_fresh or not stored_by_key,
        "summary": {
            **(artifact.get("summary") or {}),
            "reviewed_cards": applied,
            "pending_cards": max(0, len(cards) - applied),
            "stale_decisions": stale,
        },
        "safety": {
            "writes_shadow_decisions_only": True,
            "writes_player_identity_assignments": False,
            "mutates_production_identity": False,
            "eligible_for_player_stats": False,
            "eligible_for_heatmaps": False,
        },
        "cards": cards,
    }


def save_identity_roster_subject_review(
    path: Path,
    updates: list[dict[str, Any]],
    *,
    match_doc: dict[str, Any] | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    artifact = _load_object(path / REVIEW_ARTIFACT_FILENAME)
    digest = identity_review_artifact_digest(artifact)
    cards_by_key: dict[str, dict[str, Any]] = {}
    for source_card in artifact.get("cards") or []:
        if not isinstance(source_card, dict) or not source_card.get("review_card_key"):
            continue
        card = dict(source_card)
        card["operator_roster_options"] = _operator_roster_options(card, match_doc)
        cards_by_key[str(card["review_card_key"])] = card
    existing = _load_optional_decisions(path / REVIEW_DECISIONS_FILENAME)
    decisions_by_key = {
        str(row.get("review_card_key")): dict(row)
        for row in existing.get("decisions") or []
        if isinstance(row, dict) and row.get("review_card_key")
    } if existing.get("source_artifact_digest") == digest else {}
    timestamp = updated_at or datetime.now(timezone.utc).isoformat()
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("Each subject review update must be an object")
        key = str(update.get("review_card_key") or "")
        card = cards_by_key.get(key)
        if card is None:
            raise ValueError(f"Unknown review_card_key: {key or '<missing>'}")
        decision = update.get("decision")
        if decision in (None, "clear_decision"):
            decisions_by_key.pop(key, None)
            continue
        decision = str(decision)
        if decision not in PERSISTED_DECISIONS:
            raise ValueError(f"Unsupported persisted decision: {decision}")
        if decision not in _effective_allowed_actions(card):
            raise ValueError(f"Decision {decision} is blocked for {key}")
        player_id = _validated_player_id(card, decision, update.get("player_id"))
        decisions_by_key[key] = {
            "review_card_key": key,
            "candidate_subject_id": card.get("candidate_subject_id"),
            "decision": decision,
            "player_id": player_id,
            "comment": str(update.get("comment") or "").strip() or None,
            "updated_at": timestamp,
        }
    document = {
        "schema_version": "0.1.0",
        "updated_at": timestamp,
        "mode": "shadow_operator_review",
        "source_artifact": REVIEW_ARTIFACT_FILENAME,
        "source_artifact_digest": digest,
        "safety": {
            "writes_shadow_decisions_only": True,
            "writes_player_identity_assignments": False,
            "mutates_production_identity": False,
            "eligible_for_player_stats": False,
            "eligible_for_heatmaps": False,
        },
        "decisions": [decisions_by_key[key] for key in sorted(decisions_by_key)],
    }
    _write_atomic(path / REVIEW_DECISIONS_FILENAME, document)
    return load_identity_roster_subject_review(path, match_doc=match_doc)


def _validated_player_id(card: dict[str, Any], decision: str, value: Any) -> str | None:
    if decision == "mark_unresolved":
        return None
    if decision == "confirm_recommended_player":
        recommended = str((card.get("recommended_player") or {}).get("player_id") or "")
        if not recommended:
            raise ValueError("Recommended player is missing")
        if value not in (None, "", recommended):
            raise ValueError("player_id does not match the recommended player")
        return recommended
    player_id = str(value or "")
    allowed = {
        str(row.get("player_id"))
        for row in card.get("operator_roster_options") or card.get("roster_candidates") or []
        if row.get("player_id")
    }
    if not player_id or player_id not in allowed:
        raise ValueError("player_id must be one of the same-team operator roster options")
    return player_id


def _operator_roster_options(
    card: dict[str, Any],
    match_doc: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    ranked_by_id = {
        str(row.get("player_id")): dict(row)
        for row in card.get("roster_candidates") or []
        if isinstance(row, dict) and row.get("player_id")
    }
    team_label = str(card.get("team_label") or "U")
    options: dict[str, dict[str, Any]] = dict(ranked_by_id)
    for team_index, team in enumerate((match_doc or {}).get("teams") or []):
        if not isinstance(team, dict):
            continue
        current_label = "A" if team_index == 0 else "B" if team_index == 1 else "U"
        if current_label != team_label:
            continue
        for player in team.get("players") or []:
            if not isinstance(player, dict) or not player.get("id"):
                continue
            player_id = str(player["id"])
            existing = options.get(player_id) or {}
            options[player_id] = {
                **existing,
                "player_id": player_id,
                "player_name": player.get("name") or existing.get("player_name") or player_id,
                "team_label": current_label,
                "ranked_candidate": player_id in ranked_by_id,
            }
    return sorted(
        options.values(),
        key=lambda row: (
            str(row.get("player_name") or row.get("player_id") or "").casefold(),
            str(row.get("player_id") or ""),
        ),
    )


def _effective_decision_contract(card: dict[str, Any]) -> dict[str, Any]:
    contract = dict(card.get("decision_contract") or {})
    schema = dict(contract.get("decision_schema") or {})
    schema["player_id"] = [
        str(row["player_id"])
        for row in card.get("operator_roster_options") or []
        if row.get("player_id")
    ]
    contract["decision_schema"] = schema
    return contract


def _effective_allowed_actions(card: dict[str, Any]) -> list[str]:
    actions = [str(value) for value in card.get("allowed_actions") or []]
    if (
        card.get("review_status") == "blocked_conflict"
        and card.get("roster_candidates")
        and "assign_roster_player" not in actions
    ):
        actions.insert(0, "assign_roster_player")
    return actions


def identity_review_artifact_digest(artifact: dict[str, Any]) -> str:
    normalized = _without_generated_at(artifact)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _without_generated_at(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_generated_at(item) for key, item in value.items() if key != "generated_at"}
    if isinstance(value, list):
        return [_without_generated_at(item) for item in value]
    return value


def _load_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path.name} not found")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def _load_optional_decisions(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"decisions": []}
    return _load_object(path)


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
