from __future__ import annotations

"""Read-only crop quality summaries and compact diagnostic montages."""

from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def build_cross_capture_crop_diagnostics(
    *,
    reference_gallery: dict[str, Any],
    target_anchor_crops: dict[str, Any],
    reference_root: Path,
    target_root: Path,
    output_directory: Path,
    reference_crop_report: dict[str, Any] | None = None,
    target_crop_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize existing crops and render one bounded montage per domain."""

    output_directory.mkdir(parents=True, exist_ok=True)
    reference_groups = [
        (
            str(player.get("player_name") or player.get("player_id") or "?"),
            [
                crop
                for domain in player.get("capture_domains") or []
                for crop in domain.get("crops") or []
                if isinstance(crop, dict)
            ],
        )
        for player in reference_gallery.get("players") or []
        if isinstance(player, dict)
    ]
    target_groups = [
        (
            str(card.get("candidate_subject_id") or "?"),
            [
                crop
                for crop in card.get("anchor_crops") or []
                if isinstance(crop, dict)
            ],
        )
        for card in target_anchor_crops.get("cards") or []
        if isinstance(card, dict)
    ]
    h1_montage = output_directory / "h1_reference_crops_montage.jpg"
    h2_montage = output_directory / "h2_query_crops_montage.jpg"
    h1_montage_result = _write_montage(
        reference_groups,
        reference_root,
        h1_montage,
    )
    h2_montage_result = _write_montage(
        target_groups,
        target_root,
        h2_montage,
    )
    return {
        "h1": _quality_summary(reference_groups, reference_root, reference_crop_report),
        "h2": _quality_summary(target_groups, target_root, target_crop_report),
        "montages": {
            "h1_reference_crops": h1_montage_result,
            "h2_query_crops": h2_montage_result,
        },
        "safety": {
            "read_only": True,
            "changes_crop_selector_thresholds": False,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
        },
    }


def _quality_summary(
    groups: list[tuple[str, list[dict[str, Any]]]],
    root: Path,
    crop_report: dict[str, Any] | None,
) -> dict[str, Any]:
    dimensions: list[tuple[int, int]] = []
    bbox_areas: list[float] = []
    brightness: list[float] = []
    blur: list[float] = []
    missing = 0
    invalid = 0
    crop_count = 0
    crops_per_group: list[int] = []
    for _label, crops in groups:
        crops_per_group.append(len(crops))
        for crop in crops:
            crop_count += 1
            bbox = crop.get("bbox_xyxy") or []
            if len(bbox) == 4:
                bbox_areas.append(
                    max(0.0, float(bbox[2]) - float(bbox[0]))
                    * max(0.0, float(bbox[3]) - float(bbox[1]))
                )
            image = cv2.imread(str(root / str(crop.get("artifact") or "")))
            if image is None:
                missing += 1
                continue
            if image.size == 0:
                invalid += 1
                continue
            height, width = image.shape[:2]
            dimensions.append((width, height))
            grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            brightness.append(float(grayscale.mean()))
            blur.append(float(cv2.Laplacian(grayscale, cv2.CV_64F).var()))
    rejection_counts = (
        (crop_report or {}).get("summary", {}).get("rejection_counts")
        or (crop_report or {}).get("rejection_counts")
        or {}
    )
    return {
        "groups": len(groups),
        "crop_count": crop_count,
        "crop_dimensions": _distribution(
            [f"{width}x{height}" for width, height in dimensions]
        ),
        "bbox_area": _numeric_summary(bbox_areas),
        "blur_laplacian_variance": _numeric_summary(blur),
        "brightness_gray_mean": _numeric_summary(brightness),
        "crops_per_group": _numeric_summary(
            [float(value) for value in crops_per_group]
        ),
        "missing_artifacts": missing,
        "invalid_artifacts": invalid,
        "rejection_waterfall": dict(sorted(rejection_counts.items())),
    }


def _write_montage(
    groups: list[tuple[str, list[dict[str, Any]]]],
    root: Path,
    output_path: Path,
) -> dict[str, str | None]:
    cells: list[np.ndarray] = []
    for label, crops in groups:
        if not crops:
            continue
        crop = max(
            crops,
            key=lambda row: float(row.get("selection_score") or 0.0),
        )
        image = cv2.imread(str(root / str(crop.get("artifact") or "")))
        if image is None or image.size == 0:
            continue
        thumb = cv2.resize(image, (80, 160), interpolation=cv2.INTER_AREA)
        canvas = np.full((190, 180, 3), 20, dtype=np.uint8)
        canvas[5:165, 50:130] = thumb
        cv2.putText(
            canvas,
            label[:24],
            (6, 182),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
        cells.append(canvas)
    if not cells:
        return {
            "montage_status": "unavailable_no_valid_crops",
            "montage_path": None,
        }
    columns = 5
    rows = (len(cells) + columns - 1) // columns
    montage = np.full((rows * 190, columns * 180, 3), 12, dtype=np.uint8)
    for index, cell in enumerate(cells):
        row, column = divmod(index, columns)
        montage[
            row * 190 : (row + 1) * 190,
            column * 180 : (column + 1) * 180,
        ] = cell
    cv2.imwrite(str(output_path), montage)
    return {
        "montage_status": "ready",
        "montage_path": str(output_path),
    }


def _numeric_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    sorted_values = sorted(values)
    middle = len(sorted_values) // 2
    median = (
        sorted_values[middle]
        if len(sorted_values) % 2
        else (sorted_values[middle - 1] + sorted_values[middle]) / 2.0
    )
    return {
        "count": len(values),
        "min": round(sorted_values[0], 4),
        "median": round(median, 4),
        "max": round(sorted_values[-1], 4),
    }


def _distribution(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
