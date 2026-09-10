#!/usr/bin/env python3
"""Run the evaluation-only interesting-action benchmark for one logical match."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.interesting_action_benchmark import benchmark_candidates
from app.services.interesting_action_candidates import build_interesting_action_candidates, build_logical_candidate_signals


DEFAULT_GROUP_ID = "match-group-c3fbd48a-356d-44a0-a740-c630de69b527"
DEFAULT_GOLDSET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "corgi_verisk_interesting_action_goldset.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--goldset", type=Path, default=DEFAULT_GOLDSET)
    parser.add_argument("--output", type=Path, help="Optional shadow-only JSON output path.")
    args = parser.parse_args()
    manifest = _read_json(config.STORAGE_DIR / "published" / "match-groups" / args.group_id / "manifest.json")
    source_rows = []
    for member in manifest.get("members", []):
        if not isinstance(member, dict):
            continue
        match_id = str(member.get("source_match_id") or "")
        if not match_id:
            continue
        match_dir = config.STORAGE_DIR / "matches" / match_id
        source_rows.append({
            "source_match_id": match_id,
            "logical_offset_sec": member.get("logical_start_sec"),
            "possession_segments": _read_json(match_dir / "possession_segments.json").get("segments", []),
            "possession_frames": _read_json(match_dir / "possession_candidates.json").get("frames", []),
            "pass_candidates": _read_json(match_dir / "pass_candidates.json").get("candidates", []),
            "restart_candidates": _read_json(match_dir / "restart_candidates.json").get("candidates", []),
            "contact_events": _read_json(match_dir / "event_candidates.json").get("events", []),
            "momentum_points": _read_json(match_dir / "attacking_momentum.json").get("points", []),
            "match_phase_periods": _read_json(match_dir / "match_phase_config.json").get("periods", []),
        })
    logical = build_logical_candidate_signals(source_rows)
    span = float((manifest.get("timing") or {}).get("timeline_span_sec") or 0.0)
    candidates = build_interesting_action_candidates(logical, timeline_span_sec=span)
    goldset = _read_json(args.goldset)
    benchmark = benchmark_candidates(candidates, list(goldset.get("manual_windows") or []))
    signal_audit = _signal_audit(logical["signals"], list(goldset.get("manual_windows") or []), source_rows)
    benchmark["signal_audit"] = signal_audit
    benchmark["miss_diagnostics"] = _miss_diagnostics(benchmark["manual_matches"], candidates, signal_audit)
    benchmark["logical_timeline"] = {"group_id": args.group_id, "timeline_span_sec": span, "signal_count": len(logical["signals"]), "evidence_gap_count": len(logical["evidence_gaps"])}
    output = json.dumps(benchmark, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


def _signal_audit(
    signals: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    audit = []
    for window in windows:
        start, end = float(window["start_time_sec"]), float(window["end_time_sec"])
        rows = [row for row in signals if float(row["start_time_sec"]) <= end and float(row["end_time_sec"]) >= start]
        kinds = {str(row["kind"]) for row in rows}
        trusted_positions = _trusted_ball_positions_in_window(sources, start, end)
        audit.append({
            "window_id": window["window_id"],
            "start_time_sec": start,
            "end_time_sec": end,
            "signals_present": sorted(kinds),
            "signals_absent_or_weak": [kind for kind in ("regain", "pass", "ball_progression", "restart", "contact", "momentum_rise") if kind not in kinds],
            "signal_count": len(rows),
            "trusted_ball_position_count": len(trusted_positions),
            "trusted_controlled_ball_position_count": sum(str(row.get("status") or "") == "controlled" and bool(row.get("team_id")) for row in trusted_positions),
            "ball_progression_signal_count": sum(str(row["kind"]) == "ball_progression" for row in rows),
        })
    return audit


def _trusted_ball_positions_in_window(sources: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    rows = []
    for source in sources:
        offset = _number(source.get("logical_offset_sec"), 0.0)
        for frame in source.get("possession_frames", []):
            if not isinstance(frame, dict) or str(frame.get("ball_source") or "") != "detected":
                continue
            if _number(frame.get("ball_confidence"), 0.0) < 0.5 or not _valid_position(frame.get("ball_position_m")):
                continue
            logical_time = offset + _number(frame.get("time_sec"), -1.0)
            if start <= logical_time <= end:
                rows.append(frame)
    return rows


def _valid_position(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _miss_diagnostics(
    manual_matches: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    signal_audit: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Explain benchmark misses without feeding manual windows into generation."""

    audit_by_window = {str(row.get("window_id") or ""): row for row in signal_audit}
    diagnostics = []
    for result in manual_matches:
        if result.get("matched"):
            continue
        window = result["manual_window"]
        start, end = float(window["start_time_sec"]), float(window["end_time_sec"])
        overlapping = [row for row in candidates if float(row["start_time_sec"]) < end and float(row["end_time_sec"]) > start]
        nearest = min(overlapping, key=lambda row: abs(float(row["start_time_sec"]) - start)) if overlapping else None
        if nearest is None:
            reason = "no_candidate_overlaps_manual_window"
        else:
            start_error = float(nearest["start_time_sec"]) - start
            coverage = _manual_coverage(nearest, start, end)
            if start_error < -4.0:
                reason = "nearest_overlapping_candidate_exceeds_max_early_start"
            elif start_error > 4.0:
                reason = "nearest_overlapping_candidate_exceeds_max_late_start"
            elif coverage < 0.25:
                reason = "nearest_overlapping_candidate_has_insufficient_manual_coverage"
            else:
                reason = "no_candidate_satisfies_benchmark_match_rule"
        diagnostics.append({
            "window_id": window["window_id"],
            "miss_reason": reason,
            "signals_present": audit_by_window.get(str(window["window_id"]), {}).get("signals_present", []),
            "nearest_overlapping_candidate": _candidate_summary(nearest) if nearest else None,
        })
    return diagnostics


def _manual_coverage(candidate: dict[str, Any], start: float, end: float) -> float:
    overlap = max(0.0, min(float(candidate["end_time_sec"]), end) - max(float(candidate["start_time_sec"]), start))
    return overlap / (end - start) if end > start else 0.0


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "start_time_sec": candidate["start_time_sec"],
        "end_time_sec": candidate["end_time_sec"],
        "interestingness_score": candidate["interestingness_score"],
        "confidence": candidate["confidence"],
        "evidence": candidate["evidence"],
    }


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


if __name__ == "__main__":
    main()
