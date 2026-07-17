from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_stitching_audit import (
    BACKGROUND,
    MUTED,
    TEXT,
    TRANSITION_COLOR,
    _draw_box,
    _fit_image,
    _read_frame,
    _title_bar,
    _write_contact_sheets,
)


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_joint_occlusion_visual_audit"
ALGORITHM_VERSION = "0.2.0"

SOURCE_COLORS = ((0, 140, 255), (0, 210, 255))
TARGET_COLORS = ((255, 190, 0), (255, 100, 0))
PRIMARY_STATUSES = {"ambiguous", "identity_contradiction", "suspected_swap"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_joint_assignment_audit_manifest(
    assignments_doc: dict[str, Any],
    tracklets_doc: dict[str, Any],
    *,
    benchmark_id: str,
    benchmark_label: str,
    video_path: str,
    video_time_offset_sec: float = 0.0,
    control_limit: int = 3,
    generated_at: str | None = None,
) -> dict[str, Any]:
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_doc.get("tracklets") or []
        if row.get("tracklet_id") is not None
    }
    cases = list(assignments_doc.get("cases") or [])
    primary = [row for row in cases if str((row.get("decision") or {}).get("status")) in PRIMARY_STATUSES]
    controls = [row for row in cases if str((row.get("decision") or {}).get("status")) == "keep_current"]
    selected = sorted(primary, key=_case_sort_key) + sorted(controls, key=_case_sort_key)[: max(0, control_limit)]

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for case in selected:
        source_ids = [str(value) for value in case.get("source_tracklet_ids") or []]
        target_ids = [str(value) for value in case.get("target_tracklet_ids") or []]
        missing = [value for value in source_ids + target_ids if value not in tracklets]
        if len(source_ids) != 2 or len(target_ids) != 2 or missing:
            skipped.append(
                {
                    "case_key": case.get("case_key"),
                    "reason": "invalid_or_missing_tracklets",
                    "missing_tracklet_ids": missing,
                }
            )
            continue
        source_frame = int(case.get("start_frame") or 0) - 1
        target_frame = int(case.get("end_frame") or source_frame) + 1
        source_endpoints = [
            _endpoint_payload(tracklets[tracklet_id], before_frame=source_frame, video_time_offset_sec=video_time_offset_sec)
            for tracklet_id in source_ids
        ]
        target_endpoints = [
            _endpoint_payload(tracklets[tracklet_id], after_frame=target_frame, video_time_offset_sec=video_time_offset_sec)
            for tracklet_id in target_ids
        ]
        if any(endpoint is None for endpoint in source_endpoints + target_endpoints):
            skipped.append(
                {
                    "case_key": case.get("case_key"),
                    "reason": "missing_endpoint_position",
                    "source_tracklet_ids": source_ids,
                    "target_tracklet_ids": target_ids,
                }
            )
            continue
        sources = [endpoint for endpoint in source_endpoints if endpoint is not None]
        targets = [endpoint for endpoint in target_endpoints if endpoint is not None]
        for index, endpoint in enumerate(sources, start=1):
            endpoint["side_index"] = index
        for index, endpoint in enumerate(targets, start=1):
            endpoint["side_index"] = index
        case_key = str(case.get("case_key") or "")
        items.append(
            {
                "audit_index": len(items) + 1,
                "case_key": case_key,
                "card_filename": f"{len(items) + 1:03d}-{_short_key(case_key)}.jpg",
                "team_label": case.get("team_label"),
                "event": {
                    "start_frame": case.get("start_frame"),
                    "end_frame": case.get("end_frame"),
                    "start_time_sec": case.get("start_time_sec"),
                    "end_time_sec": case.get("end_time_sec"),
                    "video_time_sec": round(
                        max(
                            0.0,
                            (
                                float(case.get("start_time_sec") or 0.0)
                                + float(case.get("end_time_sec") or case.get("start_time_sec") or 0.0)
                            )
                            / 2.0
                            - video_time_offset_sec,
                        ),
                        3,
                    ),
                    "occlusion_event_ids": case.get("occlusion_event_ids") or [],
                    "event_confidence": case.get("event_confidence"),
                },
                "sources": sources,
                "targets": targets,
                "assignments": case.get("assignments") or [],
                "shadow_decision": case.get("decision") or {},
                "manual_review": {
                    "status": "pending",
                    "correct_assignment_id": None,
                    "confirmed_pairs": [],
                    "reviewer": None,
                    "reviewed_at": None,
                    "notes": "",
                },
            }
        )
    statuses = Counter(str((item.get("shadow_decision") or {}).get("status") or "unknown") for item in items)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "mode": "developer_visual_audit",
        "benchmark": {
            "benchmark_id": benchmark_id,
            "label": benchmark_label,
            "video_path": video_path,
            "video_time_offset_sec": round(video_time_offset_sec, 3),
        },
        "source": {
            "assignment_algorithm": assignments_doc.get("algorithm") or {},
            "primary_statuses": sorted(PRIMARY_STATUSES),
            "control_limit": max(0, control_limit),
        },
        "summary": {
            "review_items": len(items),
            "pending": len(items),
            "skipped": len(skipped),
            "shadow_statuses": dict(sorted(statuses.items())),
        },
        "items": items,
        "skipped": skipped,
    }


