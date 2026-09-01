from __future__ import annotations

"""Build one fail-closed public report above pinned published match parts.

This Phase 3 service intentionally reads only a match-group manifest plus the
published ``aggregate_inputs.json`` and ``public_report.json`` files already
validated by Phase 2.  It never opens a physical analysis directory.
"""

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_groups import (
    MATCH_GROUPS_DIR,
    PUBLISHED_MATCHES_DIR,
    MatchGroupError,
    get_match_group,
    validate_match_group,
)


AGGREGATE_REPORT_SCHEMA_VERSION = "1.0.0"
AGGREGATE_REPORT_TYPE = "public_aggregate_match_report"
AGGREGATE_ENGINE_POLICY_VERSION = "1.0.0"

# This is deliberately data, rather than a recursive JSON operation.  A
# future metric requires an explicit decision before it can enter a report.
AGGREGATION_REGISTRY = {
    "player.movement.total_distance_m": "sum",
    "player.movement.observed_distance_m": "sum",
    "player.movement.estimated_short_gap_distance_m": "sum",
    "player.movement.movement_time_sec": "sum",
    "player.movement.detected_time_sec": "sum",
    "player.movement.high_intensity_distance_m": "sum",
    "player.movement.sprint_count": "sum",
    "player.movement.peak_speed_kmh": "max",
    "player.movement.avg_speed_kmh": "recompute_distance_over_movement_time",
    "team.movement.total_distance_m": "sum",
    "team.movement.high_intensity_distance_m": "sum",
    "team.movement.sprint_count": "sum",
    "team.movement.peak_speed_kmh": "max",
    "ball.passes": "sum_counts_recompute_rate",
    "ball.possession": "sum_controlled_counts_recompute_share",
    "identity_coverage": "sum_counts_recompute_rates",
    "timelines.possession": "rebase_and_concat_primitives",
    "timelines.attacking_momentum": "rebase_and_concat_points",
    "spatial.heatmaps": "not_available_without_canonical_orientation",
    "spatial.team_shape": "not_available_without_canonical_orientation",
}

_SUM_MOVEMENT_FIELDS = (
    "total_distance_m",
    "observed_distance_m",
    "estimated_short_gap_distance_m",
    "movement_time_sec",
    "detected_time_sec",
    "high_intensity_distance_m",
    "sprint_count",
)
_MAX_MOVEMENT_FIELDS = ("peak_speed_kmh",)
_PASS_COUNT_FIELDS = (
    "attempts",
    "completed",
    "failed",
    "restart_attempts",
    "accepted",
)
_READY_STATUSES = frozenset({"ready", "available", "completed", "fresh"})
_QUALITY_ORDER = {"not_available": 0, "low": 1, "medium": 2, "high": 3}


def generate_match_group_report(group_id: str) -> dict[str, Any]:
    """Generate and atomically publish the report for one pinned group.

    Validation happens before any aggregate bytes are written.  Thus a stale,
    missing, or tampered child generation leaves an earlier coherent aggregate
    report untouched.
    """
    manifest = get_match_group(group_id)
    validation = validate_match_group(group_id)
    if validation.get("status") != "compatible":
        reasons = validation.get("blocking_reasons") or []
        detail = str((reasons[0] if reasons else {}).get("detail") or "Match group is not compatible.")
        raise MatchGroupError("aggregate_generation_blocked", detail)
    sources = _load_pinned_sources(manifest)
    report = build_match_group_public_report(manifest, sources)
    _atomic_write_json(MATCH_GROUPS_DIR / group_id / "public_report.json", report)
    return report


