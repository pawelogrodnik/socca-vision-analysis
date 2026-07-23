from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
import uuid

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_jersey_number_common import normalize_normalized_bbox
from app.services.identity_jersey_number_common import normalize_jersey_number
from app.services.identity_jersey_number_common import normalize_safe_relative_artifact_path


REVIEW_ARTIFACT_FILENAME = "identity_roster_subject_review_shadow.json"
REVIEW_DECISIONS_FILENAME = "identity_roster_subject_review_decisions_shadow.json"
VISUAL_PRE_AUDIT_FILENAME = "identity_jersey_number_visual_pre_audit_shadow.json"
VISUAL_PRE_AUDIT_SCHEMA_VERSION = "0.2.0"
VISUAL_PRE_AUDIT_ALGORITHM_NAME = "identity_jersey_number_visual_pre_audit"
VISUAL_PRE_AUDIT_ALGORITHM_VERSION = "1.1.0"
PROHIBITED_PRE_AUDIT_KEYS = {
    "jersey_number_annotation_suggestion",
    "digit_visibility",
    "occlusion_state",
    "blur_level",
    "perspective_state",
    "panel_height_ratio",
    "kit_profile",
    "number_panel_bbox_normalized",
    "number_panel_artifact",
    "number",
    "number_absent",
    "digit_string",
    "visual_state",
    "state",
    "label_state",
}
PERSISTED_DECISIONS = {
    "confirm_recommended_player",
    "assign_roster_player",
    "mark_unresolved",
}
TELEMETRY_EVENT_TYPES = {
    "session_started",
    "card_opened",
    "activity",
    "session_completed",
    "remediation_action",
}
MAX_ACTIVE_DELTA_SECONDS = 30.0
JERSEY_ANNOTATION_ENUMS = {
    "digit_visibility": {"full", "partial", "none", "unknown"},
    "occlusion_state": {"none", "partial", "heavy", "unknown"},
    "blur_level": {"none", "mild", "heavy", "unknown"},
    "perspective_state": {"frontal", "angled", "severe", "unknown"},
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
    pre_audits_by_crop = _fresh_pre_audits(path, artifact)
    stored_by_key = {
        str(row.get("review_card_key")): row
        for row in decisions_doc.get("decisions") or []
        if isinstance(row, dict) and row.get("review_card_key")
    }
    annotations_by_crop = _stored_crop_annotations(decisions_doc) if decisions_fresh else {}
    panel_annotations_by_crop = _stored_panel_annotations(decisions_doc) if decisions_fresh else {}
    cards: list[dict[str, Any]] = []
    applied = 0
    stale = 0
    for source_card in artifact.get("cards") or []:
        card = dict(source_card)
        card["operator_roster_options"] = _operator_roster_options(card, match_doc)
        card["decision_contract"] = _effective_decision_contract(card)
        card["allowed_actions"] = _effective_allowed_actions(card)
        card = _with_crop_annotations(
            card, annotations_by_crop, panel_annotations_by_crop, pre_audits_by_crop
        )
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
        "schema_version": "0.4.0",
        "mode": "shadow_operator_review",
        "source_artifact_digest": artifact_digest,
        "decisions_fresh": decisions_fresh or not stored_by_key,
        "summary": {
            **(artifact.get("summary") or {}),
            "reviewed_cards": applied,
            "pending_cards": max(0, len(cards) - applied),
            "stale_decisions": stale,
            "fresh_jersey_number_pre_audits": len(pre_audits_by_crop),
        },
        "safety": {
            "writes_shadow_decisions_only": True,
            "writes_player_identity_assignments": False,
            "mutates_production_identity": False,
            "eligible_for_player_stats": False,
            "eligible_for_heatmaps": False,
        },
        "operator_telemetry": (
            decisions_doc.get("operator_telemetry")
            if decisions_fresh
            else _empty_operator_telemetry()
        ) or _empty_operator_telemetry(),
        "cards": cards,
    }


