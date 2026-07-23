from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

from app.services.identity_jersey_number_common import canonical_digest, normalize_jersey_number


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_panel_readiness"
ALGORITHM_VERSION = "1.0.0"
MONTAGE_FILENAME = "identity_jersey_number_panel_readiness_montage.png"
READINESS_FILENAME = "identity_jersey_number_panel_readiness.json"
AUDIT_PANEL_SIZE = (160, 96)
HARD_BLOCKERS = (
    "no_manually_reviewed_montage",
    "assistant_only_label_ground_truth",
    "missing_bbox",
    "unknown_glyph_height",
    "median_glyph_height_below_8",
)


def validate_normalized_bbox(value: Any) -> tuple[float, float, float, float]:
    """Validate operator bbox without changing caller-owned data."""
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
    ):
        raise ValueError("number_panel_bbox_normalized must contain four finite numbers")
    bbox = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in bbox):
        raise ValueError("number_panel_bbox_normalized must contain four finite numbers")
    x1, y1, x2, y2 = bbox
    if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
        raise ValueError("number_panel_bbox_normalized must be within normalized bounds")
    return cast(tuple[float, float, float, float], bbox)


def normalized_bbox_to_pixels(
    bbox: Any,
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """Convert normalized xyxy to tight half-open pixels using floor/ceil/clamp."""
    if width <= 0 or height <= 0:
        raise ValueError("source image dimensions must be positive")
    x1, y1, x2, y2 = validate_normalized_bbox(bbox)
    pixels = (
        max(0, min(width, math.floor(x1 * width))),
        max(0, min(height, math.floor(y1 * height))),
        max(0, min(width, math.ceil(x2 * width))),
        max(0, min(height, math.ceil(y2 * height))),
    )
    if pixels[0] >= pixels[2] or pixels[1] >= pixels[3]:
        raise ValueError("normalized bbox produces an empty pixel panel")
    return pixels


def extract_number_panel(source_image: Any, bbox: Any) -> tuple[Any, tuple[int, int, int, int]]:
    """Extract a copy of tight panel; source image is never mutated."""
    height, width = source_image.shape[:2]
    pixels = normalized_bbox_to_pixels(bbox, width=width, height=height)
    x1, y1, x2, y2 = pixels
    return source_image[y1:y2, x1:x2].copy(), pixels


def raw_pixel_digest(image: Any) -> str:
    """Digest raw, contiguous pixel bytes (not PNG encoding)."""
    return hashlib.sha256(image.tobytes(order="C")).hexdigest()


def build_identity_jersey_number_panel_readiness(
    subject_review_doc: dict[str, Any],
    *,
    artifact_root: Path,
    output_root: Path | None = None,
    generated_at: str | None = None,
    montage_reviewed: bool | None = None,
) -> dict[str, Any]:
    """Build bounded, read-only panel extraction/readiness artifact.

    This consumes the already loaded operator subject-review document. It does
    not infer labels, train models, or write identity assignments.
    """
    _require_cv2()
    import cv2

    source = subject_review_doc
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for card in source.get("cards") or []:
        if not isinstance(card, dict):
            continue
        evidence = card.get("visual_evidence") or {}
        for crop in evidence.get("anchor_crops") or []:
            if isinstance(crop, dict):
                rows.append(_panel_row(card, crop, artifact_root=artifact_root, cv2=cv2))
    rows.sort(key=lambda row: (str(row.get("subject_id") or ""), int(row.get("frame") or 0), str(row["crop_id"])))

    if output_root is not None:
        output_root.mkdir(parents=True, exist_ok=True)
        montage_path = output_root / MONTAGE_FILENAME
        _write_montage(rows, montage_path, cv2=cv2)
        _write_panel_artifacts(rows, output_root, cv2=cv2)
    else:
        montage_path = None

    for row in rows:
        row.pop("_source_image", None)
        row.pop("_panel", None)
    summary = _summary(rows)
    blockers = _hard_blockers(source, rows, montage_reviewed=montage_reviewed)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_readiness_audit",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": {
            "bbox_rounding": "floor_min_ceil_max_clamp",
            "audit_panel_size": list(AUDIT_PANEL_SIZE),
        }},
        "source": {"subject_review_digest": canonical_digest(source)},
        "safety": {
            "mutates_subject_review": False,
            "writes_player_identity_assignments": False,
            "eligible_for_training": False,
            "eligible_for_identity": False,
        },
        "status": "ready" if not blockers else "blocked",
        "hard_blockers": blockers,
        "summary": summary,
        "montage": {
            "filename": MONTAGE_FILENAME,
            "written": montage_path is not None,
            "manually_reviewed": _montage_reviewed(source, montage_reviewed),
        },
        "panels": rows,
    }
    result = {
        "identity_jersey_number_panel_readiness": report,
        "identity_jersey_number_panel_readiness_report": report,
    }
    if output_root is not None:
        _write_json(output_root / READINESS_FILENAME, report)
    return result


