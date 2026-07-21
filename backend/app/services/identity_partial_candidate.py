from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.services.global_identity import calculate_movement_stats
from app.services.identity_promotion_safety import canonical_document_digest


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_partial_candidate_apply"
ALGORITHM_VERSION = "0.1.0"


def build_partial_candidate_artifacts(
    promotion_plan: dict[str, Any],
    remediation_plan: dict[str, Any],
    match_doc: dict[str, Any],
    *,
    pitch_config_doc: dict[str, Any] | None = None,
    production_stats_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build candidate-only identity, stats and heatmaps from safe exact observations."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    fps = _match_fps(match_doc)
    rows = [
        _candidate_row(row)
        for row in remediation_plan.get("eligible_observations") or []
        if isinstance(row, dict) and row.get("player_id") and row.get("frame") is not None
    ]
    hard_conflicts = _hard_conflicts(rows)
    blocked = bool(remediation_plan.get("errors") or hard_conflicts)
    partial = bool(
        remediation_plan.get("excluded_fragments")
        or remediation_plan.get("unresolved_fragments")
        or promotion_plan.get("unresolved_subjects")
        or promotion_plan.get("structural_subjects")
    )
    candidate_status = "blocked" if blocked else "partial_candidate" if partial else "complete_candidate"
    assignments = _build_assignments(rows)
    timeline = _build_timeline(rows, fps)
    stats = _build_stats(rows, match_doc, fps)
    heatmaps = _build_heatmaps(rows, match_doc, pitch_config_doc or {})
    pitch_width, pitch_length = _pitch_dimensions(match_doc, pitch_config_doc or {})
    diff = _build_diff(
        stats,
        production_stats_doc or {},
        remediation_plan,
        promotion_plan,
    )
    source = {
        "promotion_plan_digest": canonical_document_digest(promotion_plan),
        "remediation_plan_digest": canonical_document_digest(remediation_plan),
        "match_digest": canonical_document_digest(match_doc),
        "pitch_config_digest": canonical_document_digest(pitch_config_doc or {}),
    }
    shared = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "candidate_status": candidate_status,
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source": source,
    }
    diff = {**shared, **diff}
    assignments_doc = {
        **shared,
        "assignment_scope": "exact_candidate_fragment",
        "assignments": assignments,
        "summary": {
            "assignments": len(assignments),
            "players": len({row["player_id"] for row in assignments}),
            "observations": len(rows),
        },
    }
    timeline_doc = {
        **shared,
        "timeline_semantics": {
            "detected": "full identity contribution",
            "predicted_occluded": "continuity only; no observed distance or heatmap",
            "unresolved_missing_excluded": "no player identity contribution",
        },
        "players": timeline,
        "summary": {
            "players": len(timeline),
            "observations": len(rows),
            "detected_observations": sum(row["status"] == "detected" for row in rows),
        },
    }
    stats_doc = {
        **shared,
        "calculation_method": "exact_partial_candidate_fragments",
        "players": stats,
        "summary": {
            "players": len(stats),
            "playing_time_sec": round(sum(row["time"]["playing_time_sec"] for row in stats), 3),
            "distance_m": round(sum(row["distance"]["total_distance_m"] for row in stats), 2),
        },
    }
    heatmaps_doc = {
        **shared,
        "method": "candidate_detected_pitch_bins",
        "pitch_dimensions_m": {
            "width_m": pitch_width,
            "length_m": pitch_length,
        },
        "heatmaps": heatmaps,
        "summary": {
            "players": len(heatmaps),
            "samples": sum(row["samples"] for row in heatmaps),
        },
    }
    manifest = {
        **shared,
        "status": candidate_status,
        "safety": {
            "writes_candidate_only": True,
            "mutates_production_identity": False,
            "public_package_uses_candidate": False,
            "hard_conflicts": len(hard_conflicts),
        },
        "coverage": {
            "eligible_observations": len(rows),
            "excluded_fragments": len(remediation_plan.get("excluded_fragments") or []),
            "unresolved_fragments": len(remediation_plan.get("unresolved_fragments") or []),
            "unresolved_subjects": len(promotion_plan.get("unresolved_subjects") or []),
        },
        "artifacts": {
            "player_identity_assignments": "player_identity_assignments_candidate_v2.json",
            "resolved_player_timeline": "resolved_player_timeline_candidate_v2.json",
            "resolved_player_stats": "resolved_player_stats_candidate_v2.json",
            "player_heatmaps": "player_heatmaps_candidate_v2.json",
            "candidate_vs_production_diff": "identity_candidate_vs_production_diff.json",
        },
        "hard_conflicts": hard_conflicts,
    }
    return {
        "player_identity_assignments_candidate_v2.json": assignments_doc,
        "resolved_player_timeline_candidate_v2.json": timeline_doc,
        "resolved_player_stats_candidate_v2.json": stats_doc,
        "player_heatmaps_candidate_v2.json": heatmaps_doc,
        "identity_candidate_vs_production_diff.json": diff,
        "identity_candidate_apply_manifest.json": manifest,
    }


