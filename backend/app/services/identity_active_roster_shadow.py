from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_active_roster_shadow"
ALGORITHM_VERSION = "0.1.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "max_active_per_team": 7,
    "continuity_window_frames": 15,
    "continuity_bonus": 0.35,
    "duplicate_bbox_iou": 0.95,
    "duplicate_bbox_containment": 0.90,
    "duplicate_pitch_distance_m": 0.10,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_identity_active_roster_shadow(
    candidate_doc: dict[str, Any],
    candidate_overlay_doc: dict[str, Any],
    *,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
    include_overlay: bool = False,
) -> dict[str, dict[str, Any]]:
    """Select at most seven visual candidates per team without changing identity."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or now_iso()
    players = list(candidate_overlay_doc.get("players") or [])
    candidate_by_subject = {
        str(row.get("candidate_subject_id") or ""): row
        for row in candidate_doc.get("subjects") or []
    }
    rows_by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for player in players:
        subject_id = str(player.get("candidate_subject_id") or player.get("stable_subject_id") or "")
        metadata = candidate_by_subject.get(subject_id) or {}
        for position in player.get("overlay_positions") or []:
            rows_by_frame[int(position.get("frame") or 0)].append(
                {
                    **position,
                    "candidate_subject_id": subject_id,
                    "candidate_player_id": player.get("stable_player_id"),
                    "team_label": str(player.get("team_label") or "U"),
                    "role": str(player.get("role") or "field_player"),
                    "anchored": (
                        bool(player.get("anchored"))
                        if "anchored" in player
                        else bool(metadata.get("production_subject_ids"))
                    ),
                    "requires_review": bool(player.get("requires_review")),
                }
            )

    decisions: dict[tuple[str, int], dict[str, Any]] = {}
    before_counts: Counter[tuple[int, str]] = Counter()
    after_counts: Counter[tuple[int, str]] = Counter()
    last_active_frame: dict[str, int] = {}
    suppression_counts: Counter[str] = Counter()
    duplicate_samples: list[dict[str, Any]] = []
    overflow_samples: list[dict[str, Any]] = []

    for frame, frame_rows in sorted(rows_by_frame.items()):
        by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in frame_rows:
            by_team[str(row["team_label"])].append(row)
        for team, team_rows in sorted(by_team.items()):
            before_counts[(frame, team)] = len(team_rows)
            if team not in {"A", "B"}:
                for row in team_rows:
                    _record_decision(decisions, row, frame, active=False, reason="unknown_team_not_roster")
                    suppression_counts["unknown_team_not_roster"] += 1
                continue

            duplicate_ranked = sorted(
                team_rows,
                key=lambda row: _duplicate_rank_key(
                    row,
                    frame=frame,
                    last_active_frame=last_active_frame,
                    params=params,
                ),
            )
            deduplicated: list[dict[str, Any]] = []
            for row in duplicate_ranked:
                duplicate = next((kept for kept in deduplicated if _same_observation(row, kept, params)), None)
                if duplicate is None:
                    deduplicated.append(row)
                    continue
                _record_decision(decisions, row, frame, active=False, reason="duplicate_same_observation")
                suppression_counts["duplicate_same_observation"] += 1
                if len(duplicate_samples) < 100:
                    duplicate_samples.append(
                        {
                            "frame": frame,
                            "team_label": team,
                            "kept_subject_id": duplicate["candidate_subject_id"],
                            "suppressed_subject_id": row["candidate_subject_id"],
                        }
                    )

            deduplicated.sort(
                key=lambda row: _rank_key(
                    row,
                    frame=frame,
                    last_active_frame=last_active_frame,
                    params=params,
                )
            )
            limit = int(params["max_active_per_team"])
            active_rows = deduplicated[:limit]
            overflow_rows = deduplicated[limit:]
            after_counts[(frame, team)] = len(active_rows)
            for row in active_rows:
                _record_decision(decisions, row, frame, active=True, reason="selected")
                last_active_frame[str(row["candidate_subject_id"])] = frame
            for row in overflow_rows:
                _record_decision(decisions, row, frame, active=False, reason="team_active_cap_lower_rank")
                suppression_counts["team_active_cap_lower_rank"] += 1
                if len(overflow_samples) < 100:
                    overflow_samples.append(
                        {
                            "frame": frame,
                            "team_label": team,
                            "candidate_subject_id": row["candidate_subject_id"],
                            "candidate_player_id": row.get("candidate_player_id"),
                            "score": round(_score(row, frame=frame, last_active_frame=last_active_frame, params=params), 4),
                        }
                    )

    subjects = _subject_decision_rows(candidate_by_subject, decisions)
    summary = _summary(
        rows_by_frame,
        decisions,
        before_counts=before_counts,
        after_counts=after_counts,
        suppression_counts=suppression_counts,
        max_active=int(params["max_active_per_team"]),
    )
    roster = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_active_roster_validation",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": params},
        "source": {"candidate_algorithm": candidate_doc.get("algorithm") or {}},
        "safety": {
            "mutates_production_identity": False,
            "eligible_for_player_stats": False,
            "eligible_for_roster_assignment": False,
        },
        "summary": summary,
        "subjects": subjects,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_active_roster_validation",
        "algorithm": roster["algorithm"],
        "status": "ready_for_visual_validation" if summary["frames_over_cap_after"] == 0 else "blocked",
        "summary": summary,
        "gates": {
            "production_identity_untouched": True,
            "active_positions_excluded_from_statistics": True,
            "never_more_than_configured_team_size": summary["frames_over_cap_after"] == 0,
            "does_not_fabricate_candidate_positions": True,
        },
        "duplicate_samples": duplicate_samples,
        "overflow_samples": overflow_samples,
        "limitations": [
            "This selector only changes the shadow visualization candidate set.",
            "Suppressed candidates remain available for review and are not deleted.",
            "The selector must not feed player statistics before a separate promotion gate.",
        ],
    }
    documents = {
        "identity_active_roster_shadow": roster,
        "identity_active_roster_shadow_report": report,
    }
    if include_overlay:
        documents["identity_active_roster_shadow_overlay"] = _filtered_overlay(
            candidate_overlay_doc,
            decisions,
            summary=summary,
            generated_at=generated,
        )
    return documents


def _rank_key(
    row: dict[str, Any],
    *,
    frame: int,
    last_active_frame: dict[str, int],
    params: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        -_score(row, frame=frame, last_active_frame=last_active_frame, params=params),
        str(row.get("candidate_subject_id") or ""),
    )


def _duplicate_rank_key(
    row: dict[str, Any],
    *,
    frame: int,
    last_active_frame: dict[str, int],
    params: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        -int(bool(row.get("anchored"))),
        -int(bool(row.get("eligible_for_distance") or row.get("eligible_for_heatmap"))),
        -int(str(row.get("source") or "detected") == "detected"),
        -_score(row, frame=frame, last_active_frame=last_active_frame, params=params),
        str(row.get("candidate_subject_id") or ""),
    )


def _score(
    row: dict[str, Any],
    *,
    frame: int,
    last_active_frame: dict[str, int],
    params: dict[str, Any],
) -> float:
    source = str(row.get("source") or "detected")
    score = {"detected": 3.0, "occluded": 1.8, "predicted": 1.5}.get(source, 0.0)
    score += max(0.0, min(1.0, float(row.get("confidence") or 0.0)))
    if row.get("eligible_for_distance") or row.get("eligible_for_heatmap"):
        score += 1.5
    if row.get("play_area_status") == "inside_play":
        score += 0.35
    if row.get("footpoint_reliable"):
        score += 0.2
    if row.get("appearance_reliable"):
        score += 0.15
    if row.get("anchored"):
        score += 0.15
    if row.get("role") == "goalkeeper":
        score += 0.05
    if row.get("requires_review"):
        score -= 0.05
    previous = last_active_frame.get(str(row.get("candidate_subject_id") or ""))
    window = max(1, int(params["continuity_window_frames"]))
    if previous is not None and 0 <= frame - previous <= window:
        score += float(params["continuity_bonus"]) * (1.0 - ((frame - previous) / (window + 1)))
    return score


def _same_observation(left: dict[str, Any], right: dict[str, Any], params: dict[str, Any]) -> bool:
    left_tracklet = left.get("tracklet_id")
    right_tracklet = right.get("tracklet_id")
    if left_tracklet and right_tracklet and str(left_tracklet) == str(right_tracklet):
        return True
    iou, containment = _bbox_overlap(left.get("bbox_xyxy"), right.get("bbox_xyxy"))
    pitch_distance = _pitch_distance(left.get("pitch_m"), right.get("pitch_m"))
    return (
        (not left.get("anchored") or not right.get("anchored"))
        and iou >= float(params["duplicate_bbox_iou"])
        and pitch_distance is not None
        and pitch_distance <= float(params["duplicate_pitch_distance_m"])
        and containment >= float(params["duplicate_bbox_containment"])
    )


def _bbox_overlap(left: Any, right: Any) -> tuple[float, float]:
    if not _valid_vector(left, 4) or not _valid_vector(right, 4):
        return 0.0, 0.0
    lx1, ly1, lx2, ly2 = (float(value) for value in left[:4])
    rx1, ry1, rx2, ry2 = (float(value) for value in right[:4])
    intersection = max(0.0, min(lx2, rx2) - max(lx1, rx1)) * max(0.0, min(ly2, ry2) - max(ly1, ry1))
    left_area = max(0.0, lx2 - lx1) * max(0.0, ly2 - ly1)
    right_area = max(0.0, rx2 - rx1) * max(0.0, ry2 - ry1)
    union = left_area + right_area - intersection
    iou = intersection / union if union > 0 else 0.0
    containment = intersection / min(left_area, right_area) if min(left_area, right_area) > 0 else 0.0
    return iou, containment


def _pitch_distance(left: Any, right: Any) -> float | None:
    if not _valid_vector(left, 2) or not _valid_vector(right, 2):
        return None
    return math.hypot(float(left[0]) - float(right[0]), float(left[1]) - float(right[1]))


def _record_decision(
    decisions: dict[tuple[str, int], dict[str, Any]],
    row: dict[str, Any],
    frame: int,
    *,
    active: bool,
    reason: str,
) -> None:
    decisions[(str(row["candidate_subject_id"]), frame)] = {
        "active": active,
        "reason": reason,
    }


def _subject_decision_rows(
    candidate_by_subject: dict[str, dict[str, Any]],
    decisions: dict[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    by_subject: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for (subject_id, frame), decision in decisions.items():
        by_subject[subject_id].append((frame, decision))
    rows: list[dict[str, Any]] = []
    for subject_id, frame_decisions in sorted(by_subject.items()):
        metadata = candidate_by_subject.get(subject_id) or {}
        frame_decisions.sort(key=lambda item: item[0])
        active_frames = sum(bool(decision["active"]) for _, decision in frame_decisions)
        rows.append(
            {
                "candidate_subject_id": subject_id,
                "candidate_player_id": metadata.get("candidate_player_id"),
                "team_label": metadata.get("team_label"),
                "active_frames": active_frames,
                "suppressed_frames": len(frame_decisions) - active_frames,
                "decision_runs": _compress_decisions(frame_decisions),
            }
        )
    return rows


def _compress_decisions(frame_decisions: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for frame, decision in frame_decisions:
        if (
            runs
            and frame == int(runs[-1]["end_frame"]) + 1
            and bool(runs[-1]["active"]) == bool(decision["active"])
            and runs[-1]["reason"] == decision["reason"]
        ):
            runs[-1]["end_frame"] = frame
            runs[-1]["frames"] += 1
            continue
        runs.append(
            {
                "start_frame": frame,
                "end_frame": frame,
                "frames": 1,
                "active": bool(decision["active"]),
                "reason": decision["reason"],
            }
        )
    return runs


def _summary(
    rows_by_frame: dict[int, list[dict[str, Any]]],
    decisions: dict[tuple[str, int], dict[str, Any]],
    *,
    before_counts: Counter[tuple[int, str]],
    after_counts: Counter[tuple[int, str]],
    suppression_counts: Counter[str],
    max_active: int,
) -> dict[str, Any]:
    reliable_detected = 0
    reliable_detected_active = 0
    for frame, rows in rows_by_frame.items():
        for row in rows:
            if str(row.get("team_label") or "U") not in {"A", "B"}:
                continue
            if str(row.get("source") or "detected") != "detected":
                continue
            if not (row.get("eligible_for_distance") or row.get("eligible_for_heatmap")):
                continue
            reliable_detected += 1
            if (decisions.get((str(row["candidate_subject_id"]), frame)) or {}).get("active"):
                reliable_detected_active += 1
    max_before = {
        team: max((count for (frame, label), count in before_counts.items() if label == team), default=0)
        for team in ("A", "B")
    }
    max_after = {
        team: max((count for (frame, label), count in after_counts.items() if label == team), default=0)
        for team in ("A", "B")
    }
    frames_before = len(
        {frame for (frame, team), count in before_counts.items() if team in {"A", "B"} and count > max_active}
    )
    frames_after = len(
        {frame for (frame, team), count in after_counts.items() if team in {"A", "B"} and count > max_active}
    )
    active_positions = sum(bool(value["active"]) for value in decisions.values())
    return {
        "candidate_positions": sum(len(rows) for rows in rows_by_frame.values()),
        "active_positions": active_positions,
        "suppressed_positions": len(decisions) - active_positions,
        "suppression_counts": dict(sorted(suppression_counts.items())),
        "max_active_before": max_before,
        "max_active_after": max_after,
        "frames_over_cap_before": frames_before,
        "frames_over_cap_after": frames_after,
        "reliable_detected_positions": reliable_detected,
        "reliable_detected_retained": reliable_detected_active,
        "reliable_detected_retention_ratio": round(
            reliable_detected_active / reliable_detected,
            4,
        ) if reliable_detected else 1.0,
        "product_readiness": "shadow_ready_for_visual_validation",
        "promotion_readiness": "blocked_pending_identity_fragmentation_validation",
    }


def _filtered_overlay(
    candidate_overlay_doc: dict[str, Any],
    decisions: dict[tuple[str, int], dict[str, Any]],
    *,
    summary: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    players: list[dict[str, Any]] = []
    for player in candidate_overlay_doc.get("players") or []:
        subject_id = str(player.get("candidate_subject_id") or player.get("stable_subject_id") or "")
        positions = [
            position
            for position in player.get("overlay_positions") or []
            if (decisions.get((subject_id, int(position.get("frame") or 0))) or {}).get("active")
        ]
        if positions:
            players.append({**player, "overlay_positions": positions, "overlay_positions_count": len(positions)})
    return {
        **candidate_overlay_doc,
        "generated_at": generated_at,
        "mode": "shadow_active_roster_validation",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "summary": {**(candidate_overlay_doc.get("summary") or {}), **summary},
        "players": players,
    }


def _valid_vector(value: Any, length: int) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= length
