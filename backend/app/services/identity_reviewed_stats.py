from __future__ import annotations

"""Reviewed-only timeline, heatmaps and conservative movement summaries."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.global_identity import (
    MAX_STATS_ESTIMATED_GAP_SEC,
    MAX_STATS_SPEED_MPS,
    STATS_OBSERVED_GAP_FRAMES,
    calculate_movement_stats,
)
from app.services.identity_canonical_io import load_json_cached_or
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_effective_observation import (
    iter_effective_reviewed_observations,
)
from app.services.identity_reviewed_coverage import summarize_effective_observations
from app.services.identity_reviewed_progress import PROGRESS_SCHEMA_VERSION
from app.services.identity_reviewed_workload import build_reviewed_player_workload
from app.services.reviewed_sprint_policy import (
    classify_reviewed_sprints,
    reviewed_sprint_policy,
)
from app.services.identity_review_scope import (
    TEAM_STATS_ONLY,
    identity_review_scope_digest,
    identity_review_scope_read_model,
    review_scope_dependency_matches,
    team_review_scope,
)
from app.services.identity_reviewed_scope_eligibility import team_attribution_state
from app.services.video import read_match_video_metadata


def build_reviewed_stats(match_path: Path, snapshot: dict[str, Any], match_doc: dict[str, Any], pitch_config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    tracklets = {str(row.get("tracklet_id")): row for row in _load(match_path / "tracklets.json").get("tracklets") or []}
    video_metadata = read_match_video_metadata(match_path, match_doc)
    fps = float(video_metadata["fps"])
    if fps <= 0:
        raise ValueError("Source video does not expose a valid FPS value")
    effective_observations = list(iter_effective_reviewed_observations(
        tracklets,
        list(snapshot.get("tracklet_assignments") or []),
        list(snapshot.get("observation_overrides") or []),
        list(snapshot.get("observation_demotions") or []),
        list(snapshot.get("canonical_observation_assignments") or []),
        list(snapshot.get("segment_observation_assignments") or []),
    ))
    identity_coverage, _ = summarize_effective_observations(
        effective_observations,
        match_doc,
    )
    observations_by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observations_by_safe_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for effective in effective_observations:
        if (
            effective.get("identity_status") != "confirmed"
            or not effective.get("canonical_player_id")
            or effective.get("visual_trusted") is False
            or effective.get("play_area_status", "inside_play") != "inside_play"
        ):
            continue
        if team_review_scope(match_doc, str(effective.get("team_label") or "U")) == TEAM_STATS_ONLY:
            continue
        movement_position = _movement_position(effective)
        if movement_position is None:
            continue
        observations_by_player[str(effective["canonical_player_id"])].append(movement_position)
    # Team movement is deliberately independent from player-name eligibility.
    # A current canonical A/B attribution remains useful to team statistics
    # even when the player's identity is unresolved or conflicts with another
    # player from that same team.
    for effective in effective_observations:
        safe_team_position = reviewed_safe_team_movement_observation(effective)
        if safe_team_position is not None:
            observations_by_safe_team[str(safe_team_position["team_label"])].append(
                safe_team_position
            )
    players = []
    heatmaps = []
    timeline = []
    for player_id, rows in sorted(observations_by_player.items()):
        rows.sort(key=lambda row: (int(row.get("frame") or 0), str(row.get("tracklet_id") or "")))
        detected = [row for row in rows if row.get("pitch_m")]
        fragments = _fragments(rows)
        frames = sorted({int(row.get("frame") or 0) for row in rows})
        detected_time_sec = round(len(frames) / fps, 3)
        movement = [calculate_movement_stats(fragment, fps) for fragment in fragments if len(fragment) >= 2]
        movement_summary = _aggregate_movement_stats(movement)
        intensity_summary = movement_summary.get("intensity") if isinstance(movement_summary.get("intensity"), dict) else {}
        sprint_reference = _sprint_reference(movement)
        sprint_detection = reviewed_sprint_policy(
            peak_sustained_speed_kmh=sprint_reference["peak_sustained_speed_kmh"],
            speed_quality=sprint_reference["speed_quality"],
            detected_time_sec=detected_time_sec,
        )
        sprint_result = classify_reviewed_sprints(fragments, fps=fps, policy=sprint_detection)
        intensity_summary.update({
            "sprint_count": sprint_result["sprint_count"],
            "sprint_time_sec": sprint_result["sprint_time_sec"],
            "sprint_distance_m": sprint_result["sprint_distance_m"],
            "max_sprint_speed_kmh": sprint_result["max_sprint_speed_kmh"],
            "validated_sprint_peak_kmh": sprint_result["max_sprint_speed_kmh"],
            "raw_sprint_segment_peak_kmh": sprint_result["raw_sprint_segment_peak_kmh"],
            "sprint_candidate_count": sprint_result["sprint_candidate_count"],
            "rejected_sprint_candidate_count": sprint_result["rejected_sprint_candidate_count"],
            "best_sprint_candidate_speed_kmh": sprint_result["best_sprint_candidate_speed_kmh"],
            "best_sprint_candidate_duration_sec": sprint_result["best_sprint_candidate_duration_sec"],
            "best_sprint_candidate_distance_m": sprint_result["best_sprint_candidate_distance_m"],
            "best_sprint_candidate_reason": sprint_result["best_sprint_candidate_reason"],
            "best_rejected_sprint_candidate": sprint_result["best_rejected_sprint_candidate"],
            "sprint_detection": sprint_detection,
        })
        workload = build_reviewed_player_workload(
            fragments,
            fps=fps,
            video_duration_sec=float(video_metadata["duration_sec"]),
            canonical={
                "detected_time_sec": detected_time_sec,
                "total_distance_m": movement_summary.get("total_distance_m"),
                "high_intensity_distance_m": intensity_summary.get("high_intensity_distance_m"),
                "high_intensity_time_sec": intensity_summary.get("high_intensity_time_sec"),
                "sprint_count": intensity_summary.get("sprint_count"),
                "sprint_time_sec": intensity_summary.get("sprint_time_sec"),
                "sprint_distance_m": intensity_summary.get("sprint_distance_m"),
                "max_sprint_speed_kmh": intensity_summary.get("max_sprint_speed_kmh"),
                "sprint_events": sprint_result["events"],
            },
        )
        workload["sprint_detection"] = {**sprint_detection, "reference_speed_quality": sprint_reference["speed_quality"]}
        expected_movement_segments = sum(
            _expected_movement_segments(fragment, fps) for fragment in fragments
        )
        first, last = (frames[0], frames[-1]) if frames else (None, None)
        positions = [row["pitch_m"] for row in detected if isinstance(row.get("pitch_m"), list) and len(row["pitch_m"]) >= 2]
        player = {
            "player_id": player_id,
            "player_name": rows[0].get("player_name") or player_id,
            "team_label": rows[0].get("team_label"),
            "roster_number": rows[0].get("roster_number"),
            "first_confirmed_observation": first,
            "last_confirmed_observation": last,
            "confirmed_fragments": len(fragments),
            "confirmed_tracklets": sorted({str(row["tracklet_id"]) for row in rows}),
            "confirmed_detected_observations": len(frames),
            "detected_frames": len(frames),
            "detected_time_sec": detected_time_sec,
            "confirmed_observation_span_sec": (
                round((last - first + 1) / fps, 3)
                if first is not None and last is not None
                else 0.0
            ),
            "playing_time_sec": None,
            "coverage_denominator": "unknown",
            "average_pitch_position_m": _mean(positions),
            "heatmap_samples": len(positions),
            "workload": workload,
            **movement_summary,
            "expected_positive_movement_segments": expected_movement_segments,
            "longest_confirmed_gap_sec": _longest_gap(frames, fps),
            "readiness": {
                "identity": "ready_with_review",
                "detected_time": "ready_with_review",
                "playing_time": "not_available",
                "heatmap": "ready_with_review" if positions else "not_available",
                "average_position": "ready_with_review" if positions else "not_available",
                "distance": "experimental" if movement else "not_available",
                "speed": "experimental" if movement else "not_available",
                "intensity": "experimental" if movement else "not_available",
                "possession": "not_available",
                "passes": "not_available",
            },
        }
        players.append(player)
        timeline.append({"player_id": player_id, "player_name": player["player_name"], "team_label": player["team_label"], "observations": rows})
        heatmaps.append({"player_id": player_id, "team_label": player["team_label"], "samples": len(positions), "positions_m": positions, "bin_dimensions": [12, 8]})
    teams = _reviewed_team_movement(observations_by_safe_team, fps)
    snapshot_digest = str(snapshot["semantic_digest"])
    coverage = _coverage(snapshot)
    progress = _load(match_path / "reviewed_identity_progress.json")
    coverage_readiness = (
        progress.get("coverage_readiness")
        if progress.get("schema_version") == PROGRESS_SCHEMA_VERSION
        and progress.get("source_snapshot_digest") == snapshot_digest
        and review_scope_dependency_matches(match_doc, progress)
        else None
    )
    stats_status = (
        "completed"
        if not coverage_readiness or coverage_readiness.get("allows_finalize") is True
        else "incomplete_identity_coverage"
    )
    shared = {"schema_version": "1.0.0", "generated_at": datetime.now(timezone.utc).isoformat(), "source_snapshot_digest": snapshot_digest, "source_review_scope_digest": identity_review_scope_digest(match_doc), "identity_review_scope": identity_review_scope_read_model(match_doc), "video_timing": {"fps": fps, "frame_count": video_metadata["frame_count"], "duration_sec": video_metadata["duration_sec"], "source": video_metadata["source"], "filename": video_metadata["filename"]}, "safety": {"production_stats_mutated": False, "reran_yolo": False, "reran_tracking": False}}
    documents = {"reviewed_player_timeline.json": {**shared, "players": timeline}, "reviewed_player_stats.json": {**shared, "players": players, "teams": teams, "global_coverage": coverage, "identity_coverage": identity_coverage}, "reviewed_player_heatmaps.json": {**shared, "pitch_dimensions_m": {"width_m": (pitch_config or {}).get("width_m"), "length_m": (pitch_config or {}).get("length_m")}, "heatmaps": heatmaps}, "reviewed_stats_readiness.json": {**shared, "schema_version": "2.0.0" if coverage_readiness else shared["schema_version"], "status": stats_status, "global_coverage": coverage, "identity_coverage": identity_coverage, "coverage_readiness": coverage_readiness, "team_shape": {"status": "not_available", "reason": "MVP stores player positions but does not infer a formation."}, "possession": {"status": "not_available", "reason": "Reviewed player attribution is not enabled in this MVP."}, "passes": {"status": "not_available", "reason": "Reviewed player attribution is not enabled in this MVP."}}}
    for name, document in documents.items(): write_identity_json_atomic(match_path / name, document)
    return documents


def _coverage(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("summary") or {}
    return {"coverage_unit": summary.get("coverage_unit"), "reliable_player_observations_total": summary.get("reliable_player_observations_total"), "confirmed_observations": summary.get("confirmed_detected_observations"), "unresolved_observations": summary.get("unresolved_detected_observations"), "conflicted_observations": summary.get("conflicted_detected_observations"), "ignored_observations": summary.get("ignored_detected_observations"), "confirmed_ratio": summary.get("confirmed_detected_observation_ratio"), "unresolved_ratio": summary.get("unresolved_detected_observation_ratio")}


def _movement_position(effective: dict[str, Any]) -> dict[str, Any] | None:
    if (
        effective.get("visual_trusted") is False
        or effective.get("play_area_status", "inside_play") != "inside_play"
    ):
        return None
    pitch_m = effective.get("smoothed_pitch_m") or effective.get("pitch_m")
    if not _valid_pitch_point(pitch_m):
        return None
    return {
        **effective,
        "pitch_m": pitch_m,
        "source": "detected",
        "status": "detected",
        "visual_trusted": effective.get("visual_trusted", True),
        "play_area_status": effective.get("play_area_status") or "inside_play",
        "observation_identity_source": effective.get("identity_source"),
        "eligible_for_distance": True,
        "eligible_for_heatmap": True,
    }


def reviewed_safe_team_movement_observation(
    effective: dict[str, Any],
) -> dict[str, Any] | None:
    """Return one safe movement observation for a canonically known team.

    Team movement must be governed by current team attribution, not whether a
    person has been named. This is intentionally stricter than merely checking
    ``team_label``: explicit Team-U and live A/B contradictions remain out of
    both team totals, while player-only conflicts within a known team stay in.
    """
    if reviewed_team_movement_exclusion_reason(effective) is not None:
        return None
    return _movement_position(effective)


def reviewed_team_movement_exclusion_reason(effective: dict[str, Any]) -> str | None:
    """Explain why an effective observation cannot contribute team movement."""
    label = str(effective.get("team_label") or "").upper()
    if label not in {"A", "B"}:
        return "team_unknown"

    team_state = team_attribution_state(
        {
            "effective_team_label": label,
            "detected_team_labels": effective.get("detected_team_labels"),
            "mixed_hint": effective.get("mixed_hint"),
            "current_decision": effective.get("current_decision"),
        }
    )
    if team_state == "cross_team":
        return "cross_team_conflict"
    if team_state != f"certain_{label}":
        return "team_unknown"

    identity_status = str(effective.get("identity_status") or "unresolved")
    if identity_status in {"referee", "false_detection", "ignored"}:
        return "non_player"
    if identity_status == "team_unknown":
        return "team_unknown"
    if identity_status not in {
        "confirmed",
        "stable_anonymous",
        "unresolved",
        "conflicted",
        "blocked",
    }:
        return "other_identity_status"
    if effective.get("visual_trusted") is False:
        return "visually_untrusted"
    if effective.get("play_area_status", "inside_play") != "inside_play":
        return "outside_play"
    pitch_m = effective.get("smoothed_pitch_m") or effective.get("pitch_m")
    if not _valid_pitch_point(pitch_m):
        return "invalid_pitch_point"
    return None


def _reviewed_team_movement(
    observations_by_team: dict[str, list[dict[str, Any]]], fps: float
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for team_label in ("A", "B"):
        seen: set[tuple[str, int]] = set()
        positions = []
        for row in observations_by_team.get(team_label, []):
            key = (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
            if key in seen:
                continue
            seen.add(key)
            positions.append(row)
        positions.sort(key=lambda row: (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0)))
        fragments = _fragments(positions)
        movement = [calculate_movement_stats(fragment, fps) for fragment in fragments if len(fragment) >= 2]
        summary = _aggregate_movement_stats(movement)
        intensity = summary["intensity"]
        rows.append(
            {
                "team_label": team_label,
                "movement_authority": "reviewed_safe_team_observations",
                "team_attribution_contract": "canonical_certain_team_without_required_player_name",
                "total_distance_m": summary["total_distance_m"],
                "observed_distance_m": summary["observed_distance_m"],
                "estimated_short_gap_distance_m": summary["estimated_short_gap_distance_m"],
                "accepted_movement_segments": summary["accepted_movement_segments"],
                "safe_observation_count": len(positions),
                "high_intensity_distance_m": intensity["high_intensity_distance_m"],
                "sprint_distance_m": intensity["sprint_distance_m"],
                "sprint_count": intensity["sprint_count"],
            }
        )
    return rows


def _sprint_reference(movement: list[dict[str, Any]]) -> dict[str, Any]:
    """Select quality from the fragment that actually supplied the peak.

    Aggregate display quality stays conservative (the worst fragment) because
    it describes all player movement.  Sprint policy instead asks whether the
    selected sustained-peak evidence is itself credible.
    """
    quality_rank = {"not_available": 0, "low": 1, "medium": 2, "high": 3}
    candidates = [
        row for row in movement
        if float(row.get("peak_sustained_speed_kmh") or 0.0) > 0
    ]
    selected = max(
        candidates,
        key=lambda row: (
            float(row.get("peak_sustained_speed_kmh") or 0.0),
            quality_rank.get(str(row.get("speed_quality") or "not_available"), 0),
            float(row.get("detected_time_sec") or 0.0),
        ),
        default={},
    )
    return {
        "peak_sustained_speed_kmh": float(selected.get("peak_sustained_speed_kmh") or 0.0),
        "speed_quality": str(selected.get("speed_quality") or "not_available"),
    }


def _aggregate_movement_stats(movement: list[dict[str, Any]]) -> dict[str, Any]:
    observed_distance = sum(float(item.get("observed_distance_m") or 0.0) for item in movement)
    estimated_gap_distance = sum(float(item.get("estimated_gap_distance_m") or 0.0) for item in movement)
    total_distance = observed_distance + estimated_gap_distance
    movement_time = sum(float(item.get("playing_time_sec") or 0.0) for item in movement)
    detected_time = sum(float(item.get("detected_time_sec") or 0.0) for item in movement)
    avg_speed_mps = total_distance / movement_time if movement_time > 0 else 0.0
    observed_avg_speed_mps = observed_distance / detected_time if detected_time > 0 else 0.0
    peak_speed_mps = max(
        (float(item.get("peak_sustained_speed_mps") or 0.0) for item in movement),
        default=0.0,
    )
    raw_top_speed_mps = max(
        (float(item.get("raw_segment_top_speed_mps") or 0.0) for item in movement),
        default=0.0,
    )
    intensity_rows = [
        item.get("intensity")
        for item in movement
        if isinstance(item.get("intensity"), dict)
    ]
    high_intensity_distance = sum(
        float(item.get("high_intensity_distance_m") or 0.0)
        for item in intensity_rows
    )
    sprint_distance = sum(
        float(item.get("sprint_distance_m") or 0.0) for item in intensity_rows
    )
    return {
        "observed_distance_m": round(observed_distance, 2),
        "estimated_short_gap_distance_m": round(estimated_gap_distance, 2),
        "total_distance_m": round(total_distance, 2),
        # This is the denominator of avg_speed_mps below.  Persisting it makes
        # a future aggregate recompute the same metric without averaging
        # fragment-level speeds.
        "movement_time_sec": round(movement_time, 3),
        "observed_movement_segments": sum(
            int(item.get("observed_segments") or 0) for item in movement
        ),
        "estimated_gap_movement_segments": sum(
            int(item.get("estimated_gap_segments") or 0) for item in movement
        ),
        "accepted_movement_segments": sum(
            int(item.get("observed_segments") or 0)
            + int(item.get("estimated_gap_segments") or 0)
            for item in movement
        ),
        "skipped_outlier_segments": sum(
            int(item.get("skipped_outlier_segments") or 0) for item in movement
        ),
        "skipped_long_gap_segments": sum(
            int(item.get("skipped_long_gap_segments") or 0) for item in movement
        ),
        "speed": {
            "avg_speed_mps": round(avg_speed_mps, 3),
            "avg_speed_kmh": round(avg_speed_mps * 3.6, 2),
            "observed_avg_speed_mps": round(observed_avg_speed_mps, 3),
            "peak_sustained_speed_mps": round(peak_speed_mps, 3),
            "peak_sustained_speed_kmh": round(peak_speed_mps * 3.6, 2),
            "top_speed_mps": round(peak_speed_mps, 3),
            "top_speed_kmh": round(peak_speed_mps * 3.6, 2),
            "raw_segment_top_speed_mps": round(raw_top_speed_mps, 3),
            "raw_segment_top_speed_kmh": round(raw_top_speed_mps * 3.6, 2),
            "speed_quality": _lowest_quality(
                [str(item.get("speed_quality") or "not_available") for item in movement]
            ),
            "speed_window_sec": max(
                (float(item.get("speed_window_sec") or 0.0) for item in movement),
                default=0.0,
            ),
            "samples_used": sum(int(item.get("samples_used") or 0) for item in movement),
            "sustained_speed_windows": sum(
                int(item.get("sustained_speed_windows") or 0) for item in movement
            ),
        },
        "intensity": {
            "high_intensity_threshold_kmh": _common_number(
                intensity_rows, "high_intensity_threshold_kmh"
            ),
            "high_intensity_time_sec": round(
                sum(float(item.get("high_intensity_time_sec") or 0.0) for item in intensity_rows),
                3,
            ),
            "high_intensity_distance_m": round(high_intensity_distance, 2),
            "high_intensity_segments": sum(
                int(item.get("high_intensity_segments") or 0) for item in intensity_rows
            ),
            "high_intensity_distance_ratio": (
                round(high_intensity_distance / total_distance, 4)
                if total_distance > 0
                else 0.0
            ),
            "sprint_count": sum(int(item.get("sprint_count") or 0) for item in intensity_rows),
            "sprint_time_sec": round(
                sum(float(item.get("sprint_time_sec") or 0.0) for item in intensity_rows),
                3,
            ),
            "sprint_distance_m": round(sprint_distance, 2),
            "sprint_distance_ratio": (
                round(sprint_distance / total_distance, 4)
                if total_distance > 0
                else 0.0
            ),
            "max_sprint_speed_kmh": max(
                (float(item.get("max_sprint_speed_kmh") or 0.0) for item in intensity_rows),
                default=0.0,
            ),
            "trusted_speed_segments": sum(
                int(item.get("trusted_speed_segments") or 0) for item in intensity_rows
            ),
            "sprint_candidate_count": sum(
                int(item.get("sprint_candidate_count") or 0) for item in intensity_rows
            ),
            "rejected_sprint_candidate_count": sum(
                int(item.get("rejected_sprint_candidate_count") or 0)
                for item in intensity_rows
            ),
        },
    }


def _lowest_quality(values: list[str]) -> str:
    quality_order = {"not_available": 0, "low": 1, "medium": 2, "high": 3}
    available = [value for value in values if value in quality_order]
    if not available:
        return "not_available"
    return min(available, key=lambda value: quality_order[value])


def _common_number(rows: list[dict[str, Any]], key: str) -> float:
    return float(next((row.get(key) for row in rows if row.get(key) is not None), 0.0))
def _fragments(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out=[]; current=[]
    for row in rows:
        if not current or (row["tracklet_id"] == current[-1]["tracklet_id"] and int(row["frame"]) - int(current[-1]["frame"]) <= 10): current.append(row)
        else: out.append(current); current=[row]
    return [*out, current] if current else out
def _mean(points: list[list[float]]) -> list[float] | None: return [round(sum(float(point[i]) for point in points) / len(points), 3) for i in (0, 1)] if points else None
def _longest_gap(frames: list[int], fps: float) -> float: return round(max((right-left for left,right in zip(frames,frames[1:])), default=0)/fps,3)
def _valid_pitch_point(value: Any) -> bool: return isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2])
def _expected_movement_segments(rows: list[dict[str, Any]], fps: float) -> int:
    expected = 0
    for left, right in zip(rows, rows[1:]):
        if not _valid_pitch_point(left.get("pitch_m")) or not _valid_pitch_point(right.get("pitch_m")):
            continue
        frame_gap = int(right.get("frame") or 0) - int(left.get("frame") or 0)
        if frame_gap <= 0:
            continue
        left_time = float(left.get("time_sec") or int(left.get("frame") or 0) / fps)
        right_time = float(right.get("time_sec") or int(right.get("frame") or 0) / fps)
        elapsed = max(1 / fps, right_time - left_time)
        dx = float(right["pitch_m"][0]) - float(left["pitch_m"][0])
        dy = float(right["pitch_m"][1]) - float(left["pitch_m"][1])
        distance = (dx * dx + dy * dy) ** 0.5
        speed = distance / elapsed
        accepted_gap = (
            frame_gap <= STATS_OBSERVED_GAP_FRAMES
            or elapsed <= MAX_STATS_ESTIMATED_GAP_SEC
        )
        if distance > 0.01 and speed <= MAX_STATS_SPEED_MPS and accepted_gap:
            expected += 1
    return expected
def _load(path: Path) -> dict[str, Any]:
    # Tolerant loader; participates in the request-scoped source
    # materialization so finalize does not re-parse 264MB tracklets.json.
    return load_json_cached_or(path, {})