def _candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "detected")
    continuity_status = "predicted" if status == "occluded" else status
    return {
        **row,
        "frame": int(row["frame"]),
        "time_sec": round(float(row.get("time_sec") or 0.0), 6),
        "status": status,
        "source": continuity_status,
        "eligible_for_distance": bool(row.get("eligible_for_distance")) and status == "detected",
        "eligible_for_heatmap": bool(row.get("eligible_for_heatmap")) and status == "detected",
    }


def _hard_conflicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    by_source: dict[tuple[int, str], set[str]] = defaultdict(set)
    by_player_frame: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[(row["frame"], str(row.get("tracklet_id") or ""))].add(str(row["player_id"]))
        by_player_frame[(str(row["player_id"]), row["frame"])].append(row)
    for (frame, tracklet_id), players in sorted(by_source.items()):
        if len(players) > 1:
            conflicts.append({
                "code": "candidate_same_source_multiple_players",
                "frame": frame,
                "tracklet_id": tracklet_id,
                "player_ids": sorted(players),
            })
    for (player_id, frame), observations in sorted(by_player_frame.items()):
        if len(observations) > 1:
            conflicts.append({
                "code": "candidate_parallel_player_observations",
                "frame": frame,
                "player_id": player_id,
                "candidate_subject_ids": sorted({str(row.get("candidate_subject_id") or "") for row in observations}),
            })
    return conflicts


def _build_assignments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assignments: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["player_id"]), str(row.get("candidate_subject_id") or ""), str(row.get("tracklet_id") or ""))].append(row)
    for (player_id, subject_id, tracklet_id), observations in sorted(grouped.items()):
        for sequence in _contiguous_sequences(observations):
            start = sequence[0]["frame"]
            end = sequence[-1]["frame"]
            assignments.append({
                "assignment_key": _assignment_key(player_id, subject_id, tracklet_id, start, end),
                "player_id": player_id,
                "candidate_subject_id": subject_id,
                "tracklet_id": tracklet_id,
                "start_frame": start,
                "end_frame": end,
                "observation_count": len(sequence),
                "source_review_card_keys": sorted({str(row.get("review_card_key") or "") for row in sequence}),
            })
    return assignments


def _build_timeline(rows: list[dict[str, Any]], fps: float) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["player_id"])].append(row)
    result: list[dict[str, Any]] = []
    for player_id, observations in sorted(grouped.items()):
        observations.sort(key=lambda row: (row["frame"], str(row.get("candidate_subject_id") or "")))
        result.append({
            "player_id": player_id,
            "start_frame": observations[0]["frame"],
            "end_frame": observations[-1]["frame"],
            "playing_time_sec": round(len({row["frame"] for row in observations}) / fps, 3),
            "candidate_subject_ids": sorted({str(row.get("candidate_subject_id") or "") for row in observations}),
            "tracklet_ids": sorted({str(row.get("tracklet_id") or "") for row in observations}),
            "observations": observations,
        })
    return result


