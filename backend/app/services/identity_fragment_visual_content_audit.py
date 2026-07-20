from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_fragment_consolidation_goldset import (
    classify_fragment_consolidation_proposal,
)
from app.services.identity_fragment_visual_content import build_endpoint_key


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_fragment_visual_content_audit"
ALGORITHM_VERSION = "0.1.0"

BACKGROUND = (14, 21, 34)
TEXT = (235, 240, 248)
MUTED = (155, 170, 190)
ACCENT = (0, 220, 255)


def build_identity_fragment_visual_content_audit_manifest(
    consolidation_doc: dict[str, Any],
    goldset_doc: dict[str, Any],
    *,
    benchmark_id: str,
    benchmark_label: str,
    video_path: str,
    video_time_offset_sec: float = 0.0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Select endpoint crops where content validity matters for a merge decision."""
    gold_index = {
        str(row.get("candidate_key") or ""): row
        for row in goldset_doc.get("items") or []
        if str(row.get("benchmark_id") or "") == benchmark_id
    }
    selected: dict[str, dict[str, Any]] = {}
    proposal_reasons: dict[str, set[str]] = {}
    for proposal in consolidation_doc.get("proposals") or []:
        proposal_key = str(proposal.get("proposal_key") or "")
        gold = gold_index.get(proposal_key) or {}
        status = str(gold.get("review_status") or "pending")
        strict = classify_fragment_consolidation_proposal(proposal)
        reasons: list[str] = []
        if status in {"confirmed_different", "uncertain"}:
            reasons.append(f"identity_goldset_{status}")
        if bool(strict.get("auto_accept")):
            reasons.append("strict_shadow_auto_accept_candidate")
        if not reasons:
            continue
        for side in ("source", "target"):
            endpoint = proposal.get(f"{side}_endpoint") or {}
            endpoint_key = build_endpoint_key(proposal, side=side)
            selected.setdefault(
                endpoint_key,
                {
                    "endpoint_key": endpoint_key,
                    "candidate_subject_id": proposal.get(f"{side}_candidate_subject_id"),
                    "candidate_player_id": proposal.get(f"{side}_candidate_player_id"),
                    "team_label": proposal.get(f"{side}_team_label"),
                    "frame": int(endpoint.get("frame") or 0),
                    "bbox_xyxy": endpoint.get("bbox_xyxy"),
                    "proposal_keys": [],
                    "selection_reasons": [],
                },
            )
            selected[endpoint_key]["proposal_keys"].append(proposal_key)
            proposal_reasons.setdefault(endpoint_key, set()).update(reasons)

    fps = _infer_fps(consolidation_doc)
    items: list[dict[str, Any]] = []
    for endpoint_key in sorted(selected, key=lambda key: (selected[key]["frame"], key)):
        endpoint = selected[endpoint_key]
        endpoint["proposal_keys"] = sorted(set(endpoint["proposal_keys"]))
        endpoint["selection_reasons"] = sorted(proposal_reasons[endpoint_key])
        source_time = int(endpoint["frame"]) / fps
        items.append(
            {
                "audit_index": len(items) + 1,
                "endpoint_key": endpoint_key,
                "card_filename": f"{len(items) + 1:03d}-{_short_key(endpoint_key)}.jpg",
                **endpoint,
                "source_time_sec": round(source_time, 3),
                "video_time_sec": round(max(0.0, source_time - video_time_offset_sec), 3),
                "manual_review": {
                    "status": "pending",
                    "reviewed_at": None,
                    "reviewer": None,
                    "notes": "",
                },
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "mode": "developer_visual_audit",
        "audit_kind": "fragment_endpoint_content",
        "benchmark": {
            "benchmark_id": benchmark_id,
            "label": benchmark_label,
            "video_path": video_path,
            "video_time_offset_sec": round(float(video_time_offset_sec), 3),
        },
        "source": {
            "consolidation_algorithm": consolidation_doc.get("algorithm") or {},
            "goldset_id": goldset_doc.get("goldset_id"),
            "goldset_version": goldset_doc.get("version"),
        },
        "ui": {
            "title": f"P1.11 endpoint content audit: {benchmark_label}",
            "description": (
                "Classify only the object indicated by the arrow and box. "
                "This does not decide whether two fragments are the same person."
            ),
            "download_filename": f"identity_fragment_visual_content_reviewed_{benchmark_label}.json",
        },
        "summary": {
            "review_items": len(items),
            "pending": len(items),
            "reviewed": 0,
        },
        "items": items,
    }


def render_identity_fragment_visual_content_audit(
    manifest: dict[str, Any],
    *,
    video_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cards_dir = output_dir / "cards"
    cards_dir.mkdir(exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"Could not open audit video: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if fps <= 0 or frame_count <= 0:
        capture.release()
        raise ValueError(f"Invalid audit video metadata: {video_path}")
    try:
        for item in manifest.get("items") or []:
            frame_index = min(
                frame_count - 1,
                max(0, int(round(float(item["video_time_sec"]) * fps))),
            )
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError(f"Could not read audit frame {frame_index}")
            card = _render_card(item, frame)
            card_path = cards_dir / str(item["card_filename"])
            if not cv2.imwrite(str(card_path), card, [cv2.IMWRITE_JPEG_QUALITY, 93]):
                raise RuntimeError(f"Could not write audit card: {card_path}")
    finally:
        capture.release()
    manifest["render"] = {"video_fps": round(fps, 6), "video_frames": frame_count}
    (output_dir / "audit_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "index.html").write_text(_render_html(manifest), encoding="utf-8")
    return manifest


def _render_card(item: dict[str, Any], frame: np.ndarray) -> np.ndarray:
    canvas = np.full((900, 1600, 3), BACKGROUND, dtype=np.uint8)
    context = _fit(frame, 920, 690)
    context_x = 20 + (920 - context.shape[1]) // 2
    context_y = 70 + (690 - context.shape[0]) // 2
    canvas[context_y : context_y + context.shape[0], context_x : context_x + context.shape[1]] = context
    bbox = item.get("bbox_xyxy")
    if _is_bbox(bbox):
        sx = context.shape[1] / frame.shape[1]
        sy = context.shape[0] / frame.shape[0]
        scaled = [
            int(round(float(bbox[0]) * sx)) + context_x,
            int(round(float(bbox[1]) * sy)) + context_y,
            int(round(float(bbox[2]) * sx)) + context_x,
            int(round(float(bbox[3]) * sy)) + context_y,
        ]
        _draw_marker(canvas, scaled)
        exact_crop = _exact_crop(frame, bbox)
        exact_view = _fit(exact_crop, 260, 500)
        exact_x = 970 + (260 - exact_view.shape[1]) // 2
        exact_y = 150 + (500 - exact_view.shape[0]) // 2
        canvas[exact_y : exact_y + exact_view.shape[0], exact_x : exact_x + exact_view.shape[1]] = exact_view
        expanded_crop = _expanded_crop(frame, bbox)
        expanded_view = _fit(expanded_crop, 320, 500)
        expanded_x = 1260 + (320 - expanded_view.shape[1]) // 2
        expanded_y = 150 + (500 - expanded_view.shape[0]) // 2
        canvas[
            expanded_y : expanded_y + expanded_view.shape[0],
            expanded_x : expanded_x + expanded_view.shape[1],
        ] = expanded_view
    cv2.putText(canvas, f"ENDPOINT CONTENT  #{int(item['audit_index']):03d}", (24, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.86, ACCENT, 2, cv2.LINE_AA)
    cv2.putText(canvas, "FULL FRAME CONTEXT", (24, 790), cv2.FONT_HERSHEY_SIMPLEX, 0.68, TEXT, 2, cv2.LINE_AA)
    cv2.putText(canvas, "EXACT BBOX", (970, 720), cv2.FONT_HERSHEY_SIMPLEX, 0.68, TEXT, 2, cv2.LINE_AA)
    cv2.putText(canvas, "EXPANDED CONTEXT", (1260, 720), cv2.FONT_HERSHEY_SIMPLEX, 0.68, TEXT, 2, cv2.LINE_AA)
    details = (
        f"{item.get('candidate_player_id')} | team={item.get('team_label')} | "
        f"frame={item.get('frame')} | reasons={','.join(item.get('selection_reasons') or [])}"
    )
    cv2.putText(canvas, details[:145], (24, 842), cv2.FONT_HERSHEY_SIMPLEX, 0.52, MUTED, 1, cv2.LINE_AA)
    return canvas


def _render_html(manifest: dict[str, Any]) -> str:
    cards = []
    for item in manifest.get("items") or []:
        key = escape(str(item["endpoint_key"]))
        cards.append(
            f'<article class="audit-card" data-endpoint-key="{key}" data-review-status="pending">'
            f'<img src="cards/{escape(str(item["card_filename"]))}" loading="lazy" alt="Endpoint content card">'
            f'<div class="meta"><strong>#{int(item["audit_index"]):03d} {escape(str(item.get("candidate_player_id") or ""))}</strong>'
            '<div class="actions">'
            f'<button data-key="{key}" data-value="person">Person</button>'
            f'<button data-key="{key}" data-value="partial_person">Partial person</button>'
            f'<button data-key="{key}" data-value="not_person">Not a person</button>'
            f'<button data-key="{key}" data-value="unclear">Unclear</button>'
            '</div><span class="state">Pending</span></div></article>'
        )
    embedded = json.dumps(manifest, ensure_ascii=True).replace("</", "<\\/")
    filename = json.dumps(str((manifest.get("ui") or {}).get("download_filename") or "endpoint-content-reviewed.json"))
    title = escape(str((manifest.get("ui") or {}).get("title") or "Endpoint content audit"))
    description = escape(str((manifest.get("ui") or {}).get("description") or ""))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0b1220;color:#eef2f8}}body{{margin:0}}header{{position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;gap:20px;padding:18px 24px;background:#0b1220ee;border-bottom:1px solid #273650}}h1{{font-size:22px;margin:0 0 6px}}p{{margin:0;color:#9aacbf}}main{{display:grid;gap:18px;padding:20px}}.audit-card{{border:1px solid #273650;border-radius:6px;overflow:hidden;background:#101a2b}}.audit-card[data-review-status="person"],.audit-card[data-review-status="partial_person"]{{border-color:#22c55e}}.audit-card[data-review-status="not_person"]{{border-color:#ef4444}}.audit-card[data-review-status="unclear"]{{border-color:#facc15}}img{{display:block;width:100%;height:auto}}.meta{{display:flex;align-items:center;gap:16px;padding:12px 16px}}.actions{{display:flex;gap:8px;margin-left:auto}}button{{border:1px solid #3a4b67;border-radius:5px;padding:8px 12px;background:#172338;color:#eef2f8;font:inherit;cursor:pointer}}button.active{{border-color:#eef2f8;background:#30425f}}#download{{background:#16a34a;border-color:#16a34a;font-weight:700}}.state{{min-width:100px;text-align:right;color:#a9b8ca}}
</style></head><body><header><div><h1>{title}</h1><p><span id="progress">0/{len(manifest.get('items') or [])}</span> reviewed. {description}</p></div><button id="download">Download reviewed manifest</button></header><main>{''.join(cards)}</main><script>
const manifest={embedded};const byKey=new Map(manifest.items.map(item=>[item.endpoint_key,item]));const labels={{person:"Person",partial_person:"Partial person",not_person:"Not a person",unclear:"Unclear"}};
function update(){{const reviewed=manifest.items.filter(item=>item.manual_review.status!=="pending").length;document.getElementById("progress").textContent=`${{reviewed}}/${{manifest.items.length}}`;manifest.summary.reviewed=reviewed;manifest.summary.pending=manifest.items.length-reviewed;}}
document.querySelectorAll("[data-value]").forEach(button=>button.addEventListener("click",()=>{{const item=byKey.get(button.dataset.key);item.manual_review.status=button.dataset.value;item.manual_review.reviewed_at=new Date().toISOString();const card=document.querySelector(`[data-endpoint-key="${{button.dataset.key}}"]`);card.dataset.reviewStatus=button.dataset.value;card.querySelectorAll("[data-value]").forEach(candidate=>candidate.classList.toggle("active",candidate===button));card.querySelector(".state").textContent=labels[button.dataset.value];update();}}));
document.getElementById("download").addEventListener("click",()=>{{const blob=new Blob([JSON.stringify(manifest,null,2)],{{type:"application/json"}});const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download={filename};link.click();URL.revokeObjectURL(link.href);}});
</script></body></html>"""


def _infer_fps(document: dict[str, Any]) -> float:
    for proposal in document.get("proposals") or []:
        frames = int(proposal.get("gap_frames") or 0)
        seconds = float(proposal.get("gap_seconds") or 0.0)
        if frames > 0 and seconds > 0:
            return max(frames / seconds, 1e-6)
    return 30.0


def _expanded_crop(frame: np.ndarray, bbox: Any) -> np.ndarray:
    x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    pad_x = width * 1.5
    pad_y = height * 0.65
    left = max(0, int(round(x1 - pad_x)))
    top = max(0, int(round(y1 - pad_y)))
    right = min(frame.shape[1], int(round(x2 + pad_x)))
    bottom = min(frame.shape[0], int(round(y2 + pad_y)))
    return frame[top:bottom, left:right]


def _exact_crop(frame: np.ndarray, bbox: Any) -> np.ndarray:
    x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
    left = max(0, int(round(x1)))
    top = max(0, int(round(y1)))
    right = min(frame.shape[1], int(round(x2)))
    bottom = min(frame.shape[0], int(round(y2)))
    return frame[top:bottom, left:right]


def _fit(image: np.ndarray, max_width: int, max_height: int) -> np.ndarray:
    scale = min(max_width / max(1, image.shape[1]), max_height / max(1, image.shape[0]))
    return cv2.resize(image, (max(1, int(round(image.shape[1] * scale))), max(1, int(round(image.shape[0] * scale)))), interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)


def _draw_marker(image: np.ndarray, bbox: list[int]) -> None:
    x1, y1, x2, y2 = bbox
    cv2.rectangle(image, (x1, y1), (x2, y2), ACCENT, 2)
    center = ((x1 + x2) // 2, max(12, y1 - 12))
    cv2.arrowedLine(image, (center[0], max(4, center[1] - 34)), center, ACCENT, 3, tipLength=0.35)


def _is_bbox(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= 4 and float(value[2]) > float(value[0]) and float(value[3]) > float(value[1])


def _short_key(value: str) -> str:
    return value.rsplit(":", 1)[-1][:10] if value else "unknown"
