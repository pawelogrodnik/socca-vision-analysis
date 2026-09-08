"""Fail-closed, read-only reconciliation for canonical merged publications."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from app.services.artifact_lineage import canonical_json_sha256


EPSILON = 0.011


def audit_merged_match(storage_dir: Path, identifier: str) -> dict[str, Any]:
    published = storage_dir / "published"
    groups, matches = published / "match-groups", published / "matches"
    group_id, merged_id = _resolve(groups, matches, identifier)
    manifest = _read(groups / group_id / "manifest.json")
    report = _read(matches / merged_id / "public_report.json")
    summary = _read(matches / merged_id / "summary.json")
    sources = _sources(matches, manifest)
    checks: list[dict[str, Any]] = []
    provenance = _required_object(checks, "merged provenance", report, "merged_provenance") or {}
    _lineage(checks, sources, provenance)
    expected_teams, expected_players = _inventory(checks, manifest, summary, report, sources, merged_id, provenance)
    duration = _timing(checks, manifest, report, sources)
    _teams(checks, report, sources, expected_teams)
    _players(checks, report, sources, expected_players, duration)
    _possession_and_passes(checks, report, sources)
    _timelines(checks, report, sources, duration)
    _key_moments(checks, report, duration)
    _advanced_availability(checks, report)
    failures = [check for check in checks if check["status"] == "fail"]
    unavailable = [check for check in checks if check["status"] in {"unavailable", "not_audited"}]
    merged_dir = matches / merged_id
    return {
        "schema_version": "1.1.0", "status": "fail" if failures else "pass", "group_id": group_id,
        "merged_published_match_id": merged_id,
        "source_inventory": {
            "sources": [{"published_id": source["published_id"], "source_match_id": source["member"].get("source_match_id"), "logical_start_sec": source["member"].get("logical_start_sec"), "logical_end_sec": source["member"].get("logical_end_sec"), "duration_sec": source["member"].get("analyzed_duration_sec"), "aggregate_inputs_bytes": source["aggregate_inputs_bytes"], "public_report_bytes": source["public_report_bytes"], "reviewed_identity_digest": source["member"].get("reviewed_identity_digest")} for source in sources],
            "merged_public_report_bytes": (merged_dir / "public_report.json").stat().st_size,
            "source_kind": summary.get("source_kind") or "physical",
            "spatial": _object(_object(manifest.get("compatibility")).get("capabilities")).get("spatial"),
        },
        "checks": checks,
        "summary": {"checks": len(checks), "passed": len(checks) - len(failures) - len(unavailable), "failed": len(failures), "unavailable": len(unavailable)},
    }


def format_audit_summary(result: dict[str, Any]) -> str:
    summary = result["summary"]
    return f"Merged-match acceptance {result['status'].upper()}: {result['merged_published_match_id']} ({result['group_id']}); {summary['passed']} passed, {summary['failed']} failed, {summary['unavailable']} unavailable or not independently audited."


def _sources(matches: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    members = manifest.get("members")
    if not isinstance(members, list):
        raise ValueError("manifest members must be a list")
    result = []
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("manifest member must be an object")
        published_id = str(member.get("published_id") or "")
        directory = matches / published_id
        result.append({"member": member, "published_id": published_id, "aggregate": _read(directory / "aggregate_inputs.json"), "report": _read(directory / "public_report.json"), "aggregate_inputs_bytes": (directory / "aggregate_inputs.json").stat().st_size, "public_report_bytes": (directory / "public_report.json").stat().st_size})
    return result


def _lineage(checks: list[dict[str, Any]], sources: list[dict[str, Any]], provenance: dict[str, Any]) -> None:
    pins = provenance.get("sources")
    if not isinstance(pins, list):
        _fail(checks, "merged provenance sources required", "list", pins)
        pins = []
    by_id = {str(pin.get("published_id") or ""): pin for pin in pins if isinstance(pin, dict)}
    for source in sources:
        identifier, member, aggregate = source["published_id"], source["member"], source["aggregate"]
        lineage = _required_object(checks, f"source {identifier} aggregate lineage", aggregate, "source") or {}
        pin = by_id.get(identifier, {})
        public_digest = canonical_json_sha256(source["report"])
        aggregate_domain = copy.deepcopy(aggregate)
        _object(aggregate_domain.get("source")).pop("aggregation_input_semantic_digest", None)
        aggregate_digest = canonical_json_sha256(aggregate_domain)
        for label, expected, row, key in (
            ("aggregate public digest", public_digest, lineage, "public_report_semantic_digest"), ("manifest public digest", public_digest, member, "public_report_semantic_digest"), ("provenance public digest", public_digest, pin, "public_report_semantic_digest"),
            ("aggregate self digest", aggregate_digest, lineage, "aggregation_input_semantic_digest"), ("manifest aggregate digest", aggregate_digest, member, "aggregation_input_semantic_digest"), ("provenance aggregate digest", aggregate_digest, pin, "aggregation_input_semantic_digest"),
        ):
            _compare(checks, f"source {identifier} {label}", expected, _required_string(checks, f"source {identifier} {label}", row, key))
        identity = [member.get("reviewed_identity_digest"), lineage.get("reviewed_identity_digest"), pin.get("reviewed_identity_digest")]
        _compare(checks, f"source {identifier} reviewed identity pins", True, all(isinstance(value, str) and value == identity[0] for value in identity))
        checks.append({"name": f"source {identifier} reviewed identity digest recomputation", "status": "unavailable", "expected": "physical Reviewed Identity artifact", "actual": "compact inputs expose authoritative pins only"})


def _inventory(checks: list[dict[str, Any]], manifest: dict[str, Any], summary: dict[str, Any], report: dict[str, Any], sources: list[dict[str, Any]], merged_id: str, provenance: dict[str, Any]) -> tuple[set[str], set[str]]:
    _compare(checks, "canonical merged identity", merged_id, report.get("id"))
    _compare(checks, "merged source_kind", "merged", summary.get("source_kind"))
    _compare(checks, "three-or-more physical sources", True, len(sources) >= 3)
    _compare(checks, "physical source report type", True, all(source["report"].get("report_type") == "public_match_report" for source in sources))
    _compare(checks, "source pins", [source["published_id"] for source in sources], [row.get("published_id") for row in provenance.get("sources", []) if isinstance(row, dict)])
    team_ids: set[str] = set()
    player_ids: set[str] = set()
    for source in sources:
        identifier = source["published_id"]
        aggregate_team_ids = _ids(checks, f"source {identifier} aggregate team", _required_list(checks, f"source {identifier} aggregate teams", source["aggregate"], "teams"), "team_id")
        public_team_ids = _ids(checks, f"source {identifier} public team", _required_list(checks, f"source {identifier} public teams", source["report"], "teams"), "team_id")
        _compare(checks, f"source {identifier} team identity inputs", aggregate_team_ids, public_team_ids)
        team_ids.update(aggregate_team_ids)
        public_player_ids = _ids(checks, f"source {identifier} public player", _required_list(checks, f"source {identifier} public players", source["report"], "players"), "player_id")
        aggregate_player_ids = _ids(checks, f"source {identifier} aggregate player", _required_list(checks, f"source {identifier} aggregate players", source["aggregate"], "players"), "player_id")
        _compare(checks, f"source {identifier} player identity inputs", public_player_ids, aggregate_player_ids)
        player_ids.update(public_player_ids)
    _compare(checks, "expected stable team cardinality", 2, len(team_ids))
    return team_ids, player_ids


def _timing(checks: list[dict[str, Any]], manifest: dict[str, Any], report: dict[str, Any], sources: list[dict[str, Any]]) -> float:
    offset = 0.0
    for index, source in enumerate(sources, 1):
        member = source["member"]
        duration = _required_number(checks, f"source {index} duration", member, "analyzed_duration_sec")
        start = _required_number(checks, f"source {index} logical start", member, "logical_start_sec")
        end = _required_number(checks, f"source {index} logical end", member, "logical_end_sec")
        if None not in (duration, start, end):
            _compare(checks, f"source {index} logical start", offset, start)
            _compare(checks, f"source {index} logical end", offset + duration, end)
            offset += duration
    _compare(checks, "merged analyzed duration", offset, _required_number(checks, "manifest analyzed duration", _required_object(checks, "manifest timing", manifest, "timing") or {}, "analyzed_duration_sec"))
    _compare(checks, "merged report duration", offset, _required_number(checks, "merged report duration", _required_object(checks, "merged match", report, "match") or {}, "duration_sec"))
    return offset


def _teams(checks: list[dict[str, Any]], report: dict[str, Any], sources: list[dict[str, Any]], expected_ids: set[str]) -> None:
    rows = _required_list(checks, "merged teams", report, "teams")
    actual_ids = _ids(checks, "merged team", rows, "team_id")
    _compare(checks, "merged team IDs", expected_ids, actual_ids)
    actual = _lookup(rows, "team_id")
    for team_id in expected_ids:
        if team_id not in actual: continue
        movements = [_team_movement(checks, source, team_id) for source in sources]
        for key in ("total_distance_m", "high_intensity_distance_m", "sprint_count"):
            values = [_required_number(checks, f"source team {team_id} {key}", movement, key) for movement in movements]
            if all(value is not None for value in values): _compare(checks, f"team {team_id} {key}", sum(value for value in values if value is not None), _required_number(checks, f"team {team_id} {key} actual", actual[team_id], key))
        peaks = [_required_number(checks, f"source team {team_id} peak", movement, "peak_speed_kmh") for movement in movements]
        if all(value is not None for value in peaks): _compare(checks, f"team {team_id} peak_speed_kmh", max(value for value in peaks if value is not None), _required_number(checks, f"team {team_id} peak actual", actual[team_id], "peak_speed_kmh"))
        primitives = (("attempts_by_team_id", "pass_attempts"), ("completed_by_team_id", "completed_passes"), ("failed_by_team_id", "failed_passes"), ("restart_attempts_by_team_id", "restart_passes"), ("accepted_by_team_id", "accepted_passes"))
        for map_key, actual_key in primitives:
            values = [_sparse_team_pass_count(checks, source, map_key, team_id) for source in sources]
            if all(value is not None for value in values): _compare(checks, f"team {team_id} {actual_key}", sum(value for value in values if value is not None), _required_number(checks, f"team {team_id} {actual_key} actual", actual[team_id], actual_key))
        attempts, completed = _required_number(checks, f"team {team_id} attempts actual", actual[team_id], "pass_attempts"), _required_number(checks, f"team {team_id} completed actual", actual[team_id], "completed_passes")
        if attempts is not None and completed is not None and attempts > 0: _compare(checks, f"team {team_id} completion rate", round(completed / attempts * 100, 1), _required_number(checks, f"team {team_id} rate actual", actual[team_id], "completion_rate"))


def _players(checks: list[dict[str, Any]], report: dict[str, Any], sources: list[dict[str, Any]], expected_ids: set[str], duration: float) -> None:
    rows = _required_list(checks, "merged players", report, "players")
    actual_ids = _ids(checks, "merged player", rows, "player_id")
    _compare(checks, "merged player IDs", expected_ids, actual_ids)
    actual = _lookup(rows, "player_id")
    for player_id in expected_ids:
        if player_id not in actual: continue
        public, movement, _offsets = _player_parts(checks, sources, player_id)
        for key in ("playing_time_sec", "high_intensity_time_sec", "sprint_time_sec", "sprint_distance_m"):
            _sum_check(checks, f"player {player_id} {key}", public, key, actual[player_id])
        for key in ("detected_time_sec", "total_distance_m", "high_intensity_distance_m", "sprint_count"):
            _sum_check(checks, f"player {player_id} {key}", movement, key, actual[player_id])
        _max_check(checks, f"player {player_id} peak_speed_kmh", movement, "peak_speed_kmh", actual[player_id])
        _max_check(checks, f"player {player_id} max_sprint_speed_kmh", public, "max_sprint_speed_kmh", actual[player_id])
        distances = [_required_number(checks, f"source player {player_id} distance", row, "total_distance_m") for row in movement]
        times = [_required_number(checks, f"source player {player_id} movement time", row, "movement_time_sec") for row in movement]
        if all(value is not None for value in distances + times) and sum(times) > 0: _compare(checks, f"player {player_id} avg_speed_kmh", sum(distances) / sum(times) * 3.6, _required_number(checks, f"player {player_id} avg speed actual", actual[player_id], "avg_speed_kmh"))
        playings = [_required_number(checks, f"source player {player_id} playing time", row, "playing_time_sec") for row in public]
        if all(value is not None for value in playings) and duration > 0: _compare(checks, f"player {player_id} coverage_ratio", min(1.0, sum(playings) / duration), _required_number(checks, f"player {player_id} coverage actual", actual[player_id], "coverage_ratio"))
        expected_flags = sorted({flag for row in public for flag in _required_strings(checks, f"source player {player_id} quality flags", row, "quality_flags")})
        _compare(checks, f"player {player_id} quality_flags", expected_flags, _required_strings(checks, f"player {player_id} quality flags actual", actual[player_id], "quality_flags"))
        _workload(checks, player_id, actual[player_id], duration)


def _possession_and_passes(checks: list[dict[str, Any]], report: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    ball = _required_object(checks, "merged ball", report, "ball") or {}
    controlled = contested = free = total = 0.0
    for source in sources:
        for window in _timeline_rows(checks, source, "possession", "windows"):
            counts = _required_object(checks, "source possession counts", window, "controlled_frames_by_team_id") or {}
            values = [_required_number(checks, "source possession controlled", counts, key) for key in counts]
            if all(value is not None for value in values): controlled += sum(value for value in values if value is not None)
            for key in ("contested_frames", "free_frames", "total_frames"):
                value = _required_number(checks, f"source possession {key}", window, key)
                if value is not None:
                    if key == "contested_frames": contested += value
                    elif key == "free_frames": free += value
                    else: total += value
    if total: _compare(checks, "possession controlled coverage", controlled / total, _required_number(checks, "controlled coverage actual", ball, "controlled_coverage")); _compare(checks, "possession known coverage", (controlled + contested + free) / total, _required_number(checks, "known coverage actual", ball, "known_possession_coverage"))
    for source_key, actual_key in (("attempts", "pass_attempts"), ("completed", "completed_passes"), ("failed", "failed_passes"), ("restart_attempts", "restart_passes"), ("accepted", "accepted_passes")):
        values = [_required_number(checks, f"source passes {source_key}", _passes(checks, source), source_key) for source in sources]
        if all(value is not None for value in values): _compare(checks, f"passes {source_key}", sum(value for value in values if value is not None), _required_number(checks, f"passes {actual_key} actual", ball, actual_key))
    attempts, completed = _required_number(checks, "pass attempts actual", ball, "pass_attempts"), _required_number(checks, "pass completed actual", ball, "completed_passes")
    if attempts is not None and completed is not None and attempts > 0: _compare(checks, "pass completion rate", round(completed / attempts * 100, 1), _required_number(checks, "pass rate actual", ball, "completion_rate"))


def _timelines(checks: list[dict[str, Any]], report: dict[str, Any], sources: list[dict[str, Any]], duration: float) -> None:
    ball = _required_object(checks, "merged ball timeline", report, "ball") or {}
    _rebase_check(checks, "possession timeline", ball.get("possession_timeline"), sources, "possession", "windows", duration)
    momentum = _required_object(checks, "merged momentum", ball, "attacking_momentum") or {}
    _rebase_check(checks, "momentum timeline", momentum.get("timeline"), sources, "attacking_momentum", "points", duration)
    if isinstance(momentum.get("timeline"), list):
        good = all(isinstance(row, dict) and all(_finite(row.get(key)) is not None for key in ("team_a_value", "team_b_value", "signed_score")) and _finite(row.get("team_a_value")) >= 0 and _finite(row.get("team_b_value")) <= 0 and abs(_finite(row.get("signed_score")) - _finite(row.get("team_a_value")) - _finite(row.get("team_b_value"))) <= EPSILON for row in momentum["timeline"])
        _compare(checks, "momentum canonical signs", True, good)


def _key_moments(checks: list[dict[str, Any]], report: dict[str, Any], duration: float) -> None:
    key_moments = report.get("key_moments")
    if key_moments is None: checks.append({"name": "key moments", "status": "unavailable", "expected": "optional canonical object", "actual": "absent"}); return
    if not isinstance(key_moments, dict): _fail(checks, "key moments object", "object", key_moments); return
    status = key_moments.get("status")
    moments = _required_list(checks, "key moments", key_moments, "moments")
    if status == "not_available":
        _compare(checks, "key moments unavailable list", [], moments)
        return
    if status != "ready":
        _fail(checks, "key moments status", "ready or not_available", status)
        return
    _compare(checks, "key moments ready cardinality", True, len(moments) > 0)
    ordering, bounded = [], True
    for moment in moments:
        if not isinstance(moment, dict): _fail(checks, "key moment row", "object", moment); bounded = False; continue
        start, time, end, importance = (_required_number(checks, label, moment, key) for label, key in (("key moment start", "window_start_sec"), ("key moment time", "time_sec"), ("key moment end", "window_end_sec"), ("key moment importance", "importance_score")))
        kind, team, moment_id = _required_string(checks, "key moment type", moment, "type"), _required_string(checks, "key moment team", moment, "team_id"), _required_string(checks, "key moment id", moment, "moment_id")
        if None not in (start, time, end, importance, kind, team, moment_id): bounded = bounded and 0 <= start <= time <= end <= duration + EPSILON; ordering.append((-importance, time, kind, team, moment_id))
    _compare(checks, "key moments bounds", True, bounded)
    _compare(checks, "key moments deterministic ordering", sorted(ordering), ordering)


def _advanced_availability(checks: list[dict[str, Any]], report: dict[str, Any]) -> None:
    provenance = _object(report.get("merged_provenance"))
    player_heatmaps = [row.get("heatmap") for row in _required_list(checks, "merged players spatial", report, "players") if isinstance(row, dict)]
    _advanced_output(
        checks,
        label="spatial",
        present=any(value is not None for value in player_heatmaps),
        final_declaration=provenance.get("spatial_heatmaps"),
    )
    _advanced_output(
        checks,
        label="team shape",
        present=report.get("team_shape") is not None,
        final_declaration=provenance.get("team_shape"),
    )


def _advanced_output(checks: list[dict[str, Any]], *, label: str, present: bool, final_declaration: Any) -> None:
    declaration = _advanced_declaration(final_declaration)
    if declaration == "unavailable":
        _compare(checks, f"{label} final provenance consistency", False, present)
        return
    if declaration == "available" and not present:
        _fail(checks, f"{label} final provenance consistency", "present output", "absent")
        return
    if present:
        checks.append({"name": f"{label} mathematical reconciliation", "status": "not_audited", "expected": "independent oracle", "actual": final_declaration})
        return
    checks.append({"name": f"{label} fail-closed absence", "status": "pass", "expected": "absent output", "actual": "absent"})


def _advanced_declaration(value: Any) -> str:
    if value == "merged":
        return "available"
    if isinstance(value, str) and value.startswith("unavailable:"):
        return "unavailable"
    if isinstance(value, dict):
        status = value.get("status")
        if status in {"not_available", "unavailable"}:
            return "unavailable"
        if status in {"ready", "available", "completed", "fresh", "merged"}:
            return "available"
    return "unknown"


def _team_movement(checks: list[dict[str, Any]], source: dict[str, Any], team_id: str) -> dict[str, Any]:
    row = next((row for row in _required_list(checks, f"source {source['published_id']} teams", source["aggregate"], "teams") if isinstance(row, dict) and row.get("team_id") == team_id), None)
    if row is None: _fail(checks, f"source team {team_id} row", "present", None); return {}
    return _required_object(checks, f"source team {team_id} movement", row, "movement") or {}
def _passes(checks: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]: return _required_object(checks, f"source {source['published_id']} passes", _required_object(checks, f"source {source['published_id']} ball", source["aggregate"], "ball") or {}, "passes") or {}
def _sparse_team_pass_count(checks: list[dict[str, Any]], source: dict[str, Any], map_key: str, team_id: str) -> float | None:
    values = _passes(checks, source).get(map_key)
    if not isinstance(values, dict):
        _fail(checks, f"source {source['published_id']} {map_key} required", "object", values)
        return None
    if team_id not in values:
        return 0.0
    count = _finite(values[team_id])
    if count is None or count < 0 or not count.is_integer():
        _fail(checks, f"source {source['published_id']} {map_key} {team_id}", "non-negative integer", values[team_id])
        return None
    return count
def _player_parts(checks: list[dict[str, Any]], sources: list[dict[str, Any]], player_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float]]:
    public: list[dict[str, Any]] = []; movement: list[dict[str, Any]] = []; offsets: list[float] = []
    for source in sources:
        public_row = next((row for row in _required_list(checks, f"source {source['published_id']} players", source["report"], "players") if isinstance(row, dict) and row.get("player_id") == player_id), None)
        if public_row is None: continue
        aggregate_row = next((row for row in _required_list(checks, f"source {source['published_id']} aggregate players", source["aggregate"], "players") if isinstance(row, dict) and row.get("player_id") == player_id), None)
        if aggregate_row is None: _fail(checks, f"source player {player_id} aggregate row", "present", None); continue
        offset = _required_number(checks, f"source player {player_id} offset", source["member"], "logical_start_sec")
        if offset is not None: public.append(public_row); movement.append(_required_object(checks, f"source player {player_id} movement", aggregate_row, "movement") or {}); offsets.append(offset)
    return public, movement, offsets
def _sum_check(checks: list[dict[str, Any]], name: str, rows: list[dict[str, Any]], key: str, actual: dict[str, Any]) -> None:
    values = [_required_number(checks, f"source {name}", row, key) for row in rows]
    if all(value is not None for value in values): _compare(checks, name, sum(value for value in values if value is not None), _required_number(checks, f"{name} actual", actual, key))
def _max_check(checks: list[dict[str, Any]], name: str, rows: list[dict[str, Any]], key: str, actual: dict[str, Any]) -> None:
    values = [_required_number(checks, f"source {name}", row, key) for row in rows]
    if all(value is not None for value in values): _compare(checks, name, max(value for value in values if value is not None), _required_number(checks, f"{name} actual", actual, key))
def _workload(checks: list[dict[str, Any]], player_id: str, actual: dict[str, Any], duration: float) -> None:
    expected = []
    start = 0.0
    while start < duration:
        end = min(duration, start + 300.0)
        expected.append((start, end))
        start = end
    actual_workload = actual.get("workload")
    _compare(checks, f"player {player_id} workload presence", True, isinstance(actual_workload, dict))
    workload = _required_object(checks, f"player {player_id} workload", actual, "workload") or {}
    pairs = []
    for window in _required_list(checks, f"player {player_id} workload windows", workload, "activity_windows"):
        if isinstance(window, dict):
            start, end = _required_number(checks, f"player {player_id} workload start actual", window, "start_time_sec"), _required_number(checks, f"player {player_id} workload end actual", window, "end_time_sec")
            if start is not None and end is not None: pairs.append((start, end))
    _compare_pairs(checks, f"player {player_id} workload timeline", expected, pairs)
    _compare(checks, f"player {player_id} workload bounds", True, all(0 <= start <= end <= duration + EPSILON for start, end in pairs))
def _timeline_rows(checks: list[dict[str, Any]], source: dict[str, Any], name: str, key: str) -> list[Any]:
    timelines = _required_object(checks, f"source {source['published_id']} timelines", source["aggregate"], "timelines") or {}
    return _required_list(checks, f"source {source['published_id']} {name}", _required_object(checks, f"source {source['published_id']} {name} object", timelines, name) or {}, key)
def _rebase_check(checks: list[dict[str, Any]], name: str, actual: Any, sources: list[dict[str, Any]], source_name: str, key: str, duration: float) -> None:
    if not isinstance(actual, list): _fail(checks, f"{name} actual", "list", actual); return
    expected = []
    for source in sources:
        offset, source_duration = _required_number(checks, f"source {source['published_id']} {name} offset", source["member"], "logical_start_sec"), _required_number(checks, f"source {source['published_id']} {name} duration", source["member"], "analyzed_duration_sec")
        if offset is None or source_duration is None: continue
        for row in _timeline_rows(checks, source, source_name, key):
            if not isinstance(row, dict): _fail(checks, f"source {source['published_id']} {name} row", "object", row); continue
            start, end = _required_number(checks, f"source {source['published_id']} {name} start", row, "start_time_sec"), _required_number(checks, f"source {source['published_id']} {name} end", row, "end_time_sec")
            if start is None or end is None: continue
            if start < 0 or start >= source_duration or end < start: _fail(checks, f"source {source['published_id']} {name} primitive bounds", f"0 <= start < {source_duration} and end >= start", {"start": start, "end": end}); continue
            expected.append((start + offset, min(end + offset, offset + source_duration)))
    pairs = []
    for row in actual:
        if isinstance(row, dict):
            start, end = _required_number(checks, f"{name} start actual", row, "start_time_sec"), _required_number(checks, f"{name} end actual", row, "end_time_sec")
            if start is not None and end is not None: pairs.append((start, end))
        else: _fail(checks, f"{name} row", "object", row)
    _compare_pairs(checks, f"{name} exact source rebasing", expected, pairs)
    _compare(checks, f"{name} within merged timeline", True, all(0 <= start <= end <= duration + EPSILON for start, end in pairs))


def _ids(checks: list[dict[str, Any]], name: str, rows: list[Any], key: str) -> set[str]:
    values = []
    for row in rows:
        if not isinstance(row, dict):
            _fail(checks, f"{name} row", "object", row)
            continue
        values.append(_required_string(checks, f"{name} {key}", row, key))
    values = [value for value in values if value is not None]
    _compare(checks, f"{name} IDs unique", len(values), len(set(values)))
    return set(values)
def _lookup(rows: list[Any], key: str) -> dict[str, dict[str, Any]]: return {str(row[key]): row for row in rows if isinstance(row, dict) and isinstance(row.get(key), str)}
def _compare_pairs(checks: list[dict[str, Any]], name: str, expected: list[tuple[float, float]], actual: list[tuple[float, float]]) -> None:
    passed = len(expected) == len(actual) and all(
        all(abs(left - right) <= EPSILON for left, right in zip(expected_pair, actual_pair))
        for expected_pair, actual_pair in zip(expected, actual)
    )
    checks.append({"name": name, "status": "pass" if passed else "fail", "expected": _json_value(expected), "actual": _json_value(actual)})


def _compare(checks: list[dict[str, Any]], name: str, expected: Any, actual: Any) -> None:
    numeric = isinstance(expected, (int, float)) and not isinstance(expected, bool) and isinstance(actual, (int, float)) and not isinstance(actual, bool)
    passed = math.isfinite(float(expected)) and math.isfinite(float(actual)) and abs(float(expected) - float(actual)) <= EPSILON if numeric else expected == actual
    checks.append({"name": name, "status": "pass" if passed else "fail", "expected": _json_value(expected), "actual": _json_value(actual)})
def _fail(checks: list[dict[str, Any]], name: str, expected: Any, actual: Any) -> None: checks.append({"name": name, "status": "fail", "expected": _json_value(expected), "actual": _json_value(actual)})


def _json_value(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(_json_value(item) for item in value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value
def _required_number(checks: list[dict[str, Any]], name: str, row: dict[str, Any], key: str) -> float | None:
    value = _finite(row.get(key))
    if value is None: _fail(checks, f"{name} required", "finite number", row.get(key))
    return value
def _required_string(checks: list[dict[str, Any]], name: str, row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if not isinstance(value, str) or not value: _fail(checks, f"{name} required", "non-empty string", value); return None
    return value
def _required_object(checks: list[dict[str, Any]], name: str, row: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = row.get(key)
    if not isinstance(value, dict): _fail(checks, f"{name} required", "object", value); return None
    return value
def _required_list(checks: list[dict[str, Any]], name: str, row: dict[str, Any], key: str) -> list[Any]:
    value = row.get(key)
    if not isinstance(value, list): _fail(checks, f"{name} required", "list", value); return []
    return value
def _required_strings(checks: list[dict[str, Any]], name: str, row: dict[str, Any], key: str) -> list[str]:
    value = _required_list(checks, name, row, key)
    if not all(isinstance(item, str) for item in value): _fail(checks, f"{name} entries", "strings", value); return []
    return sorted(value)
def _finite(value: Any) -> float | None: return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None
def _object(value: Any) -> dict[str, Any]: return value if isinstance(value, dict) else {}
def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError(f"Expected an object in {path}")
    return value
def _resolve(groups: Path, matches: Path, identifier: str) -> tuple[str, str]:
    if identifier.startswith("match-group-"): return identifier, str(_read(groups / identifier / "merged_projection.json")["merged_published_match_id"])
    group_id = str(_read(matches / identifier / "summary.json").get("backing_group_id") or "")
    if not group_id: raise ValueError(f"{identifier} is not a merged published match")
    return group_id, identifier
