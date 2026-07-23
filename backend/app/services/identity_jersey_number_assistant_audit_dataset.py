from __future__ import annotations

from typing import Any

from app.services.identity_jersey_number_common import (
    canonical_digest,
    normalize_jersey_number,
    team_label,
)


ASSISTANT_ANNOTATION_SOURCE = "assistant_visual_audit_high_confidence"
ASSISTANT_QUALITY_FIELDS = (
    "view",
    "digit_visibility",
    "occlusion_state",
    "blur_level",
    "perspective_state",
    "panel_height_ratio",
    "kit_profile",
    "clean_jersey_visible",
    "number_panel_visible",
)


def build_assistant_audit_dataset_source(
    subject_review_doc: dict[str, Any],
    assistant_visual_audit_shadow: dict[str, Any],
    *,
    source_match_key: str,
    roster_reference: dict[str, Any],
) -> dict[str, Any]:
    source_match = str(source_match_key or "").strip()
    if not source_match:
        raise ValueError("source_match_key is required")
    review_digest = canonical_digest(subject_review_doc)
    _validate_audit(assistant_visual_audit_shadow, review_digest)
    roster_team_id, roster_numbers = _roster_reference_numbers(roster_reference)
    audit_high_confidence = assistant_visual_audit_shadow.get("observations_are_high_confidence") is True
    review_crops = _review_crops(subject_review_doc)
    cards: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    labels_by_subject: dict[str, list[dict[str, str]]] = {}
    seen: set[str] = set()
    exclusions: list[dict[str, str]] = []
    for row in assistant_visual_audit_shadow.get("observations") or []:
        if (
            not isinstance(row, dict)
            or not (audit_high_confidence or _high_confidence(row))
            or not row.get("jersey_number")
        ):
            continue
        crop_id = str(row.get("anchor_crop_id") or "").strip()
        if not crop_id or crop_id in seen:
            exclusions.append({"anchor_crop_id": crop_id or "<missing>", "reason": "duplicate_anchor_crop_label"})
            continue
        seen.add(crop_id)
        crop = review_crops.get(crop_id)
        if crop is None:
            exclusions.append({"anchor_crop_id": crop_id, "reason": "crop_absent_from_subject_review"})
            continue
        if row.get("diagnostic_training_authorized") is False:
            raise ValueError("assistant artifact is not diagnostic-training-authorized")
        artifact = str(crop.get("torso_artifact") or crop.get("artifact") or "")
        if row.get("artifact") and str(row["artifact"]) != artifact:
            raise ValueError("assistant artifact does not match subject review")
        number = normalize_jersey_number(row["jersey_number"])
        if number is None:
            exclusions.append({"anchor_crop_id": crop_id, "reason": "invalid_jersey_number"})
            continue
        if crop["team_label"] == "B":
            exclusions.append({"anchor_crop_id": crop_id, "reason": "team_b_scope"})
            continue
        if crop["team_label"] != "A":
            exclusions.append({"anchor_crop_id": crop_id, "reason": "unknown_or_missing_team_scope"})
            continue
        roster_players = roster_numbers.get(number) or []
        if not roster_players:
            exclusions.append({"anchor_crop_id": crop_id, "reason": "number_not_in_roster_reference"})
            continue
        if len(roster_players) != 1:
            exclusions.append({"anchor_crop_id": crop_id, "reason": "number_not_unique_in_roster_reference"})
            continue
        subject_id = str(crop.get("candidate_subject_id") or "")
        labels_by_subject.setdefault(subject_id, []).append(
            {"anchor_crop_id": crop_id, "jersey_number": number}
        )
        cards.append(
            {
                "anchor_crop_id": crop_id,
                "artifact": artifact,
                "torso_artifact": crop.get("torso_artifact"),
                "frame": crop.get("frame"),
                "bbox_xyxy": crop.get("bbox_xyxy"),
                "candidate_subject_id": crop.get("candidate_subject_id"),
                "tracklet_id": crop.get("tracklet_id"),
                "team_label": crop.get("team_label"),
                "team_id": roster_team_id,
                "team_id_provenance": "roster_reference_team_scope",
            }
        )
        observation = {
            "anchor_crop_id": crop_id,
            "state": "number_confirmed",
            "number": number,
            "source": ASSISTANT_ANNOTATION_SOURCE,
            "annotation_source": ASSISTANT_ANNOTATION_SOURCE,
            "provenance": {
                "subject_review_digest": review_digest,
                "assistant_audit_digest": canonical_digest(row),
                "assistant_artifact_authorized": True,
                "label_scope": "anchor_crop_only",
                "subject_level_label_inference": "disabled",
                "diagnostic_only": True,
                "team_id_provenance": "roster_reference_team_scope",
                "explicit_quality_fields": [
                    field for field in ASSISTANT_QUALITY_FIELDS if field in row
                ],
            },
        }
        if "confidence" in row:
            observation["confidence"] = float(row["confidence"])
        for field in ASSISTANT_QUALITY_FIELDS:
            if field in row:
                observation[field] = row[field]
        observations.append(observation)
    cards_doc = {"cards": sorted(cards, key=lambda row: row["anchor_crop_id"])}
    reviewed_doc = {"observations": sorted(observations, key=lambda row: row["anchor_crop_id"])}
    subject_label_conflicts = [
        {
            "candidate_subject_id": subject_id,
            "jersey_numbers": sorted({row["jersey_number"] for row in labels}),
            "anchor_crop_ids": sorted(row["anchor_crop_id"] for row in labels),
        }
        for subject_id, labels in sorted(labels_by_subject.items())
        if len({row["jersey_number"] for row in labels}) > 1
    ]
    exclusion_counts: dict[str, int] = {}
    for exclusion in exclusions:
        reason = exclusion["reason"]
        exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
    return {
        "cards_doc": cards_doc,
        "reviewed_observations_doc": reviewed_doc,
        "provenance": {
            "subject_review_digest": review_digest,
            "assistant_visual_audit_digest": canonical_digest(assistant_visual_audit_shadow),
            "annotation_source": ASSISTANT_ANNOTATION_SOURCE,
            "source_match_key": source_match,
            "team_id": roster_team_id,
            "team_id_provenance": "roster_reference_team_scope",
            "roster_reference_digest": canonical_digest(roster_reference),
            "roster_reference_policy": "team_a_only_unique_player_jersey_numbers",
            "raw_diagnostic_exclusions": exclusions,
            "raw_diagnostic_exclusion_count": len(exclusions),
            "raw_diagnostic_exclusion_counts": dict(sorted(exclusion_counts.items())),
            "crop_local_labels_only": True,
            "subject_label_conflicts": subject_label_conflicts,
            "subject_label_conflict_count": len(subject_label_conflicts),
        },
    }


