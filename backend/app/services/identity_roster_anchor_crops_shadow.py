from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.4.0"
ALGORITHM_NAME = "identity_roster_anchor_crops_shadow"
ALGORITHM_VERSION = "0.4.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "min_anchors": 3,
    "max_anchors": 5,
    "min_detection_confidence": 0.55,
    "min_neighbor_detection_confidence": 0.35,
    "min_bbox_width_px": 8,
    "min_bbox_height_px": 18,
    "occlusion_padding_frames": 3,
    "max_same_frame_iou": 0.02,
    "max_same_frame_intersection_over_min_area": 0.12,
    "temporal_diversity_weight": 0.20,
    "tracklet_diversity_bonus": 0.05,
}


def build_identity_roster_anchor_crops_shadow(
    roster_anchor_doc: dict[str, Any],
    timeline_doc: dict[str, Any],
    *,
    occlusion_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Choose representative crops for P1.15 cards without changing identity.

    The selector consumes candidate-subject observations, not production slots.
    It only references frames and bboxes; rendering the referenced crops is an
    evaluator/UI concern and cannot affect production identity or statistics.
    """
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    timeline_by_subject = {
        str(row.get("shadow_subject_id")): row
        for row in timeline_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("shadow_subject_id")
    }
    occlusion_index = _occlusion_index(occlusion_doc or {}, parameters=params)
    frame_observation_index = _frame_observation_index(timeline_doc, parameters=params)
    cards: list[dict[str, Any]] = []
    for roster_card in sorted(
        roster_anchor_doc.get("cards") or [],
        key=lambda row: (str(row.get("team_label") or "U"), str(row.get("candidate_subject_id") or "")),
    ):
        subject_id = str(roster_card.get("candidate_subject_id") or "")
        timeline_subject = timeline_by_subject.get(subject_id)
        cards.append(
            _build_card(
                roster_card,
                timeline_subject,
                occlusion_index=occlusion_index,
                frame_observation_index=frame_observation_index,
                parameters=params,
            )
        )

    summary = _summary(cards)
    safety = {
        "mutates_candidate_identity": False,
        "mutates_production_identity": False,
        "writes_player_identity_assignments": False,
        "automatically_assigns_roster_players": False,
        "automatic_assignments": 0,
        "eligible_for_player_stats": False,
        "eligible_for_heatmaps": False,
        "renders_production_overlay": False,
    }
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "source": {
            "roster_anchor_algorithm": roster_anchor_doc.get("algorithm") or {},
            "timeline_algorithm": timeline_doc.get("algorithm") or {},
            "occlusion_algorithm": (occlusion_doc or {}).get("algorithm") or None,
            "source_match_key": (roster_anchor_doc.get("source") or {}).get("source_match_key"),
            "source_video_key": (roster_anchor_doc.get("source") or {}).get("source_video_key"),
            "team_ids": sorted({
                str(card.get("team_id"))
                for card in roster_anchor_doc.get("cards") or []
                if card.get("team_id")
            }),
        },
        "safety": safety,
        "summary": summary,
        "cards": cards,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": artifact["algorithm"],
        "status": "ready_for_visual_audit" if summary["selected_crops"] else "no_reliable_crops",
        "summary": summary,
        "gates": {
            "production_identity_untouched": True,
            "candidate_subject_is_selection_unit": True,
            "only_detected_inside_play_observations_selected": all(
                crop.get("selection_eligible")
                for card in cards
                for crop in card.get("anchor_crops") or []
            ),
            "manual_confirmation_still_required": True,
        },
        "limitations": [
            "Selected crops are review evidence, not roster assignments.",
            "A card with fewer than three reliable observations remains incomplete.",
            "Visual audit is required before connecting these cards to operator UI.",
        ],
    }
    return {
        "identity_roster_anchor_crops_shadow": artifact,
        "identity_roster_anchor_crops_shadow_report": report,
    }


def _build_card(
    roster_card: dict[str, Any],
    timeline_subject: dict[str, Any] | None,
    *,
    occlusion_index: dict[str, list[tuple[int, int]]],
    frame_observation_index: dict[int, list[dict[str, Any]]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    subject_id = str(roster_card.get("candidate_subject_id") or "")
    observations = list((timeline_subject or {}).get("observations") or [])
    eligible: list[dict[str, Any]] = []
    rejected = Counter()
    for observation in observations:
        reasons = _rejection_reasons(
            observation,
            occlusion_index,
            frame_observation_index,
            parameters=parameters,
        )
        if reasons:
            rejected.update(reasons)
            continue
        eligible.append(_scored_candidate(observation))
    _normalize_size_scores(eligible)
    selected = _select_diverse(eligible, roster_card, parameters=parameters)
    anchor_crops = [
        {
            "anchor_crop_id": _anchor_crop_id(
                subject_id,
                int(row["frame"]),
                roster_card.get("source_match_key"),
                roster_card.get("source_video_key"),
                roster_card.get("team_id"),
            ),
            "source_match_key": roster_card.get("source_match_key"),
            "source_video_key": roster_card.get("source_video_key"),
            "team_id": roster_card.get("team_id"),
            "artifact": _artifact_path(subject_id, index, int(row["frame"])),
            "frame": int(row["frame"]),
            "time_sec": _round_or_none(row.get("time_sec"), 3),
            "tracklet_id": row.get("tracklet_id"),
            "bbox_xyxy": list(row["bbox_xyxy"]),
            "detection_confidence": _round_or_none(row.get("confidence"), 4),
            "team_confidence": _round_or_none(row.get("team_confidence"), 4),
            "appearance_reliable_ratio": _round_or_none(row.get("appearance_reliable_ratio"), 4),
            "quality_class": row.get("quality_class"),
            "selection_score": round(float(row["selection_score"]), 6),
            "selection_eligible": True,
            "selection_reasons": list(row["selection_reasons"]),
        }
        for index, row in enumerate(sorted(selected, key=lambda item: int(item["frame"])), start=1)
    ]
    minimum = int(parameters["min_anchors"])
    if len(anchor_crops) >= minimum:
        status = "ready_for_visual_audit"
    elif anchor_crops:
        status = "insufficient_reliable_crops"
    else:
        status = "no_reliable_crops"
    return {
        "anchor_key": roster_card.get("anchor_key"),
        "candidate_subject_id": subject_id,
        "team_label": roster_card.get("team_label"),
        "source_match_key": roster_card.get("source_match_key"),
        "source_video_key": roster_card.get("source_video_key"),
        "team_id": roster_card.get("team_id"),
        "role": roster_card.get("role"),
        "start_frame": int(roster_card.get("start_frame") or 0),
        "end_frame": int(roster_card.get("end_frame") or 0),
        "roster_status": roster_card.get("status"),
        "recommended_player_id": roster_card.get("recommended_player_id"),
        "recommended_player_name": roster_card.get("recommended_player_name"),
        "status": status,
        "observations_considered": len(observations),
        "eligible_observations": len(eligible),
        "selected_crop_count": len(anchor_crops),
        "rejected_observations": dict(sorted(rejected.items())),
        "anchor_crops": anchor_crops,
        "automatic_assignment": False,
        "eligible_for_player_stats": False,
        "requires_operator_review": True,
    }


def _rejection_reasons(
    observation: dict[str, Any],
    occlusion_index: dict[str, list[tuple[int, int]]],
    frame_observation_index: dict[int, list[dict[str, Any]]],
    *,
    parameters: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if observation.get("status") != "detected":
        reasons.append("not_detected")
    if not observation.get("appearance_reliable"):
        reasons.append("appearance_unreliable")
    if not observation.get("footpoint_reliable"):
        reasons.append("footpoint_unreliable")
    if observation.get("play_area_status") != "inside_play":
        reasons.append("outside_play_area")
    if float(observation.get("confidence") or 0.0) < float(parameters["min_detection_confidence"]):
        reasons.append("low_detection_confidence")
    bbox = observation.get("bbox_xyxy") or []
    if len(bbox) != 4:
        reasons.append("invalid_bbox")
    else:
        width = max(0.0, float(bbox[2]) - float(bbox[0]))
        height = max(0.0, float(bbox[3]) - float(bbox[1]))
        if width < float(parameters["min_bbox_width_px"]) or height < float(parameters["min_bbox_height_px"]):
            reasons.append("bbox_too_small")
    frame = int(observation.get("frame") or 0)
    tracklet_id = str(observation.get("tracklet_id") or "")
    if any(start <= frame <= end for start, end in occlusion_index.get(tracklet_id) or []):
        reasons.append("near_occlusion_event")
    if _has_same_frame_overlap(observation, frame_observation_index.get(frame) or [], parameters=parameters):
        reasons.append("overlaps_nearby_person")
    return reasons


def _frame_observation_index(
    timeline_doc: dict[str, Any],
    *,
    parameters: dict[str, Any],
) -> dict[int, list[dict[str, Any]]]:
    minimum_confidence = float(parameters["min_neighbor_detection_confidence"])
    index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for subject in timeline_doc.get("subjects") or []:
        if not isinstance(subject, dict):
            continue
        subject_id = str(subject.get("shadow_subject_id") or "")
        for observation in subject.get("observations") or []:
            if not isinstance(observation, dict):
                continue
            bbox = observation.get("bbox_xyxy") or []
            if (
                observation.get("status") == "detected"
                and len(bbox) == 4
                and float(observation.get("confidence") or 0.0) >= minimum_confidence
            ):
                index[int(observation.get("frame") or 0)].append(
                    {
                        "shadow_subject_id": subject_id,
                        "tracklet_id": str(observation.get("tracklet_id") or ""),
                        "bbox_xyxy": [float(value) for value in bbox],
                    }
                )
    return dict(index)


def _has_same_frame_overlap(
    observation: dict[str, Any],
    same_frame_observations: list[dict[str, Any]],
    *,
    parameters: dict[str, Any],
) -> bool:
    bbox = observation.get("bbox_xyxy") or []
    if len(bbox) != 4:
        return False
    candidate_bbox = [float(value) for value in bbox]
    candidate_tracklet = str(observation.get("tracklet_id") or "")
    max_iou = float(parameters["max_same_frame_iou"])
    max_min_area = float(parameters["max_same_frame_intersection_over_min_area"])
    for other in same_frame_observations:
        other_bbox = other.get("bbox_xyxy") or []
        if len(other_bbox) != 4:
            continue
        if candidate_tracklet and candidate_tracklet == str(other.get("tracklet_id") or ""):
            continue
        iou, intersection_over_min_area = _bbox_overlap_metrics(candidate_bbox, other_bbox)
        if iou > max_iou or intersection_over_min_area > max_min_area:
            return True
    return False


def _bbox_overlap_metrics(a: list[float], b: list[float]) -> tuple[float, float]:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    intersection_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    min_area = min(area_a, area_b)
    iou = intersection / union if union > 0 else 0.0
    intersection_over_min_area = intersection / min_area if min_area > 0 else 0.0
    return iou, intersection_over_min_area


def _scored_candidate(observation: dict[str, Any]) -> dict[str, Any]:
    bbox = [float(value) for value in observation["bbox_xyxy"]]
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    return {
        **observation,
        "bbox_xyxy": [round(value, 3) for value in bbox],
        "bbox_area": width * height,
        "bbox_height": height,
        "selection_reasons": [
            "detected",
            "inside_play",
            "appearance_reliable",
            "footpoint_reliable",
            "outside_occlusion_window",
        ],
    }


def _normalize_size_scores(rows: list[dict[str, Any]]) -> None:
    max_area = max((float(row["bbox_area"]) for row in rows), default=1.0)
    max_height = max((float(row["bbox_height"]) for row in rows), default=1.0)
    for row in rows:
        confidence = min(1.0, max(0.0, float(row.get("confidence") or 0.0)))
        appearance = min(1.0, max(0.0, float(row.get("appearance_reliable_ratio") or 0.0)))
        team = min(1.0, max(0.0, float(row.get("team_confidence") or 0.0)))
        area = float(row["bbox_area"]) / max_area
        height = float(row["bbox_height"]) / max_height
        trusted = 1.0 if row.get("quality_class") == "trusted" else 0.5
        row["base_score"] = (
            0.30 * confidence
            + 0.20 * appearance
            + 0.15 * team
            + 0.20 * area
            + 0.10 * height
            + 0.05 * trusted
        )


def _select_diverse(
    rows: list[dict[str, Any]],
    roster_card: dict[str, Any],
    *,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    target = min(int(parameters["max_anchors"]), len(rows))
    start = int(roster_card.get("start_frame") or min(int(row["frame"]) for row in rows))
    end = int(roster_card.get("end_frame") or max(int(row["frame"]) for row in rows))
    duration = max(1, end - start + 1)
    bins: list[list[dict[str, Any]]] = [[] for _ in range(target)]
    for row in rows:
        relative = min(duration - 1, max(0, int(row["frame"]) - start))
        index = min(target - 1, int(relative * target / duration))
        bins[index].append(row)
    selected: list[dict[str, Any]] = []
    used_frames: set[int] = set()
    used_tracklets: set[str] = set()
    for bucket in bins:
        if not bucket:
            continue
        choice = max(
            bucket,
            key=lambda row: (float(row["base_score"]), -int(row["frame"])),
        )
        _append_selected(choice, selected, used_frames, used_tracklets, duration, parameters)
    while len(selected) < target:
        remaining = [row for row in rows if int(row["frame"]) not in used_frames]
        if not remaining:
            break
        choice = max(
            remaining,
            key=lambda row: (
                _diverse_score(row, selected, used_tracklets, duration, parameters),
                -int(row["frame"]),
            ),
        )
        _append_selected(choice, selected, used_frames, used_tracklets, duration, parameters)
    return selected


def _append_selected(
    row: dict[str, Any],
    selected: list[dict[str, Any]],
    used_frames: set[int],
    used_tracklets: set[str],
    duration: int,
    parameters: dict[str, Any],
) -> None:
    item = dict(row)
    item["selection_score"] = _diverse_score(row, selected, used_tracklets, duration, parameters)
    if selected:
        item["selection_reasons"] = [*item["selection_reasons"], "temporal_diversity"]
    tracklet_id = str(item.get("tracklet_id") or "")
    if tracklet_id and tracklet_id not in used_tracklets:
        item["selection_reasons"] = [*item["selection_reasons"], "tracklet_diversity"]
    selected.append(item)
    used_frames.add(int(item["frame"]))
    if tracklet_id:
        used_tracklets.add(tracklet_id)


def _diverse_score(
    row: dict[str, Any],
    selected: list[dict[str, Any]],
    used_tracklets: set[str],
    duration: int,
    parameters: dict[str, Any],
) -> float:
    if selected:
        min_distance = min(abs(int(row["frame"]) - int(item["frame"])) for item in selected)
        temporal = min(1.0, min_distance / max(1.0, duration / 2.0))
    else:
        temporal = 1.0
    tracklet_id = str(row.get("tracklet_id") or "")
    new_tracklet = 1.0 if tracklet_id and tracklet_id not in used_tracklets else 0.0
    return (
        float(row["base_score"])
        + float(parameters["temporal_diversity_weight"]) * temporal
        + float(parameters["tracklet_diversity_bonus"]) * new_tracklet
    )


def _occlusion_index(
    occlusion_doc: dict[str, Any],
    *,
    parameters: dict[str, Any],
) -> dict[str, list[tuple[int, int]]]:
    padding = int(parameters["occlusion_padding_frames"])
    index: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for event in occlusion_doc.get("events") or []:
        start = int(event.get("start_frame") or 0) - padding
        end = int(event.get("end_frame") or start) + padding
        for tracklet_id in event.get("tracklet_ids") or []:
            index[str(tracklet_id)].append((start, end))
    return {key: _merge_intervals(value) for key, value in index.items()}


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _summary(cards: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(card.get("status") or "unknown") for card in cards)
    rejection_counts: Counter[str] = Counter()
    for card in cards:
        rejection_counts.update(card.get("rejected_observations") or {})
    return {
        "cards": len(cards),
        "cards_ready_for_visual_audit": statuses.get("ready_for_visual_audit", 0),
        "cards_with_insufficient_reliable_crops": statuses.get("insufficient_reliable_crops", 0),
        "cards_without_reliable_crops": statuses.get("no_reliable_crops", 0),
        "selected_crops": sum(int(card.get("selected_crop_count") or 0) for card in cards),
        "eligible_observations": sum(int(card.get("eligible_observations") or 0) for card in cards),
        "status_counts": dict(sorted(statuses.items())),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "automatic_assignments": 0,
        "eligible_for_player_stats": 0,
    }


def _artifact_path(subject_id: str, index: int, frame: int) -> str:
    safe_subject = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in subject_id)
    return f"anchor_crops/{safe_subject}/{index:02d}_f{frame:06d}.jpg"


def _anchor_crop_id(
    subject_id: str,
    frame: int,
    source_match_key: Any,
    source_video_key: Any,
    team_id: Any,
) -> str:
    payload = json.dumps(
        {
            "candidate_subject_id": subject_id,
            "frame": frame,
            "source_match_key": source_match_key,
            "source_video_key": source_video_key,
            "team_id": team_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"anchor-crop:v3:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _round_or_none(value: Any, digits: int) -> float | None:
    return round(float(value), digits) if isinstance(value, (int, float)) else None
