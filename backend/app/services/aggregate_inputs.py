from __future__ import annotations

"""Compact, deterministic inputs for a future logical-match aggregator.

This module deliberately consumes only a publishable package and its generated
public report.  It never opens source videos, raw tracklets, or Reviewed
Identity snapshots.  Match-group storage and aggregation are intentionally
outside this Phase 1 boundary.
"""

from typing import Any

from app.services.artifact_lineage import canonical_json_sha256
from app.services.public_match_report import pass_counts_for_team_label


AGGREGATE_INPUTS_SCHEMA_VERSION = "1.0.0"
AGGREGATION_POLICY_VERSION = "1.0.0"


class AggregateInputsError(ValueError):
    """Raised when a publishable source cannot supply a safe aggregate input."""


def build_aggregate_inputs(
    package: dict[str, Any],
    *,
    public_report: dict[str, Any],
    published_id: str,
) -> dict[str, Any]:
    """Build one exact, aggregate-ready projection for a reviewed publication."""

    match = _record(package.get("match"))
    source_match_id = _required_id(match.get("id"), "match.id")
    if str(public_report.get("source_match_id") or "") != source_match_id:
        raise AggregateInputsError("Public report source_match_id does not match package match.id")
    if str(public_report.get("id") or "") != published_id:
        raise AggregateInputsError("Public report id does not match published_id")

    reviewed_identity_digest = _validated_reviewed_identity_digest(package)
    team_by_label, roster_team_by_player = _stable_source_ids(package)
    team_rows = _build_teams(package, team_by_label)
    players = _build_players(package, roster_team_by_player, team_by_label)
    possession = _build_possession(package, team_by_label)
    passes = _build_passes(package, team_by_label)
    timelines = _build_timelines(package, team_by_label, possession, passes)

    document: dict[str, Any] = {
        "schema_version": AGGREGATE_INPUTS_SCHEMA_VERSION,
        "aggregation_policy_version": AGGREGATION_POLICY_VERSION,
        "source": {
            "source_match_id": source_match_id,
            "published_id": published_id,
            "reviewed_identity_digest": reviewed_identity_digest,
            "public_report_semantic_digest": canonical_json_sha256(public_report),
        },
        "timing": _timing(match, _record(package.get("reviewed_player_stats"))),
        "teams": team_rows,
        "players": players,
        "identity_coverage": _identity_coverage(package),
        "ball": {
            "possession": possession,
            "passes": passes,
        },
        "timelines": timelines,
        "spatial": _spatial(package),
        "metric_readiness": _metric_readiness(package, possession, passes),
    }
    document["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(document)
    return document


def _stable_source_ids(package: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    match = _record(package.get("match"))
    teams = _list(match.get("teams"))
    match_team_ids = {_required_id(_record(team).get("id"), "match.teams[].id") for team in teams}
    roster_team_by_player: dict[str, str] = {}
    for team in teams:
        row = _record(team)
        team_id = _required_id(row.get("id"), "match.teams[].id")
        for player in _list(row.get("players")):
            player_id = _required_id(_record(player).get("id"), "match.teams[].players[].id")
            previous = roster_team_by_player.setdefault(player_id, team_id)
            if previous != team_id:
                raise AggregateInputsError(f"player_id {player_id!r} appears in more than one source team")

    team_by_label: dict[str, str] = {}
    label_by_team_id: dict[str, str] = {}
    team_config = _record(package.get("team_config"))
    for raw_team in _list(team_config.get("teams")):
        team = _record(raw_team)
        label = _required_label(team.get("team_label"), "team_config.teams[].team_label")
        team_id = _required_id(team.get("team_id"), "team_config.teams[].team_id")
        if team_id not in match_team_ids:
            raise AggregateInputsError(
                f"team_config label {label!r} references team_id {team_id!r} outside match.teams"
            )
        previous = team_by_label.setdefault(label, team_id)
        if previous != team_id:
            raise AggregateInputsError(f"team_label {label!r} maps to more than one stable team_id")
        previous_label = label_by_team_id.setdefault(team_id, label)
        if previous_label != label:
            raise AggregateInputsError(f"stable team_id {team_id!r} maps to more than one local team label")
    return team_by_label, roster_team_by_player


def _validated_reviewed_identity_digest(package: dict[str, Any]) -> str:
    digest = _required_id(package.get("reviewed_identity_digest"), "reviewed_identity_digest")
    documents = {
        "reviewed_player_stats": _record(package.get("reviewed_player_stats")),
        "reviewed_player_heatmaps": _record(package.get("reviewed_player_heatmaps")),
        "reviewed_stats_readiness": _record(package.get("reviewed_stats_readiness")),
    }
    for name, document in documents.items():
        document_digest = _required_id(document.get("source_snapshot_digest"), f"{name}.source_snapshot_digest")
        if document_digest != digest:
            raise AggregateInputsError(f"{name} is not from the published Reviewed Identity generation")
    manifest = _record(package.get("reviewed_output_manifest"))
    identity = _record(manifest.get("reviewed_identity"))
    stats = _record(manifest.get("stats"))
    if _required_id(identity.get("digest"), "reviewed_output_manifest.reviewed_identity.digest") != digest:
        raise AggregateInputsError("reviewed output manifest identity digest is stale")
    if _required_id(stats.get("source_snapshot_digest"), "reviewed_output_manifest.stats.source_snapshot_digest") != digest:
        raise AggregateInputsError("reviewed output manifest stats digest is stale")
    if documents["reviewed_stats_readiness"].get("status") != "completed":
        raise AggregateInputsError("reviewed stats readiness is not completed")
    if identity.get("status") != "fresh" or stats.get("status") != "completed" or manifest.get("stale") is True:
        raise AggregateInputsError("reviewed output manifest is not fresh and completed")
    return digest


def _build_teams(package: dict[str, Any], team_by_label: dict[str, str]) -> list[dict[str, Any]]:
    team_stats = _record(package.get("team_stats"))
    movement_by_team: dict[str, dict[str, Any]] = {}
    for raw_row in _list(team_stats.get("teams")):
        row = _record(raw_row)
        label = _required_label(row.get("team_label"), "team_stats.teams[].team_label")
        team_id = _team_id_for_label(label, team_by_label, "team_stats")
        declared_team_id = row.get("team_id")
        if declared_team_id is not None and str(declared_team_id) != team_id:
            raise AggregateInputsError(f"team_stats label {label!r} disagrees with stable team_id")
        if team_id in movement_by_team:
            raise AggregateInputsError(f"team_stats has duplicate stable team_id {team_id!r}")
        movement = _numeric_fields(
            row,
            ("total_distance_m", "high_intensity_distance_m", "sprint_count"),
        )
        peak_speed = _first_number(row, ("peak_sustained_speed_kmh", "top_speed_kmh"))
        if peak_speed is not None:
            movement["peak_speed_kmh"] = peak_speed
        movement["average_speed"] = {
            "status": "not_available",
            "reason": "canonical_team_movement_time_missing",
        }
        movement_by_team[team_id] = movement

    rows = []
    for label, team_id in sorted(team_by_label.items(), key=lambda item: item[1]):
        movement = movement_by_team.get(team_id)
        rows.append(
            {
                "team_id": team_id,
                "source_team_label": label,
                "movement": movement
                if movement is not None
                else {"status": "not_available", "reason": "team_movement_row_missing"},
            }
        )
    return rows


def _build_players(
    package: dict[str, Any],
    roster_team_by_player: dict[str, str],
    team_by_label: dict[str, str],
) -> list[dict[str, Any]]:
    stats = _record(package.get("reviewed_player_stats"))
    rows = []
    seen: set[str] = set()
    for raw_row in _list(stats.get("players")):
        row = _record(raw_row)
        player_id = _required_id(row.get("player_id"), "reviewed_player_stats.players[].player_id")
        team_id = roster_team_by_player.get(player_id)
        if team_id is None:
            raise AggregateInputsError(
                f"reviewed player {player_id!r} is not a real roster player in match.teams"
            )
        label = _required_label(row.get("team_label"), "reviewed_player_stats.players[].team_label")
        configured_team_id = _team_id_for_label(label, team_by_label, "reviewed_player_stats")
        if configured_team_id != team_id:
            raise AggregateInputsError(
                f"reviewed player {player_id!r} has a local team label inconsistent with roster team_id"
            )
        if player_id in seen:
            raise AggregateInputsError(f"reviewed_player_stats has duplicate player_id {player_id!r}")
        seen.add(player_id)

        intensity = _record(row.get("intensity"))
        speed = _record(row.get("speed"))
        movement = _numeric_fields(
            row,
            ("total_distance_m", "observed_distance_m", "estimated_short_gap_distance_m", "detected_time_sec"),
        )
        movement_time = _number_or_none(row.get("movement_time_sec"))
        if movement_time is not None:
            movement["movement_time_sec"] = movement_time
        else:
            movement["average_speed"] = {
                "status": "not_available",
                "reason": "canonical_movement_time_missing",
            }
        for source, destination in (
            (intensity, "high_intensity_distance_m"),
            (intensity, "sprint_count"),
        ):
            value = _number_or_none(source.get(destination))
            if value is not None:
                movement[destination] = value
        peak_speed = _first_number(speed, ("peak_sustained_speed_kmh", "top_speed_kmh"))
        if peak_speed is not None:
            movement["peak_speed_kmh"] = peak_speed
        if not movement:
            movement = {"status": "not_available", "reason": "reviewed_movement_primitives_missing"}

        identity: dict[str, Any] = {"coverage_status": "not_available", "reason": "player_reliable_denominator_missing"}
        confirmed = _number_or_none(row.get("confirmed_detected_observations"))
        if confirmed is not None:
            identity["confirmed_observations"] = confirmed
        rows.append({"player_id": player_id, "team_id": team_id, "movement": movement, "identity": identity})
    return sorted(rows, key=lambda row: str(row["player_id"]))


def _build_possession(package: dict[str, Any], team_by_label: dict[str, str]) -> dict[str, Any]:
    possession = _record(package.get("possession_report"))
    summary = _record(possession.get("summary"))
    status = _analytics_status(package, "possession", possession.get("status"))
    controlled = _record(summary.get("team_controlled_frames"))
    if not controlled:
        return {"status": "not_available", "reason": "team_controlled_frames_missing"}
    by_team = _stable_count_map(controlled, team_by_label, "possession.team_controlled_frames")
    result: dict[str, Any] = {"status": status, "controlled_frames_by_team_id": by_team}
    for source, destination in (
        ("known_possession_frames", "known_frames"),
        ("free_frames", "free_frames"),
        ("unknown_frames", "unknown_frames"),
        ("contested_frames", "contested_frames"),
        ("processed_frames", "processed_frames"),
    ):
        value = _number_or_none(summary.get(source))
        if value is not None:
            result[destination] = value
    return result


def _build_passes(package: dict[str, Any], team_by_label: dict[str, str]) -> dict[str, Any]:
    passes = _record(package.get("pass_candidates"))
    summary = _record(passes.get("summary"))
    status = _analytics_status(package, "passes", passes.get("status"))
    maps = {
        "attempts_by_team_id": _record(summary.get("team_pass_attempts")),
        "completed_by_team_id": _record(summary.get("team_completed_passes")),
        "failed_by_team_id": _record(summary.get("team_failed_passes")),
    }
    if not any(maps.values()):
        return {"status": "not_available", "reason": "team_pass_counts_missing"}
    result: dict[str, Any] = {"status": status}
    for destination, source in maps.items():
        if source:
            result[destination] = _stable_count_map(source, team_by_label, f"passes.{destination}")
    for source, destination in (
        ("pass_attempts", "attempts"),
        ("completed_passes", "completed"),
        ("failed_passes", "failed"),
        ("restart_pass_attempts", "restart_attempts"),
        ("final_stat_passes", "accepted"),
    ):
        value = _number_or_none(summary.get(source))
        if value is not None:
            result[destination] = value

    candidate_rows = passes.get("candidates")
    if not isinstance(candidate_rows, list):
        raise AggregateInputsError(
            "pass_candidates.candidates is required to derive stable-team restart and accepted pass counts"
        )
    restart_attempts_by_team_id: dict[str, int] = {}
    accepted_by_team_id: dict[str, int] = {}
    for label, team_id in sorted(team_by_label.items(), key=lambda item: item[1]):
        counts = pass_counts_for_team_label(package, label)
        restart_attempts_by_team_id[team_id] = counts["restart_passes"]
        accepted_by_team_id[team_id] = counts["accepted_passes"]
    result["restart_attempts_by_team_id"] = restart_attempts_by_team_id
    result["accepted_by_team_id"] = accepted_by_team_id
    return result


def _build_timelines(
    package: dict[str, Any],
    team_by_label: dict[str, str],
    possession: dict[str, Any],
    passes: dict[str, Any],
) -> dict[str, Any]:
    possession_doc = _record(package.get("possession_report"))
    possession_windows = []
    for raw_item in _list(possession_doc.get("possession_timeline")):
        item = _record(raw_item)
        counts = _record(item.get("team_controlled_frames"))
        if not counts:
            continue
        window: dict[str, Any] = {
            "start_time_sec": _required_number(item.get("start_time_sec"), "possession timeline start_time_sec"),
            "end_time_sec": _required_number(item.get("end_time_sec"), "possession timeline end_time_sec"),
            "controlled_frames_by_team_id": _stable_count_map(
                counts, team_by_label, "possession_timeline.team_controlled_frames"
            ),
        }
        for source, destination in (
            ("contested_frames", "contested_frames"),
            ("free_frames", "free_frames"),
            ("unknown_frames", "unknown_frames"),
            ("frames", "total_frames"),
        ):
            value = _number_or_none(item.get(source))
            if value is not None:
                window[destination] = value
        possession_windows.append(window)

    momentum_doc = _record(package.get("attacking_momentum"))
    momentum_status = _analytics_status(package, "momentum", momentum_doc.get("status"))
    momentum_points = []
    for raw_item in _list(momentum_doc.get("points")):
        item = _record(raw_item)
        values: dict[str, Any] = {}
        for label, key in (("A", "team_a_value"), ("B", "team_b_value")):
            value = _number_or_none(item.get(key))
            if value is not None:
                values[_team_id_for_label(label, team_by_label, "attacking_momentum")] = value
        point: dict[str, Any] = {
            "start_time_sec": _required_number(item.get("start_time_sec"), "momentum start_time_sec"),
            "end_time_sec": _required_number(item.get("end_time_sec"), "momentum end_time_sec"),
            "team_values_by_team_id": dict(sorted(values.items())),
        }
        dominant_label = item.get("dominant_team_label")
        if dominant_label in {"A", "B"}:
            point["dominant_team_id"] = _team_id_for_label(str(dominant_label), team_by_label, "attacking_momentum")
        for key in ("confidence", "positional_confidence", "event_confidence", "intensity"):
            value = _number_or_none(item.get(key))
            if value is not None:
                point[key] = value
        momentum_points.append(point)

    return {
        "possession": {
            "status": possession.get("status"),
            "windows": sorted(possession_windows, key=lambda row: (row["start_time_sec"], row["end_time_sec"])),
        }
        if possession_windows
        else {"status": "not_available", "reason": "source_local_windows_missing"},
        "attacking_momentum": {
            "status": momentum_status,
            "product_readiness": momentum_doc.get("product_readiness"),
            "signal_quality": momentum_doc.get("signal_quality"),
            "quality": momentum_doc.get("quality") or _record(momentum_doc.get("summary")).get("quality"),
            "points": sorted(momentum_points, key=lambda row: (row["start_time_sec"], row["end_time_sec"])),
        }
        if momentum_points
        else {"status": "not_available", "reason": "source_local_points_missing"},
        "pass_source_status": passes.get("status"),
    }


def _identity_coverage(package: dict[str, Any]) -> dict[str, Any]:
    stats = _record(package.get("reviewed_player_stats"))
    readiness = _record(package.get("reviewed_stats_readiness"))
    source = _record(stats.get("identity_coverage")) or _record(stats.get("global_coverage"))
    if not source:
        return {"status": "not_available", "reason": "reviewed_identity_coverage_missing"}
    result: dict[str, Any] = {"status": str(readiness.get("status") or "completed")}
    for source_key, destination in (
        ("coverage_unit", "coverage_unit"),
        ("confirmed_observations", "confirmed_observations"),
        ("reliable_player_observations_total", "reliable_observations"),
        ("unresolved_observations", "unresolved_observations"),
        ("conflicted_observations", "conflicted_observations"),
        ("ignored_observations", "ignored_observations"),
    ):
        value = source.get(source_key)
        if isinstance(value, str) and value:
            result[destination] = value
        elif _number_or_none(value) is not None:
            result[destination] = _number_or_none(value)
    return result


def _spatial(package: dict[str, Any]) -> dict[str, Any]:
    heatmaps = _record(package.get("reviewed_player_heatmaps"))
    pitch = _record(heatmaps.get("pitch_dimensions_m")) or _record(package.get("pitch_config"))
    result: dict[str, Any] = {
        "orientation": "unproven",
        "heatmaps": {"status": "not_available", "reason": "canonical_orientation_not_proven"},
        "team_shape": {"status": "not_available", "reason": "canonical_orientation_and_sample_weights_not_proven"},
    }
    width = _number_or_none(pitch.get("width_m"))
    length = _number_or_none(pitch.get("length_m"))
    if width is not None and length is not None:
        result["pitch_dimensions_m"] = {"width_m": width, "length_m": length}
    return result


def _metric_readiness(package: dict[str, Any], possession: dict[str, Any], passes: dict[str, Any]) -> dict[str, Any]:
    reviewed_readiness = _record(package.get("reviewed_stats_readiness"))
    team_stats = _record(package.get("team_stats"))
    return {
        "reviewed_identity": str(reviewed_readiness.get("status") or "not_available"),
        "team_movement": {
            "status": "available" if _list(team_stats.get("teams")) else "not_available",
            "source": team_stats.get("source"),
        },
        "player_movement": {"status": "available", "source": "reviewed_player_stats"},
        "possession": {
            "status": possession.get("status"),
            "reviewed_player_attribution": _record(reviewed_readiness.get("possession")).get("status"),
        },
        "passes": {
            "status": passes.get("status"),
            "reviewed_player_attribution": _record(reviewed_readiness.get("passes")).get("status"),
        },
        "spatial": "not_available",
        "team_shape": "not_available",
    }


def _timing(match: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    video = _record(match.get("video"))
    stats_timing = _record(stats.get("video_timing"))
    result: dict[str, Any] = {}
    for source in (stats_timing, video):
        for source_key, destination in (("duration_sec", "analyzed_duration_sec"), ("fps", "fps"), ("frame_count", "frame_count")):
            if destination not in result:
                value = _number_or_none(source.get(source_key))
                if value is not None:
                    result[destination] = value
    if "analyzed_duration_sec" not in result:
        raise AggregateInputsError("Published source does not expose analyzed duration")
    return result


def _analytics_status(package: dict[str, Any], feature: str, fallback: Any) -> str:
    readiness = _record(package.get("analytics_readiness"))
    feature_status = _record(readiness.get("features")).get(feature)
    if isinstance(feature_status, dict) and isinstance(feature_status.get("status"), str):
        return str(feature_status["status"])
    if isinstance(fallback, str) and fallback:
        return fallback
    return "not_available"


def _stable_count_map(source: dict[str, Any], team_by_label: dict[str, str], context: str) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for label, value in source.items():
        numeric = _number_or_none(value)
        if numeric is None:
            raise AggregateInputsError(f"{context} requires numeric counts")
        team_id = _team_id_for_label(str(label), team_by_label, context)
        if team_id in result:
            raise AggregateInputsError(f"{context} has more than one value for stable team_id {team_id!r}")
        result[team_id] = numeric
    return dict(sorted(result.items()))


def _team_id_for_label(label: str, team_by_label: dict[str, str], context: str) -> str:
    team_id = team_by_label.get(label)
    if not team_id:
        raise AggregateInputsError(f"{context} cannot map local team label {label!r} to a stable team_id")
    return team_id


def _required_id(value: Any, context: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise AggregateInputsError(f"{context} requires a stable ID")
    return result


def _required_label(value: Any, context: str) -> str:
    result = str(value or "").strip()
    if not result or result == "U":
        raise AggregateInputsError(f"{context} requires a source-local team label")
    return result


def _required_number(value: Any, context: str) -> int | float:
    result = _number_or_none(value)
    if result is None:
        raise AggregateInputsError(f"{context} requires a numeric value")
    return result


def _numeric_fields(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, int | float]:
    result = {}
    for key in keys:
        value = _number_or_none(source.get(key))
        if value is not None:
            result[key] = value
    return result


def _first_number(source: dict[str, Any], keys: tuple[str, ...]) -> int | float | None:
    for key in keys:
        value = _number_or_none(source.get(key))
        if value is not None:
            return value
    return None


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
