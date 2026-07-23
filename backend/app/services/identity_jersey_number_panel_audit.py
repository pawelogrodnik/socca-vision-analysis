from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from statistics import median
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_jersey_number_common import normalize_normalized_bbox
from app.services.identity_jersey_number_common import normalize_safe_relative_artifact_path


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_panel_audit"
ALGORITHM_VERSION = "0.1.0"
PANEL_RESIZE_WIDTH = 96
PANEL_RESIZE_HEIGHT = 64
MONTAGE_FILENAME = "number_panel_montage.jpg"
READINESS_FILENAME = "number_panel_dataset_readiness.json"
MIN_READABLE_CROPS = 50
MIN_READABLE_EPISODES = 20
MIN_NEGATIVES = 30
MIN_MEDIAN_DIGIT_HEIGHT = 8.0


def audit_identity_jersey_number_panels(
    dataset_doc: dict[str, Any],
    *,
    output_root: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    samples = [dict(row) for row in dataset_doc.get("samples") or [] if isinstance(row, dict)]
    ordered = sorted(
        samples,
        key=lambda row: (
            str(row.get("source_match_key") or ""),
            str(row.get("source_video_key") or ""),
            int(row.get("frame") or 0),
            str(row.get("anchor_crop_id") or ""),
        ),
    )
    rows = [_audit_row(row) for row in ordered]
    output_root.mkdir(parents=True, exist_ok=True)
    montage_path = output_root / MONTAGE_FILENAME
    _write_montage(rows, montage_path)
    summary = _summary(rows, dataset_doc)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "mode": "shadow_panel_readiness_audit",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": {
                "panel_resize_width": PANEL_RESIZE_WIDTH,
                "panel_resize_height": PANEL_RESIZE_HEIGHT,
                "minimum_readable_crops": MIN_READABLE_CROPS,
                "minimum_readable_visibility_episodes": MIN_READABLE_EPISODES,
                "minimum_negative_crops": MIN_NEGATIVES,
                "minimum_median_digit_height_px": MIN_MEDIAN_DIGIT_HEIGHT,
            },
        },
        "source": {
            "dataset_digest": dataset_doc.get("dataset_digest"),
            "dataset_version": dataset_doc.get("dataset_version"),
            "dataset_summary_digest": canonical_digest(dataset_doc.get("summary") or {}),
            "samples": len(ordered),
        },
        "outputs": {
            "number_panel_dataset_readiness": READINESS_FILENAME,
            "number_panel_montage": MONTAGE_FILENAME,
        },
        "summary": summary,
        "gates": {
            "readable_panel_crop_minimum": summary["readable_full_number_crops"] >= MIN_READABLE_CROPS,
            "readable_visibility_episode_minimum": summary["readable_visibility_episodes"] >= MIN_READABLE_EPISODES,
            "negative_crop_minimum": summary["negative_crops"] >= MIN_NEGATIVES,
            "median_digit_height_minimum": (
                summary["estimated_digit_height_px"]["median"] is not None
                and float(summary["estimated_digit_height_px"]["median"]) >= MIN_MEDIAN_DIGIT_HEIGHT
            ),
            "manual_panel_audit_required": True,
        },
        "samples": rows,
    }
    report["status"] = (
        "ready_for_panel_digit_experiment"
        if (
            report["gates"]["readable_panel_crop_minimum"]
            and report["gates"]["readable_visibility_episode_minimum"]
            and report["gates"]["negative_crop_minimum"]
            and report["gates"]["median_digit_height_minimum"]
        )
        else "insufficient_panel_readiness"
    )
    return report