def render_joint_assignment_audit(
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
            source_context_time = max(float(row["video_time_sec"]) for row in item["sources"])
            target_context_time = min(float(row["video_time_sec"]) for row in item["targets"])
            source_context = _read_frame(capture, source_context_time, fps, frame_count)
            transition = _read_frame(capture, float(item["event"]["video_time_sec"]), fps, frame_count)
            target_context = _read_frame(capture, target_context_time, fps, frame_count)
            source_frames = [
                _read_frame(capture, float(endpoint["video_time_sec"]), fps, frame_count)
                for endpoint in item["sources"]
            ]
            target_frames = [
                _read_frame(capture, float(endpoint["video_time_sec"]), fps, frame_count)
                for endpoint in item["targets"]
            ]
            card = _render_card(item, source_context, transition, target_context, source_frames, target_frames)
            card_path = cards_dir / str(item["card_filename"])
            if not cv2.imwrite(str(card_path), card, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                raise RuntimeError(f"Could not write audit card: {card_path}")
            cards.append(card)
    finally:
        capture.release()
    sheet_names = _write_contact_sheets(cards, sheets_dir, cards_per_sheet=max(1, cards_per_sheet))
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


def _render_card(
    item: dict[str, Any],
    source_context: np.ndarray,
    transition: np.ndarray,
    target_context: np.ndarray,
    source_frames: list[np.ndarray],
    target_frames: list[np.ndarray],
) -> np.ndarray:
    panel_size = (800, 450)
    card = np.full((1120, 2400, 3), BACKGROUND, dtype=np.uint8)
    card[:450, :800] = _context_panel(source_context, item["sources"], "BEFORE OCCLUSION", SOURCE_COLORS, panel_size)
    card[:450, 800:1600] = _context_panel(
        transition,
        item["sources"] + item["targets"],
        "OCCLUSION EVENT",
        SOURCE_COLORS + TARGET_COLORS,
        panel_size,
    )
    card[:450, 1600:2400] = _context_panel(target_context, item["targets"], "AFTER OCCLUSION", TARGET_COLORS, panel_size)
    crop_size = (390, 610)
    for index, (endpoint, frame, color) in enumerate(zip(item["sources"], source_frames, SOURCE_COLORS)):
        crop = _joint_crop_panel(frame, endpoint.get("bbox_xyxy"), f"S{index + 1} {endpoint['tracklet_id']}", color, crop_size)
        card[480:1090, 20 + index * 410 : 410 + index * 410] = crop
    for index, (endpoint, frame, color) in enumerate(zip(item["targets"], target_frames, TARGET_COLORS)):
        crop = _joint_crop_panel(frame, endpoint.get("bbox_xyxy"), f"T{index + 1} {endpoint['tracklet_id']}", color, crop_size)
        x = 840 + index * 410
        card[480:1090, x : x + 390] = crop
    _draw_details(card, item, x=1680, y=500)
    return card


def _context_panel(
    frame: np.ndarray,
    endpoints: list[dict[str, Any]],
    title: str,
    colors: tuple[tuple[int, int, int], ...],
    size: tuple[int, int],
) -> np.ndarray:
    height, width = frame.shape[:2]
    panel = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    sx, sy = size[0] / width, size[1] / height
    for index, endpoint in enumerate(endpoints):
        bbox = endpoint.get("bbox_xyxy")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        scaled = [int(bbox[0] * sx), int(bbox[1] * sy), int(bbox[2] * sx), int(bbox[3] * sy)]
        prefix = "S" if index < len(endpoints) and str(endpoint.get("side")) == "source" else "T"
        _draw_box(panel, scaled, colors[index], f"{prefix}{endpoint.get('side_index')}", thickness=3)
    _title_bar(panel, title, TRANSITION_COLOR)
    return panel


def _joint_crop_panel(
    frame: np.ndarray,
    bbox: Any,
    title: str,
    color: tuple[int, int, int],
    size: tuple[int, int],
) -> np.ndarray:
    output_width, output_height = size
    title_height = 42
    output = np.full((output_height, output_width, 3), BACKGROUND, dtype=np.uint8)
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return output
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
    x1, x2 = max(0, min(width - 1, x1)), max(1, min(width, x2))
    y1, y2 = max(0, min(height - 1, y1)), max(1, min(height, y2))
    box_width, box_height = max(1, x2 - x1), max(1, y2 - y1)
    pad_x, pad_y = max(42, int(box_width * 1.6)), max(34, int(box_height * 0.8))
    left, top = max(0, x1 - pad_x), max(0, y1 - pad_y)
    right, bottom = min(width, x2 + pad_x), min(height, y2 + pad_y)
    crop = frame[top:bottom, left:right].copy()
    if crop.size:
        relative = [x1 - left, y1 - top, x2 - left, y2 - top]
        cv2.rectangle(crop, (relative[0], relative[1]), (relative[2], relative[3]), color, 2)
        center_x = (relative[0] + relative[2]) // 2
        arrow_start = max(8, relative[1] - max(18, box_height // 4))
        cv2.arrowedLine(
            crop,
            (center_x, arrow_start),
            (center_x, max(1, relative[1] + 1)),
            color,
            2,
            cv2.LINE_AA,
            tipLength=0.24,
        )
        content_width = output_width - 8
        content_height = output_height - title_height - 8
        fitted = _fit_image(crop, content_width, content_height)
        offset_y = title_height + (content_height - fitted.shape[0]) // 2
        offset_x = 4 + (content_width - fitted.shape[1]) // 2
        output[offset_y : offset_y + fitted.shape[0], offset_x : offset_x + fitted.shape[1]] = fitted
    cv2.rectangle(output, (0, 0), (output_width - 1, output_height - 1), color, 2)
    cv2.rectangle(output, (0, 0), (output_width - 1, title_height), BACKGROUND, -1)
    cv2.putText(output, title, (12, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    return output


def _draw_details(card: np.ndarray, item: dict[str, Any], *, x: int, y: int) -> None:
    assignments = {str(row.get("assignment_id")): row for row in item.get("assignments") or []}
    assignment_a = assignments.get("assignment_a") or {}
    assignment_b = assignments.get("assignment_b") or {}
    decision = item.get("shadow_decision") or {}
    lines = [
        (f"#{item['audit_index']:02d} team={item.get('team_label')} shadow={decision.get('status')}", TEXT),
        (f"frames {item['event'].get('start_frame')}..{item['event'].get('end_frame')}", MUTED),
        ("A: S1 -> T1, S2 -> T2", SOURCE_COLORS[1]),
        (f"   mean cost={_fmt(assignment_a.get('mean_cost'))} current={assignment_a.get('matches_current_identity')}", TEXT),
        ("B: S1 -> T2, S2 -> T1", TARGET_COLORS[0]),
        (f"   mean cost={_fmt(assignment_b.get('mean_cost'))} current={assignment_b.get('matches_current_identity')}", TEXT),
        (f"best={decision.get('best_assignment_id') or decision.get('recommended_assignment_id')}", TEXT),
        (f"margin={_fmt(decision.get('margin'))} confidence={_fmt(decision.get('confidence'))}", TEXT),
        (f"reasons: {','.join(str(value) for value in decision.get('reasons') or []) or 'none'}", MUTED),
        ("Choose which mapping preserves real people.", TRANSITION_COLOR),
        ("MANUAL REVIEW: PENDING", TRANSITION_COLOR),
    ]
    for index, (line, color) in enumerate(lines):
        value = line if len(line) <= 76 else f"{line[:73]}..."
        cv2.putText(card, value, (x, y + index * 34), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)


def _endpoint_payload(
    tracklet: dict[str, Any],
    *,
    video_time_offset_sec: float,
    before_frame: int | None = None,
    after_frame: int | None = None,
) -> dict[str, Any] | None:
    positions = sorted(
        tracklet.get("positions_m") or tracklet.get("positions") or [],
        key=lambda row: (int(row.get("frame") or 0), float(row.get("time_sec") or 0.0)),
    )
    if before_frame is not None:
        candidates = [row for row in positions if int(row.get("frame") or 0) <= before_frame]
        position = candidates[-1] if candidates else None
        side = "source"
    else:
        candidates = [row for row in positions if int(row.get("frame") or 0) >= int(after_frame or 0)]
        position = candidates[0] if candidates else None
        side = "target"
    if position is None:
        return None
    time_sec = float(position.get("time_sec") or 0.0)
    bbox = position.get("bbox_xyxy")
    return {
        "tracklet_id": str(tracklet.get("tracklet_id") or ""),
        "frame": int(position.get("frame") or 0),
        "source_time_sec": round(time_sec, 3),
        "video_time_sec": round(max(0.0, time_sec - video_time_offset_sec), 3),
        "bbox_xyxy": [int(round(float(value))) for value in bbox] if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None,
        "pitch_m": position.get("smoothed_pitch_m") or position.get("pitch_m"),
        "confidence": position.get("confidence"),
        "side": side,
    }


def _render_html(manifest: dict[str, Any]) -> str:
    cards: list[str] = []
    for item in manifest.get("items") or []:
        key = escape(str(item["case_key"]))
        status = escape(str((item.get("shadow_decision") or {}).get("status") or "unknown"))
        cards.append(
            "".join(
                [
                    f'<article class="audit-card" data-case-key="{key}" data-review-status="pending">',
                    f'<img class="card-image" src="cards/{escape(str(item["card_filename"]))}" loading="lazy" alt="Joint identity audit card">',
                    '<div class="audit-meta">',
                    f'<strong>#{int(item["audit_index"]):02d} {status}</strong>',
                    '<div class="review-actions">',
                    f'<button type="button" data-key="{key}" data-value="assignment_a">Assignment A</button>',
                    f'<button type="button" data-key="{key}" data-value="assignment_b">Assignment B</button>',
                    f'<button type="button" data-key="{key}" data-value="partial" data-source="S1" data-target="T1">Only S1→T1</button>',
                    f'<button type="button" data-key="{key}" data-value="partial" data-source="S1" data-target="T2">Only S1→T2</button>',
                    f'<button type="button" data-key="{key}" data-value="partial" data-source="S2" data-target="T1">Only S2→T1</button>',
                    f'<button type="button" data-key="{key}" data-value="partial" data-source="S2" data-target="T2">Only S2→T2</button>',
                    f'<button type="button" data-key="{key}" data-value="neither">Neither</button>',
                    f'<button type="button" data-key="{key}" data-value="uncertain">Uncertain</button>',
                    '</div><span class="review-state">Pending</span></div></article>',
                ]
            )
        )
    label = escape(str((manifest.get("benchmark") or {}).get("label") or "benchmark"))
    embedded = json.dumps(manifest, ensure_ascii=True).replace("</", "<\\/")
    total = int((manifest.get("summary") or {}).get("review_items") or 0)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Joint occlusion identity audit - {label}</title><style>
:root {{ color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0b1220;color:#eef2f8 }}
body {{ margin:0 }} header {{ position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;align-items:center;padding:18px 24px;background:#0b1220ee;border-bottom:1px solid #273650 }}
h1 {{ margin:0 0 6px;font-size:22px;letter-spacing:0 }} p {{ margin:0;color:#9aacbf }} main {{ display:grid;gap:18px;padding:20px }}
.audit-card {{ border:1px solid #273650;background:#101a2b;border-radius:6px;overflow:hidden }} .audit-card[data-review-status="assignment_a"],.audit-card[data-review-status="assignment_b"] {{ border-color:#22c55e }} .audit-card[data-review-status="partial"] {{ border-color:#38bdf8 }} .audit-card[data-review-status="neither"] {{ border-color:#ef4444 }} .audit-card[data-review-status="uncertain"] {{ border-color:#facc15 }}
.card-image {{ display:block;width:100%;height:auto;cursor:zoom-in }} .audit-meta {{ display:flex;gap:18px;align-items:center;padding:12px 16px }} .review-actions {{ display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px;margin-left:auto;max-width:980px }}
button {{ border:1px solid #3a4b67;border-radius:5px;padding:8px 12px;background:#172338;color:#eef2f8;font:inherit;cursor:pointer }} button:hover,button.active {{ background:#30425f;border-color:#eef2f8 }} #download {{ background:#16a34a;border-color:#16a34a;font-weight:700 }}
#lightbox {{ position:fixed;inset:0;z-index:10;display:none;align-items:center;justify-content:center;padding:24px;background:#020611f2 }} #lightbox.open {{ display:flex }} #lightbox img {{ max-width:calc(100vw - 48px);max-height:calc(100vh - 48px);cursor:zoom-out }}
</style></head><body><header><div><h1>Joint occlusion audit: {label}</h1><p><span id="progress">0/{total}</span> reviewed. Compare S1/S2 before with T1/T2 after.</p></div><button id="download">Download reviewed manifest</button></header>
<main>{''.join(cards)}</main><div id="lightbox"><img id="lightbox-image" alt="Enlarged joint audit card"></div><script>
const manifest={embedded};const byKey=new Map(manifest.items.map(item=>[item.case_key,item]));
function progress(){{const n=manifest.items.filter(item=>item.manual_review.status!=="pending").length;document.getElementById("progress").textContent=`${{n}}/${{manifest.items.length}}`;manifest.summary.reviewed=n;manifest.summary.pending=manifest.items.length-n}}
document.querySelectorAll("[data-value]").forEach(button=>button.addEventListener("click",()=>{{const item=byKey.get(button.dataset.key);const value=button.dataset.value;item.manual_review.status=value;item.manual_review.correct_assignment_id=value.startsWith("assignment_")?value:null;item.manual_review.confirmed_pairs=value==="partial"?[{{source:button.dataset.source,target:button.dataset.target}}]:[];item.manual_review.reviewed_at=new Date().toISOString();const card=document.querySelector(`[data-case-key="${{button.dataset.key}}"]`);card.dataset.reviewStatus=value;card.querySelectorAll("[data-value]").forEach(node=>node.classList.toggle("active",node===button));card.querySelector(".review-state").textContent=value==="partial"?`Only ${{button.dataset.source}} → ${{button.dataset.target}}`:value;progress()}}));
const box=document.getElementById("lightbox"),image=document.getElementById("lightbox-image");document.querySelectorAll(".card-image").forEach(node=>node.addEventListener("click",()=>{{image.src=node.src;box.classList.add("open")}}));box.addEventListener("click",()=>{{box.classList.remove("open");image.removeAttribute("src")}});document.addEventListener("keydown",event=>{{if(event.key==="Escape")box.click()}});
document.getElementById("download").addEventListener("click",()=>{{const blob=new Blob([JSON.stringify(manifest,null,2)],{{type:"application/json"}}),link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download="identity_joint_occlusion_audit_reviewed_{label}.json";link.click();URL.revokeObjectURL(link.href)}});
</script></body></html>"""


def _case_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    return int(row.get("start_frame") or 0), str(row.get("case_key") or "")


def _short_key(value: str) -> str:
    return value.rsplit(":", 1)[-1][:10] if value else "unknown"


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.4f}"
