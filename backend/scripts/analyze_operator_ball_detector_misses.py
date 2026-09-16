#!/usr/bin/env python3
"""Explain existing operator-anchor detector misses from persisted artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.shot_review_editor import EDITORIAL_DIRECTORY, load_shot_review_document
from evaluation.operator_ball_anchor_shadow import DEFAULT_WINDOW_SEC, extract_operator_ball_anchors
from evaluation.operator_ball_detector_miss_analysis import (
    analyze_operator_ball_detector_misses,
    compact_operator_ball_detector_miss_analysis,
    export_detector_miss_evidence,
    operator_ball_detector_miss_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--published-id")
    selection.add_argument("--all", action="store_true", help="Analyze every publication with valid operator anchors.")
    parser.add_argument("--window-sec", type=float, default=DEFAULT_WINDOW_SEC)
    parser.add_argument("--output", type=Path, help="Optional full JSON output outside match storage.")
    parser.add_argument("--compact-output", type=Path, help="Optional compact JSON output outside match storage.")
    parser.add_argument("--markdown-output", type=Path, help="Optional Markdown output outside match storage.")
    parser.add_argument("--evidence-dir", type=Path, help="Optional external directory for exact-frame images.")
    args = parser.parse_args()
    if args.window_sec <= 0:
        raise ValueError("window_sec must be positive")
    reports = []
    for published_id in _published_ids(args.published_id, args.all):
        anchors = extract_operator_ball_anchors(load_shot_review_document(published_id))
        sources = _source_artifacts(anchors)
        report = analyze_operator_ball_detector_misses(anchors, sources, window_sec=args.window_sec)
        report["published_id"] = published_id
        if args.output:
            _write_external(args.output, report)
        if args.compact_output:
            _write_external(args.compact_output, compact_operator_ball_detector_miss_analysis(report))
        if args.markdown_output:
            _write_text_external(args.markdown_output, operator_ball_detector_miss_markdown(report))
        evidence = None
        if args.evidence_dir:
            _assert_external(args.evidence_dir)
            evidence = export_detector_miss_evidence(report, sources, args.evidence_dir)
        reports.append({"published_id": published_id, "summary": report["summary"], "detector_miss_count": report["detector_miss_count"], "evidence": evidence})
    print(json.dumps({"evaluation_only": True, "inference_invoked": False, "canonical_artifacts_mutated": False, "reports": reports}, indent=2, ensure_ascii=False))


def _published_ids(published_id: str | None, all_mode: bool) -> list[str]:
    if published_id:
        return [published_id]
    return sorted(path.stem for path in EDITORIAL_DIRECTORY.glob("*.json") if not path.name.endswith(".authority.json")) if all_mode else []


def _source_artifacts(anchors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for source_match_id in sorted({str(anchor["source_match_id"]) for anchor in anchors}):
        root = config.MATCHES_DIR / source_match_id
        match, candidates, tracks = _read(root / "match.json"), _read(root / "ball_candidates.json"), _read(root / "ball_tracks.json")
        candidate_parameters, track_parameters = _record(candidates.get("parameters")), _record(tracks.get("parameters"))
        rows[source_match_id] = {
            "fps": float(_record(match.get("video")).get("fps") or 0.0),
            "video_path": _record(match.get("video")).get("path"),
            "max_link_speed_mps": track_parameters.get("max_link_speed_mps") or candidate_parameters.get("max_link_speed_mps"),
            "min_start_conf": track_parameters.get("min_start_conf") or candidate_parameters.get("min_start_conf"),
            "ball_selection_policy": track_parameters.get("ball_selection_policy") or candidate_parameters.get("ball_selection_policy"),
            "ball_candidates": candidates,
            "ball_tracks": tracks,
        }
    return rows


def _assert_external(path: Path) -> None:
    resolved = path.resolve()
    matches = config.MATCHES_DIR.resolve()
    if resolved == matches or matches in resolved.parents:
        raise ValueError("diagnostic_output_must_not_be_under_match_storage")


def _write_external(path: Path, value: dict[str, Any]) -> None:
    _assert_external(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_text_external(path: Path, value: str) -> None:
    _assert_external(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    main()
