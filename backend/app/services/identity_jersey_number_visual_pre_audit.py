from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_jersey_number_visual_pre_audit"
ALGORITHM_VERSION = "1.1.0"


def build_identity_jersey_number_visual_pre_audit(
    subject_review_doc: dict[str, Any],
    *,
    crop_root: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    review_digest = canonical_digest(subject_review_doc)
    crop_rows = _crop_rows(subject_review_doc)
    suggestions = [
        _suggestion(row, crop_root=crop_root, source_review_digest=review_digest)
        for row in crop_rows
    ]
    statuses = {
        status: sum(row["status"] == status for row in suggestions)
        for status in sorted({row["status"] for row in suggestions})
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_visual_pre_audit",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": {}},
        "source": {
            "subject_review_digest": review_digest,
            "review_crop_entries_digest": canonical_digest(crop_rows),
        },
        "safety": {
            "mutates_subject_review": False,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "eligible_for_training": False,
            "eligible_for_player_stats": False,
            "writes_player_identity_assignments": False,
            "automatic_assignments": 0,
        },
        "summary": {
            "crop_entries": len(crop_rows),
            "suggestions": len(suggestions),
            "status_counts": statuses,
        },
        "suggestions": suggestions,
    }


def _crop_rows(subject_review_doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in subject_review_doc.get("cards") or []:
        if not isinstance(card, dict):
            continue
        for crop in ((card.get("visual_evidence") or {}).get("anchor_crops") or []):
            if isinstance(crop, dict) and crop.get("anchor_crop_id"):
                rows.append(
                    {
                        "review_card_key": card.get("review_card_key"),
                        "candidate_subject_id": card.get("candidate_subject_id"),
                        "anchor_crop_id": crop.get("anchor_crop_id"),
                        "artifact": crop.get("torso_artifact") or crop.get("artifact"),
                    }
                )
    return sorted(rows, key=lambda row: (str(row["review_card_key"] or ""), str(row["anchor_crop_id"])))


def _suggestion(
    row: dict[str, Any],
    *,
    crop_root: Path,
    source_review_digest: str,
) -> dict[str, Any]:
    artifact = str(row.get("artifact") or "")
    path = crop_root / artifact
    base = {
        **row,
        "source_review_digest": source_review_digest,
        "source_crop_digest": canonical_digest(row),
        "crop_sha256": _sha256(path) if _safe_artifact_path(artifact) else None,
        "status": "unavailable",
        "jersey_number_visual_diagnostics": _unknown_diagnostics(),
    }
    if not _safe_artifact_path(artifact) or not path.is_file():
        base["status"] = "missing_crop"
    else:
        try:
            import cv2
            import numpy as np
        except ImportError:
            base["status"] = "opencv_unavailable"
        else:
            image = cv2.imread(str(path))
            if image is None:
                base["status"] = "corrupt_crop"
            else:
                base["status"] = "audited"
                base["jersey_number_visual_diagnostics"] = _visual_diagnostics(image, cv2=cv2, np=np)
    base["row_digest"] = canonical_digest(base)
    return base


def _visual_diagnostics(image: Any, *, cv2: Any, np: Any) -> dict[str, Any]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    contrast = float(np.percentile(gray, 95) - np.percentile(gray, 5))
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    normalized_laplacian = min(1.0, max(0.0, float(np.var(laplacian)) / (float(np.var(gray)) + 1.0)))
    _, panel_reliable, digit_evidence = _panel_evidence(gray, cv2=cv2, np=np)
    return {
        "digit_signal": "likely_full" if panel_reliable and digit_evidence else "likely_partial" if panel_reliable else "indeterminate",
        "normalized_laplacian": round(normalized_laplacian, 6),
        "panel_detected": panel_reliable,
        "brightness": round(brightness, 3),
        "contrast": round(contrast, 3),
    }


def _panel_evidence(gray: Any, *, cv2: Any, np: Any) -> tuple[float | None, bool, bool]:
    height, width = gray.shape[:2]
    threshold = float(np.percentile(gray, 90))
    mask = (gray >= threshold).astype("uint8") * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, panel_width, panel_height = cv2.boundingRect(contour)
        center_x = (x + panel_width / 2.0) / max(1.0, width)
        ratio = panel_height / max(1.0, height)
        if 0.25 <= center_x <= 0.75 and 0.15 <= ratio <= 0.90 and panel_width >= width * 0.12:
            candidates.append((panel_width * panel_height, x, y, panel_width, panel_height, ratio))
    if not candidates:
        return None, False, False
    _, x, y, panel_width, panel_height, ratio = max(candidates)
    panel = gray[y : y + panel_height, x : x + panel_width]
    edges = cv2.Canny(panel, 40, 120)
    edge_ratio = float(np.mean(edges > 0))
    return round(float(ratio), 6), True, edge_ratio >= 0.08


def _unknown_diagnostics() -> dict[str, Any]:
    return {
        "digit_signal": "indeterminate",
    }


def _safe_artifact_path(artifact: str) -> bool:
    path = Path(artifact)
    return bool(artifact and not path.is_absolute() and ".." not in path.parts)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()
