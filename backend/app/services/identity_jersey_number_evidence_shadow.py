from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import (
    EVIDENCE_STATES,
    canonical_digest,
    normalize_jersey_number,
    round_or_none,
    stable_key,
    team_label,
)


SCHEMA_VERSION = "0.3.0"
ALGORITHM_NAME = "identity_jersey_number_evidence_shadow"
ALGORITHM_VERSION = "1.2.0"
DEFAULT_PARAMETERS: dict[str, Any] = {
    "minimum_bbox_width_px": 24.0,
    "minimum_bbox_height_px": 48.0,
    "minimum_detection_confidence": 0.75,
    "minimum_appearance_reliable_ratio": 0.70,
    "minimum_read_confidence": 0.80,
    "torso_roi_normalized": [0.10, 0.02, 0.90, 0.68],
}


def build_identity_jersey_number_evidence_shadow(
    anchor_crops_doc: dict[str, Any],
    roster_doc: dict[str, Any],
    *,
    observations_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build per-crop evidence. Missing OCR is always unreadable, never absent."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    observations = _observation_map(observations_doc or {})
    unique_lookup = roster_doc.get("unique_number_lookup") or {}
    evidence: list[dict[str, Any]] = []
    audit_cards: list[dict[str, Any]] = []
    for card in anchor_crops_doc.get("cards") or []:
        if not isinstance(card, dict):
            continue
        subject_id = str(card.get("candidate_subject_id") or "")
        label = team_label(card.get("team_label"))
        for crop in card.get("anchor_crops") or []:
            if not isinstance(crop, dict) or not crop.get("anchor_crop_id"):
                continue
            row = _evidence_row(
                crop,
                subject_id=subject_id,
                label=label,
                observation=observations.get(str(crop["anchor_crop_id"])),
                unique_lookup=unique_lookup,
                parameters=params,
            )
            evidence.append(row)
            if row["quality"]["eligible"]:
                audit_cards.append(
                    {
                    "evidence_key": row["evidence_key"],
                    "anchor_crop_id": row["anchor_crop_id"],
                    "candidate_subject_id": subject_id,
                    "tracklet_id": row["tracklet_id"],
                    "team_label": label,
                    "frame": row["frame"],
                    "artifact": row["artifact"],
                    "torso_roi_normalized": row["torso_roi_normalized"],
                    "quality": row["quality"],
                    "current_evidence": {
                        "state": row["state"],
                        "number": row["number"],
                        "confidence": row["confidence"],
                        "view": row["view"],
                        "clean_jersey_visible": row["clean_jersey_visible"],
                        "number_panel_visible": row["number_panel_visible"],
                        "reason_codes": row["reason_codes"],
                    },
                    "allowed_review_states": sorted(EVIDENCE_STATES),
                    }
                )
    counts = Counter(row["state"] for row in evidence)
    quality_counts = Counter(row["quality"]["status"] for row in evidence)
    source = {
        "anchor_crops_digest": canonical_digest(anchor_crops_doc),
        "roster_digest": canonical_digest(roster_doc),
        "observations_digest": canonical_digest(observations_doc or {}),
    }
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": params},
        "source": source,
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_assignments": 0,
            "no_read_is_not_number_absent": True,
        },
        "summary": {
            "evidence_rows": len(evidence),
            "state_counts": dict(sorted(counts.items())),
            "quality_counts": dict(sorted(quality_counts.items())),
            "reviewable_rows": sum(row["quality"]["eligible"] for row in evidence),
        },
        "evidence": sorted(evidence, key=lambda row: (row["candidate_subject_id"], row["frame"], row["evidence_key"])),
    }
    audit = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_operator_audit",
        "algorithm": artifact["algorithm"],
        "source": source,
        "summary": {
            **artifact["summary"],
            "audit_cards": len(audit_cards),
            "excluded_unreliable_cards": len(evidence) - len(audit_cards),
        },
        "cards": sorted(audit_cards, key=lambda row: (row["candidate_subject_id"], row["frame"])),
        "review_contract": {
            "unit": "reliable_torso_crop",
            "states": sorted(EVIDENCE_STATES),
            "number_required_for": ["number_confirmed", "number_conflict"],
            "number_absent_requires_visible_clean_jersey": True,
            "number_absent_requires_number_panel_visibility": True,
            "unreadable_includes_recognizer_not_run": True,
        },
    }
    return {
        "identity_jersey_number_evidence_shadow": artifact,
        "identity_jersey_number_audit": audit,
    }


