from __future__ import annotations

"""Reviewed-only timeline, heatmaps and conservative movement summaries."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.global_identity import calculate_movement_stats
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.video import read_match_video_metadata


def build_reviewed_stats(match_path: Path, snapshot: dict[str, Any], match_doc: dict[str, Any], pitch_config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    tracklets = {str(row.get("tracklet_id")): row for row in _load(match_path / "tracklets.json").get("tracklets") or []}
    video_metadata = read_match_video_metadata(match_path, match_doc)
    fps = float(video_metadata["fps"])
    if fps <= 0:
        raise ValueError("Source video does not expose a valid FPS value")
    observations_by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    overrides = {
        (str(row.get("tracklet_id")), int(row.get("frame") or 0)): row
        for row in snapshot.get("observation_overrides") or []
    }
    for assignment in snapshot.get("tracklet_assignments") or []:
        for position in tracklets.get(str(assignment["tracklet_id"]), {}).get("positions_m") or []:
            if str(position.get("status") or "detected") != "detected": continue
            frame = int(position.get("frame") or 0)
            effective = {**assignment, **(overrides.get((str(assignment["tracklet_id"]), frame)) or {})}
            if effective.get("identity_status") != "confirmed" or not effective.get("canonical_player_id"):
                continue
            observations_by_player[str(effective["canonical_player_id"])].append({**position, "tracklet_id": assignment["tracklet_id"], "candidate_subject_id": assignment.get("candidate_subject_id"), "observation_identity_source": effective.get("identity_source"), "observation_key": effective.get("observation_key"), "player_name": effective.get("player_name"), "team_label": effective.get("team_label"), "roster_number": effective.get("roster_number"), "eligible_for_distance": True, "eligible_for_heatmap": True})
    players = []
    heatmaps = []
    timeline = []
    for player_id, rows in sorted(observations_by_player.items()):
        rows.sort(key=lambda row: (int(row.get("frame") or 0), str(row.get("tracklet_id") or "")))
        detected = [row for row in rows if row.get("pitch_m")]
        fragments = _fragments(rows)
        movement = [calculate_movement_stats(fragment, fps) for fragment in fragments if len(fragment) >= 2]
        frames = sorted({int(row.get("frame") or 0) for row in rows})
        first, last = (frames[0], frames[-1]) if frames else (None, None)
        positions = [row["pitch_m"] for row in detected if isinstance(row.get("pitch_m"), list) and len(row["pitch_m"]) >= 2]
        player = {"player_id": player_id, "player_name": rows[0].get("player_name") or player_id, "team_label": rows[0].get("team_label"), "roster_number": rows[0].get("roster_number"), "first_confirmed_observation": first, "last_confirmed_observation": last, "confirmed_fragments": len(fragments), "confirmed_tracklets": sorted({str(row["tracklet_id"]) for row in rows}), "detected_frames": len(frames), "detected_time_sec": round(len(frames) / fps, 3), "confirmed_observation_span_sec": round((last - first + 1) / fps, 3) if first is not None and last is not None else 0.0, "playing_time_sec": None, "coverage_denominator": "unknown", "average_pitch_position_m": _mean(positions), "heatmap_samples": len(positions), "observed_distance_m": round(sum(float(item["observed_distance_m"]) for item in movement), 2), "estimated_short_gap_distance_m": round(sum(float(item["estimated_gap_distance_m"]) for item in movement), 2), "total_distance_m": round(sum(float(item["total_distance_m"]) for item in movement), 2), "longest_confirmed_gap_sec": _longest_gap(frames, fps), "readiness": {"identity": "ready_with_review", "detected_time": "ready_with_review", "playing_time": "not_available", "heatmap": "ready_with_review" if positions else "not_available", "average_position": "ready_with_review" if positions else "not_available", "distance": "experimental" if movement else "not_available", "possession": "not_available", "passes": "not_available"}}
        players.append(player)
        timeline.append({"player_id": player_id, "player_name": player["player_name"], "team_label": player["team_label"], "observations": rows})
        heatmaps.append({"player_id": player_id, "team_label": player["team_label"], "samples": len(positions), "positions_m": positions, "bin_dimensions": [12, 8]})
    snapshot_digest = str(snapshot["semantic_digest"])
    coverage = _coverage(snapshot)
    shared = {"schema_version": "1.0.0", "generated_at": datetime.now(timezone.utc).isoformat(), "source_snapshot_digest": snapshot_digest, "video_timing": {"fps": fps, "frame_count": video_metadata["frame_count"], "duration_sec": video_metadata["duration_sec"], "source": video_metadata["source"], "filename": video_metadata["filename"]}, "safety": {"production_stats_mutated": False, "reran_yolo": False, "reran_tracking": False}}
    documents = {"reviewed_player_timeline.json": {**shared, "players": timeline}, "reviewed_player_stats.json": {**shared, "players": players, "global_coverage": coverage}, "reviewed_player_heatmaps.json": {**shared, "pitch_dimensions_m": {"width_m": (pitch_config or {}).get("width_m"), "length_m": (pitch_config or {}).get("length_m")}, "heatmaps": heatmaps}, "reviewed_stats_readiness.json": {**shared, "status": "completed", "global_coverage": coverage, "team_shape": {"status": "not_available", "reason": "MVP stores player positions but does not infer a formation."}, "possession": {"status": "not_available", "reason": "Reviewed player attribution is not enabled in this MVP."}, "passes": {"status": "not_available", "reason": "Reviewed player attribution is not enabled in this MVP."}}}
    for name, document in documents.items(): write_identity_json_atomic(match_path / name, document)
    return documents


def _coverage(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("summary") or {}
    return {"coverage_unit": summary.get("coverage_unit"), "reliable_player_observations_total": summary.get("reliable_player_observations_total"), "confirmed_observations": summary.get("confirmed_detected_observations"), "unresolved_observations": summary.get("unresolved_detected_observations"), "conflicted_observations": summary.get("conflicted_detected_observations"), "ignored_observations": summary.get("ignored_detected_observations"), "confirmed_ratio": summary.get("confirmed_detected_observation_ratio"), "unresolved_ratio": summary.get("unresolved_detected_observation_ratio")}
def _fragments(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out=[]; current=[]
    for row in rows:
        if not current or (row["tracklet_id"] == current[-1]["tracklet_id"] and int(row["frame"]) - int(current[-1]["frame"]) <= 10): current.append(row)
        else: out.append(current); current=[row]
    return [*out, current] if current else out
def _mean(points: list[list[float]]) -> list[float] | None: return [round(sum(float(point[i]) for point in points) / len(points), 3) for i in (0, 1)] if points else None
def _longest_gap(frames: list[int], fps: float) -> float: return round(max((right-left for left,right in zip(frames,frames[1:])), default=0)/fps,3)
def _load(path: Path) -> dict[str, Any]:
    import json
    return json.loads(path.read_text(encoding="utf-8"))
