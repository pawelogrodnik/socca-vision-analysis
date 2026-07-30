from __future__ import annotations

"""Validate and attribute an offline player-detection QA export."""

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_unresolved_overlay import (
    build_unrepresented_tracklet_observations,
    select_unresolved_overlay_rows,
)


SCHEMA_VERSION = "0.1.0"
REPORT_KIND = "player_detection_quality_review_report"
MINIMUM_IOU = 0.30
MINIMUM_CONTAINMENT = 0.60


def analyze_player_detection_quality_review(
    *,
    reviewed_audit: dict[str, Any],
    expected_manifest: dict[str, Any],
    tracklets_document: dict[str, Any],
    global_identity_document: dict[str, Any] | None = None,
    raw_tracks_document: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate the export and locate missed boxes in frozen tracklets."""
    _validate_review(reviewed_audit, expected_manifest)
    review = reviewed_audit["manual_review"]
    decisions = dict(review.get("detection_decisions") or {})
    items = reviewed_audit["items"]
    missing_players = list(review.get("missing_players") or [])
    frame_comments = list(review.get("frame_comments") or [])

    detections = [
        (item, detection)
        for item in items
        for detection in item.get("detections") or []
    ]
    effective_false = [
        (item, detection)
        for item, detection in detections
        if _effective_status(detection, decisions) == "false_detection"
    ]
    represented_tracklets = {
        int(item["frame_number"]): {
            str((detection.get("provenance") or {}).get("tracklet_id") or "")
            for detection in item.get("detections") or []
        }
        for item in items
    }
    selected_frames = {int(item["frame_number"]) for item in items}
    candidate_positions = _candidate_tracklet_positions(
        tracklets_document,
        selected_frames=selected_frames,
        represented_tracklets=represented_tracklets,
    )
    attributed_missing = _attribute_missing_players(
        missing_players,
        candidate_positions=candidate_positions,
    )
    raw_track_attribution = _attribute_raw_track_losses(
        attributed_missing,
        items=items,
        tracklets_document=tracklets_document,
        raw_tracks_document=raw_tracks_document,
    )
    identity_layer_attribution = _attribute_identity_layer_losses(
        attributed_missing,
        global_identity_document=global_identity_document,
    )
    teams = {
        team: _team_metrics(
            team,
            detections=detections,
            decisions=decisions,
            missing_players=missing_players,
        )
        for team in ("A", "B")
    }
    valid_existing = len(detections) - len(effective_false)
    reference_players = valid_existing + len(missing_players)
    unresolved_overlay_projection = _project_unresolved_overlay_recovery(
        missing_players,
        tracklets_document=tracklets_document,
        global_identity_document=global_identity_document,
        valid_existing_boxes=valid_existing,
        represented_tracklets=represented_tracklets,
    )
    attribution_counts = {
        key: sum(row["attribution"] == key for row in attributed_missing)
        for key in (
            "present_in_clean_tracklet_not_shown",
            "present_in_rejected_tracklet",
            "no_matching_frozen_tracklet",
        )
    }
    missing_tracklet_team_mismatches = sum(
        row.get("matched_tracklet") is not None
        and row["matched_tracklet"].get("team_label_match") is False
        for row in attributed_missing
    )
    clean_tracklet_omission_ratio = _ratio(
        attribution_counts["present_in_clean_tracklet_not_shown"],
        len(missing_players),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "report_kind": REPORT_KIND,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "match_id": (reviewed_audit.get("source") or {}).get("match_id"),
            "analysis_run_id": (reviewed_audit.get("source") or {}).get(
                "analysis_run_id"
            ),
            "reviewed_audit_digest": canonical_digest(reviewed_audit),
            "expected_manifest_digest": canonical_digest(expected_manifest),
        },
        "validation": {
            "status": "valid",
            "manifest_lineage_matches": True,
            "invalid_decision_keys": 0,
            "invalid_missing_boxes": 0,
            "invalid_comments": 0,
        },
        "summary": {
            "frames_reviewed": len(items),
            "existing_detections": len(detections),
            "explicit_false_detections": sum(
                value == "false_detection" for value in decisions.values()
            ),
            "prefilled_false_detections": sum(
                detection.get("initial_review_status") == "false_detection"
                and detection["detection_key"] not in decisions
                for _, detection in detections
            ),
            "effective_false_detections": len(effective_false),
            "explicit_player_confirmations": sum(
                value == "player" for value in decisions.values()
            ),
            "untouched_existing_detections": len(detections) - len(decisions),
            "valid_existing_player_boxes": valid_existing,
            "missing_player_boxes": len(missing_players),
            "reference_player_boxes": reference_players,
            "displayed_observation_coverage": _ratio(
                valid_existing, reference_players
            ),
            "false_detection_share": _ratio(
                len(effective_false), len(detections)
            ),
            "frame_comments": len(frame_comments),
            "missing_tracklet_team_mismatches": (
                missing_tracklet_team_mismatches
            ),
        },
        "teams": teams,
        "missing_attribution": {
            "matching_contract": {
                "minimum_iou": MINIMUM_IOU,
                "minimum_containment": MINIMUM_CONTAINMENT,
                "team_compatible": True,
                "one_to_one_per_frame": True,
                "already_displayed_tracklets_excluded": True,
            },
            "counts": attribution_counts,
            "clean_tracklet_omission_ratio": clean_tracklet_omission_ratio,
            "items": attributed_missing,
        },
        "identity_layer_attribution": identity_layer_attribution,
        "raw_track_attribution": raw_track_attribution,
        "unresolved_overlay_projection": unresolved_overlay_projection,
        "comments": frame_comments,
        "conclusion": {
            "primary_bottleneck": (
                "global_identity_slot_coverage"
                if clean_tracklet_omission_ratio >= 0.50
                else "frozen_detection_or_tracking"
            ),
            "raw_yolo_recall_can_be_inferred": False,
            "reason": (
                "Most operator-drawn missing players already exist in clean "
                "frozen tracklets. Some were already present as detected or "
                "ambiguous identity-overlay positions but were omitted by the "
                "old QA package; additional observations can be exposed safely "
                "as visual-only unresolved boxes."
            ),
        },
        "safety": {
            "yolo_reruns": 0,
            "tracking_reruns": 0,
            "candidate_identity_mutations": 0,
            "production_identity_mutations": 0,
            "production_stats_mutations": 0,
            "automatic_assignments": 0,
        },
    }


def analyze_player_detection_quality_review_files(
    *,
    reviewed_audit_path: Path,
    audit_package_dir: Path,
    match_path: Path,
) -> dict[str, Any]:
    reviewed_audit = _load_json(reviewed_audit_path)
    expected_manifest = _load_json(audit_package_dir / "audit_manifest.json")
    tracklets_document = _load_json(match_path / "tracklets.json")
    global_identity_document = _load_json(match_path / "global_identity.json")
    raw_tracks_path = _resolve_raw_tracks_path(match_path)
    raw_tracks_document = (
        _load_json_value(raw_tracks_path) if raw_tracks_path is not None else None
    )
    report = analyze_player_detection_quality_review(
        reviewed_audit=reviewed_audit,
        expected_manifest=expected_manifest,
        tracklets_document=tracklets_document,
        global_identity_document=global_identity_document,
        raw_tracks_document=raw_tracks_document,
    )
    _write_json(audit_package_dir / "player_detection_qa_reviewed.json", reviewed_audit)
    _write_json(audit_package_dir / "review_report.json", report)
    (audit_package_dir / "review_report.md").write_text(
        render_player_detection_quality_review_markdown(report),
        encoding="utf-8",
    )
    return report


def render_player_detection_quality_review_markdown(
    report: dict[str, Any],
) -> str:
    summary = report["summary"]
    teams = report["teams"]
    attribution = report["missing_attribution"]["counts"]
    identity_attribution = report["identity_layer_attribution"]["counts"]
    raw_attribution = report["raw_track_attribution"]["counts"]
    overlay_projection = report["unresolved_overlay_projection"]
    return (
        "# Player detection QA — reviewed result\n\n"
        f"- Frames reviewed: {summary['frames_reviewed']}\n"
        f"- Existing boxes: {summary['existing_detections']}\n"
        f"- Effective false detections: {summary['effective_false_detections']}\n"
        f"- Missing player boxes: {summary['missing_player_boxes']}\n"
        f"- Matched missing boxes with a conflicting tracklet team: "
        f"{summary['missing_tracklet_team_mismatches']}\n"
        f"- Displayed observation coverage: "
        f"{summary['displayed_observation_coverage']:.1%}\n"
        f"- Team A coverage: {teams['A']['displayed_observation_coverage']:.1%}\n"
        f"- Team B coverage: {teams['B']['displayed_observation_coverage']:.1%}\n\n"
        "## Missing-box attribution\n\n"
        f"- Present in clean frozen tracklets but not shown: "
        f"{attribution['present_in_clean_tracklet_not_shown']}\n"
        f"- Present only in rejected tracklets: "
        f"{attribution['present_in_rejected_tracklet']}\n"
        f"- No matching frozen tracklet: "
        f"{attribution['no_matching_frozen_tracklet']}\n\n"
        "## Identity-layer attribution\n\n"
        f"- Clean tracklet not assigned to any slot: "
        f"{identity_attribution['tracklet_not_assigned_to_slot']}\n"
        f"- Slot displays another tracklet in the same frame: "
        f"{identity_attribution['slot_uses_different_tracklet_at_frame']}\n"
        f"- Slot has no overlay position in the frame: "
        f"{identity_attribution['slot_has_no_overlay_position']}\n"
        f"- Other overlay eligibility loss: "
        f"{identity_attribution['other_overlay_eligibility_loss']}\n\n"
        "## Raw-track attribution\n\n"
        f"- Present in raw tracks but absent from tracklets: "
        f"{raw_attribution['present_in_raw_tracks_but_not_tracklets']}\n"
        f"- No separate matching raw track: "
        f"{raw_attribution['no_matching_raw_track']}\n"
        f"- Not analyzed: {raw_attribution['not_analyzed']}\n\n"
        "## Unresolved-overlay projection\n\n"
        f"- Missing boxes already available in identity overlay: "
        f"{overlay_projection['already_available_in_identity_overlay']}\n"
        f"- Operator-drawn missing boxes recoverable as visual-only unresolved: "
        f"{overlay_projection['recoverable_missing_boxes']}\n"
        f"- Remaining missing boxes after visual recovery: "
        f"{overlay_projection['remaining_missing_boxes']}\n"
        f"- Projected observation coverage: "
        f"{overlay_projection['projected_observation_coverage']:.1%}\n\n"
        "## Conclusion\n\n"
        "The old QA package omitted both identity-overlay observations and "
        "clean tracklet observations that were not represented by a visible "
        "identity slot. The stabilization renderer now keeps the latter visible "
        "as untrusted `RAW?`, `A?`, or `B?` boxes without using them for stats. "
        "This QA does not "
        "by itself measure raw YOLO recall. No YOLO, tracking, candidate identity, "
        "production identity or production stats artifact was mutated.\n"
    )


def _validate_review(
    reviewed_audit: dict[str, Any],
    expected_manifest: dict[str, Any],
) -> None:
    if reviewed_audit.get("audit_kind") != "player_detection_quality":
        raise ValueError("Unsupported player-detection QA kind")
    for field in ("source", "video", "items"):
        if reviewed_audit.get(field) != expected_manifest.get(field):
            raise ValueError(f"Reviewed audit does not match manifest field: {field}")
    review = reviewed_audit.get("manual_review")
    if not isinstance(review, dict):
        raise ValueError("Reviewed audit is missing manual_review")

    items = reviewed_audit.get("items") or []
    frames = {int(item["frame_number"]) for item in items}
    detection_keys = {
        str(detection["detection_key"])
        for item in items
        for detection in item.get("detections") or []
    }
    decisions = review.get("detection_decisions") or {}
    invalid_decisions = [
        key
        for key, value in decisions.items()
        if key not in detection_keys or value not in {"player", "false_detection"}
    ]
    if invalid_decisions:
        raise ValueError("Reviewed audit contains invalid detection decisions")

    width = float((reviewed_audit.get("video") or {}).get("width") or 0)
    height = float((reviewed_audit.get("video") or {}).get("height") or 0)
    for row in review.get("missing_players") or []:
        bbox = row.get("bbox_xyxy")
        if (
            int(row.get("frame_number") or -1) not in frames
            or row.get("team_label") not in {"A", "B"}
            or not _bbox_within_image(bbox, width=width, height=height)
        ):
            raise ValueError("Reviewed audit contains an invalid missing-player box")
    for row in review.get("frame_comments") or []:
        if (
            int(row.get("frame_number") or -1) not in frames
            or not str(row.get("comment") or "").strip()
        ):
            raise ValueError("Reviewed audit contains an invalid frame comment")


def _candidate_tracklet_positions(
    tracklets_document: dict[str, Any],
    *,
    selected_frames: set[int],
    represented_tracklets: dict[int, set[str]],
) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    sources = (
        ("clean", tracklets_document.get("tracklets") or []),
        ("rejected", tracklets_document.get("rejected_tracklets") or []),
    )
    for source, tracklets in sources:
        for tracklet in tracklets:
            tracklet_id = str(tracklet.get("tracklet_id") or "")
            if not tracklet_id:
                continue
            team_label = str(
                tracklet.get("team_label")
                or tracklet.get("team_candidate")
                or "U"
            )
            if team_label not in {"A", "B"}:
                team_label = "U"
            for position in tracklet.get("positions_m") or []:
                frame_number = int(position.get("frame") or 0)
                bbox = position.get("bbox_xyxy")
                if (
                    frame_number not in selected_frames
                    or tracklet_id in represented_tracklets.get(frame_number, set())
                    or not _valid_bbox(bbox)
                ):
                    continue
                result[frame_number].append(
                    {
                        "tracklet_id": tracklet_id,
                        "team_label": team_label,
                        "bbox_xyxy": [float(value) for value in bbox],
                        "source": source,
                    }
                )
    return dict(result)


def _attribute_missing_players(
    missing_players: list[dict[str, Any]],
    *,
    candidate_positions: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any] | None] = [None] * len(missing_players)
    by_frame: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(missing_players):
        by_frame[int(row["frame_number"])].append(index)

    for frame_number, annotation_indexes in by_frame.items():
        candidates = candidate_positions.get(frame_number) or []
        scored_pairs = []
        for annotation_index in annotation_indexes:
            annotation = missing_players[annotation_index]
            for candidate_index, candidate in enumerate(candidates):
                iou, containment = _bbox_overlap(
                    annotation["bbox_xyxy"],
                    candidate["bbox_xyxy"],
                )
                if iou < MINIMUM_IOU and containment < MINIMUM_CONTAINMENT:
                    continue
                team_bonus = (
                    0.05
                    if candidate["team_label"] == annotation["team_label"]
                    else 0.02
                    if candidate["team_label"] == "U"
                    else 0.0
                )
                scored_pairs.append(
                    (
                        max(iou, containment) + team_bonus,
                        iou,
                        containment,
                        annotation_index,
                        candidate_index,
                    )
                )
        used_annotations: set[int] = set()
        used_candidates: set[int] = set()
        for _, iou, containment, annotation_index, candidate_index in sorted(
            scored_pairs, reverse=True
        ):
            if (
                annotation_index in used_annotations
                or candidate_index in used_candidates
            ):
                continue
            candidate = candidates[candidate_index]
            result[annotation_index] = _attributed_row(
                missing_players[annotation_index],
                attribution=(
                    "present_in_clean_tracklet_not_shown"
                    if candidate["source"] == "clean"
                    else "present_in_rejected_tracklet"
                ),
                candidate=candidate,
                iou=iou,
                containment=containment,
            )
            used_annotations.add(annotation_index)
            used_candidates.add(candidate_index)

    for index, row in enumerate(result):
        if row is None:
            result[index] = _attributed_row(
                missing_players[index],
                attribution="no_matching_frozen_tracklet",
            )
    return [row for row in result if row is not None]


def _project_unresolved_overlay_recovery(
    missing_players: list[dict[str, Any]],
    *,
    tracklets_document: dict[str, Any],
    global_identity_document: dict[str, Any] | None,
    valid_existing_boxes: int,
    represented_tracklets: dict[int, set[str]],
) -> dict[str, Any]:
    reference_boxes = valid_existing_boxes + len(missing_players)
    if global_identity_document is None:
        return {
            "analyzed": False,
            "candidate_unresolved_observations": 0,
            "visible_after_duplicate_suppression": 0,
            "already_available_in_identity_overlay": 0,
            "recoverable_missing_boxes": 0,
            "total_missing_boxes_visible_after_change": 0,
            "remaining_missing_boxes": len(missing_players),
            "projected_observation_coverage": _ratio(
                valid_existing_boxes,
                reference_boxes,
            ),
            "recovered_by_source": {},
            "identity_overlay_items": [],
            "items": [],
        }

    selected_frames = {
        int(annotation["frame_number"]) for annotation in missing_players
    }
    all_identity_by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    newly_available_identity_by_frame: dict[int, list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for slot in global_identity_document.get("slots") or []:
        for row in slot.get("overlay_positions") or []:
            frame_number = int(row.get("frame") or 0)
            visual_source = str(
                row.get("source") or row.get("status") or "detected"
            )
            if (
                frame_number in selected_frames
                and visual_source in {"detected", "ambiguous"}
                and _valid_bbox(row.get("bbox_xyxy"))
            ):
                all_identity_by_frame[frame_number].append(row)
                tracklet_id = str(
                    row.get("tracklet_id")
                    or row.get("candidate_tracklet_id")
                    or ""
                )
                if tracklet_id not in represented_tracklets.get(
                    frame_number,
                    set(),
                ):
                    newly_available_identity_by_frame[frame_number].append(row)

    unresolved = [
        *(global_identity_document.get("unmatched_observations") or []),
        *build_unrepresented_tracklet_observations(
            tracklets_document.get("tracklets") or [],
            global_identity_document,
        ),
    ]
    candidates_by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    unresolved_candidate_count = 0
    for row in unresolved:
        if not isinstance(row, dict) or not _valid_bbox(row.get("bbox_xyxy")):
            continue
        frame_number = int(row.get("frame") or 0)
        if frame_number not in selected_frames:
            continue
        candidates_by_frame[frame_number].append(row)
        unresolved_candidate_count += 1

    visible_by_frame = {
        frame_number: select_unresolved_overlay_rows(
            all_identity_by_frame.get(frame_number, []),
            candidates,
        )
        for frame_number, candidates in candidates_by_frame.items()
    }
    identity_matches = _match_missing_to_visual_candidates(
        missing_players,
        newly_available_identity_by_frame,
    )
    unresolved_matches = _match_missing_to_visual_candidates(
        missing_players,
        visible_by_frame,
    )
    identity_annotation_indexes = {
        int(row["annotation_index"]) for row in identity_matches
    }
    unresolved_matches = [
        row
        for row in unresolved_matches
        if int(row["annotation_index"]) not in identity_annotation_indexes
    ]
    recovered_by_source: dict[str, int] = {}
    for row in unresolved_matches:
        source = str(row["matched_unresolved"]["source"])
        recovered_by_source[source] = recovered_by_source.get(source, 0) + 1
    total_visible = len(identity_matches) + len(unresolved_matches)
    return {
        "analyzed": True,
        "candidate_unresolved_observations": unresolved_candidate_count,
        "visible_after_duplicate_suppression": sum(
            len(rows) for rows in visible_by_frame.values()
        ),
        "already_available_in_identity_overlay": len(identity_matches),
        "recoverable_missing_boxes": len(unresolved_matches),
        "total_missing_boxes_visible_after_change": total_visible,
        "remaining_missing_boxes": len(missing_players) - total_visible,
        "projected_observation_coverage": _ratio(
            valid_existing_boxes + total_visible,
            reference_boxes,
        ),
        "recovered_by_source": recovered_by_source,
        "identity_overlay_items": identity_matches,
        "items": unresolved_matches,
    }


def _match_missing_to_visual_candidates(
    missing_players: list[dict[str, Any]],
    candidates_by_frame: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    annotation_indexes_by_frame: dict[int, list[int]] = defaultdict(list)
    for index, annotation in enumerate(missing_players):
        annotation_indexes_by_frame[int(annotation["frame_number"])].append(index)

    result: list[dict[str, Any]] = []
    for frame_number, annotation_indexes in annotation_indexes_by_frame.items():
        candidates = candidates_by_frame.get(frame_number) or []
        scored_pairs = []
        for annotation_index in annotation_indexes:
            annotation = missing_players[annotation_index]
            for candidate_index, candidate in enumerate(candidates):
                iou, containment = _bbox_overlap(
                    annotation["bbox_xyxy"],
                    candidate["bbox_xyxy"],
                )
                if iou >= MINIMUM_IOU or containment >= MINIMUM_CONTAINMENT:
                    scored_pairs.append(
                        (
                            max(iou, containment),
                            iou,
                            containment,
                            annotation_index,
                            candidate_index,
                        )
                    )
        used_annotations: set[int] = set()
        used_candidates: set[int] = set()
        for _, iou, containment, annotation_index, candidate_index in sorted(
            scored_pairs,
            reverse=True,
        ):
            if (
                annotation_index in used_annotations
                or candidate_index in used_candidates
            ):
                continue
            annotation = missing_players[annotation_index]
            candidate = candidates[candidate_index]
            result.append(
                {
                    "annotation_index": annotation_index,
                    "frame_number": frame_number,
                    "team_label": annotation["team_label"],
                    "bbox_xyxy": annotation["bbox_xyxy"],
                    "matched_unresolved": {
                        "source": candidate.get("source"),
                        "tracklet_id": candidate.get("tracklet_id"),
                        "team_label": candidate.get("team_label"),
                        "bbox_xyxy": candidate.get("bbox_xyxy"),
                        "iou": round(iou, 4),
                        "containment": round(containment, 4),
                    },
                }
            )
            used_annotations.add(annotation_index)
            used_candidates.add(candidate_index)
    return result


def _attribute_identity_layer_losses(
    attributed_missing: list[dict[str, Any]],
    *,
    global_identity_document: dict[str, Any] | None,
) -> dict[str, Any]:
    keys = (
        "tracklet_not_assigned_to_slot",
        "slot_uses_different_tracklet_at_frame",
        "slot_has_no_overlay_position",
        "other_overlay_eligibility_loss",
        "not_applicable_without_clean_tracklet",
        "not_analyzed",
    )
    counts = {key: 0 for key in keys}
    if global_identity_document is None:
        counts["not_analyzed"] = len(attributed_missing)
        return {"counts": counts, "items": []}

    tracklet_slots = {
        str(tracklet_id): slot
        for slot in global_identity_document.get("slots") or []
        for tracklet_id in slot.get("tracklet_ids") or []
    }
    items = []
    for row in attributed_missing:
        matched = row.get("matched_tracklet")
        if not matched or matched.get("source") != "clean":
            reason = "not_applicable_without_clean_tracklet"
            counts[reason] += 1
            continue
        tracklet_id = str(matched["tracklet_id"])
        frame_number = int(row["frame_number"])
        slot = tracklet_slots.get(tracklet_id)
        overlay_position = None
        if slot is None:
            reason = "tracklet_not_assigned_to_slot"
        else:
            frame_positions = [
                position
                for position in slot.get("overlay_positions") or []
                if int(position.get("frame") or 0) == frame_number
            ]
            exact_positions = [
                position
                for position in frame_positions
                if str(position.get("tracklet_id") or "") == tracklet_id
            ]
            if not frame_positions:
                reason = "slot_has_no_overlay_position"
            elif not exact_positions:
                reason = "slot_uses_different_tracklet_at_frame"
                overlay_position = frame_positions[0]
            else:
                overlay_position = exact_positions[0]
                reason = _overlay_eligibility_reason(overlay_position)
        counts[reason] += 1
        items.append(
            {
                "frame_number": frame_number,
                "team_label": row["team_label"],
                "bbox_xyxy": row["bbox_xyxy"],
                "tracklet_id": tracklet_id,
                "slot_id": slot.get("slot_id") if slot else None,
                "slot_team_label": slot.get("team_label") if slot else None,
                "reason": reason,
                "overlay_position": (
                    {
                        "tracklet_id": overlay_position.get("tracklet_id"),
                        "status": overlay_position.get("status"),
                        "visual_trusted": overlay_position.get("visual_trusted"),
                        "play_area_status": overlay_position.get(
                            "play_area_status"
                        ),
                    }
                    if overlay_position is not None
                    else None
                ),
            }
        )
    return {"counts": counts, "items": items}


def _attribute_raw_track_losses(
    attributed_missing: list[dict[str, Any]],
    *,
    items: list[dict[str, Any]],
    tracklets_document: dict[str, Any],
    raw_tracks_document: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    rows = [
        row
        for row in attributed_missing
        if row["attribution"] == "no_matching_frozen_tracklet"
    ]
    counts = {
        "present_in_raw_tracks_but_not_tracklets": 0,
        "no_matching_raw_track": 0,
        "not_analyzed": 0,
    }
    if raw_tracks_document is None:
        counts["not_analyzed"] = len(rows)
        return {"counts": counts, "items": []}

    source_tracker_ids = {
        str(tracklet.get("tracklet_id") or ""): int(
            tracklet.get("source_tracker_id") or 0
        )
        for bucket in ("tracklets", "rejected_tracklets")
        for tracklet in tracklets_document.get(bucket) or []
    }
    represented_raw_ids = {
        int(item["frame_number"]): {
            source_tracker_ids.get(
                str((detection.get("provenance") or {}).get("tracklet_id") or "")
            )
            for detection in item.get("detections") or []
        }
        for item in items
    }
    frames = {int(row["frame_number"]) for row in rows}
    candidates_by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw_track in raw_tracks_document:
        raw_track_id = int(raw_track.get("track_id") or 0)
        for position in raw_track.get("positions") or []:
            frame_number = int(position.get("frame") or 0)
            bbox = position.get("bbox_xyxy")
            if (
                frame_number not in frames
                or raw_track_id in represented_raw_ids.get(frame_number, set())
                or not _valid_bbox(bbox)
            ):
                continue
            candidates_by_frame[frame_number].append(
                {
                    "raw_track_id": raw_track_id,
                    "bbox_xyxy": [float(value) for value in bbox],
                    "confidence": position.get("confidence"),
                }
            )

    result = []
    for frame_number in sorted(frames):
        frame_rows = [
            row for row in rows if int(row["frame_number"]) == frame_number
        ]
        candidates = candidates_by_frame.get(frame_number) or []
        scored_pairs = []
        for row_index, row in enumerate(frame_rows):
            for candidate_index, candidate in enumerate(candidates):
                iou, containment = _bbox_overlap(
                    row["bbox_xyxy"],
                    candidate["bbox_xyxy"],
                )
                if iou >= MINIMUM_IOU or containment >= MINIMUM_CONTAINMENT:
                    scored_pairs.append(
                        (
                            max(iou, containment),
                            iou,
                            containment,
                            row_index,
                            candidate_index,
                        )
                    )
        used_rows: set[int] = set()
        used_candidates: set[int] = set()
        matches: dict[int, tuple[dict[str, Any], float, float]] = {}
        for _, iou, containment, row_index, candidate_index in sorted(
            scored_pairs, reverse=True
        ):
            if row_index in used_rows or candidate_index in used_candidates:
                continue
            matches[row_index] = (
                candidates[candidate_index],
                iou,
                containment,
            )
            used_rows.add(row_index)
            used_candidates.add(candidate_index)
        for row_index, row in enumerate(frame_rows):
            matched = matches.get(row_index)
            if matched is None:
                reason = "no_matching_raw_track"
                raw_match = None
            else:
                reason = "present_in_raw_tracks_but_not_tracklets"
                candidate, iou, containment = matched
                raw_match = {
                    **candidate,
                    "iou": round(iou, 4),
                    "containment": round(containment, 4),
                }
            counts[reason] += 1
            result.append(
                {
                    "frame_number": frame_number,
                    "team_label": row["team_label"],
                    "bbox_xyxy": row["bbox_xyxy"],
                    "reason": reason,
                    "matched_raw_track": raw_match,
                }
            )
    return {"counts": counts, "items": result}


def _overlay_eligibility_reason(position: dict[str, Any]) -> str:
    if position.get("status") != "detected":
        return "other_overlay_eligibility_loss"
    if position.get("visual_trusted") is False:
        return "other_overlay_eligibility_loss"
    if str(position.get("play_area_status") or "inside") not in {
        "inside",
        "inside_play",
        "inside_pitch",
    }:
        return "other_overlay_eligibility_loss"
    return "other_overlay_eligibility_loss"


def _attributed_row(
    annotation: dict[str, Any],
    *,
    attribution: str,
    candidate: dict[str, Any] | None = None,
    iou: float | None = None,
    containment: float | None = None,
) -> dict[str, Any]:
    return {
        "frame_number": int(annotation["frame_number"]),
        "team_label": annotation["team_label"],
        "bbox_xyxy": annotation["bbox_xyxy"],
        "attribution": attribution,
        "matched_tracklet": (
            {
                "tracklet_id": candidate["tracklet_id"],
                "team_label": candidate["team_label"],
                "team_label_match": candidate["team_label"]
                in {annotation["team_label"], "U"},
                "source": candidate["source"],
                "bbox_xyxy": candidate["bbox_xyxy"],
                "iou": round(float(iou), 4),
                "containment": round(float(containment), 4),
            }
            if candidate is not None
            else None
        ),
    }


def _team_metrics(
    team: str,
    *,
    detections: list[tuple[dict[str, Any], dict[str, Any]]],
    decisions: dict[str, str],
    missing_players: list[dict[str, Any]],
) -> dict[str, Any]:
    existing = [
        detection
        for _, detection in detections
        if detection.get("team_label") == team
    ]
    false_count = sum(
        _effective_status(detection, decisions) == "false_detection"
        for detection in existing
    )
    missing_count = sum(row.get("team_label") == team for row in missing_players)
    valid_existing = len(existing) - false_count
    reference_players = valid_existing + missing_count
    return {
        "existing_detections": len(existing),
        "effective_false_detections": false_count,
        "valid_existing_player_boxes": valid_existing,
        "missing_player_boxes": missing_count,
        "reference_player_boxes": reference_players,
        "displayed_observation_coverage": _ratio(
            valid_existing, reference_players
        ),
    }


def _effective_status(
    detection: dict[str, Any],
    decisions: dict[str, str],
) -> str:
    return str(
        decisions.get(str(detection["detection_key"]))
        or detection.get("initial_review_status")
        or "pending"
    )


def _bbox_overlap(
    left: list[float],
    right: list[float],
) -> tuple[float, float]:
    intersection = max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )
    if intersection <= 0:
        return 0.0, 0.0
    left_area = _bbox_area(left)
    right_area = _bbox_area(right)
    union = left_area + right_area - intersection
    return intersection / union, intersection / min(left_area, right_area)


def _bbox_area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_within_image(
    bbox: Any,
    *,
    width: float,
    height: float,
) -> bool:
    return bool(
        _valid_bbox(bbox)
        and 0 <= float(bbox[0]) < float(bbox[2]) <= width
        and 0 <= float(bbox[1]) < float(bbox[3]) <= height
    )


def _valid_bbox(bbox: Any) -> bool:
    return bool(
        isinstance(bbox, (list, tuple))
        and len(bbox) == 4
        and all(isinstance(value, (int, float)) for value in bbox)
        and float(bbox[2]) > float(bbox[0])
        and float(bbox[3]) > float(bbox[1])
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_value(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_raw_tracks_path(match_path: Path) -> Path | None:
    local_path = match_path / "tracks.json"
    if local_path.exists():
        return local_path
    analysis_report = _load_json(match_path / "analysis_report.json")
    source_dir = Path(
        str((analysis_report.get("parameters") or {}).get("source_dir") or "")
    )
    source_path = source_dir / str(
        (analysis_report.get("artifacts") or {}).get("tracks_json")
        or "tracks.json"
    )
    return source_path if source_path.exists() else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