def _evidence_row(
    crop: dict[str, Any],
    *,
    subject_id: str,
    label: str,
    observation: dict[str, Any] | None,
    unique_lookup: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    bbox = [float(value) for value in crop.get("bbox_xyxy") or [0, 0, 0, 0]]
    width = max(0.0, bbox[2] - bbox[0]) if len(bbox) == 4 else 0.0
    height = max(0.0, bbox[3] - bbox[1]) if len(bbox) == 4 else 0.0
    reasons: list[str] = []
    if not bool(crop.get("selection_eligible", True)):
        reasons.append("anchor_crop_not_selection_eligible")
    if str(crop.get("quality_class") or "") not in {"trusted", "recoverable"}:
        reasons.append("anchor_crop_quality_too_low")
    if width < float(parameters["minimum_bbox_width_px"]):
        reasons.append("bbox_too_narrow")
    if height < float(parameters["minimum_bbox_height_px"]):
        reasons.append("bbox_too_short")
    if float(crop.get("detection_confidence") or 0.0) < float(parameters["minimum_detection_confidence"]):
        reasons.append("low_detection_confidence")
    if float(crop.get("appearance_reliable_ratio") or 0.0) < float(parameters["minimum_appearance_reliable_ratio"]):
        reasons.append("appearance_unreliable")
    quality_eligible = not reasons
    state, number, confidence, observation_reasons = _normalize_observation(
        observation,
        label=label,
        unique_lookup=unique_lookup,
        minimum_read_confidence=float(parameters["minimum_read_confidence"]),
    )
    reasons.extend(observation_reasons)
    evidence_key = stable_key(
        "jersey-evidence",
        {"anchor_crop_id": crop.get("anchor_crop_id"), "subject_id": subject_id, "frame": crop.get("frame")},
    )
    return {
        "evidence_key": evidence_key,
        "anchor_crop_id": str(crop.get("anchor_crop_id")),
        "candidate_subject_id": subject_id,
        "tracklet_id": str(crop.get("tracklet_id") or ""),
        "team_label": label,
        "frame": int(crop.get("frame") or 0),
        "time_sec": round_or_none(crop.get("time_sec"), 3),
        "artifact": crop.get("artifact"),
        "source_bbox_xyxy": bbox,
        "torso_roi_normalized": list(parameters["torso_roi_normalized"]),
        "quality": {
            "status": "reliable" if quality_eligible else "rejected",
            "eligible": quality_eligible,
            "bbox_width_px": round(width, 2),
            "bbox_height_px": round(height, 2),
            "detection_confidence": round_or_none(crop.get("detection_confidence"), 4),
            "appearance_reliable_ratio": round_or_none(crop.get("appearance_reliable_ratio"), 4),
        },
        "state": state if quality_eligible else "number_unreadable",
        "number": number if quality_eligible else None,
        "confidence": confidence if quality_eligible else 0.0,
        "raw_confidence": (
            round_or_none((observation or {}).get("raw_confidence"), 4)
            if quality_eligible
            else 0.0
        ),
        "calibrated_confidence": (
            round_or_none(
                (observation or {}).get("calibrated_confidence", confidence),
                4,
            )
            if quality_eligible
            else 0.0
        ),
        "confidence_tier": (
            str((observation or {}).get("confidence_tier") or "unknown")
            if quality_eligible
            else "rejected"
        ),
        "digits": [int(value) for value in str(number)] if number is not None else [],
        "view": str((observation or {}).get("view") or "unknown"),
        "clean_jersey_visible": bool((observation or {}).get("clean_jersey_visible", False)),
        "number_panel_visible": _number_panel_visible(observation),
        "visibility_episode_id": str(
            (observation or {}).get("visibility_episode_id")
            or crop.get("visibility_episode_id")
            or ""
        ) or None,
        "observation_source": _observation_source(observation),
        "reason_codes": sorted(set(reasons)),
    }


def _observation_source(observation: dict[str, Any] | None) -> dict[str, Any]:
    if not observation:
        return {"kind": "not_run", "method": None}
    structured = observation.get("observation_source")
    if isinstance(structured, dict):
        return {
            "kind": str(structured.get("kind") or "automatic_recognizer"),
            "method": structured.get("method"),
            "model_digest": structured.get("model_digest"),
        }
    legacy = observation.get("source")
    if isinstance(legacy, dict):
        return {
            "kind": str(legacy.get("kind") or "legacy"),
            "method": legacy.get("method"),
            "model_digest": legacy.get("model_digest"),
        }
    return {
        "kind": str(legacy or "automatic_recognizer"),
        "method": observation.get("recognition_method"),
        "model_digest": observation.get("model_digest"),
    }


def _normalize_observation(
    observation: dict[str, Any] | None,
    *,
    label: str,
    unique_lookup: dict[str, Any],
    minimum_read_confidence: float,
) -> tuple[str, str | None, float, list[str]]:
    if not observation:
        return "number_unreadable", None, 0.0, ["recognizer_not_run"]
    raw_state = str(observation.get("state") or observation.get("status") or "number_unreadable")
    state = {
        "readable": "number_confirmed",
        "absent_candidate": "number_absent",
        "unreadable": "number_unreadable",
        "partial": "number_unreadable",
    }.get(raw_state, raw_state)
    if state not in EVIDENCE_STATES:
        state = "number_unreadable"
    number = normalize_jersey_number(observation.get("number"))
    confidence_value = (
        observation.get("calibrated_confidence")
        if observation.get("calibrated_confidence") is not None
        else observation.get("confidence")
    )
    confidence = max(0.0, min(1.0, float(confidence_value or 0.0)))
    reasons: list[str] = []
    if state in {"number_confirmed", "number_conflict"} and number is None:
        state = "number_unreadable"
        reasons.append("invalid_or_missing_number")
    if state == "number_confirmed" and confidence < minimum_read_confidence:
        state = "number_unreadable"
        number = None
        reasons.append("read_confidence_below_threshold")
    if state == "number_confirmed" and label != "U" and f"{label}:{number}" not in unique_lookup:
        state = "number_conflict"
        reasons.append("number_not_unique_trusted_roster_match")
    if state == "number_absent" and not _number_panel_visible(observation):
        state = "number_unreadable"
        reasons.append("number_absent_without_number_panel_evidence")
    if state in {"number_absent", "number_unreadable"}:
        number = None
    return state, number, round(confidence, 4), reasons


def _number_panel_visible(observation: dict[str, Any] | None) -> bool:
    if not observation:
        return False
    if "number_panel_visible" in observation:
        return bool(observation.get("number_panel_visible"))
    return bool(
        observation.get("clean_jersey_visible", False)
        and str(observation.get("view") or "unknown") == "back"
    )


def _observation_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = document.get("observations") or document.get("decisions") or []
    if isinstance(rows, dict):
        return {
            str(key): ({**value, "anchor_crop_id": key} if isinstance(value, dict) else {"status": value})
            for key, value in rows.items()
        }
    return {
        str(row.get("anchor_crop_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("anchor_crop_id")
    }