def save_identity_roster_subject_review(
    path: Path,
    updates: list[dict[str, Any]],
    *,
    match_doc: dict[str, Any] | None = None,
    updated_at: str | None = None,
    telemetry_events: list[dict[str, Any]] | None = None,
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
    annotations_by_crop = (
        _stored_crop_annotations(existing)
        if existing.get("source_artifact_digest") == digest
        else {}
    )
    panel_annotations_by_crop = (
        _stored_panel_annotations(existing)
        if existing.get("source_artifact_digest") == digest
        else {}
    )
    timestamp = updated_at or datetime.now(timezone.utc).isoformat()
    telemetry_state = _load_telemetry_state(existing) if existing.get("source_artifact_digest") == digest else _empty_telemetry_state()
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("Each subject review update must be an object")
        key = str(update.get("review_card_key") or "")
        card = cards_by_key.get(key)
        if card is None:
            raise ValueError(f"Unknown review_card_key: {key or '<missing>'}")
        update_id = str(update.get("update_id") or "")
        already_processed = bool(update_id and update_id in telemetry_state["processed_update_ids"])
        previous = decisions_by_key.get(key)
        crop_id = str(update.get("anchor_crop_id") or "")
        if crop_id:
            crop_ids = {
                str(crop.get("anchor_crop_id"))
                for crop in ((card.get("visual_evidence") or {}).get("anchor_crops") or [])
                if isinstance(crop, dict) and crop.get("anchor_crop_id")
            }
            if crop_id not in crop_ids:
                raise ValueError(f"Unknown anchor_crop_id for {key}: {crop_id}")
            if update.get("clear_jersey_number_annotation"):
                annotations_by_crop.pop(crop_id, None)
            elif "jersey_number_annotation" in update:
                annotations_by_crop[crop_id] = {
                    "anchor_crop_id": crop_id,
                    **_normalize_crop_annotation(update["jersey_number_annotation"]),
                    "updated_at": timestamp,
                }
            if update.get("clear_number_panel_annotation"):
                panel_annotations_by_crop.pop(crop_id, None)
            elif "number_panel_annotation" in update:
                crop = next(
                    crop
                    for crop in ((card.get("visual_evidence") or {}).get("anchor_crops") or [])
                    if isinstance(crop, dict) and str(crop.get("anchor_crop_id") or "") == crop_id
                )
                panel_annotations_by_crop[crop_id] = {
                    "anchor_crop_id": crop_id,
                    **_normalize_number_panel_annotation(
                        update["number_panel_annotation"], crop=crop, root=path
                    ),
                    "updated_at": timestamp,
                }
            if update_id:
                telemetry_state["processed_update_ids"].add(update_id)
            if "decision" not in update:
                continue
        elif (
            "jersey_number_annotation" in update
            or update.get("clear_jersey_number_annotation")
            or "number_panel_annotation" in update
            or update.get("clear_number_panel_annotation")
        ):
            raise ValueError("anchor_crop_id is required for crop annotation")
        decision = update.get("decision")
        if decision in (None, "clear_decision"):
            decisions_by_key.pop(key, None)
            if previous and not already_processed:
                telemetry_state["decisions_changed"] += 1
            if update_id:
                telemetry_state["processed_update_ids"].add(update_id)
            continue
        decision = str(decision)
        if decision not in PERSISTED_DECISIONS:
            raise ValueError(f"Unsupported persisted decision: {decision}")
        if decision not in _effective_allowed_actions(card):
            raise ValueError(f"Decision {decision} is blocked for {key}")
        player_id = _validated_player_id(card, decision, update.get("player_id"))
        next_decision = {
            "review_card_key": key,
            "candidate_subject_id": card.get("candidate_subject_id"),
            "decision": decision,
            "player_id": player_id,
            "comment": str(update.get("comment") or "").strip() or None,
            "updated_at": timestamp,
        }
        if previous and not already_processed and _decision_signature(previous) != _decision_signature(next_decision):
            telemetry_state["decisions_changed"] += 1
        decisions_by_key[key] = next_decision
        if update_id:
            telemetry_state["processed_update_ids"].add(update_id)
    _merge_telemetry_events(telemetry_state, telemetry_events or [], cards_by_key)
    operator_telemetry = _build_operator_telemetry(telemetry_state, decisions_by_key)
    document = {
        "schema_version": "0.3.0",
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
        "operator_telemetry": operator_telemetry,
        "telemetry_state": _serialize_telemetry_state(telemetry_state),
        "decisions": [decisions_by_key[key] for key in sorted(decisions_by_key)],
        "jersey_number_annotations": [annotations_by_crop[key] for key in sorted(annotations_by_crop)],
        "number_panel_annotations": [
            panel_annotations_by_crop[key] for key in sorted(panel_annotations_by_crop)
        ],
    }
    _write_atomic(path / REVIEW_DECISIONS_FILENAME, document)
    return load_identity_roster_subject_review(path, match_doc=match_doc)


def _decision_signature(value: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(value.get("decision") or ""),
        str(value.get("player_id") or ""),
        str(value.get("comment") or ""),
    )


def _stored_crop_annotations(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["anchor_crop_id"]): _normalize_crop_annotation(row)
        for row in document.get("jersey_number_annotations") or []
        if isinstance(row, dict) and row.get("anchor_crop_id")
    }


