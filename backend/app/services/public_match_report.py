from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any

from app.config import MATCHES_DIR
from app.services.stabilization import _heatmap_quality, _safe_artifact_id, _write_player_heatmap_png


REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENT_PUBLIC_MATCHES_DIR = REPO_ROOT / "client" / "public" / "published" / "matches"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(value: Any, digits: int = 2) -> float:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return 0.0


def _nested(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def _team_display_map(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    match = package.get("match") if isinstance(package.get("match"), dict) else {}
    for index, team in enumerate(match.get("teams") or []):
        if not isinstance(team, dict):
            continue
        team_id = str(team.get("id") or f"team-{index + 1}")
        rows[team_id] = {
            "team_id": team_id,
            "team_name": str(team.get("name") or team_id),
            "display_color": team.get("color"),
        }

    team_config = package.get("team_config") if isinstance(package.get("team_config"), dict) else {}
    for team in team_config.get("teams") or []:
        if not isinstance(team, dict):
            continue
        team_label = str(team.get("team_label") or "")
        team_id = team.get("team_id")
        key = str(team_id or team_label)
        rows[key] = {
            "team_id": team_id,
            "team_label": team_label,
            "team_name": team.get("team_name") or f"Team {team_label}",
            "display_color": team.get("display_color") or team.get("detected_color_hex"),
        }
        if team_label:
            rows[team_label] = rows[key]
    return rows


def _possession_share(package: dict[str, Any], team_label: str) -> float | None:
    possession = package.get("possession_report") if isinstance(package.get("possession_report"), dict) else {}
    summary = possession.get("summary") if isinstance(possession.get("summary"), dict) else {}
    controlled = summary.get("team_controlled_frames") if isinstance(summary.get("team_controlled_frames"), dict) else {}
    values = [float(value) for value in controlled.values() if isinstance(value, (int, float))]
    total = sum(values)
    if total <= 0:
        return None
    return round(float(controlled.get(team_label) or 0.0) / total * 100.0, 1)


def _pass_counts(package: dict[str, Any], team_label: str) -> dict[str, int]:
    pass_doc = package.get("pass_candidates") if isinstance(package.get("pass_candidates"), dict) else {}
    candidates = pass_doc.get("candidates") if isinstance(pass_doc.get("candidates"), list) else []
    team_candidates = [
        item
        for item in candidates
        if isinstance(item, dict) and str(item.get("from_team_label") or "") == team_label
    ]
    return {
        "pass_candidates": len(team_candidates),
        "same_team_pass_candidates": sum(1 for item in team_candidates if item.get("pass_type") == "same_team_pass"),
        "turnover_or_interception_candidates": sum(
            1 for item in team_candidates if item.get("pass_type") == "turnover_or_interception"
        ),
        "progressive_pass_candidates": sum(1 for item in team_candidates if item.get("is_progressive") is True),
        "accepted_passes": sum(
            1
            for item in team_candidates
            if item.get("final_stat_eligible") is True or item.get("review_status") == "accepted"
        ),
    }


def _public_possession_timeline(package: dict[str, Any]) -> list[dict[str, Any]]:
    possession = package.get("possession_report") if isinstance(package.get("possession_report"), dict) else {}
    timeline = possession.get("possession_timeline") if isinstance(possession.get("possession_timeline"), list) else []
    rows = []
    cumulative_team_a_frames = 0
    cumulative_team_b_frames = 0
    for item in timeline:
        if not isinstance(item, dict):
            continue
        team_frames = item.get("team_controlled_frames") if isinstance(item.get("team_controlled_frames"), dict) else {}
        start_time_sec = _round(item.get("start_time_sec"), 2)
        end_time_sec = _round(item.get("end_time_sec"), 2)
        start_minute = int(start_time_sec // 60)
        end_minute = max(start_minute + 1, int(ceil(end_time_sec / 60)))
        team_a_frames = int(team_frames.get("A") or 0)
        team_b_frames = int(team_frames.get("B") or 0)
        known_team_frames = team_a_frames + team_b_frames
        team_a_percent = (team_a_frames / known_team_frames * 100.0) if known_team_frames > 0 else 0.0
        team_b_percent = 100.0 - team_a_percent if known_team_frames > 0 else 0.0
        cumulative_team_a_frames += team_a_frames
        cumulative_team_b_frames += team_b_frames
        cumulative_known_team_frames = cumulative_team_a_frames + cumulative_team_b_frames
        cumulative_team_a_percent = (
            cumulative_team_a_frames / cumulative_known_team_frames * 100.0
            if cumulative_known_team_frames > 0
            else 0.0
        )
        cumulative_team_b_percent = (
            100.0 - cumulative_team_a_percent if cumulative_known_team_frames > 0 else 0.0
        )
        rows.append(
            {
                "index": int(item.get("index") or len(rows)),
                "minute": start_minute + 1,
                "label": f"{start_minute}-{end_minute}m",
                "start_time_sec": start_time_sec,
                "end_time_sec": end_time_sec,
                "team_a_frames": team_a_frames,
                "team_b_frames": team_b_frames,
                "known_team_frames": known_team_frames,
                "team_a_percent": _round(team_a_percent, 1),
                "team_b_percent": _round(team_b_percent, 1),
                "cumulative_team_a_frames": cumulative_team_a_frames,
                "cumulative_team_b_frames": cumulative_team_b_frames,
                "cumulative_known_team_frames": cumulative_known_team_frames,
                "cumulative_team_a_percent": _round(cumulative_team_a_percent, 1),
                "cumulative_team_b_percent": _round(cumulative_team_b_percent, 1),
                "free_frames": int(item.get("free_frames") or 0),
                "unknown_frames": int(item.get("unknown_frames") or 0),
                "team_a_share": _round(item.get("team_a_share"), 4),
                "team_b_share": _round(item.get("team_b_share"), 4),
                "controlled_coverage": _round(item.get("controlled_coverage"), 4),
                "controlled_coverage_percent": _round(float(item.get("controlled_coverage") or 0.0) * 100.0, 1),
                "unknown_coverage": _round(item.get("unknown_coverage"), 4),
            }
        )
    return rows


def _resolved_team_names(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resolved = package.get("resolved_player_stats") if isinstance(package.get("resolved_player_stats"), dict) else {}
    names: dict[str, dict[str, Any]] = {}
    for player in resolved.get("players") or []:
        if not isinstance(player, dict):
            continue
        team_label = str(player.get("team_label") or "")
        if not team_label:
            continue
        current = names.setdefault(team_label, {"counts": {}, "ids": {}, "colors": {}})
        team_name = player.get("team_name")
        if team_name:
            counts = current["counts"]
            counts[str(team_name)] = counts.get(str(team_name), 0) + 1
        team_id = player.get("team_id")
        if team_id:
            ids = current["ids"]
            ids[str(team_id)] = ids.get(str(team_id), 0) + 1
    result: dict[str, dict[str, Any]] = {}
    for team_label, row in names.items():
        counts = row["counts"]
        ids = row["ids"]
        result[team_label] = {
            "team_name": max(counts, key=counts.get) if counts else None,
            "team_id": max(ids, key=ids.get) if ids else None,
        }
    return result


def _public_teams(package: dict[str, Any]) -> list[dict[str, Any]]:
    team_stats = package.get("team_stats") if isinstance(package.get("team_stats"), dict) else {}
    team_rows = team_stats.get("teams") if isinstance(team_stats.get("teams"), list) else []
    match = package.get("match") if isinstance(package.get("match"), dict) else {}
    video = match.get("video") if isinstance(match.get("video"), dict) else {}
    match_duration_sec = _round(video.get("duration_sec"))
    display = _team_display_map(package)
    resolved_names = _resolved_team_names(package)
    rows = []
    for team in team_rows:
        if not isinstance(team, dict):
            continue
        team_label = str(team.get("team_label") or "")
        team_id = team.get("team_id")
        display_row = display.get(str(team_id or "")) or display.get(team_label) or {}
        resolved_team = resolved_names.get(team_label) or {}
        rows.append(
            {
                "team_label": team_label,
                "team_id": resolved_team.get("team_id") or team_id,
                "team_name": resolved_team.get("team_name")
                or display_row.get("team_name")
                or team.get("team_name")
                or f"Team {team_label}",
                "display_color": display_row.get("display_color") or team.get("display_color"),
                "playing_time_sec": match_duration_sec,
                "total_distance_m": _round(team.get("total_distance_m")),
                "high_intensity_distance_m": _round(team.get("high_intensity_distance_m")),
                "sprint_count": int(team.get("sprint_count") or 0),
                "avg_speed_kmh": _round(team.get("avg_speed_kmh") or team.get("average_speed_kmh")),
                "peak_speed_kmh": _round(team.get("peak_sustained_speed_kmh") or team.get("top_speed_kmh")),
                "possession_share_percent": _possession_share(package, team_label),
                **_pass_counts(package, team_label),
            }
        )
    return rows


def _public_match(package: dict[str, Any]) -> dict[str, Any]:
    match = package.get("match") if isinstance(package.get("match"), dict) else {}
    video = match.get("video") if isinstance(match.get("video"), dict) else {}
    return {
        "id": str(match.get("id") or ""),
        "title": str(match.get("title") or "Untitled match"),
        "match_date": match.get("match_date"),
        "season": match.get("season"),
        "venue": match.get("venue"),
        "format": match.get("format"),
        "duration_sec": _round(video.get("duration_sec")),
    }


def _load_stable_players(source_match_dir: Path | None) -> dict[str, dict[str, Any]]:
    if not source_match_dir:
        return {}
    path = source_match_dir / "stable_players.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    players = doc.get("players") if isinstance(doc.get("players"), list) else []
    rows: dict[str, dict[str, Any]] = {}
    for player in players:
        if not isinstance(player, dict):
            continue
        stable_subject_id = str(player.get("stable_subject_id") or "")
        stable_player_id = str(player.get("stable_player_id") or "")
        if stable_subject_id:
            rows[stable_subject_id] = player
        if stable_player_id:
            rows[stable_player_id] = player
    return rows


def _stint_window(stable_player: dict[str, Any], stint_id: str | None) -> tuple[int | None, int | None]:
    if not stint_id:
        return None, None
    for stint in stable_player.get("stints") or []:
        if not isinstance(stint, dict) or str(stint.get("stint_id") or "") != str(stint_id):
            continue
        start = stint.get("start_frame")
        end = stint.get("end_frame")
        return (
            int(start) if isinstance(start, (int, float)) else None,
            int(end) if isinstance(end, (int, float)) else None,
        )
    return None, None


def _real_player_heatmap_rows(player: dict[str, Any], stable_players: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in player.get("source_stable_slots") or []:
        if not isinstance(source, dict):
            continue
        stable_player = stable_players.get(str(source.get("stable_subject_id") or "")) or stable_players.get(
            str(source.get("stable_player_id") or "")
        )
        if not stable_player:
            continue
        start_frame, end_frame = _stint_window(stable_player, source.get("stint_id"))
        for position in stable_player.get("trajectory_m") or []:
            if not isinstance(position, dict):
                continue
            frame = position.get("frame")
            if isinstance(frame, (int, float)):
                frame_int = int(frame)
                if start_frame is not None and frame_int < start_frame:
                    continue
                if end_frame is not None and frame_int > end_frame:
                    continue
            source_status = str(position.get("source") or position.get("status") or "detected")
            if source_status in {"missing", "ambiguous", "inactive"}:
                continue
            pitch_m = position.get("pitch_m")
            if not pitch_m or len(pitch_m) < 2:
                continue
            rows.append({"pitch_m": pitch_m, "source": source_status})
    return rows


def _write_public_player_heatmap(
    player: dict[str, Any],
    stable_players: dict[str, dict[str, Any]],
    *,
    heatmap_dir: Path,
    public_heatmap_base: str,
    pitch_width_m: float,
    pitch_length_m: float,
) -> dict[str, Any]:
    rows = _real_player_heatmap_rows(player, stable_players)
    player_id = str(player.get("player_id") or player.get("player_name") or "player")
    filename = f"player_{_safe_artifact_id(player_id)}.png"
    heatmap_dir.mkdir(parents=True, exist_ok=True)
    _write_player_heatmap_png(
        heatmap_dir / filename,
        rows,
        pitch_width_m=pitch_width_m,
        pitch_length_m=pitch_length_m,
        width_px=360,
        length_px=720,
    )
    detected_samples = sum(1 for row in rows if row.get("source") == "detected")
    frames = _nested(player, "frames")
    quality = _heatmap_quality(
        samples=len(rows),
        detected_samples=detected_samples,
        detected_frames=int(frames.get("detected_frames") or 0),
        ambiguous_frames=int(frames.get("ambiguous_frames") or 0),
    )
    return {
        "path": f"{public_heatmap_base}/{filename}",
        "samples": len(rows),
        "detected_samples": detected_samples,
        "quality": quality,
    }


def _public_players(
    package: dict[str, Any],
    *,
    source_match_dir: Path | None,
    heatmap_dir: Path,
    public_heatmap_base: str,
) -> list[dict[str, Any]]:
    resolved = package.get("resolved_player_stats") if isinstance(package.get("resolved_player_stats"), dict) else {}
    stable_doc = package.get("stable_players") if isinstance(package.get("stable_players"), dict) else {}
    pitch = stable_doc.get("pitch_dimensions_m") if isinstance(stable_doc.get("pitch_dimensions_m"), dict) else {}
    pitch_width_m = float(pitch.get("width_m") or 30.0)
    pitch_length_m = float(pitch.get("length_m") or 47.4)
    stable_players = _load_stable_players(source_match_dir)
    rows = []
    for player in resolved.get("players") or []:
        if not isinstance(player, dict):
            continue
        heatmap = _write_public_player_heatmap(
            player,
            stable_players,
            heatmap_dir=heatmap_dir,
            public_heatmap_base=public_heatmap_base,
            pitch_width_m=pitch_width_m,
            pitch_length_m=pitch_length_m,
        )
        time = _nested(player, "time")
        distance = _nested(player, "distance")
        speed = _nested(player, "speed")
        intensity = _nested(player, "intensity")
        rows.append(
            {
                "player_id": player.get("player_id"),
                "player_name": player.get("player_name"),
                "player_number": player.get("player_number"),
                "player_role": player.get("player_role"),
                "team_id": player.get("team_id"),
                "team_name": player.get("team_name"),
                "team_label": player.get("team_label"),
                "playing_time_sec": _round(time.get("playing_time_sec")),
                "detected_time_sec": _round(time.get("detected_time_sec")),
                "total_distance_m": _round(distance.get("total_distance_m")),
                "avg_speed_kmh": _round(speed.get("avg_speed_kmh")),
                "peak_speed_kmh": _round(speed.get("peak_sustained_speed_kmh") or speed.get("top_speed_kmh")),
                "high_intensity_distance_m": _round(intensity.get("high_intensity_distance_m")),
                "sprint_count": int(intensity.get("sprint_count") or 0),
                "heatmap": heatmap,
            }
        )
    return sorted(rows, key=lambda item: str(item.get("player_name") or item.get("player_id") or ""))


def build_public_match_report(
    package: dict[str, Any],
    *,
    published_id: str,
    source_match_dir: Path | None,
    heatmap_dir: Path,
    public_heatmap_base: str,
) -> dict[str, Any]:
    possession = package.get("possession_report") if isinstance(package.get("possession_report"), dict) else {}
    possession_summary = possession.get("summary") if isinstance(possession.get("summary"), dict) else {}
    pass_doc = package.get("pass_candidates") if isinstance(package.get("pass_candidates"), dict) else {}
    pass_summary = pass_doc.get("summary") if isinstance(pass_doc.get("summary"), dict) else {}
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "id": published_id,
        "source_match_id": _public_match(package)["id"],
        "report_type": "public_match_report",
        "stats_semantics": {
            "tracking": "confirmed_real_players_only",
            "ball": "experimental_candidates",
            "technical_debug": "excluded",
        },
        "match": _public_match(package),
        "teams": _public_teams(package),
        "players": _public_players(
            package,
            source_match_dir=source_match_dir,
            heatmap_dir=heatmap_dir,
            public_heatmap_base=public_heatmap_base,
        ),
        "ball": {
            "known_possession_coverage": _round(possession_summary.get("known_possession_coverage"), 4),
            "controlled_coverage": _round(possession_summary.get("controlled_coverage"), 4),
            "pass_candidates": int(pass_summary.get("pass_candidates") or 0),
            "same_team_pass_candidates": int(pass_summary.get("same_team_pass_candidates") or 0),
            "progressive_pass_candidates": int(pass_summary.get("progressive_pass_candidates") or 0),
            "accepted_passes": int(pass_summary.get("final_stat_passes") or 0),
            "possession_timeline": _public_possession_timeline(package),
        },
    }


def write_public_match_report_bundle(
    package: dict[str, Any],
    *,
    target_dir: Path,
    source_match_dir: Path | None = None,
    mirror_dir: Path | None = None,
) -> dict[str, Any]:
    match = package.get("match") if isinstance(package.get("match"), dict) else {}
    source_match_id = str(match.get("id") or "unknown")
    published_id = f"published-{source_match_id}"
    public_dir = mirror_dir or CLIENT_PUBLIC_MATCHES_DIR / published_id
    if public_dir.exists():
        shutil.rmtree(public_dir)
    public_heatmap_dir = public_dir / "heatmaps"
    public_heatmap_base = f"published/matches/{published_id}/heatmaps"
    report = build_public_match_report(
        package,
        published_id=published_id,
        source_match_dir=source_match_dir or MATCHES_DIR / source_match_id,
        heatmap_dir=public_heatmap_dir,
        public_heatmap_base=public_heatmap_base,
    )
    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "public_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    target_public_dir = target_dir / "public"
    if target_public_dir.exists():
        shutil.rmtree(target_public_dir)
    shutil.copytree(public_dir, target_public_dir)
    (target_dir / "public_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return report
