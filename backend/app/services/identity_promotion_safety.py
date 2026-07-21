from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from math import hypot
from typing import Any


STRUCTURAL_FLAGS = {
    "merges_production_subjects",
    "merges_multiple_production_subjects",
    "cross_production_transition",
    "uncertain_transition",
    "parallel_roster_candidate_conflict",
    "parallel_subject_observations",
    "mixed_team_evidence",
    "structural_identity_conflict",
}
STRUCTURAL_BLOCKERS = {
    "parallel_roster_candidate_conflict",
    "structural_identity_conflict",
}
SAFE_DUPLICATE_CLASSES = {
    "same_source_duplicate",
    "boundary_split_duplicate",
    "near_identical_spatial_duplicate",
}
DEFAULT_PARAMETERS = {
    "near_identical_pitch_distance_m": 0.75,
    "parallel_nearby_pitch_distance_m": 2.0,
    "parallel_distant_pitch_distance_m": 4.0,
    "near_identical_bbox_iou": 0.55,
    "active_player_limit_sustained_sec": 0.5,
    "expected_players_on_pitch_fallback": 7,
}


def canonical_document_digest(document: Any) -> str:
    payload = json.dumps(
        _without_technical_timestamps(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def structural_conflict_reasons(
    card: dict[str, Any],
    candidate: dict[str, Any],
    timeline: dict[str, Any],
) -> list[str]:
    values = {
        str(value)
        for value in (
            list(card.get("blockers") or [])
            + list(card.get("quality_flags") or [])
            + list(candidate.get("quality_flags") or [])
            + list(timeline.get("quality_flags") or [])
        )
    }
    if len({str(value) for value in candidate.get("production_subject_ids") or []}) > 1:
        values.add("merges_multiple_production_subjects")
    if int(candidate.get("cross_production_transitions") or 0) > 0:
        values.add("cross_production_transition")
    if int(candidate.get("uncertain_transitions") or 0) > 0:
        values.add("uncertain_transition")
    return sorted(values & (STRUCTURAL_FLAGS | STRUCTURAL_BLOCKERS))


def canonicalize_promoted_observations(
    observations: list[dict[str, Any]],
    *,
    parameters: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    conflicts: list[dict[str, Any]] = []
    duplicate_audit: list[dict[str, Any]] = []

    by_source: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_source[(int(row["frame"]), str(row.get("tracklet_id") or ""))].append(row)
    for (frame, tracklet_id), rows in sorted(by_source.items()):
        players = sorted({str(row["player_id"]) for row in rows})
        if len(players) > 1:
            conflicts.append(
                {
                    "code": "same_source_observation_maps_to_multiple_players",
                    "frame": frame,
                    "tracklet_id": tracklet_id,
                    "player_ids": players,
                    "candidate_subject_ids": sorted(
                        {str(row["candidate_subject_id"]) for row in rows}
                    ),
                }
            )

    by_player_frame: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_player_frame[(str(row["player_id"]), int(row["frame"]))].append(row)

    canonical: list[dict[str, Any]] = []
    for (player_id, frame), rows in sorted(by_player_frame.items()):
        ranked = sorted(rows, key=_observation_rank)
        winner = ranked[0]
        canonical.append(winner)
        for duplicate in ranked[1:]:
            duplicate_class, safe, evidence = _classify_duplicate(winner, duplicate, params)
            audit_row = {
                "player_id": player_id,
                "frame": frame,
                "classification": duplicate_class,
                "safe_to_deduplicate": safe,
                "kept_candidate_subject_id": winner["candidate_subject_id"],
                "removed_candidate_subject_id": duplicate["candidate_subject_id"],
                "kept_tracklet_id": winner.get("tracklet_id"),
                "removed_tracklet_id": duplicate.get("tracklet_id"),
                "evidence": evidence,
                "kept_observation": winner,
                "removed_observation": duplicate,
            }
            duplicate_audit.append(audit_row)
            if not safe:
                conflicts.append(
                    {
                        "code": (
                            "same_player_parallel_spatial_conflict"
                            if duplicate_class == "parallel_distant_conflict"
                            else "unsafe_parallel_player_observation"
                        ),
                        **audit_row,
                    }
                )
    return canonical, duplicate_audit, conflicts


def build_promotion_safety_sections(
    *,
    canonical_observations: list[dict[str, Any]],
    all_review_observations: list[dict[str, Any]],
    unresolved_observations: list[dict[str, Any]],
    structural_subjects: list[dict[str, Any]],
    roster: dict[str, dict[str, Any]],
    match_doc: dict[str, Any],
    team_label: str,
    fps: float,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    reliable_review = _unique_reliable_rows(all_review_observations)
    reliable_promoted = _unique_reliable_rows(canonical_observations)
    reliable_unresolved = _unique_reliable_rows(unresolved_observations)
    reviewed_count = len(reliable_review)
    promoted_count = len(reliable_promoted)
    unresolved_count = len(reliable_unresolved)
    reviewed_scope_count = promoted_count + unresolved_count

    active_validation = _active_player_validation(
        reliable_promoted,
        expected_players=_expected_players(match_doc, team_label, params),
        fps=fps,
        sustained_sec=float(params["active_player_limit_sustained_sec"]),
    )
    goalkeeper_validation = _goalkeeper_validation(reliable_promoted, roster)
    unresolved_frames = sorted({int(row["frame"]) for row in reliable_unresolved})
    unresolved_intervals = _frame_number_intervals(unresolved_frames, fps)
    player_readiness = _player_readiness(
        canonical_observations,
        unresolved_observations,
        roster,
        fps,
    )
    safety_errors = [
        *active_validation["errors"],
        *goalkeeper_validation["errors"],
    ]
    warnings = [
        *active_validation["warnings"],
        *goalkeeper_validation["warnings"],
    ]
    return {
        "coverage": {
            "coverage_denominator": "reliable_detected_review_scope",
            "all_reliable_detected_team_observations": reviewed_count,
            "promoted_reliable_detected_team_observations": promoted_count,
            "team_assignment_coverage_ratio": _ratio(promoted_count, reviewed_count),
            "reviewed_detected_frames": reviewed_scope_count,
            "promoted_detected_frames": promoted_count,
            "unresolved_detected_frames": unresolved_count,
            "promoted_detected_ratio": _ratio(promoted_count, reviewed_scope_count),
            "unresolved_detected_ratio": _ratio(unresolved_count, reviewed_scope_count),
            "team_level_unresolved_frames": len(unresolved_frames),
            "potential_player_gaps": unresolved_intervals,
            "longest_unresolved_interval_sec": max(
                (float(row["duration_sec"]) for row in unresolved_intervals), default=0.0
            ),
            "unresolved_intervals_over_1s": sum(
                float(row["duration_sec"]) > 1.0 for row in unresolved_intervals
            ),
            "unresolved_intervals_over_3s": sum(
                float(row["duration_sec"]) > 3.0 for row in unresolved_intervals
            ),
        },
        "active_player_validation": active_validation,
        "goalkeeper_validation": goalkeeper_validation,
        "structural_subjects": structural_subjects,
        "player_readiness": player_readiness,
        "downstream_readiness": {
            "player_identity": "ready_with_review" if unresolved_count else "ready",
            "possession_readiness": "not_evaluated_optional",
            "passes_readiness": "not_evaluated_optional",
            "ball_artifacts_required": False,
        },
        "errors": safety_errors,
        "warnings": warnings,
    }


def _classify_duplicate(
    left: dict[str, Any],
    right: dict[str, Any],
    parameters: dict[str, Any],
) -> tuple[str, bool, dict[str, Any]]:
    same_source = str(left.get("tracklet_id") or "") == str(right.get("tracklet_id") or "")
    pitch_distance = _distance(left.get("pitch_m"), right.get("pitch_m"))
    bbox_iou = _bbox_iou(left.get("bbox_xyxy"), right.get("bbox_xyxy"))
    boundary_split = _is_boundary_split(left, right)
    evidence = {
        "same_source": same_source,
        "boundary_split": boundary_split,
        "pitch_distance_m": round(pitch_distance, 4) if pitch_distance is not None else None,
        "bbox_iou": round(bbox_iou, 4) if bbox_iou is not None else None,
    }
    if same_source:
        return "same_source_duplicate", True, evidence
    if boundary_split:
        return "boundary_split_duplicate", True, evidence
    if (
        pitch_distance is not None
        and pitch_distance <= float(parameters["near_identical_pitch_distance_m"])
    ) or (
        pitch_distance is None
        and bbox_iou is not None
        and bbox_iou >= float(parameters["near_identical_bbox_iou"])
    ):
        return "near_identical_spatial_duplicate", True, evidence
    if pitch_distance is not None:
        if pitch_distance >= float(parameters["parallel_distant_pitch_distance_m"]):
            return "parallel_distant_conflict", False, evidence
        if pitch_distance <= float(parameters["parallel_nearby_pitch_distance_m"]):
            return "parallel_nearby_duplicate", False, evidence
    if set(left.get("structural_reasons") or []) | set(right.get("structural_reasons") or []):
        return "structural_subject_conflict", False, evidence
    return "unknown_duplicate", False, evidence


def _is_boundary_split(left: dict[str, Any], right: dict[str, Any]) -> bool:
    frame = int(left.get("frame") or 0)
    left_range = (
        int(left.get("subject_start_frame") or -1),
        int(left.get("subject_end_frame") or -1),
    )
    right_range = (
        int(right.get("subject_start_frame") or -1),
        int(right.get("subject_end_frame") or -1),
    )
    shared_production = bool(
        set(left.get("production_subject_ids") or [])
        & set(right.get("production_subject_ids") or [])
    )
    exact_boundary = (
        left_range[1] == frame == right_range[0]
        or right_range[1] == frame == left_range[0]
    )
    return left_range != right_range and exact_boundary and shared_production


def _active_player_validation(
    rows: list[dict[str, Any]],
    *,
    expected_players: int,
    fps: float,
    sustained_sec: float,
) -> dict[str, Any]:
    by_frame: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        by_frame[int(row["frame"])].add(str(row["player_id"]))
    overflow_frames = sorted(
        frame for frame, players in by_frame.items() if len(players) > expected_players
    )
    intervals = _frame_number_intervals(overflow_frames, fps)
    sustained_frames = max(2, int(round(max(fps, 0.001) * sustained_sec)))
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for interval in intervals:
        payload = {
            **interval,
            "expected_players": expected_players,
            "peak_active_players": max(
                len(by_frame[frame])
                for frame in range(interval["start_frame"], interval["end_frame"] + 1)
            ),
        }
        if int(interval["frames"]) >= sustained_frames:
            errors.append({"code": "team_active_player_limit_sustained", **payload})
        else:
            warnings.append({"code": "team_active_player_limit_spike", **payload})
    return {
        "expected_players_on_pitch": expected_players,
        "sustained_overflow_frames": sustained_frames,
        "overflow_intervals": intervals,
        "errors": errors,
        "warnings": warnings,
    }


def _goalkeeper_validation(
    rows: list[dict[str, Any]],
    roster: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    trusted_goalkeepers = {
        player_id for player_id, player in roster.items() if _is_trusted_goalkeeper(player)
    }
    by_frame: dict[int, set[str]] = defaultdict(set)
    visual_candidates = 0
    for row in rows:
        player_id = str(row["player_id"])
        if player_id in trusted_goalkeepers:
            by_frame[int(row["frame"])].add(player_id)
        elif str(row.get("role") or "") == "goalkeeper":
            visual_candidates += 1
    conflicts = [
        {"frame": frame, "player_ids": sorted(players)}
        for frame, players in sorted(by_frame.items())
        if len(players) > 1
    ]
    return {
        "trusted_goalkeeper_player_ids": sorted(trusted_goalkeepers),
        "visual_goalkeeper_candidate_observations": visual_candidates,
        "errors": [
            {"code": "multiple_goalkeepers_active", **row} for row in conflicts
        ],
        "warnings": (
            [{"code": "visual_goalkeeper_candidates_not_used_as_hard_gate", "count": visual_candidates}]
            if visual_candidates
            else []
        ),
    }


def _player_readiness(
    promoted: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    roster: dict[str, dict[str, Any]],
    fps: float,
) -> list[dict[str, Any]]:
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved_frames = {int(row["frame"]) for row in unresolved if _is_reliable_detected(row)}
    for row in promoted:
        by_player[str(row["player_id"])].append(row)
    result: list[dict[str, Any]] = []
    for player_id in sorted(roster):
        rows = _unique_reliable_rows(by_player.get(player_id, []), key_with_player=False)
        frames = sorted(int(row["frame"]) for row in rows)
        intervals = _frame_number_intervals(frames, fps)
        gaps = [
            (right["start_frame"] - left["end_frame"] - 1) / max(fps, 0.001)
            for left, right in zip(intervals, intervals[1:])
        ]
        reasons: list[str] = []
        if not rows:
            reasons.append("no_promoted_detected_observations")
        if unresolved_frames:
            reasons.append("team_contains_unresolved_detected_frames")
        result.append(
            {
                "player_id": player_id,
                "player_name": roster[player_id].get("name") or player_id,
                "detected_frames": len(rows),
                "distance_eligible_frames": sum(bool(row.get("eligible_for_distance")) for row in rows),
                "heatmap_eligible_frames": sum(bool(row.get("eligible_for_heatmap")) for row in rows),
                "coverage_denominator": "unknown",
                "detected_coverage_ratio": None,
                "distance_eligible_ratio": None,
                "heatmap_eligible_ratio": None,
                "coverage_reason": "on_pitch_interval_not_confirmed",
                "subject_fragments": len({str(row["candidate_subject_id"]) for row in rows}),
                "timeline_gaps": len(gaps),
                "longest_gap_sec": round(max(gaps, default=0.0), 3),
                "parallel_conflicts": 0,
                "readiness": "not_available" if not rows else "ready_with_review" if reasons else "ready",
                "reasons": reasons,
            }
        )
    return result


def _expected_players(
    match_doc: dict[str, Any], team_label: str, parameters: dict[str, Any]
) -> int:
    for key in ("players_per_team", "on_pitch_players_per_team"):
        value = match_doc.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    config = match_doc.get("format")
    if isinstance(config, dict):
        for key in ("players_per_team", "on_pitch_players_per_team"):
            value = config.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
    return int(parameters["expected_players_on_pitch_fallback"])


def _is_reliable_detected(row: dict[str, Any]) -> bool:
    return (
        str(row.get("status") or row.get("source") or "") == "detected"
        and str(row.get("play_area_status") or "inside_play") == "inside_play"
        and row.get("footpoint_reliable", True) is not False
    )


def _unique_reliable_rows(
    rows: list[dict[str, Any]], *, key_with_player: bool = True
) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        if not _is_reliable_detected(row):
            continue
        key = (
            str(row.get("player_id") or "") if key_with_player else "",
            int(row["frame"]),
            str(row.get("tracklet_id") or row.get("candidate_subject_id") or ""),
        )
        current = unique.get(key)
        if current is None or _observation_rank(row) < _observation_rank(current):
            unique[key] = row
    return [unique[key] for key in sorted(unique)]


def _frame_number_intervals(frames: list[int], fps: float) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for frame in sorted(set(frames)):
        if intervals and int(intervals[-1]["end_frame"]) + 1 == frame:
            intervals[-1]["end_frame"] = frame
            intervals[-1]["frames"] += 1
        else:
            intervals.append({"start_frame": frame, "end_frame": frame, "frames": 1})
    for interval in intervals:
        interval["duration_sec"] = round(int(interval["frames"]) / max(fps, 0.001), 3)
    return intervals


def _is_trusted_goalkeeper(player: dict[str, Any]) -> bool:
    values = {
        str(player.get("role") or "").strip().lower(),
        str(player.get("number") or "").strip().lower(),
        str(player.get("position") or "").strip().lower(),
    }
    return bool(values & {"goalkeeper", "keeper", "gk"})


def _observation_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if str(row.get("status") or "") == "detected" else 1,
        0 if str(row.get("play_area_status") or "inside_play") == "inside_play" else 1,
        0 if row.get("eligible_for_distance") else 1,
        -float(row.get("confidence") or 0.0),
        str(row.get("candidate_subject_id") or ""),
        str(row.get("tracklet_id") or ""),
    )


def _distance(left: Any, right: Any) -> float | None:
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
        return None
    if len(left) < 2 or len(right) < 2:
        return None
    return hypot(float(left[0]) - float(right[0]), float(left[1]) - float(right[1]))


def _bbox_iou(left: Any, right: Any) -> float | None:
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
        return None
    if len(left) < 4 or len(right) < 4:
        return None
    x1 = max(float(left[0]), float(right[0]))
    y1 = max(float(left[1]), float(right[1]))
    x2 = min(float(left[2]), float(right[2]))
    y2 = min(float(left[3]), float(right[3]))
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, float(left[2]) - float(left[0])) * max(0.0, float(left[3]) - float(left[1]))
    right_area = max(0.0, float(right[2]) - float(right[0])) * max(0.0, float(right[3]) - float(right[1]))
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _without_technical_timestamps(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_technical_timestamps(item)
            for key, item in value.items()
            if key not in {"generated_at", "updated_at", "created_at"}
        }
    if isinstance(value, list):
        return [_without_technical_timestamps(item) for item in value]
    return value
