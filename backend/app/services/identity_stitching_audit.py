from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_stitching_visual_audit"
ALGORITHM_VERSION = "0.1.0"

SOURCE_COLOR = (0, 140, 255)
TARGET_COLOR = (255, 190, 0)
TRANSITION_COLOR = (0, 220, 255)
BACKGROUND = (14, 21, 34)
TEXT = (235, 240, 248)
MUTED = (155, 170, 190)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_stitching_audit_manifest(
    stitching_doc: dict[str, Any],
    tracklets_doc: dict[str, Any],
    *,
    benchmark_id: str,
    benchmark_label: str,
    video_path: str,
    video_time_offset_sec: float = 0.0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build deterministic review rows for recommended shadow stitching edges."""
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in (tracklets_doc.get("tracklets") or [])
        if row.get("tracklet_id") is not None
    }
    recommended = sorted(
        (row for row in (stitching_doc.get("candidate_edges") or []) if row.get("recommended")),
        key=lambda row: (
            float(row.get("cost") or 0.0),
            str(row.get("source_tracklet_id") or ""),
            str(row.get("target_tracklet_id") or ""),
        ),
    )
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for edge in recommended:
        source_id = str(edge.get("source_tracklet_id") or "")
        target_id = str(edge.get("target_tracklet_id") or "")
        source = tracklets.get(source_id)
        target = tracklets.get(target_id)
        if source is None or target is None:
            skipped.append(
                {
                    "candidate_key": edge.get("candidate_key"),
                    "reason": "missing_tracklet",
                    "missing_tracklet_ids": [
                        value
                        for value, row in ((source_id, source), (target_id, target))
                        if row is None
                    ],
                }
            )
            continue
        source_position = _endpoint_position(source, use_last=True)
        target_position = _endpoint_position(target, use_last=False)
        if source_position is None or target_position is None:
            skipped.append(
                {
                    "candidate_key": edge.get("candidate_key"),
                    "reason": "missing_endpoint_position",
                    "source_tracklet_id": source_id,
                    "target_tracklet_id": target_id,
                }
            )
            continue
        source_time = float(source_position.get("time_sec") or source.get("end_time_sec") or 0.0)
        target_time = float(target_position.get("time_sec") or target.get("start_time_sec") or 0.0)
        candidate_key = str(edge.get("candidate_key") or "")
        rows.append(
            {
                "audit_index": len(rows) + 1,
                "candidate_key": candidate_key,
                "card_filename": f"{len(rows) + 1:03d}-{_short_key(candidate_key)}.jpg",
                "source": _endpoint_payload(source, source_position, video_time_offset_sec),
                "transition": {
                    "source_time_sec": round((source_time + target_time) / 2.0, 3),
                    "video_time_sec": round(max(0.0, ((source_time + target_time) / 2.0) - video_time_offset_sec), 3),
                    "gap_sec": edge.get("gap_sec"),
                },
                "target": _endpoint_payload(target, target_position, video_time_offset_sec),
                "decision": {
                    "current_identity_relation": edge.get("current_identity_relation"),
                    "source_stable_subject_ids": edge.get("source_stable_subject_ids") or [],
                    "target_stable_subject_ids": edge.get("target_stable_subject_ids") or [],
                    "source_quality_class": edge.get("source_quality_class"),
                    "target_quality_class": edge.get("target_quality_class"),
                    "cost": edge.get("cost"),
                    "base_confidence": edge.get("base_confidence"),
                    "recommendation_votes": edge.get("recommendation_votes"),
                    "recommendation_votes_required": edge.get("recommendation_votes_required"),
                    "distance_m": edge.get("distance_m"),
                    "required_speed_mps": edge.get("required_speed_mps"),
                    "velocity_prediction_distance_m": edge.get("velocity_prediction_distance_m"),
                    "appearance_distance_rgb": edge.get("appearance_distance_rgb"),
                    "bbox_area_ratio": edge.get("bbox_area_ratio"),
                    "feature_costs": edge.get("feature_costs") or {},
                    "bonuses": edge.get("bonuses") or {},
                    "penalties": edge.get("penalties") or {},
                    "evidence": edge.get("evidence") or [],
                    "occlusion_event_ids": edge.get("occlusion_event_ids") or [],
                },
                "manual_review": {
                    "status": "pending",
                    "same_person": None,
                    "reviewer": None,
                    "reviewed_at": None,
                    "notes": "",
                },
            }
        )
    relations: dict[str, int] = {}
    for row in rows:
        relation = str(row["decision"].get("current_identity_relation") or "unknown")
        relations[relation] = relations.get(relation, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "mode": "developer_visual_audit",
        "benchmark": {
            "benchmark_id": benchmark_id,
            "label": benchmark_label,
            "video_path": video_path,
            "video_time_offset_sec": round(float(video_time_offset_sec), 3),
        },
        "source": {
            "stitching_algorithm": stitching_doc.get("algorithm") or {},
            "recommended_edges": len(recommended),
        },
        "summary": {
            "review_items": len(rows),
            "pending": len(rows),
            "skipped": len(skipped),
            "current_identity_relations": dict(sorted(relations.items())),
        },
        "items": rows,
        "skipped": skipped,
    }


def render_stitching_audit(
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

    rendered_cards: list[np.ndarray] = []
    try:
        for item in manifest.get("items") or []:
            source_frame = _read_frame(capture, float(item["source"]["video_time_sec"]), fps, frame_count)
            transition_frame = _read_frame(
                capture,
                float(item["transition"]["video_time_sec"]),
                fps,
                frame_count,
            )
            target_frame = _read_frame(capture, float(item["target"]["video_time_sec"]), fps, frame_count)
            card = _render_card(item, source_frame, transition_frame, target_frame)
            card_path = cards_dir / str(item["card_filename"])
            if not cv2.imwrite(str(card_path), card, [cv2.IMWRITE_JPEG_QUALITY, 91]):
                raise RuntimeError(f"Could not write audit card: {card_path}")
            rendered_cards.append(card)
    finally:
        capture.release()

    sheet_names = _write_contact_sheets(rendered_cards, sheets_dir, cards_per_sheet=max(1, cards_per_sheet))
    manifest["render"] = {
        "video_fps": round(fps, 6),
        "video_frames": frame_count,
        "cards": len(rendered_cards),
        "contact_sheets": sheet_names,
    }
    (output_dir / "audit_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(_render_html(manifest), encoding="utf-8")
    return manifest


def _endpoint_payload(
    tracklet: dict[str, Any],
    position: dict[str, Any],
    video_time_offset_sec: float,
) -> dict[str, Any]:
    source_time = float(position.get("time_sec") or 0.0)
    bbox = position.get("bbox_xyxy")
    return {
        "tracklet_id": str(tracklet.get("tracklet_id") or ""),
        "raw_tracker_id": tracklet.get("source_tracker_id"),
        "team_label": tracklet.get("team_label") or tracklet.get("team_candidate") or "U",
        "role": tracklet.get("role") or "unknown",
        "frame": int(position.get("frame") or 0),
        "source_time_sec": round(source_time, 3),
        "video_time_sec": round(max(0.0, source_time - video_time_offset_sec), 3),
        "bbox_xyxy": [int(round(float(value))) for value in bbox] if _is_bbox(bbox) else None,
        "pitch_m": position.get("smoothed_pitch_m") or position.get("pitch_m"),
        "confidence": position.get("confidence"),
    }


def _endpoint_position(tracklet: dict[str, Any], *, use_last: bool) -> dict[str, Any] | None:
    positions = sorted(
        tracklet.get("positions_m") or tracklet.get("positions") or [],
        key=lambda row: (int(row.get("frame") or 0), float(row.get("time_sec") or 0.0)),
    )
    if not positions:
        return None
    return positions[-1] if use_last else positions[0]


def _read_frame(capture: cv2.VideoCapture, time_sec: float, fps: float, frame_count: int) -> np.ndarray:
    frame_index = min(frame_count - 1, max(0, int(round(max(0.0, time_sec) * fps))))
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = capture.read()
    if not ok or frame is None:
        raise RuntimeError(f"Could not read audit frame {frame_index} at {time_sec:.3f}s")
    return frame


def _render_card(
    item: dict[str, Any],
    source_frame: np.ndarray,
    transition_frame: np.ndarray,
    target_frame: np.ndarray,
) -> np.ndarray:
    panel_size = (800, 450)
    source_panel = _context_panel(source_frame, item["source"], "SOURCE END", SOURCE_COLOR, panel_size)
    transition_panel = _transition_panel(transition_frame, item, panel_size)
    target_panel = _context_panel(target_frame, item["target"], "TARGET START", TARGET_COLOR, panel_size)
    card = np.full((1080, 2400, 3), BACKGROUND, dtype=np.uint8)
    card[:450, 0:800] = source_panel
    card[:450, 800:1600] = transition_panel
    card[:450, 1600:2400] = target_panel

    crop_size = (480, 570)
    source_crop = _crop_panel(
        source_frame,
        item["source"].get("bbox_xyxy"),
        "SOURCE SUBJECT",
        SOURCE_COLOR,
        crop_size,
    )
    target_crop = _crop_panel(
        target_frame,
        item["target"].get("bbox_xyxy"),
        "TARGET SUBJECT",
        TARGET_COLOR,
        crop_size,
    )
    card[480:1050, 20:500] = source_crop
    card[480:1050, 520:1000] = target_crop
    _draw_details(card, item, x=1040, y=510)
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
        _draw_box(panel, scaled, color, str(endpoint.get("tracklet_id") or ""), thickness=3)
    _title_bar(panel, f"{title}  f{endpoint.get('frame')}  t={endpoint.get('source_time_sec')}s", color)
    return panel


def _transition_panel(frame: np.ndarray, item: dict[str, Any], size: tuple[int, int]) -> np.ndarray:
    height, width = frame.shape[:2]
    panel = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    sx, sy = size[0] / width, size[1] / height
    for endpoint, color, prefix in (
        (item["source"], SOURCE_COLOR, "S"),
        (item["target"], TARGET_COLOR, "T"),
    ):
        bbox = endpoint.get("bbox_xyxy")
        if _is_bbox(bbox):
            scaled = [int(bbox[0] * sx), int(bbox[1] * sy), int(bbox[2] * sx), int(bbox[3] * sy)]
            _draw_box(panel, scaled, color, prefix, thickness=2)
    gap = item["transition"].get("gap_sec")
    _title_bar(panel, f"TRANSITION  gap={_fmt(gap, 3)}s  endpoint projections", TRANSITION_COLOR)
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
        cv2.putText(
            output,
            "NO BBOX",
            (max(20, output_width // 2 - 65), output_height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            MUTED,
            2,
            cv2.LINE_AA,
        )
        return output
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
    x1, x2 = max(0, min(width - 1, x1)), max(1, min(width, x2))
    y1, y2 = max(0, min(height - 1, y1)), max(1, min(height, y2))
    box_w, box_h = max(1, x2 - x1), max(1, y2 - y1)
    pad_x, pad_y = max(36, int(box_w * 1.35)), max(30, int(box_h * 0.65))
    left, top = max(0, x1 - pad_x), max(0, y1 - pad_y)
    right, bottom = min(width, x2 + pad_x), min(height, y2 + pad_y)
    crop = frame[top:bottom, left:right].copy()
    if crop.size:
        relative_bbox = [x1 - left, y1 - top, x2 - left, y2 - top]
        _highlight_subject(crop, relative_bbox, color)
        content_width = output_width - 8
        content_height = output_height - title_height - 8
        fitted = _fit_image(crop, content_width, content_height)
        oy = title_height + (content_height - fitted.shape[0]) // 2
        ox = 4 + (content_width - fitted.shape[1]) // 2
        output[oy : oy + fitted.shape[0], ox : ox + fitted.shape[1]] = fitted
    cv2.rectangle(output, (0, 0), (output_width - 1, output_height - 1), color, 3)
    cv2.rectangle(output, (0, 0), (output_width - 1, title_height), BACKGROUND, -1)
    cv2.putText(output, title, (12, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2, cv2.LINE_AA)
    return output


def _highlight_subject(
    image: np.ndarray,
    bbox: list[int],
    color: tuple[int, int, int],
) -> None:
    x1, y1, x2, y2 = bbox
    height, width = image.shape[:2]
    x1, x2 = max(0, min(width - 1, x1)), max(1, min(width - 1, x2))
    y1, y2 = max(0, min(height - 1, y1)), max(1, min(height - 1, y2))
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 5)
    center_x = (x1 + x2) // 2
    arrow_start_y = max(12, y1 - max(28, (y2 - y1) // 3))
    arrow_end_y = min(height - 1, max(1, y1 + 3))
    cv2.arrowedLine(
        image,
        (center_x, arrow_start_y),
        (center_x, arrow_end_y),
        color,
        5,
        cv2.LINE_AA,
        tipLength=0.28,
    )


def _draw_details(card: np.ndarray, item: dict[str, Any], *, x: int, y: int) -> None:
    decision = item["decision"]
    source = item["source"]
    target = item["target"]
    lines = [
        (
            f"#{item['audit_index']:02d}  {source['tracklet_id']} -> {target['tracklet_id']}  "
            f"relation={decision.get('current_identity_relation')}",
            TEXT,
        ),
        (
            f"subjects: {_join(decision.get('source_stable_subject_ids'))} -> "
            f"{_join(decision.get('target_stable_subject_ids'))}",
            TEXT,
        ),
        (
            f"quality: {decision.get('source_quality_class')} -> {decision.get('target_quality_class')}  "
            f"team: {source.get('team_label')} -> {target.get('team_label')}",
            MUTED,
        ),
        (
            f"cost={_fmt(decision.get('cost'), 4)}  confidence={_fmt(decision.get('base_confidence'), 4)}  "
            f"votes={decision.get('recommendation_votes')}/{decision.get('recommendation_votes_required')}",
            TEXT,
        ),
        (
            f"gap={_fmt(item['transition'].get('gap_sec'), 3)}s  distance={_fmt(decision.get('distance_m'), 3)}m  "
            f"speed={_fmt(decision.get('required_speed_mps'), 3)}m/s",
            TEXT,
        ),
        (
            f"appearance={_fmt(decision.get('appearance_distance_rgb'), 2)}  "
            f"velocity residual={_fmt(decision.get('velocity_prediction_distance_m'), 3)}m",
            MUTED,
        ),
        (f"evidence: {_join(decision.get('evidence'))}", MUTED),
        (f"occlusion events: {_join(decision.get('occlusion_event_ids'))}", MUTED),
        (f"candidate: {item.get('candidate_key')}", MUTED),
        ("MANUAL REVIEW: PENDING", TRANSITION_COLOR),
    ]
    for index, (line, color) in enumerate(lines):
        value = line if len(line) <= 135 else f"{line[:132]}..."
        cv2.putText(card, value, (x, y + index * 27), cv2.FONT_HERSHEY_SIMPLEX, 0.57, color, 1, cv2.LINE_AA)


def _write_contact_sheets(cards: list[np.ndarray], output_dir: Path, *, cards_per_sheet: int) -> list[str]:
    names: list[str] = []
    thumb_width, thumb_height = 1200, 540
    for start in range(0, len(cards), cards_per_sheet):
        group = cards[start : start + cards_per_sheet]
        rows = (len(group) + 1) // 2
        sheet = np.full((rows * thumb_height, 2400, 3), BACKGROUND, dtype=np.uint8)
        for index, card in enumerate(group):
            thumb = cv2.resize(card, (thumb_width, thumb_height), interpolation=cv2.INTER_AREA)
            row, column = divmod(index, 2)
            sheet[row * thumb_height : (row + 1) * thumb_height, column * thumb_width : (column + 1) * thumb_width] = thumb
        name = f"sheet-{(start // cards_per_sheet) + 1:03d}.jpg"
        path = output_dir / name
        if not cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            raise RuntimeError(f"Could not write audit contact sheet: {path}")
        names.append(name)
    return names


def _render_html(manifest: dict[str, Any]) -> str:
    items = []
    for item in manifest.get("items") or []:
        decision = item["decision"]
        candidate_key = escape(str(item["candidate_key"]))
        items.append(
            "".join(
                [
                    f'<article class="audit-card" data-candidate-key="{candidate_key}" data-review-status="pending">',
                    f'<img class="card-image" src="cards/{escape(str(item["card_filename"]))}" loading="lazy" alt="Stitch audit card" title="Click to enlarge">',
                    '<div class="audit-meta">',
                    f'<strong>#{int(item["audit_index"]):02d} {escape(item["source"]["tracklet_id"])} &rarr; {escape(item["target"]["tracklet_id"])}</strong>',
                    f'<span>{escape(str(decision.get("current_identity_relation")))} | cost {escape(_fmt(decision.get("cost"), 4))}</span>',
                    '<div class="review-actions">',
                    f'<button type="button" data-key="{candidate_key}" data-value="confirmed_same">Same person</button>',
                    f'<button type="button" data-key="{candidate_key}" data-value="confirmed_different">Different people</button>',
                    f'<button type="button" data-key="{candidate_key}" data-value="uncertain">Uncertain</button>',
                    "</div>",
                    '<span class="review-state">Pending</span>',
                    "</div></article>",
                ]
            )
        )
    summary = manifest.get("summary") or {}
    label = escape(str((manifest.get("benchmark") or {}).get("label") or "benchmark"))
    embedded_manifest = json.dumps(manifest, ensure_ascii=True).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Identity stitching audit - {label}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; background: #0b1220; color: #eef2f8; }}
    body {{ margin: 0; }}
    header {{ position: sticky; top: 0; z-index: 2; display: flex; gap: 24px; align-items: center; justify-content: space-between; padding: 18px 24px; background: #0b1220ee; border-bottom: 1px solid #273650; }}
    h1 {{ margin: 0 0 6px; font-size: 22px; letter-spacing: 0; }}
    p {{ margin: 0; color: #9aacbf; }}
    main {{ display: grid; gap: 18px; padding: 20px; }}
    .audit-card {{ border: 1px solid #273650; background: #101a2b; border-radius: 6px; overflow: hidden; }}
    .audit-card[data-review-status="confirmed_same"] {{ border-color: #22c55e; }}
    .audit-card[data-review-status="confirmed_different"] {{ border-color: #ef4444; }}
    .audit-card[data-review-status="uncertain"] {{ border-color: #facc15; }}
    .card-image {{ display: block; width: 100%; height: auto; cursor: zoom-in; }}
    .audit-meta {{ display: flex; flex-wrap: wrap; gap: 18px; align-items: center; padding: 12px 16px; }}
    .audit-meta span {{ color: #a9b8ca; }}
    .review-actions {{ display: flex; gap: 8px; margin-left: auto; }}
    button {{ border: 1px solid #3a4b67; border-radius: 5px; padding: 8px 12px; background: #172338; color: #eef2f8; font: inherit; cursor: pointer; }}
    button:hover {{ background: #22314a; }}
    button.active {{ border-color: #eef2f8; background: #30425f; }}
    #download {{ background: #16a34a; border-color: #16a34a; font-weight: 700; }}
    .review-state {{ min-width: 84px; text-align: right; }}
    #lightbox {{ position: fixed; inset: 0; z-index: 10; display: none; align-items: center; justify-content: center; padding: 24px; background: #020611f2; }}
    #lightbox.open {{ display: flex; }}
    #lightbox img {{ max-width: calc(100vw - 48px); max-height: calc(100vh - 48px); width: auto; height: auto; object-fit: contain; cursor: zoom-out; box-shadow: 0 12px 48px #000c; }}
    #lightbox-close {{ position: fixed; top: 18px; right: 18px; width: 44px; height: 44px; padding: 0; font-size: 28px; background: #101a2b; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Identity stitching visual audit: {label}</h1>
      <p><span id="progress">0/{int(summary.get("review_items") or 0)}</span> reviewed. Production identity remains unchanged.</p>
    </div>
    <button id="download" type="button">Download reviewed manifest</button>
  </header>
  <main>{''.join(items)}</main>
  <div id="lightbox" role="dialog" aria-modal="true" aria-label="Enlarged stitching audit card">
    <button id="lightbox-close" type="button" aria-label="Close enlarged image">&times;</button>
    <img id="lightbox-image" alt="Enlarged stitching audit card">
  </div>
  <script>
    const manifest = {embedded_manifest};
    const itemsByKey = new Map(manifest.items.map((item) => [item.candidate_key, item]));
    const statusLabels = {{ confirmed_same: "Same person", confirmed_different: "Different people", uncertain: "Uncertain" }};

    function updateProgress() {{
      const reviewed = manifest.items.filter((item) => item.manual_review.status !== "pending").length;
      document.getElementById("progress").textContent = `${{reviewed}}/${{manifest.items.length}}`;
      manifest.summary.pending = manifest.items.length - reviewed;
      manifest.summary.reviewed = reviewed;
    }}

    document.querySelectorAll("[data-value]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const key = button.dataset.key;
        const value = button.dataset.value;
        const item = itemsByKey.get(key);
        item.manual_review.status = value;
        item.manual_review.same_person = value === "confirmed_same" ? true : value === "confirmed_different" ? false : null;
        item.manual_review.reviewed_at = new Date().toISOString();
        const card = document.querySelector(`[data-candidate-key="${{key}}"]`);
        card.dataset.reviewStatus = value;
        card.querySelectorAll("[data-value]").forEach((candidate) => candidate.classList.toggle("active", candidate === button));
        card.querySelector(".review-state").textContent = statusLabels[value];
        updateProgress();
      }});
    }});

    const lightbox = document.getElementById("lightbox");
    const lightboxImage = document.getElementById("lightbox-image");
    function closeLightbox() {{
      lightbox.classList.remove("open");
      lightboxImage.removeAttribute("src");
    }}
    document.querySelectorAll(".card-image").forEach((image) => {{
      image.addEventListener("click", () => {{
        lightboxImage.src = image.src;
        lightbox.classList.add("open");
      }});
    }});
    document.getElementById("lightbox-close").addEventListener("click", closeLightbox);
    lightbox.addEventListener("click", (event) => {{
      if (event.target === lightbox || event.target === lightboxImage) closeLightbox();
    }});
    document.addEventListener("keydown", (event) => {{
      if (event.key === "Escape") closeLightbox();
    }});

    document.getElementById("download").addEventListener("click", () => {{
      const blob = new Blob([JSON.stringify(manifest, null, 2)], {{ type: "application/json" }});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "identity_stitching_audit_reviewed_{label}.json";
      link.click();
      URL.revokeObjectURL(link.href);
    }});
  </script>
</body>
</html>
"""


def _draw_box(
    image: np.ndarray,
    bbox: list[int],
    color: tuple[int, int, int],
    label: str,
    *,
    thickness: int,
) -> None:
    x1, y1, x2, y2 = bbox
    cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness)
    cv2.putText(image, label, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2, cv2.LINE_AA)


def _title_bar(image: np.ndarray, title: str, color: tuple[int, int, int]) -> None:
    cv2.rectangle(image, (0, 0), (image.shape[1], 34), BACKGROUND, -1)
    cv2.putText(image, title, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2, cv2.LINE_AA)


def _fit_image(image: np.ndarray, max_width: int, max_height: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(max_width / max(1, width), max_height / max(1, height))
    return cv2.resize(
        image,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
    )


def _is_bbox(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 4


def _short_key(value: str) -> str:
    return value.rsplit(":", 1)[-1][:10] if value else "unknown"


def _fmt(value: Any, digits: int) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _join(values: Any) -> str:
    rows = [str(value) for value in (values or [])]
    return ",".join(rows) if rows else "none"