def build_panel_readiness(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return build_identity_jersey_number_panel_readiness(*args, **kwargs)


def _panel_row(card: dict[str, Any], crop: dict[str, Any], *, artifact_root: Path, cv2: Any) -> dict[str, Any]:
    crop_id = str(crop.get("anchor_crop_id") or "")
    annotation = crop.get("number_panel_annotation")
    annotation = annotation if isinstance(annotation, dict) else {}
    source_artifact = str(annotation.get("number_panel_source_artifact") or "")
    crop_artifact = str(crop.get("torso_artifact") or crop.get("artifact") or "")
    row: dict[str, Any] = {
        "crop_id": crop_id,
        "subject_id": str(card.get("candidate_subject_id") or ""),
        "episode_id": _first_text(crop, "visibility_episode_id", "episode_id"),
        "tracklet_id": _first_text(crop, "tracklet_id"),
        "frame": crop.get("frame"),
        "expected": _expected_label(card, crop),
        "view": _first_text(annotation, "view") or _first_text(crop, "view") or "unknown",
        "status": "missing_bbox",
        "bbox_normalized": annotation.get("number_panel_bbox_normalized"),
        "pixel_bbox": None,
        "source_artifact": source_artifact,
        "panel_dimensions": None,
        "audit_panel_dimensions": list(AUDIT_PANEL_SIZE),
        "glyph_height_px": annotation.get("glyph_height_px"),
        "raw_pixel_sha256": None,
        "panel_artifact": None,
        "error": None,
    }
    if not annotation or "number_panel_bbox_normalized" not in annotation:
        row["error"] = "missing_bbox"
        return row
    try:
        bbox = validate_normalized_bbox(annotation["number_panel_bbox_normalized"])
    except ValueError as exc:
        row["status"] = "invalid_bbox"
        row["error"] = str(exc)
        return row
    row["bbox_normalized"] = list(bbox)
    if not source_artifact or source_artifact != crop_artifact or not _safe_relative_path(source_artifact):
        row["status"] = "stale_bbox"
        row["error"] = "number_panel_source_artifact does not match crop artifact"
        return row
    source_path = artifact_root / source_artifact
    if not source_path.is_file():
        row["status"] = "extraction_failed"
        row["error"] = "source artifact is missing"
        return row
    current_sha = _sha256(source_path)
    supplied_sha = annotation.get("number_panel_source_sha256")
    if supplied_sha is not None and str(supplied_sha) != str(current_sha):
        row["status"] = "stale_bbox"
        row["error"] = "source artifact digest changed"
        return row
    image = cv2.imread(str(source_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        row["status"] = "extraction_failed"
        row["error"] = "source artifact is not a readable image"
        return row
    try:
        panel, pixel_bbox = extract_number_panel(image, bbox)
    except ValueError as exc:
        row["status"] = "extraction_failed"
        row["error"] = str(exc)
        return row
    row.update({
        "status": "extracted",
        "pixel_bbox": list(pixel_bbox),
        "panel_dimensions": [int(panel.shape[1]), int(panel.shape[0])],
        "raw_pixel_sha256": raw_pixel_digest(panel),
        "_source_image": image,
        "_panel": panel,
    })
    return row


def _expected_label(card: dict[str, Any], crop: dict[str, Any]) -> dict[str, Any]:
    annotation = crop.get("jersey_number_annotation")
    annotation = annotation if isinstance(annotation, dict) else {}
    number_value = _first_value(annotation, "number", "expected_number", "digit_string")
    if number_value is None:
        number_value = _first_value(crop, "number", "expected_number", "digit_string")
    number = normalize_jersey_number(number_value) if number_value is not None else None
    state = str(_first_value(annotation, "label_state", "state", "visual_state") or
                _first_value(crop, "label_state", "state", "visual_state") or "unknown").strip().lower()
    source = str(_first_value(annotation, "annotation_source", "source", "label_source") or "unknown").strip().lower()
    return {
        "number": number,
        "state": state,
        "source": source,
        "assistant_only": source.startswith("assistant") or source in {"machine", "automatic", "auto"},
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    extracted = [row for row in rows if row["status"] == "extracted"]
    statuses = Counter(_label_bucket(row) for row in extracted)
    glyph_heights = [float(row["glyph_height_px"]) for row in rows if row.get("glyph_height_px") is not None]
    glyph_heights.sort()
    return {
        "total": len(rows),
        "readable": statuses.get("readable", 0),
        "partial": statuses.get("partial", 0),
        "plain": statuses.get("plain", 0),
        "unreadable": statuses.get("unreadable", 0),
        "extracted": len(extracted),
        "episodes": len({row["episode_id"] for row in rows if row.get("episode_id")}),
        "tracklets": len({row["tracklet_id"] for row in rows if row.get("tracklet_id")}),
        "subjects": len({row["subject_id"] for row in rows if row.get("subject_id")}),
        "per_number": _counts(row["expected"].get("number") for row in rows),
        "per_digit": _digit_counts(row["expected"].get("number") for row in rows),
        "per_view": _counts(row.get("view") for row in rows),
        "panel_dimension_distribution": _counts(
            "x".join(str(value) for value in row["panel_dimensions"])
            for row in extracted
        ),
        "glyph_height_distribution": _distribution(glyph_heights),
        "missing_bbox": sum(row["status"] == "missing_bbox" for row in rows),
        "invalid_bbox": sum(row["status"] == "invalid_bbox" for row in rows),
        "stale_bbox": sum(row["status"] == "stale_bbox" for row in rows),
        "extraction_failures": sum(row["status"] == "extraction_failed" for row in rows),
    }


def _label_bucket(row: dict[str, Any]) -> str:
    expected = row["expected"]
    state = expected.get("state")
    if state in {"number_absent", "plain", "none", "no_number"}:
        return "plain"
    if state in {"number_unreadable", "unreadable", "unknown"} and expected.get("number") is None:
        return "unreadable"
    if state in {"partial", "number_partial"} or str(row.get("view")) == "partial":
        return "partial"
    return "readable" if expected.get("number") is not None or state == "number_confirmed" else "unreadable"


def _hard_blockers(
    source: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    montage_reviewed: bool | None,
) -> list[str]:
    blockers: list[str] = []
    if not _montage_reviewed(source, montage_reviewed):
        blockers.append("no_manually_reviewed_montage")
    if any(row["expected"].get("assistant_only") for row in rows):
        blockers.append("assistant_only_label_ground_truth")
    if any(row["status"] == "missing_bbox" for row in rows):
        blockers.append("missing_bbox")
    if any(row.get("glyph_height_px") is None for row in rows):
        blockers.append("unknown_glyph_height")
    heights = sorted(float(row["glyph_height_px"]) for row in rows if row.get("glyph_height_px") is not None)
    if heights and _median(heights) < 8.0:
        blockers.append("median_glyph_height_below_8")
    return [blocker for blocker in HARD_BLOCKERS if blocker in blockers]


def _montage_reviewed(source: dict[str, Any], explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit is True
    if source.get("manually_reviewed_montage") is True or source.get("montage_reviewed") is True:
        return True
    review = source.get("montage_review")
    return isinstance(review, dict) and review.get("status") in {"reviewed", "approved"}


def _write_panel_artifacts(rows: list[dict[str, Any]], output_root: Path, *, cv2: Any) -> None:
    for row in rows:
        panel = row.pop("_panel", None)
        row.pop("_source_image", None)
        if panel is None:
            continue
        filename = f"panel_{hashlib.sha256(str(row['crop_id']).encode('utf-8')).hexdigest()[:16]}.png"
        target = output_root / "panels" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target), panel):
            row["status"] = "extraction_failed"
            row["error"] = "could not write panel PNG"
        else:
            row["panel_artifact"] = str(Path("panels") / filename)


def _write_montage(rows: list[dict[str, Any]], path: Path, *, cv2: Any) -> None:
    import numpy as np

    tiles: list[Any] = []
    for row in rows:
        if row.get("status") != "extracted":
            continue
        source = row.get("_source_image")
        panel = row.get("_panel")
        if source is None or panel is None:
            continue
        overlay = source.copy()
        x1, y1, x2, y2 = row["pixel_bbox"]
        cv2.rectangle(overlay, (x1, y1), (x2 - 1, y2 - 1), (0, 0, 255), 2)
        overlay = _fit_image(overlay, 220, 160, cv2=cv2)
        panel_view = _fit_image(panel, 220, 160, cv2=cv2)
        audit = _fit_image(panel, *AUDIT_PANEL_SIZE, cv2=cv2)
        tile = np.full((245, 700, 3), 245, dtype=np.uint8)
        tile[8:168, 8:228] = overlay
        tile[8:168, 238:458] = panel_view
        tile[32:128, 478:638] = audit
        label = f"crop={row['crop_id']} episode={row.get('episode_id') or '-'} tracklet={row.get('tracklet_id') or '-'}"
        expected = row["expected"]
        label2 = f"expected={expected.get('number') or expected.get('state') or '-'} view={row.get('view')}"
        cv2.putText(tile, label[:110], (8, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (10, 10, 10), 1, cv2.LINE_AA)
        cv2.putText(tile, label2[:110], (8, 214), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (10, 10, 10), 1, cv2.LINE_AA)
        tiles.append(tile)
    montage = np.vstack(tiles) if tiles else np.full((32, 32, 3), 245, dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), montage)


def _fit_image(image: Any, width: int, height: int, *, cv2: Any) -> Any:
    import numpy as np

    canvas = np.full((height, width, 3), 245, dtype=np.uint8)
    if len(image.shape) == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        image = image[:, :, :3]
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(image, (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas


def _counts(values: Any) -> dict[str, int]:
    counter = Counter(str(value) for value in values if value not in (None, ""))
    return dict(sorted(counter.items()))


def _digit_counts(values: Any) -> dict[str, int]:
    counter = Counter(char for value in values if value not in (None, "") for char in str(value))
    return dict(sorted(counter.items()))


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "median": None, "values": []}
    return {"count": len(values), "min": values[0], "max": values[-1], "median": _median(values), "values": values}


def _median(values: list[float]) -> float:
    middle = len(values) // 2
    return round(values[middle], 3) if len(values) % 2 else round((values[middle - 1] + values[middle]) / 2.0, 3)


def _first_text(value: dict[str, Any], *keys: str) -> str | None:
    result = _first_value(value, *keys)
    text = str(result or "").strip()
    return text or None


def _first_value(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if value.get(key) is not None:
            return value[key]
    return None


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value and not path.is_absolute() and ".." not in path.parts)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


def _require_cv2() -> None:
    try:
        import cv2  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for jersey panel extraction") from exc
