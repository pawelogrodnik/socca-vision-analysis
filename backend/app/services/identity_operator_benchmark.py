from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from math import hypot
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_operator_benchmark"
ALGORITHM_VERSION = "0.1.0"
POSITION_DISAGREEMENT_M = 3.0
LARGE_JUMP_SPEED_MPS = 12.0
MAX_DIFF_GROUP_GAP_FRAMES = 5


def build_identity_operator_benchmark(
    *,
    production_timeline: dict[str, Any],
    candidate_timeline: dict[str, Any],
    match_doc: dict[str, Any],
    candidate_manifest: dict[str, Any] | None = None,
    promotion_plan: dict[str, Any] | None = None,
    review_decisions: dict[str, Any] | None = None,
    label: str,
    held_out: bool = False,
    start_sec: float = 0.0,
    max_seconds: float = 0.0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    fps = _fps(candidate_timeline, production_timeline, match_doc)
    source_duration_sec = _duration(match_doc, candidate_timeline, production_timeline, fps)
    window_start_frame = max(0, int(round(start_sec * fps)))
    window_end_frame = (
        window_start_frame + max(1, int(round(max_seconds * fps))) - 1
        if max_seconds > 0.0
        else None
    )
    duration_sec = (
        min(max_seconds, max(0.0, source_duration_sec - start_sec))
        if max_seconds > 0.0
        else max(0.0, source_duration_sec - start_sec)
    )
    roster = _roster(match_doc)
    production = _filter_window(
        _normalize_production(production_timeline),
        window_start_frame,
        window_end_frame,
    )
    candidate = _filter_window(
        _normalize_candidate(candidate_timeline),
        window_start_frame,
        window_end_frame,
    )
    production_observations = _observation_count(production)
    production_baseline_available = production_observations > 0
    diff_rows = _diff_rows(production, candidate) if production_baseline_available else []
    cards = _group_diff_cards(diff_rows, fps, roster)
    cards.extend(_large_jump_cards(candidate, fps, roster))
    cards.extend(_boundary_cards(production, candidate, fps, roster))
    cards = _deduplicate_cards(cards)
    parallel_conflicts = _parallel_conflicts(candidate_timeline)
    manifest = candidate_manifest or {}
    promotion = promotion_plan or {}
    manifest_coverage = manifest.get("coverage") if isinstance(manifest.get("coverage"), dict) else {}
    promotion_coverage = promotion.get("coverage") if isinstance(promotion.get("coverage"), dict) else {}
    eligible = int(
        promotion_coverage.get("promoted_reliable_detected_team_observations")
        or promotion_coverage.get("promoted_detected_frames")
        or manifest_coverage.get("eligible_observations")
        or _observation_count(candidate)
    )
    unresolved = int(
        promotion_coverage.get("unresolved_detected_frames")
        or manifest_coverage.get("unresolved_fragments")
        or 0
    )
    excluded = int(manifest_coverage.get("excluded_fragments") or 0)
    denominator = int(
        promotion_coverage.get("all_reliable_detected_team_observations")
        or eligible + unresolved + excluded
    )
    per_player = _player_metrics(production, candidate, fps, duration_sec, roster)
    decisions = (review_decisions or {}).get("decisions") or []
    telemetry = (review_decisions or {}).get("operator_telemetry") or {}
    structural_conflicts = len(promotion.get("structural_subjects") or [])
    hard_conflicts = len(manifest.get("hard_conflicts") or [])
    reviewable = sum(bool(card.get("requires_human_review")) for card in cards)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "benchmark": {
            "label": label,
            "held_out": held_out,
            "mode": (
                "production_vs_candidate"
                if production_baseline_available
                else "candidate_safety_audit"
            ),
            "production_baseline_available": production_baseline_available,
            "fps": fps,
            "duration_sec": duration_sec,
            "source_duration_sec": source_duration_sec,
            "start_sec": start_sec,
            "max_seconds": max_seconds,
            "start_frame": window_start_frame,
            "end_frame": window_end_frame,
        },
        "source": {
            "production_timeline_digest": _digest(production_timeline),
            "candidate_timeline_digest": _digest(candidate_timeline),
            "candidate_manifest_digest": _digest(manifest),
            "promotion_plan_digest": _digest(promotion),
        },
        "operator_telemetry": telemetry,
        "metrics": {
            "manual_review_time_sec": float(telemetry.get("active_review_seconds") or 0.0),
            "review_telemetry_available": bool(telemetry.get("sessions")),
            "manual_decisions": len(decisions),
            "candidate_subjects_reviewed": len(decisions),
            "subjects_assigned": sum(row.get("decision") != "mark_unresolved" for row in decisions),
            "subjects_unresolved": sum(row.get("decision") == "mark_unresolved" for row in decisions),
            "promoted_detected_ratio": round(eligible / denominator, 6) if denominator else None,
            "unresolved_detected_ratio": round(unresolved / denominator, 6) if denominator else None,
            "false_assignment_count": None,
            "parallel_conflict_count": len(parallel_conflicts),
            "structural_conflict_count": structural_conflicts + hard_conflicts,
            "difference_cards": len(cards),
            "human_review_cards": reviewable,
            "production_observations": production_observations,
            "candidate_observations": _observation_count(candidate),
        },
        "coverage": {
            "denominator_semantics": "full_video_proxy_until_player_presence_is_human_verified",
            "players": per_player,
            "distribution": _coverage_distribution(per_player),
        },
        "parallel_conflicts": parallel_conflicts,
        "warnings": (
            []
            if production_baseline_available
            else [
                {
                    "code": "production_player_timeline_unavailable",
                    "message": (
                        "The match has no production real-player timeline. "
                        "Only candidate safety risks are included in the audit."
                    ),
                }
            ]
        ),
        "cards": cards,
        "review_contract": _review_contract(production_baseline_available),
    }


