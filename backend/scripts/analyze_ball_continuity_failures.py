#!/usr/bin/env python3
"""Create an evaluation-only root-cause report for continuity-classified misses.

The caller supplies a frozen benchmark directory and optional isolated tracks.
This command never invokes YOLO and never writes to canonical match storage.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.ball_tracking import build_ball_tracks_document
from evaluation.ball_continuity_root_cause_analysis import (
    analyze_ball_continuity_root_causes,
    analyze_selector_policy_regressions,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--benchmark-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-tracks-root", type=Path, default=None)
    parser.add_argument("--comparison-benchmark-dir", type=Path, default=None)
    parser.add_argument("--comparison-source-tracks-root", type=Path, default=None)
    parser.add_argument(
        "--focus-gold-shot-id",
        action="append",
        default=[],
        help="Optional benchmark shot id to include as a non-counted diagnostic deep dive.",
    )
    args = parser.parse_args()

    benchmark_dir = args.benchmark_dir.resolve()
    failure_analysis = _read_json(benchmark_dir / "shot_failure_analysis.json")
    manifest = _read_json(config.PUBLISHED_DIR / "match-groups" / args.group_id / "manifest.json")
    sources = _load_sources(manifest, args.source_tracks_root)
    report = analyze_ball_continuity_root_causes(
        failure_analysis,
        sources,
        focus_gold_shot_ids={str(value) for value in args.focus_gold_shot_id},
    )
    report["logical_match_group_id"] = args.group_id
    if args.comparison_benchmark_dir or args.comparison_source_tracks_root:
        if args.comparison_benchmark_dir is None or args.comparison_source_tracks_root is None:
            raise ValueError("--comparison-benchmark-dir and --comparison-source-tracks-root must be supplied together.")
        comparison_analysis = _read_json(args.comparison_benchmark_dir.resolve() / "shot_failure_analysis.json")
        comparison_sources = _load_sources(manifest, args.comparison_source_tracks_root)
        report["policy_regressions"] = analyze_selector_policy_regressions(
            failure_analysis,
            comparison_analysis,
            sources,
            comparison_sources,
        )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "ball_continuity_failure_analysis.json", report)
    (output_dir / "ball_continuity_failure_analysis.md").write_text(_markdown_summary(report), encoding="utf-8")
    print(json.dumps({"evaluation_only": True, "inference_invoked": False, "continuity_cases_analyzed": report["continuity_cases_analyzed"], "subtype_counts": report["subtype_counts"]}, indent=2))


def _markdown_summary(report: dict[str, Any]) -> str:
    lines = ["# Ball continuity root-cause analysis", "", "Evaluation-only: no YOLO inference; canonical artifacts were read only.", "", "## Subtype counts", ""]
    lines.extend(f"- `{category}`: {count}" for category, count in (report.get("subtype_counts") or {}).items())
    lines.extend(["", "## Cases", "", "| Gold shot | Timestamp | Primary subtype | First material failure | Human validation |", "|---|---:|---|---|---|"])
    for row in report.get("cases") or []:
        diagnosis = row.get("diagnosis") or {}
        lines.append(f"| {row.get('gold_shot_id')} | {row.get('timestamp_display') or row.get('logical_timestamp_sec')} | {diagnosis.get('primary_category')} | {diagnosis.get('first_material_failure')} | {'yes' if diagnosis.get('requires_human_validation') else 'no'} |")
    focus_cases = report.get("focus_cases") or []
    if focus_cases:
        lines.extend(["", "## Focus cases (excluded from subtype counts)", ""])
        for row in focus_cases:
            divergence = row.get("divergence") or {}
            lines.append(
                f"- `{row.get('gold_shot_id')}` ({row.get('timestamp_display')}): "
                f"first post-refinement change at frame `{divergence.get('divergence_frame')}`, "
                f"time `{divergence.get('divergence_time_sec')}`."
            )
    regressions = report.get("policy_regressions") or []
    if regressions:
        lines.extend(["", "## Policy regressions", ""])
        for row in regressions:
            divergence = row.get("first_divergence") or {}
            lines.append(f"- `{row.get('gold_shot_id')}` ({row.get('timestamp_display')}): first v1/v2 difference at frame `{divergence.get('frame')}`, time `{divergence.get('time_sec')}`.")
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _load_sources(manifest: dict[str, Any], tracks_root: Path | None) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for member in manifest.get("members") or []:
        if not isinstance(member, dict):
            continue
        source_match_id = str(member.get("source_match_id") or "")
        if not source_match_id:
            continue
        match_dir = config.MATCHES_DIR / source_match_id
        match = _read_json(match_dir / "match.json")
        candidates = _read_json(match_dir / "ball_candidates.json")
        tracks_path = (tracks_root / source_match_id / "ball_tracks.json") if tracks_root else match_dir / "ball_tracks.json"
        pre_refinement_tracks = build_ball_tracks_document(
            list(candidates.get("frames") or []),
            processed_frames=[int(frame) for frame in candidates.get("processed_frames") or []],
            fps=_number(_mapping(match.get("video")).get("fps"), 0.0),
            parameters=_mapping(candidates.get("parameters")),
        )
        sources.append(
            {
                "source_match_id": source_match_id,
                "ball_candidates": candidates,
                "ball_tracks": _read_json(tracks_path),
                "pre_player_refinement_tracks": pre_refinement_tracks,
                "stable_players": _read_json(match_dir / "stable_players.json"),
                "event_candidates": _read_json(match_dir / "event_candidates.json"),
                "match_phase_config": _read_json(match_dir / "match_phase_config.json"),
                "pitch_config": _read_json(match_dir / "pitch_config.json"),
            }
        )
    return sources


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


if __name__ == "__main__":
    main()