def _stored_panel_annotations(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["anchor_crop_id"]): dict(row)
        for row in document.get("number_panel_annotations") or []
        if isinstance(row, dict) and row.get("anchor_crop_id")
    }


def _with_crop_annotations(
    card: dict[str, Any],
    annotations_by_crop: dict[str, dict[str, Any]],
    panel_annotations_by_crop: dict[str, dict[str, Any]],
    pre_audits_by_crop: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    visual_evidence = dict(card.get("visual_evidence") or {})
    visual_evidence["anchor_crops"] = [
        {
            **crop,
            "jersey_number_annotation": annotations_by_crop.get(
                str(crop.get("anchor_crop_id") or "")
            ),
            "number_panel_annotation": panel_annotations_by_crop.get(
                str(crop.get("anchor_crop_id") or "")
            ),
            "jersey_number_pre_audit": pre_audits_by_crop.get(
                str(crop.get("anchor_crop_id") or "")
            ),
        }
        for crop in visual_evidence.get("anchor_crops") or []
        if isinstance(crop, dict)
    ]
    return {**card, "visual_evidence": visual_evidence}


def _fresh_pre_audits(path: Path, artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    audit_path = path / VISUAL_PRE_AUDIT_FILENAME
    if not audit_path.exists():
        return {}
    try:
        audit = _load_object(audit_path)
    except ValueError:
        return {}
    if _contains_prohibited_pre_audit_keys(audit):
        return {}
    algorithm_value = audit.get("algorithm")
    algorithm: dict[str, Any] = algorithm_value if isinstance(algorithm_value, dict) else {}
    if (
        audit.get("schema_version") != VISUAL_PRE_AUDIT_SCHEMA_VERSION
        or algorithm.get("name") != VISUAL_PRE_AUDIT_ALGORITHM_NAME
        or algorithm.get("version") != VISUAL_PRE_AUDIT_ALGORITHM_VERSION
    ):
        return {}
    rows = _review_crop_rows(artifact)
    review_digest = canonical_digest(artifact)
    if (
        (audit.get("source") or {}).get("subject_review_digest") != review_digest
        or (audit.get("source") or {}).get("review_crop_entries_digest") != canonical_digest(rows)
    ):
        return {}
    current_by_id = {str(row["anchor_crop_id"]): row for row in rows}
    fresh: dict[str, dict[str, Any]] = {}
    for suggestion in audit.get("suggestions") or []:
        if not isinstance(suggestion, dict):
            return {}
        if suggestion.get("status") != "audited":
            continue
        crop_id = str(suggestion.get("anchor_crop_id") or "")
        row = current_by_id.get(crop_id)
        if row is None or suggestion.get("source_crop_digest") != canonical_digest(row):
            continue
        artifact_name = str(row.get("artifact") or "")
        crop_path = path / artifact_name
        if not _safe_crop_path(artifact_name) or suggestion.get("crop_sha256") != _sha256(crop_path):
            continue
        expected_row_digest = canonical_digest(
            {key: value for key, value in suggestion.items() if key != "row_digest"}
        )
        if suggestion.get("row_digest") != expected_row_digest:
            continue
        fresh[crop_id] = dict(suggestion)
    return fresh


def _contains_prohibited_pre_audit_keys(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(PROHIBITED_PRE_AUDIT_KEYS & set(value)) or any(
            _contains_prohibited_pre_audit_keys(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_prohibited_pre_audit_keys(item) for item in value)
    return False


def _review_crop_rows(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in artifact.get("cards") or []:
        if not isinstance(card, dict):
            continue
        for crop in ((card.get("visual_evidence") or {}).get("anchor_crops") or []):
            if isinstance(crop, dict) and crop.get("anchor_crop_id"):
                rows.append(
                    {
                        "review_card_key": card.get("review_card_key"),
                        "candidate_subject_id": card.get("candidate_subject_id"),
                        "anchor_crop_id": crop.get("anchor_crop_id"),
                        "artifact": crop.get("torso_artifact") or crop.get("artifact"),
                    }
                )
    return sorted(rows, key=lambda row: (str(row["review_card_key"] or ""), str(row["anchor_crop_id"])))


def _safe_crop_path(artifact: str) -> bool:
    crop_path = Path(artifact)
    return bool(artifact and not crop_path.is_absolute() and ".." not in crop_path.parts)


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _normalize_crop_annotation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("jersey_number_annotation must be an object")
    normalized: dict[str, Any] = {}
    for field, allowed in JERSEY_ANNOTATION_ENUMS.items():
        if field not in value:
            continue
        raw = value.get(field)
        item = str(raw or "unknown").strip().lower()
        if item not in allowed:
            raise ValueError(f"Invalid jersey_number_annotation {field}")
        normalized[field] = item
    if "panel_height_ratio" in value:
        ratio = value.get("panel_height_ratio")
        if ratio is None:
            normalized["panel_height_ratio"] = None
        elif isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0.0 <= float(ratio) <= 1.0:
            raise ValueError("panel_height_ratio must be between zero and one or null")
        else:
            normalized["panel_height_ratio"] = round(float(ratio), 6)
    if "kit_profile" in value:
        profile = value.get("kit_profile")
        if profile is not None and not isinstance(profile, str):
            raise ValueError("kit_profile must be a string or null")
        normalized["kit_profile"] = profile.strip() or None if isinstance(profile, str) else None
    if "number_panel_bbox_normalized" in value:
        normalized["number_panel_bbox_normalized"] = normalize_normalized_bbox(
            value.get("number_panel_bbox_normalized"),
            field_name="number_panel_bbox_normalized",
        )
    if "number_panel_artifact" in value:
        normalized["number_panel_artifact"] = normalize_safe_relative_artifact_path(
            value.get("number_panel_artifact"),
            field_name="number_panel_artifact",
        )
    if "jersey_number" in value:
        jersey_number = normalize_jersey_number(value.get("jersey_number"))
        raw_jersey_number = value.get("jersey_number")
        if raw_jersey_number not in (None, "") and jersey_number is None:
            raise ValueError("jersey_number must contain 1-3 digits or be empty")
        normalized["jersey_number"] = jersey_number
    return normalized


def _normalize_number_panel_annotation(
    value: Any,
    *,
    crop: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("number_panel_annotation must be an object")
    source = str(value.get("annotation_source") or value.get("source") or "operator").strip().lower()
    if source != "operator":
        raise ValueError("number_panel_annotation source must be operator")
    artifact = str(value.get("number_panel_source_artifact") or "")
    current_artifact = str(crop.get("torso_artifact") or crop.get("artifact") or "")
    if artifact != current_artifact:
        raise ValueError("number_panel_source_artifact must match current crop")
    supplied_sha = value.get("number_panel_source_sha256")
    current_sha = _sha256(root / current_artifact)
    if supplied_sha is not None and str(supplied_sha) != str(current_sha):
        raise ValueError("number_panel_source_sha256 does not match current crop")
    bbox = value.get("number_panel_bbox_normalized")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in bbox)
    ):
        raise ValueError("number_panel_bbox_normalized must contain four numbers")
    x1, y1, x2, y2 = (float(item) for item in bbox)
    if not 0.0 <= x1 < x2 <= 1.0 or not 0.0 <= y1 < y2 <= 1.0:
        raise ValueError("number_panel_bbox_normalized must be within normalized bounds")
    coordinate_space_version = value.get("coordinate_space_version")
    if not isinstance(coordinate_space_version, str) or not coordinate_space_version.strip():
        raise ValueError("coordinate_space_version is required")
    glyph_height = value.get("glyph_height_px")
    if glyph_height is not None and (
        isinstance(glyph_height, bool)
        or not isinstance(glyph_height, (int, float))
        or float(glyph_height) <= 0
    ):
        raise ValueError("glyph_height_px must be positive or null")
    return {
        "number_panel_source_artifact": current_artifact,
        "number_panel_source_sha256": current_sha,
        "coordinate_space_version": coordinate_space_version.strip(),
        "number_panel_bbox_normalized": [round(item, 6) for item in (x1, y1, x2, y2)],
        "glyph_height_px": round(float(glyph_height), 3) if glyph_height is not None else None,
        "annotation_source": "operator",
    }


def _empty_operator_telemetry() -> dict[str, Any]:
    return {
        "review_session_started_at": None,
        "review_session_completed_at": None,
        "active_review_seconds": 0.0,
        "cards_opened": 0,
        "cards_decided": 0,
        "cards_reopened": 0,
        "decisions_changed": 0,
        "confirm_recommendation_count": 0,
        "manual_assignment_count": 0,
        "unresolved_count": 0,
        "remediation_actions_count": 0,
        "average_seconds_per_card": None,
        "cards_per_minute": None,
        "sessions": 0,
    }


def _empty_telemetry_state() -> dict[str, Any]:
    return {
        "events": [],
        "processed_event_ids": set(),
        "processed_update_ids": set(),
        "decisions_changed": 0,
    }


def _load_telemetry_state(document: dict[str, Any]) -> dict[str, Any]:
    source_value = document.get("telemetry_state")
    source: dict[str, Any] = source_value if isinstance(source_value, dict) else {}
    events = [dict(row) for row in source.get("events") or [] if isinstance(row, dict)]
    return {
        "events": events,
        "processed_event_ids": {
            str(value) for value in source.get("processed_event_ids") or [] if value
        } | {str(row.get("event_id")) for row in events if row.get("event_id")},
        "processed_update_ids": {str(value) for value in source.get("processed_update_ids") or [] if value},
        "decisions_changed": int(source.get("decisions_changed") or 0),
    }


def _merge_telemetry_events(
    state: dict[str, Any],
    events: list[dict[str, Any]],
    cards_by_key: dict[str, dict[str, Any]],
) -> None:
    for raw in events:
        if not isinstance(raw, dict):
            raise ValueError("Each telemetry event must be an object")
        event_id = str(raw.get("event_id") or "")
        if not event_id:
            raise ValueError("telemetry event_id is required")
        if event_id in state["processed_event_ids"]:
            continue
        event_type = str(raw.get("event_type") or "")
        if event_type not in TELEMETRY_EVENT_TYPES:
            raise ValueError(f"Unsupported telemetry event_type: {event_type or '<missing>'}")
        card_key = str(raw.get("review_card_key") or "")
        if card_key and card_key not in cards_by_key:
            raise ValueError(f"Unknown telemetry review_card_key: {card_key}")
        try:
            active_delta = float(raw.get("active_delta_seconds") or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError("active_delta_seconds must be numeric") from exc
        event = {
            "event_id": event_id,
            "session_id": str(raw.get("session_id") or "default"),
            "event_type": event_type,
            "occurred_at": str(raw.get("occurred_at") or datetime.now(timezone.utc).isoformat()),
            "active_delta_seconds": round(max(0.0, min(active_delta, MAX_ACTIVE_DELTA_SECONDS)), 3),
            "review_card_key": card_key or None,
        }
        state["events"].append(event)
        state["processed_event_ids"].add(event_id)


def _build_operator_telemetry(
    state: dict[str, Any],
    decisions_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    events = state["events"]
    starts = [row["occurred_at"] for row in events if row["event_type"] == "session_started"]
    completions = [row["occurred_at"] for row in events if row["event_type"] == "session_completed"]
    opened = [str(row.get("review_card_key")) for row in events if row["event_type"] == "card_opened" and row.get("review_card_key")]
    open_counts: dict[str, int] = {}
    for key in opened:
        open_counts[key] = open_counts.get(key, 0) + 1
    active_seconds = round(sum(float(row.get("active_delta_seconds") or 0.0) for row in events), 3)
    cards_decided = len(decisions_by_key)
    decisions = [str(row.get("decision") or "") for row in decisions_by_key.values()]
    result = {
        **_empty_operator_telemetry(),
        "review_session_started_at": min(starts) if starts else None,
        "review_session_completed_at": max(completions) if completions else None,
        "active_review_seconds": active_seconds,
        "cards_opened": len(opened),
        "cards_decided": cards_decided,
        "cards_reopened": sum(max(0, count - 1) for count in open_counts.values()),
        "decisions_changed": int(state["decisions_changed"]),
        "confirm_recommendation_count": decisions.count("confirm_recommended_player"),
        "manual_assignment_count": decisions.count("assign_roster_player"),
        "unresolved_count": decisions.count("mark_unresolved"),
        "remediation_actions_count": sum(row["event_type"] == "remediation_action" for row in events),
        "sessions": len({str(row.get("session_id") or "default") for row in events if row["event_type"] == "session_started"}),
    }
    if cards_decided > 0:
        result["average_seconds_per_card"] = round(active_seconds / cards_decided, 3)
    if active_seconds > 0:
        result["cards_per_minute"] = round(cards_decided * 60.0 / active_seconds, 3)
    return result


def _serialize_telemetry_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "events": state["events"],
        "processed_event_ids": sorted(state["processed_event_ids"]),
        "processed_update_ids": sorted(state["processed_update_ids"]),
        "decisions_changed": int(state["decisions_changed"]),
    }


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
