#!/usr/bin/env python3
"""Generate shadow-only local ball paths constrained by Shot Review anchors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.shot_review_editor import EDITORIAL_DIRECTORY, load_shot_review_document
from evaluation.operator_ball_anchor_shadow import (
    DEFAULT_WINDOW_SEC,
    evaluate_operator_ball_anchors,
    extract_operator_ball_anchors,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--published-id")
    selection.add_argument("--all", action="store_true", help="Evaluate every publication with valid Shot Review frame anchors.")
    parser.add_argument("--window-sec", type=float, default=DEFAULT_WINDOW_SEC)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    if args.window_sec <= 0:
        raise ValueError("window_sec must be positive")
    published_ids = _published_ids(args.published_id, args.all)
    reports = []
    for published_id in published_ids:
        document = load_shot_review_document(published_id)
        anchors = extract_operator_ball_anchors(document)
        report = evaluate_operator_ball_anchors(anchors, _source_artifacts(anchors), window_sec=args.window_sec)
        report["published_id"] = published_id
        output = _output_path(published_id, args.output_dir, all_mode=args.all)
        _write(output, report)
        reports.append({"published_id": published_id, "artifact": str(output), "summary": report["summary"]})
    print(json.dumps({"evaluation_only": True, "reports": reports}, indent=2, ensure_ascii=False))


def _published_ids(published_id: str | None, all_mode: bool) -> list[str]:
    if published_id:
        return [published_id]
    if not all_mode:
        return []
    return sorted(path.stem for path in EDITORIAL_DIRECTORY.glob("*.json") if not path.name.endswith(".authority.json"))


def _source_artifacts(anchors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for source_match_id in sorted({str(anchor["source_match_id"]) for anchor in anchors}):
        root = config.MATCHES_DIR / source_match_id
        match = _read(root / "match.json")
        candidates = _read(root / "ball_candidates.json")
        rows[source_match_id] = {
            "fps": float(_record(match.get("video")).get("fps") or 0.0),
            "max_link_speed_mps": _record(candidates.get("parameters")).get("max_link_speed_mps"),
            "ball_candidates": candidates,
            "ball_tracks": _read(root / "ball_tracks.json"),
        }
    return rows


def _output_path(published_id: str, requested: Path | None, *, all_mode: bool) -> Path:
    root = (requested or config.STORAGE_DIR / "benchmarks" / "operator-ball-anchor-shadow").resolve()
    allowed = (config.STORAGE_DIR / "benchmarks").resolve()
    if root != allowed and allowed not in root.parents:
        raise ValueError("shadow_output_must_be_under_storage_benchmarks")
    return (root / published_id / "ball_operator_anchor_shadow.json") if all_mode or requested is None else root / "ball_operator_anchor_shadow.json"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    main()