def _validate_audit(audit: dict[str, Any], review_digest: str) -> None:
    source_value = audit.get("source")
    safety_value = audit.get("safety")
    source: dict[str, Any] = source_value if isinstance(source_value, dict) else {}
    safety: dict[str, Any] = safety_value if isinstance(safety_value, dict) else {}
    if source.get("subject_review_digest") != review_digest:
        raise ValueError("assistant audit subject review digest mismatch")
    if (
        audit.get("mode") != "diagnostic_training_only"
        or safety.get("diagnostic_training_authorized") is not True
        or safety.get("writes_player_identity_assignments") is not False
    ):
        raise ValueError("assistant audit safety mode is not authorized")
    if audit.get("observations_are_high_confidence") is not True and not any(
        isinstance(row, dict) and _high_confidence(row) for row in audit.get("observations") or []
    ):
        raise ValueError("assistant audit observations lack high-confidence authorization")


def _review_crops(subject_review_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    crops: dict[str, dict[str, Any]] = {}
    for card in subject_review_doc.get("cards") or []:
        if not isinstance(card, dict):
            continue
        visual_evidence = card.get("visual_evidence")
        if not isinstance(visual_evidence, dict):
            continue
        for crop in visual_evidence.get("anchor_crops") or []:
            if not isinstance(crop, dict) or not crop.get("anchor_crop_id"):
                continue
            crop_id = str(crop["anchor_crop_id"])
            if crop_id in crops:
                raise ValueError("duplicate subject review crop")
            label = team_label(card.get("team_label"))
            crops[crop_id] = {
                **crop,
                "candidate_subject_id": card.get("candidate_subject_id"),
                "team_label": label,
            }
    return crops


def _roster_reference_numbers(roster_reference: dict[str, Any]) -> tuple[str, dict[str, list[str]]]:
    team_id = str(roster_reference.get("team_id") or "").strip()
    if not team_id:
        raise ValueError("roster_reference team_id is required")
    numbers: dict[str, list[str]] = {}
    for player in roster_reference.get("players") or []:
        if not isinstance(player, dict) or not player.get("player_id"):
            continue
        number = normalize_jersey_number(player.get("jersey_number"))
        if number is not None:
            numbers.setdefault(number, []).append(str(player["player_id"]))
    if not numbers:
        raise ValueError("roster_reference requires player jersey numbers")
    return team_id, numbers


def _high_confidence(row: dict[str, Any]) -> bool:
    return row.get("confidence_tier") == "high" or row.get("high_confidence") is True