def _build_stats(
    rows: list[dict[str, Any]], match_doc: dict[str, Any], fps: float
) -> list[dict[str, Any]]:
    roster = _roster(match_doc)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["player_id"])].append(row)
    result: list[dict[str, Any]] = []
    for player_id, observations in sorted(grouped.items()):
        fragments = _movement_fragments(observations)
        fragment_stats = [calculate_movement_stats(fragment, fps) for fragment in fragments]
        unique_frames = len({row["frame"] for row in observations})
        detected_frames = len({row["frame"] for row in observations if row["status"] == "detected"})
        playing_time = unique_frames / fps
        distance = sum(float(item["total_distance_m"]) for item in fragment_stats)
        observed_distance = sum(float(item["observed_distance_m"]) for item in fragment_stats)
        estimated_distance = sum(float(item["estimated_gap_distance_m"]) for item in fragment_stats)
        player = roster.get(player_id, {})
        result.append({
            "player_id": player_id,
            "player_name": player.get("name") or player_id,
            "team_label": player.get("team_label"),
            "team_id": player.get("team_id"),
            "player_role": player.get("role") or player.get("position") or "player",
            "time": {
                "playing_time_sec": round(playing_time, 3),
                "detected_time_sec": round(detected_frames / fps, 3),
            },
            "distance": {
                "observed_distance_m": round(observed_distance, 2),
                "estimated_short_gap_distance_m": round(estimated_distance, 2),
                "total_distance_m": round(distance, 2),
            },
            "speed": {
                "avg_speed_mps": round(distance / playing_time, 3) if playing_time else 0.0,
                "avg_speed_kmh": round(distance / playing_time * 3.6, 2) if playing_time else 0.0,
                "peak_sustained_speed_kmh": max((float(item["peak_sustained_speed_kmh"]) for item in fragment_stats), default=0.0),
            },
            "frames": {
                "active_frames": unique_frames,
                "detected_frames": detected_frames,
                "distance_eligible_frames": len({row["frame"] for row in observations if row["eligible_for_distance"]}),
                "heatmap_eligible_frames": len({row["frame"] for row in observations if row["eligible_for_heatmap"]}),
            },
            "candidate_subject_ids": sorted({str(row.get("candidate_subject_id") or "") for row in observations}),
            "fragment_count": len(fragments),
            "quality_flags": ["partial_identity_coverage"],
        })
    return result


def _movement_fragments(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("candidate_subject_id") or ""), str(row.get("tracklet_id") or ""))].append(row)
    fragments: list[list[dict[str, Any]]] = []
    for observations in grouped.values():
        for sequence in _contiguous_sequences(observations):
            fragments.append([
                {
                    **row,
                    "source": "detected" if row["eligible_for_distance"] else "predicted",
                }
                for row in sequence
            ])
    return fragments


def _build_heatmaps(
    rows: list[dict[str, Any]],
    match_doc: dict[str, Any],
    pitch_config_doc: dict[str, Any],
) -> list[dict[str, Any]]:
    width, length = _pitch_dimensions(match_doc, pitch_config_doc)
    columns, row_count = 20, 32
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["eligible_for_heatmap"] and _valid_point(row.get("pitch_m")):
            grouped[str(row["player_id"])].append(row)
    result: list[dict[str, Any]] = []
    for player_id, observations in sorted(grouped.items()):
        bins: dict[tuple[int, int], int] = defaultdict(int)
        seen_frames: set[int] = set()
        for row in sorted(observations, key=lambda item: item["frame"]):
            if row["frame"] in seen_frames:
                continue
            seen_frames.add(row["frame"])
            x, y = float(row["pitch_m"][0]), float(row["pitch_m"][1])
            col = min(columns - 1, max(0, int(x / max(width, 0.001) * columns)))
            grid_row = min(row_count - 1, max(0, int(y / max(length, 0.001) * row_count)))
            bins[(col, grid_row)] += 1
        result.append({
            "player_id": player_id,
            "samples": len(seen_frames),
            "quality": "candidate_partial",
            "grid": {"columns": columns, "rows": row_count},
            "bins": [
                {"x": col, "y": grid_row, "count": count}
                for (col, grid_row), count in sorted(bins.items())
            ],
        })
    return result


