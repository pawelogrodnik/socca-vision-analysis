from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.public_match_report import build_public_match_report
from app.services.team_shape import ensure_team_shape_artifact_fresh


TECHNICAL_PLAYER_NAME = re.compile(r"^[ABU](?:\d+\??|\?)$", re.IGNORECASE)
REPORT_INPUTS = {
    "pitch_config": "pitch_config.json",
    "team_config": "team_config.json",
    "team_stats": "team_stats.json",
    "stable_players": "stable_players.json",
    "possession_report": "possession_report.json",
    "pass_candidates": "pass_candidates.json",
    "attacking_momentum": "attacking_momentum.json",
}
REVIEWED_PACKAGE_INPUTS = {
    "reviewed_player_stats": "reviewed_player_stats.json",
    "reviewed_player_heatmaps": "reviewed_player_heatmaps.json",
    "reviewed_stats_readiness": "reviewed_stats_readiness.json",
    "reviewed_output_manifest": "reviewed_output_manifest.json",
}


def build_reviewed_match_report(match_path: Path) -> dict[str, Any]:
    match = _load_required(match_path / "match.json")
    package: dict[str, Any] = {"match": match}
    for key, filename in {**REPORT_INPUTS, **REVIEWED_PACKAGE_INPUTS}.items():
        value = _load_optional(match_path / filename)
        if value is not None:
            package[key] = value
    team_shape = ensure_team_shape_artifact_fresh(match_path)
    if team_shape is not None:
        package["team_shape"] = team_shape
    apply_reviewed_identity_to_report_package(package, required=True)

    stats_digest = str(package["reviewed_player_stats"].get("source_snapshot_digest") or "")
    report = build_public_match_report(
        package,
        published_id=str(match.get("id") or match_path.name),
        source_match_dir=None,
        heatmap_dir=None,
        public_heatmap_base="",
    )
    report["report_type"] = "reviewed_match_report"
    report["reviewed_identity_digest"] = stats_digest
    report["identity_coverage"] = package["reviewed_player_stats"].get(
        "identity_coverage"
    ) or package["reviewed_stats_readiness"].get("identity_coverage")
    report["identity_coverage_readiness"] = package[
        "reviewed_stats_readiness"
    ].get("coverage_readiness")
    return report


def apply_reviewed_identity_to_report_package(
    package: dict[str, Any],
    *,
    required: bool = False,
) -> dict[str, Any]:
    status = reviewed_identity_package_status(package)
    if not status["present"]:
        if required:
            raise ValueError("Reviewed identity output is not available")
        return package
    if not status["ready"]:
        raise ValueError(str(status["detail"]))

    legacy_resolved = package.get("resolved_player_stats")
    if isinstance(legacy_resolved, dict):
        package["legacy_resolved_player_stats"] = legacy_resolved
    package["resolved_player_stats"] = _reviewed_resolved_player_stats(package)
    package["identity_report_source"] = "reviewed_identity"
    package["reviewed_identity_digest"] = status["digest"]
    return package


def reviewed_identity_package_status(package: dict[str, Any]) -> dict[str, Any]:
    documents = {
        key: package.get(key) if isinstance(package.get(key), dict) else None
        for key in REVIEWED_PACKAGE_INPUTS
    }
    present = any(value is not None for value in documents.values())
    if not present:
        return {"present": False, "ready": False, "digest": None, "detail": None, "missing": []}

    missing = [key for key, value in documents.items() if value is None]
    if missing:
        return {
            "present": True,
            "ready": False,
            "digest": None,
            "detail": f"Reviewed identity output is incomplete. Missing: {', '.join(missing)}",
            "missing": missing,
        }

    stats = documents["reviewed_player_stats"] or {}
    heatmaps = documents["reviewed_player_heatmaps"] or {}
    readiness = documents["reviewed_stats_readiness"] or {}
    manifest = documents["reviewed_output_manifest"] or {}
    manifest_stats = manifest.get("stats") if isinstance(manifest.get("stats"), dict) else {}
    manifest_identity = (
        manifest.get("reviewed_identity")
        if isinstance(manifest.get("reviewed_identity"), dict)
        else {}
    )
    digests = {
        str(stats.get("source_snapshot_digest") or ""),
        str(heatmaps.get("source_snapshot_digest") or ""),
        str(readiness.get("source_snapshot_digest") or ""),
        str(manifest_stats.get("source_snapshot_digest") or ""),
        str(manifest_identity.get("digest") or ""),
    }
    if "" in digests or len(digests) != 1:
        return {
            "present": True,
            "ready": False,
            "digest": None,
            "detail": "Reviewed identity artifacts are not from the same identity snapshot",
            "missing": [],
        }
    digest = next(iter(digests))
    if (
        readiness.get("status") != "completed"
        or manifest_stats.get("status") != "completed"
        or manifest_identity.get("status") != "fresh"
        or manifest.get("stale") is True
    ):
        return {
            "present": True,
            "ready": False,
            "digest": digest,
            "detail": "Reviewed identity output is not fresh and completed",
            "missing": [],
        }
    return {"present": True, "ready": True, "digest": digest, "detail": None, "missing": []}


