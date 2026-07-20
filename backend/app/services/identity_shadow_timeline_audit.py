from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_stitching_audit import (
    BACKGROUND,
    MUTED,
    SOURCE_COLOR,
    TARGET_COLOR,
    TEXT,
    TRANSITION_COLOR,
    _fit_image,
    _read_frame,
    _title_bar,
    _write_contact_sheets,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_shadow_timeline_visual_audit"
ALGORITHM_VERSION = "0.1.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_shadow_timeline_audit_manifest(
    timeline_doc: dict[str, Any],
    tracklets_doc: dict[str, Any],
    *,
    benchmark_id: str,
    benchmark_label: str,
    video_path: str,
    video_time_offset_sec: float = 0.0,
    direct_control_limit: int = 4,
    missing_control_limit: int = 4,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Select a compact, deterministic event-level audit for the P1.4 timeline."""
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_doc.get("tracklets") or []
        if row.get("tracklet_id") is not None
    }
    transition_candidates = _transition_candidates(
        timeline_doc,
        direct_control_limit=max(0, int(direct_control_limit)),
    )
    represented_ranges = {
        (
            str(row.get("shadow_subject_id") or ""),
            int(row.get("start_frame") or 0),
            int(row.get("end_frame") or 0),
            str(row.get("status") or ""),
        )
        for row in transition_candidates
    }
    gap_candidates = _gap_candidates(
        timeline_doc,
        represented_ranges=represented_ranges,
        missing_control_limit=max(0, int(missing_control_limit)),
    )
    candidates = transition_candidates + gap_candidates

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in candidates:
        endpoints = _candidate_endpoints(candidate, timeline_doc, tracklets)
        if endpoints is None:
            skipped.append(
                {
                    "audit_key": candidate["audit_key"],
                    "reason": "missing_endpoint_observation",
                    "audit_kind": candidate["audit_kind"],
                }
            )
            continue
        source, target = endpoints
        source_payload = _endpoint_payload(source, video_time_offset_sec)
        target_payload = _endpoint_payload(target, video_time_offset_sec)
        midpoint_source_time = (
            float(source_payload["source_time_sec"])
            + float(target_payload["source_time_sec"])
        ) / 2.0
        item_index = len(items) + 1
        items.append(
            {
                "audit_index": item_index,
                "audit_key": candidate["audit_key"],
                "audit_kind": candidate["audit_kind"],
                "selection_reason": candidate["selection_reason"],
                "card_filename": f"{item_index:03d}-{_short_key(candidate['audit_key'])}.jpg",
                "shadow_subject_id": candidate.get("shadow_subject_id"),
                "team_label": candidate.get("team_label"),
                "source": source_payload,
                "target": target_payload,
                "timeline_state": {
                    "status": candidate.get("status"),
                    "start_frame": candidate.get("start_frame"),
                    "end_frame": candidate.get("end_frame"),
                    "frame_count": candidate.get("frame_count"),
                    "duration_sec": candidate.get("duration_sec"),
                    "reason": candidate.get("reason"),
                    "edge_key": candidate.get("edge_key"),
                    "occlusion_event_ids": candidate.get("occlusion_event_ids") or [],
                    "current_identity_relation": candidate.get("current_identity_relation"),
                    "requires_review": bool(candidate.get("requires_review")),
                    "midpoint_source_time_sec": round(midpoint_source_time, 3),
                    "midpoint_video_time_sec": round(
                        max(0.0, midpoint_source_time - video_time_offset_sec),
                        3,
                    ),
                },
                "manual_review": {
                    "status": "pending",
                    "identity_continuity": None,
                    "state_assessment": None,
                    "reviewer": None,
                    "reviewed_at": None,
                    "notes": "",
                },
            }
        )

    status_counts = Counter(str(item["timeline_state"]["status"]) for item in items)
    kind_counts = Counter(str(item["audit_kind"]) for item in items)
    available_statuses = Counter(
        str(event.get("status") or "unknown")
        for event in timeline_doc.get("transition_events") or []
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "mode": "developer_visual_audit",
        "instructions": {
            "identity_continuity": "Decide whether the highlighted source and target are the same real person.",
            "state_assessment": "If identity is continuous, decide whether the proposed timeline state explains the gap.",
            "different_identity": "When people differ, state_assessment is automatically identity_link_invalid.",
        },
        "benchmark": {
            "benchmark_id": benchmark_id,
            "label": benchmark_label,
            "video_path": video_path,
            "video_time_offset_sec": round(float(video_time_offset_sec), 3),
        },
        "source": {
            "timeline_algorithm": timeline_doc.get("algorithm") or {},
            "direct_control_limit": max(0, int(direct_control_limit)),
            "missing_control_limit": max(0, int(missing_control_limit)),
            "available_transition_statuses": dict(sorted(available_statuses.items())),
        },
        "summary": {
            "review_items": len(items),
            "pending": len(items),
            "reviewed": 0,
            "skipped": len(skipped),
            "timeline_statuses": dict(sorted(status_counts.items())),
            "audit_kinds": dict(sorted(kind_counts.items())),
        },
        "items": items,
        "skipped": skipped,
    }


def build_shadow_timeline_delta_audit_manifest(
    manifest: dict[str, Any],
    goldset: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Select only reviewed mismatches and conservative identity abstentions."""
    benchmark_id = str((manifest.get("benchmark") or {}).get("benchmark_id") or "")
    error_keys = {
        (str(row.get("benchmark_id") or ""), str(row.get("audit_key") or ""))
        for row in evaluation.get("errors") or []
    }
    references = [
        row
        for row in goldset.get("items") or []
        if str(row.get("benchmark_id") or "") == benchmark_id
        and (
            (benchmark_id, str(row.get("audit_key") or "")) in error_keys
            or row.get("expected_same_person") is False
        )
    ]
    references_by_event = {
        str(row.get("audit_key") or ""): row
        for row in references
        if row.get("audit_kind") == "accepted_transition"
    }
    references_by_range = {
        (
            str(row.get("shadow_subject_id") or ""),
            int(row.get("start_frame") or 0),
            int(row.get("end_frame") or 0),
        ): row
        for row in references
        if row.get("audit_kind") != "accepted_transition"
    }

    selected: list[dict[str, Any]] = []
    matched_reference_keys: set[str] = set()
    for source_item in manifest.get("items") or []:
        state = source_item.get("timeline_state") or {}
        reference = (
            references_by_event.get(str(source_item.get("audit_key") or ""))
            if source_item.get("audit_kind") == "accepted_transition"
            else references_by_range.get(
                (
                    str(source_item.get("shadow_subject_id") or ""),
                    int(state.get("start_frame") or 0),
                    int(state.get("end_frame") or 0),
                )
            )
        )
        if reference is None:
            continue
        item = deepcopy(source_item)
        item["reference_expectation"] = {
            "source_audit_key": reference.get("audit_key"),
            "expected_same_person": reference.get("expected_same_person"),
            "expected_state": reference.get("expected_state"),
            "reviewer": reference.get("reviewer"),
        }
        selected.append(item)
        matched_reference_keys.add(str(reference.get("audit_key") or ""))

    selected.sort(
        key=lambda row: (
            int((row.get("timeline_state") or {}).get("start_frame") or 0),
            str(row.get("audit_key") or ""),
        )
    )
    for index, item in enumerate(selected, start=1):
        item["audit_index"] = index
        item["card_filename"] = f"{index:03d}-{_short_key(str(item['audit_key']))}.jpg"
        item["manual_review"] = {
            "status": "pending",
            "identity_continuity": None,
            "state_assessment": None,
            "reviewer": None,
            "reviewed_at": None,
            "notes": "",
        }

    result = deepcopy(manifest)
    result["generated_at"] = generated_at or now_iso()
    result["mode"] = "developer_visual_delta_audit"
    result["source"]["delta_goldset"] = {
        "goldset_id": goldset.get("goldset_id"),
        "version": goldset.get("version"),
        "digest": goldset.get("goldset_digest"),
    }
    result["source"]["delta_evaluation_algorithm"] = evaluation.get("algorithm") or {}
    result["summary"] = {
        "review_items": len(selected),
        "pending": len(selected),
        "reviewed": 0,
        "skipped": len(references) - len(matched_reference_keys),
        "timeline_statuses": dict(
            sorted(Counter(str((row.get("timeline_state") or {}).get("status")) for row in selected).items())
        ),
        "audit_kinds": dict(sorted(Counter(str(row.get("audit_kind")) for row in selected).items())),
    }
    result["items"] = selected
    result["skipped"] = [
        {"source_audit_key": row.get("audit_key"), "reason": "candidate_item_not_found"}
        for row in references
        if str(row.get("audit_key") or "") not in matched_reference_keys
    ]
    return result


def render_shadow_timeline_audit(
    manifest: dict[str, Any],
    *,
    video_path: Path,
    output_dir: Path,
    cards_per_sheet: int = 4,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cards_dir = output_dir / "cards"
    sheets_dir = output_dir / "contact_sheets"
    cards_dir.mkdir(exist_ok=True)
    sheets_dir.mkdir(exist_ok=True)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"Could not open audit video: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if fps <= 0 or frame_count <= 0:
        capture.release()
        raise ValueError(f"Invalid audit video metadata: {video_path}")

    cards: list[np.ndarray] = []
    try:
        for item in manifest.get("items") or []:
            source_frame = _read_frame(
                capture,
                float(item["source"]["video_time_sec"]),
                fps,
                frame_count,
            )
            midpoint_frame = _read_frame(
                capture,
                float(item["timeline_state"]["midpoint_video_time_sec"]),
                fps,
                frame_count,
            )
            target_frame = _read_frame(
                capture,
                float(item["target"]["video_time_sec"]),
                fps,
                frame_count,
            )
            card = _render_card(item, source_frame, midpoint_frame, target_frame)
            card_path = cards_dir / str(item["card_filename"])
            if not cv2.imwrite(str(card_path), card, [cv2.IMWRITE_JPEG_QUALITY, 93]):
                raise RuntimeError(f"Could not write audit card: {card_path}")
            cards.append(card)
    finally:
        capture.release()

    sheet_names = _write_contact_sheets(
        cards,
        sheets_dir,
        cards_per_sheet=max(1, int(cards_per_sheet)),
    )
    manifest["render"] = {
        "video_fps": round(fps, 6),
        "video_frames": frame_count,
        "cards": len(cards),
        "contact_sheets": sheet_names,
    }
    (output_dir / "audit_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(_render_html(manifest), encoding="utf-8")
    return manifest


def _transition_candidates(
    timeline_doc: dict[str, Any],
    *,
    direct_control_limit: int,
) -> list[dict[str, Any]]:
    transitions = sorted(
        timeline_doc.get("transition_events") or [],
        key=lambda row: (
            int(row.get("start_frame") or 0),
            str(row.get("event_key") or ""),
        ),
    )
    primary = [
        row
        for row in transitions
        if row.get("status") != "direct_transition" or row.get("requires_review")
    ]
    primary_keys = {str(row.get("event_key") or "") for row in primary}
    controls = [
        row
        for row in transitions
        if row.get("status") == "direct_transition"
        and str(row.get("event_key") or "") not in primary_keys
    ][:direct_control_limit]
    return [
        {
            **row,
            "audit_key": str(row.get("event_key") or ""),
            "audit_kind": "accepted_transition",
            "selection_reason": (
                "cross_production_transition"
                if row.get("requires_review")
                else "non_direct_transition"
                if row.get("status") != "direct_transition"
                else "direct_transition_control"
            ),
            "frame_count": max(
                0,
                int(row.get("target_frame") or 0) - int(row.get("source_frame") or 0) - 1,
            ),
            "duration_sec": None,
        }
        for row in primary + controls
    ]


def _gap_candidates(
    timeline_doc: dict[str, Any],
    *,
    represented_ranges: set[tuple[str, int, int, str]],
    missing_control_limit: int,
) -> list[dict[str, Any]]:
    primary: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for subject in timeline_doc.get("subjects") or []:
        subject_id = str(subject.get("shadow_subject_id") or "")
        for run in subject.get("state_runs") or []:
            status = str(run.get("status") or "")
            if status not in {"predicted", "occluded", "missing"}:
                continue
            range_key = (
                subject_id,
                int(run.get("start_frame") or 0),
                int(run.get("end_frame") or 0),
                status,
            )
            if range_key in represented_ranges:
                continue
            audit_key = _gap_key(subject_id, run)
            candidate = {
                **run,
                "audit_key": audit_key,
                "audit_kind": "internal_gap",
                "selection_reason": (
                    "all_predicted_or_occluded_internal_gaps"
                    if status in {"predicted", "occluded"}
                    else "missing_gap_control"
                ),
                "shadow_subject_id": subject_id,
                "team_label": subject.get("team_label"),
                "current_identity_relation": "same_shadow_subject",
                "requires_review": False,
            }
            if status == "missing":
                missing.append(candidate)
            else:
                primary.append(candidate)
    missing.sort(
        key=lambda row: (
            -int(row.get("frame_count") or 0),
            int(row.get("start_frame") or 0),
            str(row.get("audit_key") or ""),
        )
    )
    primary.sort(
        key=lambda row: (
            int(row.get("start_frame") or 0),
            str(row.get("audit_key") or ""),
        )
    )
    return primary + missing[:missing_control_limit]


def _candidate_endpoints(
    candidate: dict[str, Any],
    timeline_doc: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if candidate["audit_kind"] == "accepted_transition":
        source = _tracklet_position(
            tracklets.get(str(candidate.get("source_tracklet_id") or "")),
            int(candidate.get("source_frame") or 0),
            prefer_before=True,
        )
        target = _tracklet_position(
            tracklets.get(str(candidate.get("target_tracklet_id") or "")),
            int(candidate.get("target_frame") or 0),
            prefer_before=False,
        )
        return (source, target) if source is not None and target is not None else None

    subject = next(
        (
            row
            for row in timeline_doc.get("subjects") or []
            if str(row.get("shadow_subject_id") or "") == str(candidate.get("shadow_subject_id") or "")
        ),
        None,
    )
    if subject is None:
        return None
    observations = sorted(
        subject.get("observations") or [],
        key=lambda row: int(row.get("frame") or 0),
    )
    start_frame = int(candidate.get("start_frame") or 0)
    end_frame = int(candidate.get("end_frame") or 0)
    source_rows = [row for row in observations if int(row.get("frame") or 0) < start_frame]
    target_rows = [row for row in observations if int(row.get("frame") or 0) > end_frame]
    if not source_rows or not target_rows:
        return None
    return source_rows[-1], target_rows[0]


def _tracklet_position(
    tracklet: dict[str, Any] | None,
    frame: int,
    *,
    prefer_before: bool,
) -> dict[str, Any] | None:
    if tracklet is None:
        return None
    positions = sorted(
        tracklet.get("positions_m") or tracklet.get("positions") or [],
        key=lambda row: int(row.get("frame") or 0),
    )
    if not positions:
        return None
    exact = next((row for row in positions if int(row.get("frame") or 0) == frame), None)
    if exact is not None:
        return {**exact, "tracklet_id": tracklet.get("tracklet_id")}
    filtered = [
        row
        for row in positions
        if (int(row.get("frame") or 0) <= frame if prefer_before else int(row.get("frame") or 0) >= frame)
    ]
    selected = (filtered[-1] if prefer_before else filtered[0]) if filtered else min(
        positions,
        key=lambda row: abs(int(row.get("frame") or 0) - frame),
    )
    return {**selected, "tracklet_id": tracklet.get("tracklet_id")}


def _endpoint_payload(observation: dict[str, Any], video_time_offset_sec: float) -> dict[str, Any]:
    source_time = float(observation.get("time_sec") or 0.0)
    bbox = observation.get("bbox_xyxy")
    return {
        "tracklet_id": str(observation.get("tracklet_id") or ""),
        "frame": int(observation.get("frame") or 0),
        "source_time_sec": round(source_time, 3),
        "video_time_sec": round(max(0.0, source_time - video_time_offset_sec), 3),
        "bbox_xyxy": [int(round(float(value))) for value in bbox] if _is_bbox(bbox) else None,
        "pitch_m": observation.get("pitch_m") or observation.get("smoothed_pitch_m"),
        "confidence": observation.get("confidence"),
        "footpoint_reliable": observation.get("footpoint_reliable"),
        "appearance_reliable": observation.get("appearance_reliable"),
    }


def _render_card(
    item: dict[str, Any],
    source_frame: np.ndarray,
    midpoint_frame: np.ndarray,
    target_frame: np.ndarray,
) -> np.ndarray:
    panel_size = (800, 450)
    source_panel = _context_panel(source_frame, item["source"], "BEFORE", SOURCE_COLOR, panel_size)
    midpoint_panel = _midpoint_panel(midpoint_frame, item, panel_size)
    target_panel = _context_panel(target_frame, item["target"], "AFTER", TARGET_COLOR, panel_size)
    card = np.full((1080, 2400, 3), BACKGROUND, dtype=np.uint8)
    card[:450, :800] = source_panel
    card[:450, 800:1600] = midpoint_panel
    card[:450, 1600:] = target_panel
    card[480:1050, 20:620] = _crop_panel(
        source_frame,
        item["source"].get("bbox_xyxy"),
        "SOURCE PERSON",
        SOURCE_COLOR,
        (600, 570),
    )
    card[480:1050, 640:1240] = _crop_panel(
        target_frame,
        item["target"].get("bbox_xyxy"),
        "TARGET PERSON",
        TARGET_COLOR,
        (600, 570),
    )
    _draw_details(card, item, x=1280, y=515)
    return card


def _context_panel(
    frame: np.ndarray,
    endpoint: dict[str, Any],
    title: str,
    color: tuple[int, int, int],
    size: tuple[int, int],
) -> np.ndarray:
    height, width = frame.shape[:2]
    panel = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    bbox = endpoint.get("bbox_xyxy")
    if _is_bbox(bbox):
        sx, sy = size[0] / width, size[1] / height
        scaled = [int(bbox[0] * sx), int(bbox[1] * sy), int(bbox[2] * sx), int(bbox[3] * sy)]
        _thin_subject_marker(panel, scaled, color)
    _title_bar(
        panel,
        f"{title}  f{endpoint.get('frame')}  t={endpoint.get('source_time_sec')}s",
        color,
    )
    return panel


def _midpoint_panel(frame: np.ndarray, item: dict[str, Any], size: tuple[int, int]) -> np.ndarray:
    panel = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    state = item["timeline_state"]
    _title_bar(
        panel,
        (
            f"GAP / TRANSITION  {state.get('status')}  "
            f"f{state.get('start_frame')}..{state.get('end_frame')}"
        ),
        TRANSITION_COLOR,
    )
    return panel


def _crop_panel(
    frame: np.ndarray,
    bbox: Any,
    title: str,
    color: tuple[int, int, int],
    size: tuple[int, int],
) -> np.ndarray:
    output_width, output_height = size
    title_height = 42
    output = np.full((output_height, output_width, 3), BACKGROUND, dtype=np.uint8)
    if not _is_bbox(bbox):
        cv2.putText(output, "NO BBOX", (24, output_height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, MUTED, 2)
        return output
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
    x1, x2 = sorted((max(0, min(width - 1, x1)), max(1, min(width, x2))))
    y1, y2 = sorted((max(0, min(height - 1, y1)), max(1, min(height, y2))))
    box_width, box_height = max(1, x2 - x1), max(1, y2 - y1)
    pad_x, pad_y = max(42, int(box_width * 1.5)), max(34, int(box_height * 0.75))
    left, top = max(0, x1 - pad_x), max(0, y1 - pad_y)
    right, bottom = min(width, x2 + pad_x), min(height, y2 + pad_y)
    crop = frame[top:bottom, left:right].copy()
    if crop.size:
        _thin_subject_marker(crop, [x1 - left, y1 - top, x2 - left, y2 - top], color)
        fitted = _fit_image(crop, output_width - 8, output_height - title_height - 8)
        offset_x = (output_width - fitted.shape[1]) // 2
        offset_y = title_height + (output_height - title_height - fitted.shape[0]) // 2
        output[offset_y : offset_y + fitted.shape[0], offset_x : offset_x + fitted.shape[1]] = fitted
    cv2.rectangle(output, (0, 0), (output_width - 1, output_height - 1), color, 2)
    cv2.rectangle(output, (0, 0), (output_width - 1, title_height), BACKGROUND, -1)
    cv2.putText(output, title, (12, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2, cv2.LINE_AA)
    return output


def _thin_subject_marker(image: np.ndarray, bbox: list[int], color: tuple[int, int, int]) -> None:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = bbox
    x1, x2 = max(0, min(width - 1, x1)), max(1, min(width - 1, x2))
    y1, y2 = max(0, min(height - 1, y1)), max(1, min(height - 1, y2))
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    center_x = (x1 + x2) // 2
    arrow_start = max(8, y1 - 24)
    cv2.arrowedLine(image, (center_x, arrow_start), (center_x, max(1, y1 - 2)), color, 2, tipLength=0.35)


def _draw_details(card: np.ndarray, item: dict[str, Any], *, x: int, y: int) -> None:
    state = item["timeline_state"]
    lines = [
        (f"#{item['audit_index']:02d}  {item['audit_kind']}  state={state.get('status')}", TEXT),
        (f"subject={item.get('shadow_subject_id')}  team={item.get('team_label')}", TEXT),
        (
            f"source={item['source'].get('tracklet_id')} f{item['source'].get('frame')}  "
            f"target={item['target'].get('tracklet_id')} f{item['target'].get('frame')}",
            TEXT,
        ),
        (
            f"range=f{state.get('start_frame')}..{state.get('end_frame')}  "
            f"frames={state.get('frame_count')}  reason={state.get('reason')}",
            MUTED,
        ),
        (f"selection={item.get('selection_reason')}", MUTED),
        (f"identity relation={state.get('current_identity_relation')}", MUTED),
        (f"occlusion events={','.join(state.get('occlusion_event_ids') or []) or 'none'}", MUTED),
        ("1. Are SOURCE and TARGET the same real person?", TRANSITION_COLOR),
        ("2. If yes, does the proposed state explain the gap?", TRANSITION_COLOR),
        ("MANUAL REVIEW: PENDING", TRANSITION_COLOR),
    ]
    for index, (line, color) in enumerate(lines):
        value = line if len(line) <= 100 else f"{line[:97]}..."
        cv2.putText(card, value, (x, y + index * 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 1, cv2.LINE_AA)


def _render_html(manifest: dict[str, Any]) -> str:
    articles: list[str] = []
    for item in manifest.get("items") or []:
        audit_key = escape(str(item["audit_key"]))
        state = escape(str(item["timeline_state"].get("status") or "unknown"))
        articles.append(
            "".join(
                [
                    f'<article class="audit-card" data-audit-key="{audit_key}" data-review-status="pending">',
                    f'<img class="card-image" src="cards/{escape(str(item["card_filename"]))}" loading="lazy" alt="Timeline audit card" title="Click to enlarge">',
                    '<div class="audit-meta">',
                    f'<div class="case-title"><strong>#{int(item["audit_index"]):02d} {state}</strong><span>{escape(str(item["audit_kind"]))}</span></div>',
                    '<fieldset><legend>1. Same real person?</legend>',
                    f'<button type="button" data-group="identity" data-key="{audit_key}" data-value="same_person">Same person</button>',
                    f'<button type="button" data-group="identity" data-key="{audit_key}" data-value="different_people">Different people</button>',
                    f'<button type="button" data-group="identity" data-key="{audit_key}" data-value="uncertain">Unclear</button>',
                    '</fieldset>',
                    '<fieldset><legend>2. Is timeline state correct?</legend>',
                    f'<button type="button" data-group="state" data-key="{audit_key}" data-value="correct">Correct</button>',
                    f'<button type="button" data-group="state" data-key="{audit_key}" data-value="should_be_predicted">Predicted</button>',
                    f'<button type="button" data-group="state" data-key="{audit_key}" data-value="should_be_occluded">Occluded</button>',
                    f'<button type="button" data-group="state" data-key="{audit_key}" data-value="should_be_missing">Missing</button>',
                    f'<button type="button" data-group="state" data-key="{audit_key}" data-value="uncertain">Unclear</button>',
                    '</fieldset>',
                    '<span class="review-state">Pending</span>',
                    '</div></article>',
                ]
            )
        )
    label = escape(str((manifest.get("benchmark") or {}).get("label") or "benchmark"))
    summary = manifest.get("summary") or {}
    embedded_manifest = json.dumps(manifest, ensure_ascii=True).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>P1.4 timeline audit - {label}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; background: #0b1220; color: #eef2f8; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; }}
    header {{ position: sticky; top: 0; z-index: 3; display: flex; gap: 24px; align-items: center; justify-content: space-between; padding: 16px 22px; background: #0b1220f2; border-bottom: 1px solid #273650; }}
    h1 {{ margin: 0 0 5px; font-size: 22px; letter-spacing: 0; }}
    p {{ margin: 0; color: #a9b8ca; }}
    main {{ display: grid; gap: 18px; padding: 20px; }}
    .audit-card {{ border: 1px solid #273650; background: #101a2b; border-radius: 6px; overflow: hidden; }}
    .audit-card[data-review-status="complete"] {{ border-color: #22c55e; }}
    .audit-card[data-review-status="partial"] {{ border-color: #facc15; }}
    .card-image {{ display: block; width: 100%; height: auto; cursor: zoom-in; }}
    .audit-meta {{ display: flex; flex-wrap: wrap; gap: 16px; align-items: center; padding: 12px 16px; }}
    .case-title {{ display: grid; min-width: 160px; }}
    .case-title span {{ color: #9aacbf; }}
    fieldset {{ display: flex; flex-wrap: wrap; gap: 7px; margin: 0; padding: 8px; border: 1px solid #30415e; border-radius: 5px; }}
    legend {{ padding: 0 6px; color: #bdc9d8; }}
    button {{ border: 1px solid #3a4b67; border-radius: 5px; padding: 8px 11px; background: #172338; color: #eef2f8; font: inherit; cursor: pointer; }}
    button:hover {{ background: #22314a; }}
    button.active {{ border-color: #f8fafc; background: #365071; }}
    #download {{ background: #16a34a; border-color: #16a34a; font-weight: 700; }}
    .review-state {{ min-width: 90px; margin-left: auto; text-align: right; color: #a9b8ca; }}
    #lightbox {{ position: fixed; inset: 0; z-index: 10; display: none; align-items: center; justify-content: center; padding: 20px; background: #020611f2; }}
    #lightbox.open {{ display: flex; }}
    #lightbox img {{ max-width: calc(100vw - 40px); max-height: calc(100vh - 40px); object-fit: contain; cursor: zoom-out; }}
    #lightbox-close {{ position: fixed; top: 16px; right: 16px; width: 44px; height: 44px; padding: 0; font-size: 28px; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>P1.4 shadow timeline audit: {label}</h1>
      <p><span id="progress">0/{int(summary.get("review_items") or 0)}</span> complete. First compare identity, then assess the proposed state.</p>
    </div>
    <button id="download" type="button">Download reviewed manifest</button>
  </header>
  <main>{''.join(articles)}</main>
  <div id="lightbox" role="dialog" aria-modal="true" aria-label="Enlarged timeline audit card">
    <button id="lightbox-close" type="button" aria-label="Close enlarged image">&times;</button>
    <img id="lightbox-image" alt="Enlarged timeline audit card">
  </div>
  <script>
    const manifest = {embedded_manifest};
    const itemsByKey = new Map(manifest.items.map((item) => [item.audit_key, item]));

    function isComplete(item) {{
      if (!item.manual_review.identity_continuity) return false;
      if (["different_people", "uncertain"].includes(item.manual_review.identity_continuity)) return true;
      return Boolean(item.manual_review.state_assessment);
    }}
    function updateCard(key) {{
      const item = itemsByKey.get(key);
      const card = document.querySelector(`[data-audit-key="${{key}}"]`);
      const complete = isComplete(item);
      const touched = Boolean(item.manual_review.identity_continuity || item.manual_review.state_assessment);
      card.dataset.reviewStatus = complete ? "complete" : touched ? "partial" : "pending";
      card.querySelector(".review-state").textContent = complete ? "Complete" : touched ? "Partial" : "Pending";
      card.querySelectorAll("[data-group]").forEach((button) => {{
        const selected = button.dataset.group === "identity"
          ? item.manual_review.identity_continuity
          : item.manual_review.state_assessment;
        button.classList.toggle("active", button.dataset.value === selected);
      }});
    }}
    function updateProgress() {{
      const reviewed = manifest.items.filter(isComplete).length;
      manifest.summary.reviewed = reviewed;
      manifest.summary.pending = manifest.items.length - reviewed;
      document.getElementById("progress").textContent = `${{reviewed}}/${{manifest.items.length}}`;
    }}
    document.querySelectorAll("[data-group]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const item = itemsByKey.get(button.dataset.key);
        if (button.dataset.group === "identity") {{
          item.manual_review.identity_continuity = button.dataset.value;
          if (button.dataset.value === "different_people") {{
            item.manual_review.state_assessment = "identity_link_invalid";
          }} else if (button.dataset.value === "uncertain") {{
            item.manual_review.state_assessment = "uncertain";
          }} else if (item.manual_review.state_assessment === "identity_link_invalid") {{
            item.manual_review.state_assessment = null;
          }} else if (item.manual_review.state_assessment === "uncertain") {{
            item.manual_review.state_assessment = null;
          }}
        }} else {{
          if (["different_people", "uncertain"].includes(item.manual_review.identity_continuity)) return;
          item.manual_review.state_assessment = button.dataset.value;
        }}
        item.manual_review.status = isComplete(item) ? "reviewed" : "partial";
        item.manual_review.reviewed_at = new Date().toISOString();
        updateCard(button.dataset.key);
        updateProgress();
      }});
    }});
    const lightbox = document.getElementById("lightbox");
    const lightboxImage = document.getElementById("lightbox-image");
    function closeLightbox() {{ lightbox.classList.remove("open"); lightboxImage.removeAttribute("src"); }}
    document.querySelectorAll(".card-image").forEach((image) => image.addEventListener("click", () => {{ lightboxImage.src = image.src; lightbox.classList.add("open"); }}));
    document.getElementById("lightbox-close").addEventListener("click", closeLightbox);
    lightbox.addEventListener("click", (event) => {{ if (event.target === lightbox || event.target === lightboxImage) closeLightbox(); }});
    document.addEventListener("keydown", (event) => {{ if (event.key === "Escape") closeLightbox(); }});
    document.getElementById("download").addEventListener("click", () => {{
      const blob = new Blob([JSON.stringify(manifest, null, 2)], {{ type: "application/json" }});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "identity_shadow_timeline_audit_reviewed_{label}.json";
      link.click();
      URL.revokeObjectURL(link.href);
    }});
  </script>
</body>
</html>
"""


def _gap_key(subject_id: str, run: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "shadow_subject_id": subject_id,
            "start_frame": int(run.get("start_frame") or 0),
            "end_frame": int(run.get("end_frame") or 0),
            "status": run.get("status"),
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"shadow-gap:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _short_key(value: str) -> str:
    return value.rsplit(":", 1)[-1][:10] if value else "unknown"


def _is_bbox(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 4
