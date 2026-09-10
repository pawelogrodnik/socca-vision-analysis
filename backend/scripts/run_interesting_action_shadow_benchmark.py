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
            "pass_candidates": _read_json(match_dir / "pass_candidates.json").get("candidates", []),
            "restart_candidates": _read_json(match_dir / "restart_candidates.json").get("candidates", []),
            "contact_events": _read_json(match_dir / "event_candidates.json").get("events", []),
            "momentum_points": _read_json(match_dir / "attacking_momentum.json").get("points", []),
        })
    logical = build_logical_candidate_signals(source_rows)
    span = float((manifest.get("timing") or {}).get("timeline_span_sec") or 0.0)
    candidates = build_interesting_action_candidates(logical, timeline_span_sec=span)
    goldset = _read_json(args.goldset)
    benchmark = benchmark_candidates(candidates, list(goldset.get("manual_windows") or []))
    benchmark["signal_audit"] = _signal_audit(logical["signals"], list(goldset.get("manual_windows") or []))
    benchmark["logical_timeline"] = {"group_id": args.group_id, "timeline_span_sec": span, "signal_count": len(logical["signals"]), "evidence_gap_count": len(logical["evidence_gaps"])}
    output = json.dumps(benchmark, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


def _signal_audit(signals: list[dict[str, Any]], windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit = []
    for window in windows:
        start, end = float(window["start_time_sec"]), float(window["end_time_sec"])
        rows = [row for row in signals if float(row["start_time_sec"]) <= end and float(row["end_time_sec"]) >= start]
        kinds = {str(row["kind"]) for row in rows}
        audit.append({
            "window_id": window["window_id"],
            "start_time_sec": start,
            "end_time_sec": end,
            "signals_present": sorted(kinds),
            "signals_absent_or_weak": [kind for kind in ("regain", "pass", "restart", "contact", "momentum_rise") if kind not in kinds],
            "signal_count": len(rows),
        })
    return audit


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


if __name__ == "__main__":
    main()