def _reviewed_resolved_player_stats(package: dict[str, Any]) -> dict[str, Any]:
    match = package.get("match") if isinstance(package.get("match"), dict) else {}
    stats = (
        package.get("reviewed_player_stats")
        if isinstance(package.get("reviewed_player_stats"), dict)
        else {}
    )
    roster_by_player = _roster_by_player(match)
    team_by_label = _team_by_label(package.get("team_config"))
    resolved_players = []
    for row in stats.get("players") or []:
        if not isinstance(row, dict) or not _is_named_player(row):
            continue
        player_id = str(row.get("player_id") or "")
        roster = roster_by_player.get(player_id, {})
        team_label = str(row.get("team_label") or "")
        team = team_by_label.get(team_label, {})
        detected_time_sec = _number(row.get("detected_time_sec"))
        reviewed_speed = row.get("speed") if isinstance(row.get("speed"), dict) else {}
        reviewed_intensity = (
            row.get("intensity") if isinstance(row.get("intensity"), dict) else {}
        )
        resolved_players.append(
            {
                "player_id": player_id,
                "player_name": str(row.get("player_name") or roster.get("name") or player_id),
                "player_number": _jersey_number(row.get("roster_number") or roster.get("number")),
                "player_role": roster.get("role"),
                "team_label": team_label,
                "team_id": team.get("team_id"),
                "team_name": team.get("team_name") or f"Team {team_label}",
                "time": {
                    "playing_time_sec": detected_time_sec,
                    "detected_time_sec": detected_time_sec,
                    "inferred_playing_time_sec": 0.0,
                    "ambiguous_time_sec": 0.0,
                    "missing_time_sec": 0.0,
                },
                "distance": {"total_distance_m": _number(row.get("total_distance_m"))},
                "speed": {
                    "avg_speed_mps": _number(reviewed_speed.get("avg_speed_mps")),
                    "avg_speed_kmh": _number(reviewed_speed.get("avg_speed_kmh")),
                    "observed_avg_speed_mps": _number(
                        reviewed_speed.get("observed_avg_speed_mps")
                    ),
                    "peak_sustained_speed_mps": _number(
                        reviewed_speed.get("peak_sustained_speed_mps")
                    ),
                    "peak_sustained_speed_kmh": _number(
                        reviewed_speed.get("peak_sustained_speed_kmh")
                    ),
                    "top_speed_mps": _number(reviewed_speed.get("top_speed_mps")),
                    "top_speed_kmh": _number(reviewed_speed.get("top_speed_kmh")),
                    "raw_segment_top_speed_mps": _number(
                        reviewed_speed.get("raw_segment_top_speed_mps")
                    ),
                    "raw_segment_top_speed_kmh": _number(
                        reviewed_speed.get("raw_segment_top_speed_kmh")
                    ),
                    "quality": reviewed_speed.get("speed_quality") or "not_available",
                },
                "intensity": {
                    "high_intensity_distance_m": _number(
                        reviewed_intensity.get("high_intensity_distance_m")
                    ),
                    "sprint_count": int(reviewed_intensity.get("sprint_count") or 0),
                    "sprint_time_sec": _number(reviewed_intensity.get("sprint_time_sec")),
                    "sprint_distance_m": _number(
                        reviewed_intensity.get("sprint_distance_m")
                    ),
                    "max_sprint_speed_kmh": _number(
                        reviewed_intensity.get("max_sprint_speed_kmh")
                    ),
                },
                "playing_time_method": "reviewed_confirmed_observations",
                "calculation_method": "reviewed_effective_observations",
                "quality_flags": [],
                "frames": {
                    "detected_frames": int(row.get("detected_frames") or 0),
                    "ambiguous_frames": 0,
                },
            }
        )

    return {
        "schema_version": "reviewed-preview-1.0.0",
        "calculation_method": "reviewed_effective_observations",
        "players": resolved_players,
    }


def _is_named_player(row: dict[str, Any]) -> bool:
    player_id = str(row.get("player_id") or "").strip()
    player_name = str(row.get("player_name") or "").strip()
    if not player_id or not player_name or player_name == player_id:
        return False
    return TECHNICAL_PLAYER_NAME.fullmatch(player_name) is None


def _roster_by_player(match: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for team in match.get("teams") or []:
        if not isinstance(team, dict):
            continue
        for player in team.get("players") or []:
            if isinstance(player, dict) and player.get("id"):
                rows[str(player["id"])] = player
    return rows


def _team_by_label(team_config: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(team_config, dict):
        return {}
    return {
        str(row.get("team_label") or ""): row
        for row in team_config.get("teams") or []
        if isinstance(row, dict) and row.get("team_label")
    }


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _jersey_number(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or normalized.lower() in {"player", "goalkeeper", "field player"}:
        return None
    return normalized


def _load_required(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path.name)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _load_optional(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _load_required(path)
