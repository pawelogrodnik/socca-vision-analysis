"""Read-only, independent reconciliation for canonical merged publications.

This deliberately does not import or call the merged-report builder.  It is a
small acceptance oracle over the compact, pinned publication artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_EPSILON = 0.011


def audit_merged_match(storage_dir: Path, identifier: str) -> dict[str, Any]:
    """Return a machine-readable reconciliation for a group or merged ID."""

    published_dir = storage_dir / "published"
    groups_dir = published_dir / "match-groups"
    matches_dir = published_dir / "matches"
    group_id, merged_id = _resolve_identifier(groups_dir, matches_dir, identifier)
    manifest = _read_object(groups_dir / group_id / "manifest.json")
    merged_dir = matches_dir / merged_id
    report = _read_object(merged_dir / "public_report.json")
    summary = _read_object(merged_dir / "summary.json")
    sources = []
    for member in manifest.get("members", []):
        if not isinstance(member, dict):
            continue
        published_id = str(member.get("published_id") or "")
        source_dir = matches_dir / published_id
        sources.append({
            "member": member,
            "published_id": published_id,
            "aggregate": _read_object(source_dir / "aggregate_inputs.json"),
            "report": _read_object(source_dir / "public_report.json"),
            "aggregate_inputs_bytes": _size(source_dir / "aggregate_inputs.json"),
            "public_report_bytes": _size(source_dir / "public_report.json"),
        })

    checks: list[dict[str, Any]] = []
    _check_inventory(checks, manifest, summary, report, sources, merged_id)
    _check_timing(checks, manifest, report, sources)
    _check_team_movement(checks, report, sources)
    _check_player_movement(checks, report, sources)
    _check_possession_and_passing(checks, report, sources)
    _check_timelines(checks, report, sources)
    _check_spatial(checks, manifest, report)
    _check_optional_identity_and_shape(checks, manifest, report, sources)

    failures = [check for check in checks if check["status"] == "fail"]
    unavailable = [check for check in checks if check["status"] == "unavailable"]
    return {
        "schema_version": "1.0.0",
        "status": "fail" if failures else "pass",
        "group_id": group_id,
        "merged_published_match_id": merged_id,
        "source_inventory": {
            "sources": [
                {
                    "published_id": source["published_id"],
                    "source_match_id": source["member"].get("source_match_id"),
                    "logical_start_sec": source["member"].get("logical_start_sec"),
                    "logical_end_sec": source["member"].get("logical_end_sec"),
                    "duration_sec": source["member"].get("analyzed_duration_sec"),
                    "aggregate_inputs_bytes": source["aggregate_inputs_bytes"],
                    "public_report_bytes": source["public_report_bytes"],
                    "reviewed_identity_digest": source["member"].get("reviewed_identity_digest"),
                }
                for source in sources
            ],
            "merged_public_report_bytes": _size(merged_dir / "public_report.json"),
            "source_kind": summary.get("source_kind") or "physical",
            "spatial": (manifest.get("compatibility") or {}).get("capabilities", {}).get("spatial"),
        },
        "checks": checks,
        "summary": {
            "checks": len(checks),
            "passed": len(checks) - len(failures) - len(unavailable),
            "failed": len(failures),
            "unavailable": len(unavailable),
        },
    }


def format_audit_summary(result: dict[str, Any]) -> str:
    summary = result["summary"]
    return (
        f"Merged-match acceptance {result['status'].upper()}: "
        f"{result['merged_published_match_id']} ({result['group_id']}); "
        f"{summary['passed']} passed, {summary['failed']} failed, "
        f"{summary['unavailable']} explicitly unavailable."
    )


def _check_inventory(checks: list[dict[str, Any]], manifest: dict[str, Any], summary: dict[str, Any], report: dict[str, Any], sources: list[dict[str, Any]], merged_id: str) -> None:
    _compare(checks, "canonical merged identity", merged_id, report.get("id"))
    _compare(checks, "merged source_kind", "merged", summary.get("source_kind"))
    _compare(checks, "three-or-more physical sources", True, len(sources) >= 3)
    _compare(checks, "physical source report type", True, all(source["report"].get("report_type") == "public_match_report" for source in sources))
    _compare(checks, "source pins", [source["member"].get("published_id") for source in sources], [item.get("published_id") for item in (report.get("merged_provenance") or {}).get("sources", [])])
    _compare(checks, "manifest source count", len(sources), len(manifest.get("members") or []))


def _check_timing(checks: list[dict[str, Any]], manifest: dict[str, Any], report: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    offset = 0.0
    for index, source in enumerate(sources, start=1):
        member = source["member"]
        duration = _number(member.get("analyzed_duration_sec"))
        _compare(checks, f"source {index} logical start", offset, _number(member.get("logical_start_sec")))
        _compare(checks, f"source {index} logical end", offset + duration, _number(member.get("logical_end_sec")))
        offset += duration
    _compare(checks, "merged analyzed duration", offset, _number((manifest.get("timing") or {}).get("analyzed_duration_sec")))
    _compare(checks, "merged report duration", offset, _number((report.get("match") or {}).get("duration_sec")))


def _check_team_movement(checks: list[dict[str, Any]], report: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    actual = _by_id(report.get("teams"), "team_id")
    source_rows = [_by_id(source["aggregate"].get("teams"), "team_id") for source in sources]
    for team_id, row in actual.items():
        expected = {key: sum(_number((source.get(team_id, {}).get("movement") or {}).get(key)) for source in source_rows) for key in ("total_distance_m", "high_intensity_distance_m", "sprint_count")}
        expected["peak_speed_kmh"] = max([_number((source.get(team_id, {}).get("movement") or {}).get("peak_speed_kmh")) for source in source_rows] or [0.0])
        for key, value in expected.items():
            _compare(checks, f"team {team_id} {key}", value, _number(row.get(key)))


def _check_player_movement(checks: list[dict[str, Any]], report: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    actual = _by_id(report.get("players"), "player_id")
    source_rows = [_by_id(source["aggregate"].get("players"), "player_id") for source in sources]
    additive = ("detected_time_sec", "total_distance_m", "high_intensity_distance_m", "sprint_count")
    peaks = ("peak_speed_kmh",)
    for player_id, row in actual.items():
        inputs = [source.get(player_id, {}).get("movement") or {} for source in source_rows]
        if not any(inputs):
            continue
        for key in additive:
            _compare(checks, f"player {player_id} {key}", sum(_number(item.get(key)) for item in inputs), _number(row.get(key)))
        for key in peaks:
            _compare(checks, f"player {player_id} {key}", max([_number(item.get(key)) for item in inputs] or [0.0]), _number(row.get(key)))
        distance = sum(_number(item.get("total_distance_m")) for item in inputs)
        movement_time = sum(_number(item.get("movement_time_sec")) for item in inputs)
        if movement_time:
            _compare(checks, f"player {player_id} avg_speed_kmh", distance / movement_time * 3.6, _number(row.get("avg_speed_kmh")))


def _check_possession_and_passing(checks: list[dict[str, Any]], report: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    ball = _object(report.get("ball"))
    windows = [window for source in sources for window in _object(_object(source["aggregate"].get("timelines")).get("possession")).get("windows", []) if isinstance(window, dict)]
    team_counts: dict[str, float] = {}
    contested = free = unknown = total = 0.0
    for window in windows:
        total += _number(window.get("total_frames"))
        contested += _number(window.get("contested_frames"))
        free += _number(window.get("free_frames"))
        unknown += _number(window.get("unknown_frames"))
        for team_id, value in _object(window.get("controlled_frames_by_team_id")).items():
            team_counts[str(team_id)] = team_counts.get(str(team_id), 0.0) + _number(value)
    controlled = sum(team_counts.values())
    if total:
        _compare(checks, "possession controlled coverage", controlled / total, _number(ball.get("controlled_coverage")))
        _compare(checks, "possession known coverage", (controlled + contested + free) / total, _number(ball.get("known_possession_coverage")))
    passes = [_object(_object(source["aggregate"].get("ball")).get("passes")) for source in sources]
    for key, actual_key in (("attempts", "pass_attempts"), ("completed", "completed_passes"), ("failed", "failed_passes"), ("restart_attempts", "restart_passes"), ("accepted", "accepted_passes")):
        _compare(checks, f"passes {key}", sum(_number(item.get(key)) for item in passes), _number(ball.get(actual_key)))
    attempts = sum(_number(item.get("attempts")) for item in passes)
    completed = sum(_number(item.get("completed")) for item in passes)
    if attempts:
        _compare(checks, "pass completion rate", round(completed / attempts * 100.0, 1), _number(ball.get("completion_rate")))


def _check_timelines(checks: list[dict[str, Any]], report: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    duration = _number(_object(report.get("match")).get("duration_sec"))
    ball = _object(report.get("ball"))
    for name, value in (("possession timeline", ball.get("possession_timeline")), ("momentum timeline", _object(ball.get("attacking_momentum")).get("timeline")), ("key moments", report.get("key_moments"))):
        if not isinstance(value, list):
            continue
        valid = True
        for point in value:
            if not isinstance(point, dict):
                valid = False
                break
            start = _number(point.get("start_time_sec", point.get("window_start_sec", point.get("time_sec"))))
            end = _number(point.get("end_time_sec", point.get("window_end_sec", point.get("time_sec"))))
            time = _number(point.get("time_sec", start))
            valid = valid and 0 <= start <= duration + _EPSILON and 0 <= time <= duration + _EPSILON and 0 <= end <= duration + _EPSILON and start <= time <= end
        _compare(checks, f"{name} within merged timeline", True, valid)
    momentum = _object(ball.get("attacking_momentum")).get("timeline")
    if isinstance(momentum, list):
        invariant = all(_number(item.get("team_a_value")) >= -_EPSILON and _number(item.get("team_b_value")) <= _EPSILON and abs(_number(item.get("signed_score")) - _number(item.get("team_a_value")) - _number(item.get("team_b_value"))) <= _EPSILON for item in momentum if isinstance(item, dict))
        _compare(checks, "momentum canonical signs", True, invariant)


def _check_spatial(checks: list[dict[str, Any]], manifest: dict[str, Any], report: dict[str, Any]) -> None:
    spatial = _object(_object(manifest.get("compatibility")).get("capabilities")).get("spatial")
    status = _object(spatial).get("status")
    heatmaps = [row.get("heatmap") for row in report.get("players", []) if isinstance(row, dict)]
    if status == "not_available":
        _compare(checks, "spatial fail-closed", True, all(item is None for item in heatmaps))
    elif status == "available":
        _compare(checks, "spatial merged output", True, any(isinstance(item, dict) for item in heatmaps))
    else:
        checks.append({"name": "spatial availability", "status": "unavailable", "expected": "declared compatibility", "actual": status})


def _check_optional_identity_and_shape(checks: list[dict[str, Any]], manifest: dict[str, Any], report: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    identity_inputs = [_object(source["aggregate"].get("identity_coverage")) for source in sources]
    identity_report = _object(report.get("identity_coverage"))
    if identity_report:
        for key in ("confirmed_observations", "reliable_observations", "unresolved_observations", "conflicted_observations"):
            if any(key in item for item in identity_inputs):
                _compare(checks, f"identity coverage {key}", sum(_number(item.get(key)) for item in identity_inputs), _number(identity_report.get(key)))
    else:
        checks.append({"name": "identity coverage presentation", "status": "unavailable", "expected": "optional merged identity coverage", "actual": "not exposed by canonical report"})

    team_shape = _object(_object(manifest.get("compatibility")).get("capabilities")).get("team_shape")
    shape_status = _object(team_shape).get("status")
    if shape_status == "not_available":
        _compare(checks, "team shape fail-closed", True, not bool(report.get("team_shape")))
    elif shape_status == "available":
        _compare(checks, "team shape merged output", True, bool(report.get("team_shape")))
    else:
        checks.append({"name": "team shape availability", "status": "unavailable", "expected": "declared compatibility", "actual": shape_status})


def _compare(checks: list[dict[str, Any]], name: str, expected: Any, actual: Any) -> None:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        passed = abs(float(expected) - float(actual)) <= _EPSILON
    else:
        passed = expected == actual
    checks.append({"name": name, "status": "pass" if passed else "fail", "expected": expected, "actual": actual})


def _resolve_identifier(groups_dir: Path, matches_dir: Path, identifier: str) -> tuple[str, str]:
    if identifier.startswith("match-group-"):
        sidecar = _read_object(groups_dir / identifier / "merged_projection.json")
        return identifier, str(sidecar["merged_published_match_id"])
    summary = _read_object(matches_dir / identifier / "summary.json")
    group_id = str(summary.get("backing_group_id") or "")
    if not group_id:
        raise ValueError(f"{identifier} is not a merged published match")
    return group_id, identifier


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def _size(path: Path) -> int:
    return path.stat().st_size


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _by_id(value: Any, key: str) -> dict[str, dict[str, Any]]:
    return {str(row.get(key)): row for row in value if isinstance(row, dict) and row.get(key)} if isinstance(value, list) else {}
