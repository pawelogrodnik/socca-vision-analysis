from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_quality_smoke_report(
    matches_dir: Path,
    *,
    match_ids: list[str] | None = None,
    min_score: float = 70.0,
    max_ghost_boxes: int = 0,
    max_low_visible_rate: float = 0.35,
    max_predicted_visible_boxes: int = 0,
) -> dict[str, Any]:
    match_paths = _selected_match_paths(matches_dir, match_ids)
    rows = [
        _match_quality_row(
            path,
            min_score=min_score,
            max_ghost_boxes=max_ghost_boxes,
            max_low_visible_rate=max_low_visible_rate,
            max_predicted_visible_boxes=max_predicted_visible_boxes,
        )
        for path in match_paths
    ]
    checked = [row for row in rows if row["status"] == "checked"]
    failed = [row for row in rows if not row["passed"]]
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "status": "passed" if not failed else "failed",
        "thresholds": {
            "min_score": min_score,
            "max_ghost_boxes": max_ghost_boxes,
            "max_low_visible_rate": max_low_visible_rate,
            "max_predicted_visible_boxes": max_predicted_visible_boxes,
        },
        "summary": {
            "matches": len(rows),
            "checked": len(checked),
            "passed": sum(1 for row in rows if row["passed"]),
            "failed": len(failed),
            "missing_quality_report": sum(1 for row in rows if row["status"] == "missing_quality_report"),
            "average_score": round(
                sum(float(row["score"]) for row in checked) / len(checked),
                2,
            )
            if checked
            else 0.0,
        },
        "matches": rows,
    }


def _selected_match_paths(matches_dir: Path, match_ids: list[str] | None) -> list[Path]:
    if match_ids:
        return [matches_dir / match_id for match_id in match_ids]
    if not matches_dir.exists():
        return []
    return sorted([path for path in matches_dir.iterdir() if path.is_dir()], key=lambda item: item.name)


def _match_quality_row(
    match_path: Path,
    *,
    min_score: float,
    max_ghost_boxes: int,
    max_low_visible_rate: float,
    max_predicted_visible_boxes: int,
) -> dict[str, Any]:
    match_id = match_path.name
    meta = _read_json(match_path / "match.json")
    quality = _read_json(match_path / "analysis_quality_report.json")
    if quality is None:
        return {
            "match_id": match_id,
            "title": meta.get("title") if isinstance(meta, dict) else None,
            "status": "missing_quality_report",
            "passed": False,
            "score": 0.0,
            "quality": "unknown",
            "failures": ["analysis_quality_report.json is missing"],
        }

    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    score = float(quality.get("score") or 0.0)
    ghost_boxes = int(summary.get("ghost_bbox_count") or 0)
    predicted_visible_boxes = int(summary.get("predicted_visible_boxes") or 0)
    low_visible_rate = float(summary.get("low_visible_rate") or 0.0)
    failures: list[str] = []
    if score < min_score:
        failures.append(f"score {score:.1f} below min_score {min_score:.1f}")
    if ghost_boxes > max_ghost_boxes:
        failures.append(f"ghost_bbox_count {ghost_boxes} above {max_ghost_boxes}")
    if predicted_visible_boxes > max_predicted_visible_boxes:
        failures.append(f"predicted_visible_boxes {predicted_visible_boxes} above {max_predicted_visible_boxes}")
    if low_visible_rate > max_low_visible_rate:
        failures.append(f"low_visible_rate {low_visible_rate:.3f} above {max_low_visible_rate:.3f}")

    return {
        "match_id": match_id,
        "title": meta.get("title") if isinstance(meta, dict) else None,
        "status": "checked",
        "passed": len(failures) == 0,
        "score": round(score, 2),
        "quality": quality.get("quality") or "unknown",
        "failures": failures,
        "warnings": quality.get("warnings") if isinstance(quality.get("warnings"), list) else [],
        "metrics": {
            "visible_avg": summary.get("visible_avg"),
            "low_visible_rate": low_visible_rate,
            "ghost_bbox_count": ghost_boxes,
            "predicted_visible_boxes": predicted_visible_boxes,
            "visual_interpolated_boxes": summary.get("visual_interpolated_boxes"),
        },
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None
