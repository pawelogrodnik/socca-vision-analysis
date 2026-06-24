from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tracks(match_path: Path) -> list[dict[str, Any]]:
    tracks_path = match_path / "tracks.json"
    if not tracks_path.exists():
        raise FileNotFoundError("tracks.json not found. Run analysis first.")
    tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
    if not isinstance(tracks, list):
        raise ValueError("tracks.json must contain a list")
    return tracks


def _point_distance(a: list[float] | tuple[float, float] | None, b: list[float] | tuple[float, float] | None) -> float | None:
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def summarize_track(track: dict[str, Any]) -> dict[str, Any]:
    positions = track.get("positions") if isinstance(track.get("positions"), list) else []
    confidences = [float(pos.get("confidence")) for pos in positions if isinstance(pos, dict) and pos.get("confidence") is not None]
    first = positions[0] if positions else {}
    last = positions[-1] if positions else {}
    return {
        "tracklet_id": int(track.get("track_id")),
        "start_time_sec": float(track.get("start_time_sec") or 0),
        "end_time_sec": float(track.get("end_time_sec") or 0),
        "duration_sec": float(track.get("duration_sec") or 0),
        "positions_count": int(track.get("positions_count") or len(positions)),
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "first_pitch_m": first.get("pitch_m") if isinstance(first, dict) else None,
        "last_pitch_m": last.get("pitch_m") if isinstance(last, dict) else None,
        "first_bbox_xyxy": first.get("bbox_xyxy") if isinstance(first, dict) else None,
        "last_bbox_xyxy": last.get("bbox_xyxy") if isinstance(last, dict) else None,
    }


def is_noise_tracklet(tracklet: dict[str, Any], *, min_duration_sec: float, min_positions: int, min_avg_confidence: float) -> bool:
    avg_confidence = tracklet.get("avg_confidence")
    if tracklet.get("duration_sec", 0) < min_duration_sec:
        return True
    if tracklet.get("positions_count", 0) < min_positions:
        return True
    if avg_confidence is not None and float(avg_confidence) < min_avg_confidence:
        return True
    if not tracklet.get("first_pitch_m") or not tracklet.get("last_pitch_m"):
        return True
    return False


def _new_candidate(index: int, tracklet: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": f"candidate-{index:03d}",
        "tracklet_ids": [tracklet["tracklet_id"]],
        "status": "needs_review",
        "team_id": None,
        "player_id": None,
        "notes": "",
        "merge_confidence": 1.0,
        "start_time_sec": tracklet["start_time_sec"],
        "end_time_sec": tracklet["end_time_sec"],
        "total_duration_sec": tracklet["duration_sec"],
        "positions_count": tracklet["positions_count"],
        "avg_confidence": tracklet.get("avg_confidence"),
        "first_pitch_m": tracklet.get("first_pitch_m"),
        "last_pitch_m": tracklet.get("last_pitch_m"),
        "sample_tracklet_id": tracklet["tracklet_id"],
        "tracklet_count": 1,
    }


def _candidate_score(candidate: dict[str, Any], tracklet: dict[str, Any], *, max_gap_sec: float, max_speed_mps: float) -> tuple[float, float, float] | None:
    gap = float(tracklet["start_time_sec"]) - float(candidate["end_time_sec"])
    if gap < -0.1 or gap > max_gap_sec:
        return None
    distance = _point_distance(candidate.get("last_pitch_m"), tracklet.get("first_pitch_m"))
    if distance is None:
        return None
    allowed_distance = max(2.5, gap * max_speed_mps + 1.25)
    if distance > allowed_distance:
        return None
    speed = distance / max(gap, 0.25)
    if speed > max_speed_mps:
        return None
    score = distance + gap * 1.5
    return score, gap, distance