def _audit_row(sample: dict[str, Any]) -> dict[str, Any]:
    cv2, _ = _image_libs()
    artifact_root = Path(str(sample.get("artifact_root") or ""))
    base_artifact = normalize_safe_relative_artifact_path(
        sample.get("artifact"),
        field_name="artifact",
    )
    panel_artifact = normalize_safe_relative_artifact_path(
        sample.get("number_panel_artifact"),
        field_name="number_panel_artifact",
    )
    panel_bbox = normalize_normalized_bbox(
        sample.get("number_panel_bbox_normalized"),
        field_name="number_panel_bbox_normalized",
    )
    row = {
        "sample_key": sample.get("sample_key"),
        "anchor_crop_id": sample.get("anchor_crop_id"),
        "source_match_key": sample.get("source_match_key"),
        "source_video_key": sample.get("source_video_key"),
        "candidate_subject_id": sample.get("candidate_subject_id"),
        "tracklet_id": sample.get("tracklet_id"),
        "visibility_episode_id": sample.get("visibility_episode_id"),
        "frame": int(sample.get("frame") or 0),
        "view": sample.get("view"),
        "label_state": sample.get("label_state"),
        "number": sample.get("number"),
        "digit_visibility": sample.get("digit_visibility"),
        "artifact": base_artifact,
        "number_panel_artifact": panel_artifact,
        "number_panel_bbox_normalized": panel_bbox,
        "panel_source_kind": (
            "explicit_panel_artifact"
            if panel_artifact
            else "deterministic_crop_from_artifact"
            if panel_bbox and base_artifact
            else "missing_panel_definition"
        ),
    }
    source_name = panel_artifact or base_artifact
    source_path = artifact_root / (source_name or "")
    row["panel_source_path"] = str(source_path) if source_name else None
    if panel_artifact is None and panel_bbox is None:
        return {
            **row,
            "status": "missing_panel_bbox",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    if panel_artifact is not None and not source_path.is_file():
        return {
            **row,
            "status": "missing_panel_artifact",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    if panel_artifact is None and (base_artifact is None or not source_path.is_file()):
        return {
            **row,
            "status": "missing_source_artifact",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    image = cv2.imread(str(source_path))
    if image is None:
        return {
            **row,
            "status": "corrupt_source_artifact",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    panel = image if panel_artifact is not None else _crop_panel(image, panel_bbox)
    if panel is None or panel.size == 0:
        return {
            **row,
            "status": "empty_panel_crop",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    resized = cv2.resize(panel, (PANEL_RESIZE_WIDTH, PANEL_RESIZE_HEIGHT), interpolation=cv2.INTER_AREA)
    return {
        **row,
        "status": "audited",
        "panel_digest": _image_digest(panel),
        "panel_width_px": int(panel.shape[1]),
        "panel_height_px": int(panel.shape[0]),
        "resized_panel_shape": [PANEL_RESIZE_HEIGHT, PANEL_RESIZE_WIDTH],
        "estimated_digit_height_px": _estimate_digit_height(resized),
    }


def _crop_panel(image: Any, bbox: list[float] | None) -> Any:
    if bbox is None:
        return None
    _, np = _image_libs()
    height, width = image.shape[:2]
    x1 = max(0, min(width - 1, int(np.floor(bbox[0] * width))))
    y1 = max(0, min(height - 1, int(np.floor(bbox[1] * height))))
    x2 = max(x1 + 1, min(width, int(np.ceil(bbox[2] * width))))
    y2 = max(y1 + 1, min(height, int(np.ceil(bbox[3] * height))))
    if x2 <= x1 or y2 <= y1:
        return None
    return image[y1:y2, x1:x2].copy()


def _image_digest(image: Any) -> str:
    payload = hashlib.sha256()
    payload.update(str(tuple(image.shape)).encode("utf-8"))
    payload.update(image.tobytes())
    return payload.hexdigest()


def _estimate_digit_height(image: Any) -> float | None:
    cv2, _ = _image_libs()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int]] = []
    area_floor = max(6, int(round(image.shape[0] * image.shape[1] * 0.003)))
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = width * height
        if area < area_floor or height < 4 or width < 2:
            continue
        if height > image.shape[0] * 0.95 or width > image.shape[1] * 0.95:
            continue
        boxes.append((x, y, width, height))
    if not boxes:
        return None
    top = min(box[1] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return round(float(bottom - top), 3)


def _summary(rows: list[dict[str, Any]], dataset_doc: dict[str, Any]) -> dict[str, Any]:
    audited = [row for row in rows if row["status"] == "audited"]
    readable = [row for row in audited if row.get("label_state") == "number_confirmed" and row.get("number")]
    readable_full = [row for row in readable if row.get("digit_visibility") != "partial"]
    readable_partial = [
        row
        for row in readable
        if row.get("digit_visibility") == "partial"
    ]
    plain_shirt = [row for row in audited if row.get("label_state") == "number_absent"]
    unreadable = [row for row in audited if row.get("label_state") == "number_unreadable"]
    digits = Counter()
    for row in readable_full:
        for digit in str(row.get("number") or ""):
            digits[digit] += 1
    readable_episode_ids = {
        str(row.get("visibility_episode_id"))
        for row in readable_full
        if row.get("visibility_episode_id")
    }
    return {
        "total_samples": len(rows),
        "total_panel_crops": len(audited),
        "readable_full_number_crops": len(readable_full),
        "partial_number_crops": len(readable_partial),
        "plain_shirt_crops": len(plain_shirt),
        "unreadable_crops": len(unreadable),
        "negative_crops": len(plain_shirt) + len(unreadable),
        "unique_visibility_episodes": len(
            {str(row.get("visibility_episode_id")) for row in audited if row.get("visibility_episode_id")}
        ),
        "readable_visibility_episodes": len(readable_episode_ids),
        "unique_tracklets": len({str(row.get("tracklet_id")) for row in audited if row.get("tracklet_id")}),
        "unique_subjects": len({str(row.get("candidate_subject_id")) for row in audited if row.get("candidate_subject_id")}),
        "counts_per_number": dict(sorted(Counter(str(row.get("number")) for row in readable_full).items())),
        "counts_per_digit": {digit: digits.get(digit, 0) for digit in [str(index) for index in range(10)]},
        "counts_per_view": dict(
            sorted(Counter(str(row.get("view") or "unknown") for row in audited).items())
        ),
        "panel_width_px": _distribution([float(row["panel_width_px"]) for row in audited if row.get("panel_width_px")]),
        "panel_height_px": _distribution([float(row["panel_height_px"]) for row in audited if row.get("panel_height_px")]),
        "estimated_digit_height_px": _distribution(
            [float(row["estimated_digit_height_px"]) for row in audited if row.get("estimated_digit_height_px") is not None]
        ),
        "missing_panel_bbox_count": sum(row["status"] == "missing_panel_bbox" for row in rows),
        "status_counts": dict(sorted(Counter(str(row["status"]) for row in rows).items())),
        "dataset_samples": (dataset_doc.get("summary") or {}).get("samples"),
    }


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "median": round(float(median(values)), 3),
        "max": round(max(values), 3),
    }


def _write_montage(rows: list[dict[str, Any]], path: Path) -> None:
    cv2, np = _image_libs()
    cards = [_render_row(row) for row in rows] or [_placeholder_card("No panel samples")]
    columns = 2 if len(cards) > 1 else 1
    card_height, card_width = cards[0].shape[:2]
    rows_needed = int(np.ceil(len(cards) / columns))
    canvas = np.full((rows_needed * card_height, columns * card_width, 3), 18, dtype=np.uint8)
    for index, card in enumerate(cards):
        row = index // columns
        column = index % columns
        top = row * card_height
        left = column * card_width
        canvas[top : top + card_height, left : left + card_width] = card
    cv2.imwrite(str(path), canvas)


def _render_row(row: dict[str, Any]) -> Any:
    _, np = _image_libs()
    height = 260
    width = 900
    canvas = np.full((height, width, 3), 20, dtype=np.uint8)
    _draw_text(canvas, 18, 26, f"{row.get('anchor_crop_id')} | {row.get('label_state')} | {row.get('number') or '-'}")
    _draw_text(
        canvas,
        18,
        48,
        f"f{row.get('frame')} | tracklet {row.get('tracklet_id') or '-'} | episode {row.get('visibility_episode_id') or '-'}",
        scale=0.45,
    )
    _draw_text(
        canvas,
        18,
        68,
        f"status: {row.get('status')} | digit-h: {row.get('estimated_digit_height_px') or 'n/a'}",
        scale=0.45,
    )
    source = _render_source_preview(row)
    panel = _render_panel_preview(row)
    resized = _render_resized_preview(row)
    canvas[86:246, 18:258] = source
    canvas[86:246, 274:514] = panel
    canvas[86:246, 530:770] = resized
    _draw_text(canvas, 18, 84, "source", scale=0.45)
    _draw_text(canvas, 274, 84, "panel", scale=0.45)
    _draw_text(canvas, 530, 84, "panel 96x64", scale=0.45)
    return canvas


def _render_source_preview(row: dict[str, Any]) -> Any:
    cv2, np = _image_libs()
    preview = np.full((160, 240, 3), 36, dtype=np.uint8)
    path_text = row.get("panel_source_path")
    if not isinstance(path_text, str) or not Path(path_text).is_file():
        return _placeholder_into(preview, "missing source")
    image = cv2.imread(path_text)
    if image is None:
        return _placeholder_into(preview, "corrupt source")
    if row.get("number_panel_artifact") is None and row.get("number_panel_bbox_normalized") is not None:
        bbox = row["number_panel_bbox_normalized"]
        draw = image.copy()
        height, width = draw.shape[:2]
        x1 = int(round(float(bbox[0]) * width))
        y1 = int(round(float(bbox[1]) * height))
        x2 = int(round(float(bbox[2]) * width))
        y2 = int(round(float(bbox[3]) * height))
        cv2.rectangle(draw, (x1, y1), (x2, y2), (0, 200, 255), 2)
        image = draw
    return _fit_image(image, preview.shape[1], preview.shape[0])


def _render_panel_preview(row: dict[str, Any]) -> Any:
    _, np = _image_libs()
    preview = np.full((160, 240, 3), 36, dtype=np.uint8)
    image = _load_panel_image(row)
    return _fit_image(image, preview.shape[1], preview.shape[0]) if image is not None else _placeholder_into(preview, "missing panel")


def _render_resized_preview(row: dict[str, Any]) -> Any:
    cv2, np = _image_libs()
    preview = np.full((160, 240, 3), 36, dtype=np.uint8)
    image = _load_panel_image(row)
    if image is None:
        return _placeholder_into(preview, "missing panel")
    resized = cv2.resize(image, (PANEL_RESIZE_WIDTH, PANEL_RESIZE_HEIGHT), interpolation=cv2.INTER_AREA)
    return _fit_image(resized, preview.shape[1], preview.shape[0])


def _load_panel_image(row: dict[str, Any]) -> Any:
    cv2, _ = _image_libs()
    path_text = row.get("panel_source_path")
    if not isinstance(path_text, str) or not Path(path_text).is_file():
        return None
    image = cv2.imread(path_text)
    if image is None:
        return None
    if row.get("number_panel_artifact") is not None:
        return image
    return _crop_panel(image, row.get("number_panel_bbox_normalized"))


def _fit_image(image: Any, width: int, height: int) -> Any:
    cv2, np = _image_libs()
    canvas = np.full((height, width, 3), 36, dtype=np.uint8)
    scale = min(width / max(1, image.shape[1]), height / max(1, image.shape[0]))
    target_width = max(1, int(round(image.shape[1] * scale)))
    target_height = max(1, int(round(image.shape[0] * scale)))
    resized = cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_AREA)
    top = (height - target_height) // 2
    left = (width - target_width) // 2
    canvas[top : top + target_height, left : left + target_width] = resized
    return canvas


def _placeholder_card(text: str) -> Any:
    _, np = _image_libs()
    canvas = np.full((260, 900, 3), 20, dtype=np.uint8)
    return _placeholder_into(canvas, text)


def _placeholder_into(canvas: Any, text: str) -> Any:
    _draw_text(canvas, 18, max(24, canvas.shape[0] // 2), text)
    return canvas


def _draw_text(
    image: Any,
    x: int,
    y: int,
    text: str,
    *,
    scale: float = 0.55,
    color: tuple[int, int, int] = (230, 230, 230),
) -> None:
    cv2, _ = _image_libs()
    cv2.putText(
        image,
        text[:90],
        (x, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        1,
        cv2.LINE_AA,
    )


def _image_libs() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV and numpy are required for jersey number panel audits") from exc
    return cv2, np