def _build_diff(
    candidate_stats: list[dict[str, Any]],
    production_stats_doc: dict[str, Any],
    remediation_plan: dict[str, Any],
    promotion_plan: dict[str, Any],
) -> dict[str, Any]:
    production = {
        str(row.get("player_id") or ""): row
        for row in production_stats_doc.get("players") or []
        if isinstance(row, dict) and row.get("player_id")
    }
    players: list[dict[str, Any]] = []
    for candidate in candidate_stats:
        player_id = str(candidate["player_id"])
        previous = production.get(player_id, {})
        previous_time = float(((previous.get("time") or {}).get("playing_time_sec") or 0.0))
        previous_distance = float(((previous.get("distance") or {}).get("total_distance_m") or 0.0))
        current_time = float(candidate["time"]["playing_time_sec"])
        current_distance = float(candidate["distance"]["total_distance_m"])
        players.append({
            "player_id": player_id,
            "player_name": candidate.get("player_name"),
            "playing_time_delta_sec": round(current_time - previous_time, 3),
            "distance_delta_m": round(current_distance - previous_distance, 2),
            "candidate_detected_frames": candidate["frames"]["detected_frames"],
            "production_detected_frames": int(((previous.get("frames") or {}).get("detected_frames") or 0)),
            "subject_count_delta": len(candidate.get("candidate_subject_ids") or []) - len(previous.get("stable_subject_ids") or []),
            "coverage_denominator_status": "unknown_on_pitch_interval",
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "candidate_vs_production",
        "players": players,
        "global": {
            "production_assigned_frames": sum(int(((row.get("frames") or {}).get("detected_frames") or 0)) for row in production.values()),
            "candidate_assigned_frames": sum(row["frames"]["detected_frames"] for row in candidate_stats),
            "candidate_unresolved_subjects": len(promotion_plan.get("unresolved_subjects") or []),
            "candidate_unresolved_fragments": len(remediation_plan.get("unresolved_fragments") or []),
            "parallel_conflicts": len([row for row in promotion_plan.get("duplicate_observations") or [] if not row.get("safe_to_deduplicate")]),
            "excluded_structural_fragments": len(remediation_plan.get("excluded_fragments") or []),
        },
    }


def _contiguous_sequences(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    sequences: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda item: item["frame"]):
        if sequences and sequences[-1][-1]["frame"] + 1 == row["frame"]:
            sequences[-1].append(row)
        else:
            sequences.append([row])
    return sequences


def _assignment_key(player_id: str, subject_id: str, tracklet_id: str, start: int, end: int) -> str:
    payload = f"{player_id}|{subject_id}|{tracklet_id}|{start}|{end}"
    return f"candidate-assignment:v2:{canonical_document_digest(payload)}"


def _roster(match_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, team in enumerate(match_doc.get("teams") or []):
        if not isinstance(team, dict):
            continue
        team_label = "A" if index == 0 else "B" if index == 1 else None
        for player in team.get("players") or []:
            if isinstance(player, dict) and player.get("id"):
                result[str(player["id"])] = {
                    **player,
                    "team_label": team_label,
                    "team_id": team.get("id"),
                }
    return result


def _match_fps(match_doc: dict[str, Any]) -> float:
    video = match_doc.get("video") if isinstance(match_doc.get("video"), dict) else {}
    return max(0.001, float(video.get("fps") or match_doc.get("fps") or 30.0))


def _pitch_dimensions(match_doc: dict[str, Any], pitch_config_doc: dict[str, Any]) -> tuple[float, float]:
    dimensions = pitch_config_doc.get("pitch_dimensions_m") or {}
    width = float(dimensions.get("width_m") or pitch_config_doc.get("width_m") or 30.0)
    length = float(dimensions.get("length_m") or pitch_config_doc.get("length_m") or 47.4)
    return width, length


def _valid_point(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= 2