def _append_candidate(candidate: dict[str, Any], tracklet: dict[str, Any], *, gap: float, distance: float) -> None:
    existing_count = max(1, int(candidate.get("tracklet_count") or 1))
    candidate["tracklet_ids"].append(tracklet["tracklet_id"])
    candidate["end_time_sec"] = max(float(candidate["end_time_sec"]), float(tracklet["end_time_sec"]))
    candidate["total_duration_sec"] = round(float(candidate.get("total_duration_sec") or 0) + float(tracklet.get("duration_sec") or 0), 3)
    candidate["positions_count"] = int(candidate.get("positions_count") or 0) + int(tracklet.get("positions_count") or 0)
    if tracklet.get("last_pitch_m"):
        candidate["last_pitch_m"] = tracklet.get("last_pitch_m")
    candidate["tracklet_count"] = existing_count + 1
    current_conf = candidate.get("avg_confidence")
    next_conf = tracklet.get("avg_confidence")
    if current_conf is not None and next_conf is not None:
        candidate["avg_confidence"] = round((float(current_conf) * existing_count + float(next_conf)) / (existing_count + 1), 4)
    merge_quality = max(0.0, min(1.0, 1.0 - (distance / 8.0) - (gap / 10.0)))
    candidate["merge_confidence"] = round(min(float(candidate.get("merge_confidence") or 1.0), merge_quality), 4)


def build_identity_candidates(
    tracklets: list[dict[str, Any]],
    *,
    min_duration_sec: float = 1.0,
    min_positions: int = 8,
    min_avg_confidence: float = 0.12,
    max_gap_sec: float = 3.0,
    max_speed_mps: float = 8.5,
) -> dict[str, Any]:
    usable: list[dict[str, Any]] = []
    noise: list[dict[str, Any]] = []
    for tracklet in tracklets:
        if is_noise_tracklet(tracklet, min_duration_sec=min_duration_sec, min_positions=min_positions, min_avg_confidence=min_avg_confidence):
            noise.append({**tracklet, "noise_reason": "short_or_low_quality"})
        else:
            usable.append(tracklet)

    usable = sorted(usable, key=lambda item: (float(item.get("start_time_sec") or 0), -float(item.get("duration_sec") or 0)))
    candidates: list[dict[str, Any]] = []
    for tracklet in usable:
        best_index: int | None = None
        best_score: tuple[float, float, float] | None = None
        for index, candidate in enumerate(candidates):
            score = _candidate_score(candidate, tracklet, max_gap_sec=max_gap_sec, max_speed_mps=max_speed_mps)
            if score is None:
                continue
            if best_score is None or score[0] < best_score[0]:
                best_index = index
                best_score = score
        if best_index is None or best_score is None:
            candidates.append(_new_candidate(len(candidates) + 1, tracklet))
        else:
            _, gap, distance = best_score
            _append_candidate(candidates[best_index], tracklet, gap=gap, distance=distance)

    candidates = sorted(candidates, key=lambda item: float(item.get("total_duration_sec") or 0), reverse=True)
    for index, candidate in enumerate(candidates, start=1):
        candidate["candidate_id"] = f"candidate-{index:03d}"

    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "parameters": {
            "min_duration_sec": min_duration_sec,
            "min_positions": min_positions,
            "min_avg_confidence": min_avg_confidence,
            "max_gap_sec": max_gap_sec,
            "max_speed_mps": max_speed_mps,
        },
        "raw_tracklets_count": len(tracklets),
        "usable_tracklets_count": len(usable),
        "noise_tracklets_count": len(noise),
        "noise_tracklet_ids": [item["tracklet_id"] for item in noise],
        "noise_tracklets": noise[:250],
        "candidates": candidates,
    }


