#!/usr/bin/env python3
"""Read-only sprint audit for physical Reviewed outputs and a logical match clock.

Examples:
  PYTHONPATH=backend backend/.venv-mps/bin/python backend/scripts/audit_reviewed_sprints.py
  PYTHONPATH=backend backend/.venv-mps/bin/python backend/scripts/audit_reviewed_sprints.py --mode stored
  PYTHONPATH=backend backend/.venv-mps/bin/python backend/scripts/audit_reviewed_sprints.py --mode decomposition
  PYTHONPATH=backend backend/.venv-mps/bin/python backend/scripts/audit_reviewed_sprints.py \\
    --mode comparison --benchmark-file /private/benchmark.json

The utility never writes match artifacts. `stored` enumerates the current
stored events; `replay` is a policy-only replay over those same stored
observations; `current_snapshot` runs the normal Reviewed Stats authority in
memory against the current Reviewed Identity snapshot.
Comparison accepts private benchmark input with an `events` list whose rows have
`logical_start_sec`, `logical_end_sec`, and `player_name` fields.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from app.services.identity_reviewed_snapshot import get_reviewed_identity_status
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.reviewed_sprint_policy import classify_reviewed_sprints, reviewed_sprint_policy


DEFAULT_SOURCES = (
    ("9c7485e4", 0.0),
    ("6d8fc20c", 1156.3),
    ("5e62625e", 1761.878),
)

def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fragments(rows: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    fragments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if not current or (
            row.get("tracklet_id") == current[-1].get("tracklet_id")
            and int(row.get("frame") or 0) - int(current[-1].get("frame") or 0) <= 10
        ):
            current.append(row)
        else:
            fragments.append(current)
            current = [row]
    if current:
        fragments.append(current)
    return fragments


def _replay_events(
    match_path: Path,
    *,
    offset_sec: float,
    coalesce: bool = True,
) -> list[dict[str, Any]]:
    stats = _load(match_path / "reviewed_player_stats.json")
    timeline = _load(match_path / "reviewed_player_timeline.json")
    fps = float(stats["video_timing"]["fps"])
    observations_by_player = {
        str(player["player_id"]): list(player.get("observations") or [])
        for player in timeline.get("players") or []
    }
    events: list[dict[str, Any]] = []
    for player in stats.get("players") or []:
        player_id = str(player.get("player_id") or "")
        detection = dict((player.get("workload") or {}).get("sprint_detection") or {})
        policy = reviewed_sprint_policy(
            peak_sustained_speed_kmh=float(detection.get("reference_peak_sustained_speed_kmh") or 0.0),
            speed_quality=str(detection.get("reference_speed_quality") or "not_available"),
            detected_time_sec=float(player.get("detected_time_sec") or 0.0),
        )
        if not coalesce:
            policy["coalesce_max_gap_sec"] = 0.0
        for event in classify_reviewed_sprints(
            _fragments(observations_by_player.get(player_id, [])), fps=fps, policy=policy
        )["events"]:
            events.append(_audit_event(event, player=player, offset_sec=offset_sec))
    return events


def _stored_events(match_path: Path, *, offset_sec: float) -> list[dict[str, Any]]:
    stats = _load(match_path / "reviewed_player_stats.json")
    evidence = _load(match_path / "reviewed_player_workload_evidence.json")
    return _events_from_stats_and_evidence(stats, evidence, offset_sec=offset_sec)


def _current_snapshot_events(match_path: Path, *, offset_sec: float) -> list[dict[str, Any]]:
    """Build current Reviewed stats in memory without replacing local artifacts."""
    snapshot = get_reviewed_identity_status(match_path)
    if snapshot.get("status") in {"missing", "stale"}:
        raise ValueError("current Reviewed Identity snapshot is unavailable")
    documents = build_reviewed_stats(
        match_path,
        snapshot,
        _load(match_path / "match.json"),
        _load(match_path / "pitch_config.json") if (match_path / "pitch_config.json").exists() else None,
        persist=False,
    )
    return _events_from_stats_and_evidence(
        documents["reviewed_player_stats.json"],
        documents["reviewed_player_workload_evidence.json"],
        offset_sec=offset_sec,
    )


def _events_from_stats_and_evidence(
    stats: dict[str, Any],
    evidence: dict[str, Any],
    *,
    offset_sec: float,
) -> list[dict[str, Any]]:
    player_by_id = {str(player.get("player_id") or ""): player for player in stats.get("players") or []}
    events: list[dict[str, Any]] = []
    for row in evidence.get("players") or []:
        player = player_by_id.get(str(row.get("player_id") or ""), {"player_id": row.get("player_id")})
        for event in (row.get("evidence") or {}).get("sprint_events") or []:
            if isinstance(event, dict):
                events.append(_audit_event(event, player=player, offset_sec=offset_sec))
    return events


def _audit_event(event: dict[str, Any], *, player: dict[str, Any], offset_sec: float) -> dict[str, Any]:
    start = float(event.get("start_time_sec") or 0.0)
    end = float(event.get("end_time_sec") or start)
    return {
        "logical_start_sec": round(start + offset_sec, 3),
        "logical_end_sec": round(end + offset_sec, 3),
        "physical_start_sec": round(start, 3),
        "physical_end_sec": round(end, 3),
        "player_id": player.get("player_id"),
        "player_name": player.get("player_name") or player.get("player_id"),
        "team_label": player.get("team_label"),
        "tracklet_id": event.get("tracklet_id"),
        "start_frame": event.get("start_frame"),
        "end_frame": event.get("end_frame"),
        "qualifying_time_sec": round(float(event.get("qualifying_time_sec") or 0.0), 3),
        "qualifying_distance_m": round(float(event.get("qualifying_distance_m") or 0.0), 2),
        "max_sustained_speed_kmh": round(float(event.get("max_speed_mps") or 0.0) * 3.6, 2),
        "merged_fragment_count": int(event.get("merged_fragment_count") or 1),
    }


def audit_sample(events: list[dict[str, Any]], *, bucket_size: int = 8) -> dict[str, list[dict[str, Any]]]:
    strongest = _select_with_player_cap(
        events,
        key=lambda row: (-float(row["max_sustained_speed_kmh"]), float(row["logical_start_sec"])),
        bucket_size=bucket_size,
    )
    selected = {id(row) for row in strongest}
    borderline = _select_with_player_cap(
        (row for row in events if id(row) not in selected),
        key=lambda row: (float(row["qualifying_time_sec"]), float(row["max_sustained_speed_kmh"]), float(row["logical_start_sec"])),
        bucket_size=bucket_size,
    )
    selected.update(id(row) for row in borderline)
    remaining = sorted((row for row in events if id(row) not in selected), key=lambda row: float(row["logical_start_sec"]))
    if not remaining:
        timeline: list[dict[str, Any]] = []
    else:
        indexes = sorted({round(index * (len(remaining) - 1) / max(1, bucket_size - 1)) for index in range(bucket_size)})
        timeline = [remaining[index] for index in indexes]
    return {"strongest": strongest, "borderline": borderline, "timeline": timeline}


def _select_with_player_cap(
    events: Iterable[dict[str, Any]],
    *,
    key: Any,
    bucket_size: int,
    player_cap: int = 3,
) -> list[dict[str, Any]]:
    """Prefer diverse players, relaxing the cap only when a bucket needs it."""
    ordered = sorted(events, key=key)
    selected: list[dict[str, Any]] = []
    per_player: dict[str, int] = {}
    deferred: list[dict[str, Any]] = []
    for event in ordered:
        player = str(event.get("player_id") or event.get("player_name") or "")
        if per_player.get(player, 0) < player_cap:
            selected.append(event)
            per_player[player] = per_player.get(player, 0) + 1
            if len(selected) == bucket_size:
                return selected
        else:
            deferred.append(event)
    return (selected + deferred)[:bucket_size]


def _clock(value: float) -> str:
    minutes, seconds = divmod(max(0.0, value), 60.0)
    return f"{int(minutes):02d}:{seconds:04.1f}"


def _sources(values: list[str]) -> tuple[tuple[str, float], ...]:
    if not values:
        return DEFAULT_SOURCES
    parsed: list[tuple[str, float]] = []
    for value in values:
        match_id, separator, offset = value.partition(":")
        if not separator or not match_id:
            raise ValueError("--source must use MATCH_ID:LOGICAL_OFFSET_SEC")
        parsed.append((match_id, float(offset)))
    return tuple(parsed)


def _events_for_sources(
    sources: tuple[tuple[str, float], ...],
    *,
    matches_root: Path,
    mode: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for match_id, offset_sec in sources:
        match_path = matches_root / match_id
        if mode == "stored":
            rows = _stored_events(match_path, offset_sec=offset_sec)
        elif mode == "replay":
            rows = _replay_events(match_path, offset_sec=offset_sec)
        elif mode == "replay_without_coalescing":
            rows = _replay_events(match_path, offset_sec=offset_sec, coalesce=False)
        elif mode == "current_snapshot":
            rows = _current_snapshot_events(match_path, offset_sec=offset_sec)
        else:
            raise ValueError(f"unsupported audit mode: {mode}")
        events.extend(rows)
    return sorted(events, key=lambda row: float(row["logical_start_sec"]))


def _policy_replay_decomposition(
    sources: tuple[tuple[str, float], ...],
    *,
    matches_root: Path,
) -> dict[str, int]:
    stored = _events_for_sources(sources, matches_root=matches_root, mode="stored")
    calibrated = _events_for_sources(
        sources,
        matches_root=matches_root,
        mode="replay_without_coalescing",
    )
    coalesced = _events_for_sources(sources, matches_root=matches_root, mode="replay")
    return {
        "stored_baseline_events": len(stored),
        "after_near_threshold_calibration_events": len(calibrated),
        "removed_by_near_threshold_calibration": len(stored) - len(calibrated),
        "after_trusted_bridge_coalescing_events": len(coalesced),
        "reduction_from_coalescing": len(calibrated) - len(coalesced),
    }


def _benchmark_comparison(
    old_events: list[dict[str, Any]],
    new_events: list[dict[str, Any]],
    benchmark_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, benchmark in enumerate(benchmark_events, start=1):
        number = int(benchmark.get("benchmark_number") or index)
        start = float(benchmark["logical_start_sec"])
        end = float(benchmark["logical_end_sec"])
        player_name = str(benchmark["player_name"])
        bucket = str(benchmark.get("bucket") or "unclassified")
        identity_invalid = bool(benchmark.get("identity_invalid"))
        old = _overlapping_event(old_events, player_name=player_name, start=start, end=end)
        new = _overlapping_event(new_events, player_name=player_name, start=start, end=end)
        if identity_invalid:
            status = "identity_invalid_not_calibrated"
        elif new is None:
            status = "rejected"
        elif _is_merged_from_old(old, new):
            status = "merged_into_one_event"
        elif _same_timing(old, new):
            status = "remains_one_event"
        else:
            status = "timing_changed"
        rows.append(
            {
                "benchmark_number": number,
                "bucket": bucket,
                "player_name": player_name,
                "logical_start_sec": start,
                "logical_end_sec": end,
                "status": status,
                "previous_event": old,
                "current_event": new,
            }
        )
    return rows


def _overlapping_event(
    events: list[dict[str, Any]],
    *,
    player_name: str,
    start: float,
    end: float,
) -> dict[str, Any] | None:
    candidates = [
        event
        for event in events
        if event.get("player_name") == player_name
        and float(event["logical_end_sec"]) >= start - 0.25
        and float(event["logical_start_sec"]) <= end + 0.25
    ]
    return min(
        candidates,
        key=lambda event: abs(float(event["logical_start_sec"]) - start),
        default=None,
    )


def _same_timing(old: dict[str, Any] | None, new: dict[str, Any] | None) -> bool:
    return bool(
        old
        and new
        and abs(float(old["logical_start_sec"]) - float(new["logical_start_sec"])) < 0.15
        and abs(float(old["logical_end_sec"]) - float(new["logical_end_sec"])) < 0.15
    )


def _is_merged_from_old(old: dict[str, Any] | None, new: dict[str, Any] | None) -> bool:
    return bool(
        old
        and new
        and int(new.get("merged_fragment_count") or 1) > 1
        and float(new["logical_start_sec"]) <= float(old["logical_start_sec"]) + 0.15
        and float(new["logical_end_sec"]) >= float(old["logical_end_sec"]) - 0.15
    )


def _load_benchmark(path: Path) -> list[dict[str, Any]]:
    payload = _load(path)
    rows = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("benchmark file must be a JSON list or an object with an events list")
    required = {"logical_start_sec", "logical_end_sec", "player_name"}
    if any(not isinstance(row, dict) or not required <= row.keys() for row in rows):
        raise ValueError("each benchmark event needs logical_start_sec, logical_end_sec, and player_name")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches-root", type=Path, default=Path("backend/storage/matches"))
    parser.add_argument("--source", action="append", default=[], help="MATCH_ID:LOGICAL_OFFSET_SEC; repeatable")
    parser.add_argument(
        "--mode",
        choices=("stored", "replay", "current_snapshot", "decomposition", "comparison"),
        default="replay",
    )
    parser.add_argument(
        "--comparison-target",
        choices=("replay", "current_snapshot"),
        default="replay",
        help="new-event source for comparison mode; replay is policy-only on stored observations",
    )
    parser.add_argument(
        "--benchmark-file",
        type=Path,
        help="private JSON benchmark input required by comparison mode; it is never written",
    )
    parser.add_argument("--bucket-size", type=int, default=8)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print only count and deterministic validation sample",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="suppress the human-readable timestamp lines for machine filtering",
    )
    arguments = parser.parse_args()

    sources = _sources(arguments.source)
    if arguments.mode == "decomposition":
        print(json.dumps(
            {
                "mode": "policy_only_replay_decomposition",
                **_policy_replay_decomposition(sources, matches_root=arguments.matches_root),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return
    if arguments.mode == "comparison":
        if arguments.benchmark_file is None:
            parser.error("--benchmark-file is required when --mode comparison")
        old_events = _events_for_sources(sources, matches_root=arguments.matches_root, mode="stored")
        events = _events_for_sources(
            sources,
            matches_root=arguments.matches_root,
            mode=arguments.comparison_target,
        )
        result = {
            "comparison_target": arguments.comparison_target,
            "old_accepted_event_count": len(old_events),
            "new_accepted_event_count": len(events),
            "policy_replay_decomposition": _policy_replay_decomposition(
                sources,
                matches_root=arguments.matches_root,
            ),
            "benchmark_comparison": _benchmark_comparison(
                old_events,
                events,
                _load_benchmark(arguments.benchmark_file),
            ),
            "post_change_sample": audit_sample(events, bucket_size=max(1, arguments.bucket_size)),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    events = _events_for_sources(sources, matches_root=arguments.matches_root, mode=arguments.mode)
    sample = audit_sample(events, bucket_size=max(1, arguments.bucket_size))
    result: dict[str, Any] = {
        "mode": arguments.mode,
        "accepted_event_count": len(events),
        "sample": sample,
    }
    if not arguments.summary:
        result["events"] = events
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if arguments.json_only:
        return
    print("\nOperator validation timestamps:")
    for bucket, rows in sample.items():
        for row in rows:
            print(f"{bucket.upper():10} {_clock(row['logical_start_sec'])}–{_clock(row['logical_end_sec'])}  {row['player_name']}  {row['max_sustained_speed_kmh']:.1f} km/h")


if __name__ == "__main__":
    main()