def build_match_group_public_report(
    manifest: Mapping[str, Any],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate verified compact inputs into one deterministic public report."""
    members = _list(manifest.get("members"))
    if len(members) != len(sources) or not members:
        raise MatchGroupError("manifest_members_invalid", "Manifest member sources are unavailable.")
    metadata = _record(manifest.get("metadata"))
    team_presentation = _team_presentation(sources)
    report: dict[str, Any] = {
        "schema_version": AGGREGATE_REPORT_SCHEMA_VERSION,
        "report_type": AGGREGATE_REPORT_TYPE,
        "group_id": str(manifest.get("group_id") or ""),
        "match": dict(metadata),
        "source_match_ids": [str(member.get("source_match_id") or "") for member in members],
        "source_published_ids": [str(member.get("published_id") or "") for member in members],
        "sources": [
            {
                "published_id": str(member.get("published_id") or ""),
                "source_match_id": str(member.get("source_match_id") or ""),
                "sequence_index": int(member.get("sequence_index") or 0),
                "logical_offset_sec": _number(member.get("logical_start_sec")),
                "aggregation_input_semantic_digest": str(member.get("aggregation_input_semantic_digest") or ""),
                "public_report_semantic_digest": str(member.get("public_report_semantic_digest") or ""),
            }
            for member in members
        ],
        "timing": {
            "analyzed_duration_sec": sum(_number(member.get("analyzed_duration_sec")) for member in members),
            "timeline_span_sec": _number(_record(manifest.get("timing")).get("timeline_span_sec")),
            "mapping": "ordered_sequential_source_durations",
        },
        "teams": _aggregate_teams(sources, team_presentation),
        "players": _aggregate_players(sources, team_presentation),
        "ball": {
            "possession": _aggregate_possession(sources),
            "passes": _aggregate_passes(sources),
        },
        "stats_semantics": _aggregate_stats_semantics(sources),
        "timelines": _aggregate_timelines(sources),
        "identity_coverage": _aggregate_identity_coverage(sources),
        "spatial": {
            "heatmaps": {"status": "not_available", "reason": "canonical_orientation_not_proven"},
            "team_shape": {"status": "not_available", "reason": "canonical_orientation_and_sample_weights_not_proven"},
        },
        "metric_readiness": _aggregate_readiness(sources),
        "aggregation": {
            "policy_version": AGGREGATE_ENGINE_POLICY_VERSION,
            "registry": dict(AGGREGATION_REGISTRY),
        },
    }
    digest_document = copy.deepcopy(report)
    report["aggregate_semantic_digest"] = canonical_json_sha256(digest_document)
    return report


def _load_pinned_sources(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for member in _list(manifest.get("members")):
        published_id = str(_record(member).get("published_id") or "")
        if not published_id:
            raise MatchGroupError("manifest_members_invalid", "Manifest member published_id is required.")
        source_dir = PUBLISHED_MATCHES_DIR / published_id
        aggregate = _load_json(source_dir / "aggregate_inputs.json", published_id)
        public = _load_json(source_dir / "public_report.json", published_id)
        source = _record(aggregate.get("source"))
        _assert_equal(source.get("aggregation_input_semantic_digest"), _record(member).get("aggregation_input_semantic_digest"), "source_generation_changed", published_id)
        _assert_equal(source.get("public_report_semantic_digest"), _record(member).get("public_report_semantic_digest"), "source_generation_changed", published_id)
        _assert_equal(canonical_json_sha256(public), source.get("public_report_semantic_digest"), "public_report_digest_mismatch", published_id)
        digest_document = copy.deepcopy(aggregate)
        _record(digest_document.get("source")).pop("aggregation_input_semantic_digest", None)
        _assert_equal(canonical_json_sha256(digest_document), source.get("aggregation_input_semantic_digest"), "aggregation_input_digest_mismatch", published_id)
        _assert_equal(source.get("source_match_id"), _record(member).get("source_match_id"), "source_generation_changed", published_id)
        rows.append({"member": dict(member), "aggregate": aggregate, "public": public})
    return rows


def _aggregate_teams(sources: list[dict[str, Any]], presentation: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, list[Mapping[str, Any]]] = {}
    for source in sources:
        for team in _list(_record(source["aggregate"]).get("teams")):
            item = _record(team)
            team_id = _required_id(item.get("team_id"), "team_id")
            rows.setdefault(team_id, []).append(_record(item.get("movement")))
    return [
        {"team_id": team_id, **dict(presentation.get(team_id) or {}), "movement": _aggregate_movement(values, require_all=True)}
        for team_id, values in sorted(rows.items())
    ]


def _aggregate_players(sources: list[dict[str, Any]], presentation: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for source in sources:
        for player in _list(_record(source["aggregate"]).get("players")):
            item = _record(player)
            player_id = _required_id(item.get("player_id"), "player_id")
            team_id = _required_id(item.get("team_id"), "player.team_id")
            previous = rows.setdefault(player_id, {"team_id": team_id, "movement": []})
            if previous["team_id"] != team_id:
                raise MatchGroupError("player_team_mismatch", "A stable player_id maps to multiple team_ids.")
            previous["movement"].append(_record(item.get("movement")))
    result = []
    for player_id, row in sorted(rows.items()):
        public = _player_presentation(sources, player_id)
        result.append({
            "player_id": player_id,
            "team_id": row["team_id"],
            **public,
            "movement": _aggregate_movement(row["movement"], require_all=False),
        })
    return result


def _aggregate_movement(values: list[Mapping[str, Any]], *, require_all: bool) -> dict[str, Any]:
    if not values or any(str(value.get("status") or "") == "not_available" for value in values):
        return {"status": "not_available", "reason": "source_movement_primitives_unavailable"}
    result: dict[str, Any] = {"status": "ready"}
    complete = True
    for field in _SUM_MOVEMENT_FIELDS:
        numbers = [_optional_number(value.get(field)) for value in values]
        if all(number is not None for number in numbers):
            result[field] = sum(number for number in numbers if number is not None)
        else:
            complete = False
    for field in _MAX_MOVEMENT_FIELDS:
        numbers = [_optional_number(value.get(field)) for value in values]
        if all(number is not None for number in numbers):
            result[field] = max(number for number in numbers if number is not None)
        else:
            complete = False
    distance = _optional_number(result.get("total_distance_m"))
    movement_time = _optional_number(result.get("movement_time_sec"))
    if distance is not None and movement_time is not None and movement_time > 0:
        result["avg_speed_kmh"] = distance / movement_time * 3.6
    else:
        result["avg_speed_kmh"] = None
        complete = False
    if not complete:
        result["status"] = "partial" if any(key in result for key in _SUM_MOVEMENT_FIELDS + _MAX_MOVEMENT_FIELDS) else "not_available"
        result["reason"] = "some_source_movement_primitives_unavailable"
    return result


def _aggregate_possession(sources: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_record(_record(source["aggregate"]).get("ball")).get("possession") for source in sources]
    records = [_record(value) for value in values]
    if not _all_ready(records) or any(not _record(value.get("controlled_frames_by_team_id")) for value in records):
        return {"status": "not_available", "reason": "source_possession_primitives_unavailable"}
    controlled = _sum_team_maps(records, "controlled_frames_by_team_id")
    result: dict[str, Any] = {"status": "ready", "controlled_frames_by_team_id": controlled}
    for field in ("known_frames", "free_frames", "unknown_frames", "contested_frames", "processed_frames"):
        numbers = [_optional_number(value.get(field)) for value in records]
        if all(number is not None for number in numbers):
            result[field] = sum(number for number in numbers if number is not None)
    known = sum(controlled.values())
    result["possession_share_percent_by_team_id"] = {
        team_id: value / known * 100 if known else None for team_id, value in sorted(controlled.items())
    }
    return result


def _aggregate_passes(sources: list[dict[str, Any]]) -> dict[str, Any]:
    records = [_record(_record(_record(source["aggregate"]).get("ball")).get("passes")) for source in sources]
    if not _all_ready(records):
        return {"status": "not_available", "reason": "source_pass_primitives_unavailable"}
    result: dict[str, Any] = {"status": "ready"}
    for field in _PASS_COUNT_FIELDS:
        team_field = f"{field}_by_team_id"
        if all(isinstance(value.get(team_field), Mapping) for value in records):
            result[team_field] = _sum_team_maps(records, team_field)
        numbers = [_optional_number(value.get(field)) for value in records]
        if all(number is not None for number in numbers):
            result[field] = sum(number for number in numbers if number is not None)
    attempts = _optional_number(result.get("attempts"))
    completed = _optional_number(result.get("completed"))
    result["completion_rate_percent"] = completed / attempts * 100 if attempts and completed is not None else None
    if "attempts_by_team_id" in result and "completed_by_team_id" in result:
        result["completion_rate_percent_by_team_id"] = {
            team_id: (
                result["completed_by_team_id"].get(team_id, 0) / attempts * 100
                if (attempts := result["attempts_by_team_id"].get(team_id, 0))
                else None
            )
            for team_id in sorted(result["attempts_by_team_id"])
        }
    return result


def _aggregate_identity_coverage(sources: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_record(_record(source["aggregate"]).get("identity_coverage")) for source in sources]
    units = {str(value.get("coverage_unit") or "") for value in values}
    if not _all_ready(values) or len(units) != 1 or not next(iter(units), ""):
        return {"status": "not_available", "reason": "coverage_units_or_primitives_unavailable"}
    fields = ("confirmed_observations", "reliable_observations", "unresolved_observations", "conflicted_observations")
    if any(any(_optional_number(value.get(field)) is None for field in fields) for value in values):
        return {"status": "not_available", "reason": "coverage_primitives_unavailable"}
    result = {"status": "ready", "coverage_unit": next(iter(units))}
    for field in fields:
        result[field] = sum(_number(value.get(field)) for value in values)
    reliable = result["reliable_observations"]
    result["confirmed_coverage_percent"] = result["confirmed_observations"] / reliable * 100 if reliable else None
    return result


def _aggregate_timelines(sources: list[dict[str, Any]]) -> dict[str, Any]:
    possession_windows = []
    momentum_points = []
    possession_ready = True
    momentum_sources = []
    for source in sources:
        offset = _number(_record(source["member"]).get("logical_start_sec"))
        timelines = _record(_record(source["aggregate"]).get("timelines"))
        possession = _record(timelines.get("possession"))
        momentum = _record(timelines.get("attacking_momentum"))
        if not _ready(possession):
            possession_ready = False
        else:
            possession_windows.extend(_rebase_rows(_list(possession.get("windows")), offset, source, "possession"))
        momentum_sources.append(momentum)
        if _ready(momentum):
            momentum_points.extend(_rebase_rows(_list(momentum.get("points")), offset, source, "momentum"))
    aggregate_momentum = _aggregate_momentum_metadata(momentum_sources)
    if not _ready(aggregate_momentum):
        aggregate_momentum.pop("points", None)
    else:
        aggregate_momentum["points"] = momentum_points
    return {
        "possession": {"status": "ready", "windows": possession_windows} if possession_ready else {"status": "not_available", "reason": "source_possession_timeline_unavailable"},
        "attacking_momentum": aggregate_momentum,
    }


def _rebase_rows(rows: list[Any], offset: float, source: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    result = []
    for raw in rows:
        row = dict(_record(raw))
        start = _optional_number(row.get("start_time_sec"))
        end = _optional_number(row.get("end_time_sec"))
        if start is None or end is None or end < start:
            raise MatchGroupError("timeline_primitive_invalid", f"{kind} timeline requires ordered numeric source-local times.")
        row["start_time_sec"] = start + offset
        row["end_time_sec"] = end + offset
        row["source_published_id"] = str(_record(source["member"]).get("published_id") or "")
        if kind == "possession":
            counts = row.get("controlled_frames_by_team_id")
            if not isinstance(counts, Mapping):
                raise MatchGroupError("timeline_primitive_invalid", "Possession timeline requires stable-team controlled frame counts.")
            controlled = {str(team_id): _number(value) for team_id, value in counts.items()}
            known = sum(controlled.values())
            row["known_team_frames"] = known
            row["possession_share_percent_by_team_id"] = {
                team_id: value / known * 100 if known else None
                for team_id, value in sorted(controlled.items())
            }
        result.append(row)
    return result


def _aggregate_momentum_metadata(values: list[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = [str(value.get("status") or "not_available") for value in values]
    status = _conservative_source_status(statuses)
    result: dict[str, Any] = {"status": status}
    for field in ("product_readiness", "signal_quality", "quality"):
        combined = _conservative_momentum_value(field, [value.get(field) for value in values])
        if combined is not None:
            result[field] = combined
    if not _ready(result):
        result["reason"] = "source_momentum_timeline_unavailable"
    return result


def _conservative_source_status(values: list[str]) -> str:
    if not values or any(value not in _READY_STATUSES for value in values):
        return "not_available"
    return values[0] if len(set(values)) == 1 else "partial"


def _conservative_momentum_value(field: str, values: list[Any]) -> str | None:
    normalized = [str(value or "not_available") for value in values]
    if not normalized:
        return None
    if field in {"signal_quality", "quality"}:
        if any(value not in _QUALITY_ORDER for value in normalized):
            return "not_available"
        return min(normalized, key=lambda value: _QUALITY_ORDER.get(value, -1))
    # Existing source semantics are authoritative.  Experimental always wins
    # over a more optimistic source label, and unknown combinations fail safe.
    if "not_available" in normalized:
        return "not_available"
    if "experimental" in normalized:
        return "experimental"
    return normalized[0] if len(set(normalized)) == 1 else "not_available"


def _aggregate_stats_semantics(sources: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        str(_record(_record(source["public"]).get("stats_semantics")).get("ball") or "")
        for source in sources
    ]
    values = [value for value in values if value]
    if not values:
        return {"ball": "not_available"}
    if "experimental_candidates" in values:
        return {"ball": "experimental_candidates"}
    return {"ball": values[0]} if len(set(values)) == 1 else {"ball": "not_available"}


def _aggregate_readiness(sources: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_record(_record(source["aggregate"]).get("metric_readiness")) for source in sources]
    keys = sorted({key for value in values for key in value})
    return {key: _conservative_status([_record(value.get(key)).get("status") if isinstance(value.get(key), Mapping) else value.get(key) for value in values]) for key in keys}


def _team_presentation(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in sources:
        for team in _list(_record(source["public"]).get("teams")):
            row = _record(team)
            team_id = str(row.get("team_id") or "")
            if team_id and team_id not in result:
                result[team_id] = {key: row[key] for key in ("team_name", "display_color") if row.get(key) is not None}
    return result


def _player_presentation(sources: list[dict[str, Any]], player_id: str) -> dict[str, Any]:
    for source in sources:
        for player in _list(_record(source["public"]).get("players")):
            row = _record(player)
            if str(row.get("player_id") or "") == player_id:
                return {key: row[key] for key in ("player_name", "player_number", "player_role") if row.get(key) is not None}
    return {}


def _sum_team_maps(records: list[Mapping[str, Any]], field: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for record in records:
        mapping = record.get(field)
        if not isinstance(mapping, Mapping):
            raise MatchGroupError("metric_primitives_invalid", f"{field} requires a stable-team count map.")
        for team_id, value in mapping.items():
            result[str(team_id)] = result.get(str(team_id), 0.0) + _number(value)
    return dict(sorted(result.items()))


def _all_ready(values: list[Mapping[str, Any]]) -> bool:
    return bool(values) and all(_ready(value) for value in values)


def _ready(value: Mapping[str, Any]) -> bool:
    return str(value.get("status") or "") in _READY_STATUSES


def _conservative_status(values: list[Any]) -> str:
    normalized = {str(value or "not_available") for value in values}
    if normalized <= _READY_STATUSES:
        return "ready"
    if normalized & _READY_STATUSES:
        return "partial"
    return "not_available"


def _load_json(path: Path, member: str) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MatchGroupError("published_source_missing", f"Could not read {path.name}.", member=member) from error
    if not isinstance(result, dict):
        raise MatchGroupError("source_json_invalid", f"{path.name} must be an object.", member=member)
    return result


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _assert_equal(current: Any, expected: Any, code: str, member: str) -> None:
    if current != expected:
        raise MatchGroupError(code, "Current published source no longer matches the pinned group generation.", member=member)


def _required_id(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise MatchGroupError("stable_id_missing", f"{field} is required for aggregate generation.")
    return result


def _number(value: Any) -> float:
    result = _optional_number(value)
    if result is None:
        raise MatchGroupError("metric_primitives_invalid", "Aggregate metric primitive must be a finite number.")
    return result


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result == result and result not in {float("inf"), float("-inf")} else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