def _load_assignment_doc(match_path: Path) -> dict[str, Any] | None:
    assignment_path = match_path / "identity_assignments.json"
    if not assignment_path.exists():
        return None
    data = json.loads(assignment_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _apply_saved_assignments(candidates: list[dict[str, Any]], saved_doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not saved_doc:
        return candidates
    saved = saved_doc.get("assignments") if isinstance(saved_doc.get("assignments"), list) else []
    by_id = {str(item.get("candidate_id")): item for item in saved if isinstance(item, dict) and item.get("candidate_id")}
    merged: list[dict[str, Any]] = []
    for candidate in candidates:
        saved_item = by_id.get(candidate["candidate_id"])
        if saved_item:
            candidate = {
                **candidate,
                "status": saved_item.get("status") or candidate.get("status") or "needs_review",
                "team_id": saved_item.get("team_id"),
                "player_id": saved_item.get("player_id"),
                "notes": saved_item.get("notes") or "",
            }
        merged.append(candidate)
    return merged


def build_identity_summary(meta: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    assigned = [item for item in candidates if item.get("status") == "assigned" and item.get("player_id")]
    ignored = [item for item in candidates if item.get("status") in {"false_positive", "opponent", "referee"}]
    needs_review = [item for item in candidates if item.get("status") in {None, "", "needs_review", "unassigned", "unknown"}]

    unique_players_by_team: dict[str, set[str]] = {}
    candidate_count_by_team: dict[str, int] = {}
    tracklet_count_by_team: dict[str, int] = {}
    for candidate in assigned:
        team_id = str(candidate.get("team_id") or "unknown-team")
        player_id = candidate.get("player_id")
        if player_id:
            unique_players_by_team.setdefault(team_id, set()).add(str(player_id))
        candidate_count_by_team[team_id] = candidate_count_by_team.get(team_id, 0) + 1
        tracklet_count_by_team[team_id] = tracklet_count_by_team.get(team_id, 0) + len(candidate.get("tracklet_ids") or [])

    roster_by_team = {
        str(team.get("id")): len(team.get("players") or [])
        for team in meta.get("teams") or []
        if isinstance(team, dict) and team.get("id")
    }

    return {
        "identity_candidates": len(candidates),
        "assigned_candidates": len(assigned),
        "needs_review_candidates": len(needs_review),
        "ignored_candidates": len(ignored),
        "assigned_tracklets": sum(len(item.get("tracklet_ids") or []) for item in assigned),
        "unique_players_total": len({str(item.get("player_id")) for item in assigned if item.get("player_id")}),
        "unique_players_by_team": {team_id: len(players) for team_id, players in unique_players_by_team.items()},
        "assigned_candidates_by_team": candidate_count_by_team,
        "assigned_tracklets_by_team": tracklet_count_by_team,
        "roster_players_by_team": roster_by_team,
    }


def build_identity_review(match_path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    tracks = load_tracks(match_path)
    tracklets = [summarize_track(track) for track in tracks if track.get("track_id") is not None]
    candidate_doc = build_identity_candidates(tracklets)
    candidates = _apply_saved_assignments(candidate_doc["candidates"], _load_assignment_doc(match_path))
    candidate_doc["candidates"] = candidates
    candidate_doc["summary"] = build_identity_summary(meta, candidates)
    (match_path / "identity_candidates.json").write_text(json.dumps(candidate_doc, indent=2), encoding="utf-8")
    return candidate_doc


def save_identity_assignments(match_path: Path, meta: dict[str, Any], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    review = build_identity_review(match_path, meta)
    candidate_ids = {candidate["candidate_id"] for candidate in review["candidates"]}
    allowed_statuses = {"needs_review", "assigned", "unknown", "false_positive", "opponent", "referee"}
    normalized: list[dict[str, Any]] = []
    for item in assignments:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id not in candidate_ids:
            continue
        status = str(item.get("status") or "needs_review")
        if status == "unassigned":
            status = "needs_review"
        if status not in allowed_statuses:
            status = "needs_review"
        player_id = item.get("player_id") or None
        team_id = item.get("team_id") or None
        if status != "assigned":
            player_id = None
        normalized.append(
            {
                "candidate_id": candidate_id,
                "status": status,
                "team_id": team_id,
                "player_id": player_id,
                "notes": item.get("notes") or "",
            }
        )

    by_id = {item["candidate_id"]: item for item in normalized}
    complete_assignments: list[dict[str, Any]] = []
    for candidate in review["candidates"]:
        complete_assignments.append(
            by_id.get(
                candidate["candidate_id"],
                {
                    "candidate_id": candidate["candidate_id"],
                    "status": "needs_review",
                    "team_id": None,
                    "player_id": None,
                    "notes": "",
                },
            )
        )

    candidates_with_assignments = _apply_saved_assignments(review["candidates"], {"assignments": complete_assignments})
    summary = build_identity_summary(meta, candidates_with_assignments)
    doc = {
        "schema_version": "0.1.0",
        "updated_at": now_iso(),
        "assignments": complete_assignments,
        "summary": summary,
    }
    (match_path / "identity_assignments.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    review["candidates"] = candidates_with_assignments
    review["summary"] = summary
    (match_path / "identity_candidates.json").write_text(json.dumps(review, indent=2), encoding="utf-8")
    return doc