def _review_contract(production_baseline_available: bool) -> dict[str, Any]:
    if not production_baseline_available:
        return {
            "allowed_decisions": ["candidate_correct", "candidate_wrong", "unclear"],
            "decision_meaning": {
                "candidate_correct": "candidate preserves the real player at this risk boundary",
                "candidate_wrong": "candidate assigns the wrong real player or an impossible transition",
                "unclear": "visual evidence is insufficient",
            },
        }
    return {
        "allowed_decisions": ["prefer_candidate", "keep_production", "both_wrong", "unclear"],
        "decision_meaning": {
            "prefer_candidate": "candidate preserves the real player better",
            "keep_production": "production preserves the real player better",
            "both_wrong": "neither side represents the real player correctly",
            "unclear": "visual evidence is insufficient",
        },
    }


def _filter_window(
    players: dict[str, list[dict[str, Any]]],
    start_frame: int,
    end_frame: int | None,
) -> dict[str, list[dict[str, Any]]]:
    return {
        player_id: [
            row
            for row in rows
            if row["frame"] >= start_frame and (end_frame is None or row["frame"] <= end_frame)
        ]
        for player_id, rows in players.items()
    }


def _normalize_production(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    players = document.get("players") if isinstance(document.get("players"), dict) else {}
    return {
        str(player_id): sorted(
            [_clean_row(row) for row in player.get("rows") or [] if isinstance(row, dict) and row.get("frame") is not None],
            key=lambda row: row["frame"],
        )
        for player_id, player in players.items()
    }


def _normalize_candidate(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for player in document.get("players") or []:
        if not isinstance(player, dict) or not player.get("player_id"):
            continue
        result[str(player["player_id"])] = sorted(
            [_clean_row(row) for row in player.get("observations") or [] if isinstance(row, dict) and row.get("frame") is not None],
            key=lambda row: row["frame"],
        )
    return result


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "frame": int(row["frame"]),
        "time_sec": float(row.get("time_sec") or 0.0),
        "bbox_xyxy": row.get("bbox_xyxy"),
        "pitch_m": row.get("pitch_m"),
        "status": str(row.get("status") or row.get("source") or "unknown"),
        "tracklet_id": str(row.get("tracklet_id") or ""),
        "subject_id": str(row.get("candidate_subject_id") or row.get("_stable_subject_id") or ""),
        "confidence": float(row.get("confidence") or 0.0),
    }


def _diff_rows(
    production: dict[str, list[dict[str, Any]]],
    candidate: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for player_id in sorted(set(production) | set(candidate)):
        left = {row["frame"]: row for row in production.get(player_id, [])}
        right = {row["frame"]: row for row in candidate.get(player_id, [])}
        for frame in sorted(set(left) | set(right)):
            production_row = left.get(frame)
            candidate_row = right.get(frame)
            if production_row is None:
                kind = "candidate_only"
                distance = None
            elif candidate_row is None:
                kind = "production_only"
                distance = None
            else:
                distance = _pitch_distance(production_row, candidate_row)
                if distance is not None and distance >= POSITION_DISAGREEMENT_M:
                    kind = "position_disagreement"
                elif production_row["status"] != candidate_row["status"]:
                    kind = "status_disagreement"
                else:
                    continue
            result.append({
                "player_id": player_id,
                "frame": frame,
                "kind": kind,
                "pitch_distance_m": distance,
                "production": production_row,
                "candidate": candidate_row,
            })
    return result


def _group_diff_cards(rows: list[dict[str, Any]], fps: float, roster: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["player_id"], row["kind"])].append(row)
    cards: list[dict[str, Any]] = []
    for (player_id, kind), values in sorted(grouped.items()):
        values.sort(key=lambda row: row["frame"])
        sequence: list[dict[str, Any]] = []
        for value in values:
            if sequence and value["frame"] - sequence[-1]["frame"] > MAX_DIFF_GROUP_GAP_FRAMES:
                cards.append(_diff_card(player_id, kind, sequence, fps, roster))
                sequence = []
            sequence.append(value)
        if sequence:
            cards.append(_diff_card(player_id, kind, sequence, fps, roster))
    return cards


def _diff_card(
    player_id: str,
    kind: str,
    sequence: list[dict[str, Any]],
    fps: float,
    roster: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sample = max(sequence, key=lambda row: float(row.get("pitch_distance_m") or 0.0)) if kind == "position_disagreement" else sequence[len(sequence) // 2]
    duration = (sequence[-1]["frame"] - sequence[0]["frame"] + 1) / fps
    max_distance = max((float(row.get("pitch_distance_m") or 0.0) for row in sequence), default=0.0)
    severity = "high" if max_distance >= 8.0 or duration >= 2.0 else "medium" if duration >= 0.5 else "low"
    return _card(
        category=kind,
        severity=severity,
        player_id=player_id,
        roster=roster,
        start_frame=sequence[0]["frame"],
        end_frame=sequence[-1]["frame"],
        sample_frame=sample["frame"],
        production=sample.get("production"),
        candidate=sample.get("candidate"),
        evidence={"observations": len(sequence), "duration_sec": round(duration, 3), "max_pitch_distance_m": round(max_distance, 3)},
        requires_human_review=severity != "low" or kind in {"candidate_only", "production_only"},
    )


def _large_jump_cards(candidate: dict[str, list[dict[str, Any]]], fps: float, roster: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for player_id, rows in sorted(candidate.items()):
        for left, right in zip(rows, rows[1:]):
            frame_gap = right["frame"] - left["frame"]
            distance = _pitch_distance(left, right)
            if frame_gap <= 0 or frame_gap > int(2 * fps) or distance is None:
                continue
            speed = distance / (frame_gap / fps)
            if speed <= LARGE_JUMP_SPEED_MPS:
                continue
            cards.append(_card(
                category="candidate_large_jump",
                severity="high",
                player_id=player_id,
                roster=roster,
                start_frame=left["frame"],
                end_frame=right["frame"],
                sample_frame=right["frame"],
                production=left,
                candidate=right,
                evidence={
                    "comparison_semantics": "candidate_before_after",
                    "distance_m": round(distance, 3),
                    "required_speed_mps": round(speed, 3),
                    "frame_gap": frame_gap,
                },
                requires_human_review=True,
            ))
    return cards


def _boundary_cards(
    production: dict[str, list[dict[str, Any]]],
    candidate: dict[str, list[dict[str, Any]]],
    fps: float,
    roster: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for player_id, rows in sorted(candidate.items()):
        if not rows:
            continue
        production_map = {row["frame"]: row for row in production.get(player_id, [])}
        for name, row in (("candidate_start_boundary", rows[0]), ("candidate_end_boundary", rows[-1])):
            cards.append(_card(
                category=name,
                severity="audit",
                player_id=player_id,
                roster=roster,
                start_frame=row["frame"],
                end_frame=row["frame"],
                sample_frame=row["frame"],
                production=production_map.get(row["frame"]),
                candidate=row,
                evidence={"time_sec": round(row["frame"] / fps, 3)},
                requires_human_review=True,
            ))
    return cards


def _card(**values: Any) -> dict[str, Any]:
    identity = ":".join(str(values[key]) for key in ("category", "player_id", "start_frame", "end_frame"))
    player = values.pop("roster").get(values["player_id"], {})
    return {
        "card_key": f"operator-diff:v1:{hashlib.sha256(identity.encode()).hexdigest()}",
        "player_name": player.get("name") or values["player_id"],
        "team_label": player.get("team_label"),
        **values,
    }


def _deduplicate_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {str(card["card_key"]): card for card in cards}
    return sorted(by_key.values(), key=lambda card: (int(card["sample_frame"]), str(card["player_name"]), str(card["category"])))


def _parallel_conflicts(document: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for player in document.get("players") or []:
        if not isinstance(player, dict):
            continue
        player_id = str(player.get("player_id") or "")
        for row in player.get("observations") or []:
            if isinstance(row, dict) and row.get("frame") is not None:
                groups[(player_id, int(row["frame"]))].append(row)
    return [
        {"player_id": player_id, "frame": frame, "observations": len(rows)}
        for (player_id, frame), rows in sorted(groups.items())
        if len(rows) > 1
    ]


def _player_metrics(
    production: dict[str, list[dict[str, Any]]],
    candidate: dict[str, list[dict[str, Any]]],
    fps: float,
    duration_sec: float,
    roster: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    denominator_frames = max(1, int(round(duration_sec * fps)))
    for player_id in sorted(set(production) | set(candidate)):
        candidate_frames = sorted({row["frame"] for row in candidate.get(player_id, [])})
        production_frames = {row["frame"] for row in production.get(player_id, [])}
        longest_gap = max((right - left - 1 for left, right in zip(candidate_frames, candidate_frames[1:])), default=0)
        player = roster.get(player_id, {})
        result.append({
            "player_id": player_id,
            "player_name": player.get("name") or player_id,
            "team_label": player.get("team_label"),
            "production_frames": len(production_frames),
            "candidate_frames": len(candidate_frames),
            "shared_frames": len(production_frames & set(candidate_frames)),
            "full_video_coverage_ratio": round(len(candidate_frames) / denominator_frames, 6),
            "longest_candidate_gap_sec": round(longest_gap / fps, 3),
            "candidate_start_frame": candidate_frames[0] if candidate_frames else None,
            "candidate_end_frame": candidate_frames[-1] if candidate_frames else None,
        })
    return result


def _coverage_distribution(players: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted(float(row["full_video_coverage_ratio"]) for row in players)
    if not values:
        return {"min": None, "median": None, "max": None}
    return {"min": values[0], "median": values[len(values) // 2], "max": values[-1]}


def _pitch_distance(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    a, b = left.get("pitch_m"), right.get("pitch_m")
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) < 2 or len(b) < 2:
        return None
    return hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _roster(match_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, team in enumerate(match_doc.get("teams") or []):
        if not isinstance(team, dict):
            continue
        label = "A" if index == 0 else "B" if index == 1 else "U"
        for player in team.get("players") or []:
            if isinstance(player, dict) and player.get("id"):
                result[str(player["id"])] = {**player, "team_label": label}
    return result


def _fps(candidate: dict[str, Any], production: dict[str, Any], match_doc: dict[str, Any]) -> float:
    video = match_doc.get("video") if isinstance(match_doc.get("video"), dict) else {}
    return max(float(candidate.get("fps") or production.get("fps") or video.get("fps") or 30.0), 0.001)


def _duration(match_doc: dict[str, Any], candidate: dict[str, Any], production: dict[str, Any], fps: float) -> float:
    video = match_doc.get("video") if isinstance(match_doc.get("video"), dict) else {}
    explicit = float(video.get("duration_sec") or 0.0)
    if explicit > 0:
        return round(explicit, 6)
    frames = [row["frame"] for rows in (*_normalize_production(production).values(), *_normalize_candidate(candidate).values()) for row in rows]
    return round((max(frames) + 1) / fps, 6) if frames else 0.0


def _observation_count(value: Any) -> int:
    if isinstance(value, dict) and isinstance(value.get("players"), list):
        return sum(len(row.get("observations") or []) for row in value["players"] if isinstance(row, dict))
    if isinstance(value, dict) and all(isinstance(rows, list) for rows in value.values()):
        return sum(len(rows) for rows in value.values())
    return 0


def _digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
