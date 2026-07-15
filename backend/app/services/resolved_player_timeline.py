from __future__ import annotations

from collections import defaultdict
from math import hypot
from pathlib import Path
from statistics import median
from typing import Any


MAX_TRUSTED_GAP_SEC = 2.0
MAX_PRESENCE_ASSIGNMENT_HOLE_SEC = 2.0
MAX_PRESENCE_FRAGMENT_BRIDGE_SEC = 5.0
MAX_PRESENCE_STRONG_BRIDGE_SEC = 20.0
MAX_PRESENCE_BOUNDARY_SPEED_MPS = 10.0
MIN_PRESENCE_BRIDGE_ROW_COVERAGE = 0.5
MIN_PRESENCE_STRONG_BRIDGE_ROW_COVERAGE = 0.85
GOALKEEPER_OPPOSITE_ZONE_RATIO = 0.28
GOALKEEPER_JUMP_DISTANCE_M = 12.0
GOALKEEPER_JUMP_MAX_GAP_SEC = 2.0


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _distance(a: Any, b: Any) -> float | None:
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) < 2 or len(b) < 2:
        return None
    return hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _slot_lookup(global_identity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for slot in global_identity.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        for key in ["stable_subject_id", "stable_player_id", "slot_id"]:
            value = str(slot.get(key) or "")
            if value:
                result[value] = slot
    return result


def _assignment_interval(assignment: dict[str, Any], slot: dict[str, Any]) -> tuple[int, int] | None:
    start = _int_or_none(assignment.get("start_frame"))
    end = _int_or_none(assignment.get("end_frame"))
    if start is not None and end is not None and end >= start:
        return start, end

    stint_id = str(assignment.get("stint_id") or "")
    if stint_id:
        for stint in slot.get("stints") or []:
            if not isinstance(stint, dict) or str(stint.get("stint_id") or "") != stint_id:
                continue
            start = _int_or_none(stint.get("start_frame"))
            end = _int_or_none(stint.get("end_frame"))
            if start is not None and end is not None and end >= start:
                return start, end
        return None

    if str(assignment.get("assignment_scope") or "stable_slot") != "stable_slot":
        return None
    frames = [
        int(row["frame"])
        for row in slot.get("overlay_positions") or []
        if isinstance(row, dict) and isinstance(row.get("frame"), (int, float))
    ]
    return (min(frames), max(frames)) if frames else None


def _source_rank(row: dict[str, Any]) -> int:
    return {
        "detected": 4,
        "predicted": 3,
        "missing": 2,
        "ambiguous": 1,
    }.get(str(row.get("source") or row.get("status") or ""), 0)


def _candidate_rank(row: dict[str, Any], previous_pitch: Any = None) -> tuple[Any, ...]:
    distance = _distance(previous_pitch, row.get("pitch_m"))
    continuity = -distance if distance is not None else -1_000_000.0
    return (
        _source_rank(row),
        int(row.get("play_area_status", "inside_play") == "inside_play"),
        int(row.get("visual_trusted") is not False),
        _number(row.get("_assignment_confidence")),
        _number(row.get("confidence")),
        continuity,
        str(row.get("_assignment_key") or ""),
    )


def _is_goalkeeper(metadata: dict[str, Any]) -> bool:
    values = {
        str(metadata.get("player_role") or "").strip().lower(),
        str(metadata.get("player_number") or "").strip().lower(),
    }
    return bool(values & {"goalkeeper", "keeper", "gk"})


def _trusted_detected_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("source") or row.get("status") or "") == "detected"
        and row.get("pitch_m")
        and row.get("play_area_status", "inside_play") == "inside_play"
    ]


def trusted_stats_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return conservative rows used for exact playing time and movement stats."""
    ordered = sorted(rows, key=lambda row: int(row.get("frame") or 0))
    detected_frames = [int(row.get("frame") or 0) for row in ordered if _source_rank(row) == 4]
    result: list[dict[str, Any]] = []
    for row in ordered:
        if row.get("play_area_status", "inside_play") != "inside_play":
            continue
        source = str(row.get("source") or row.get("status") or "")
        if source == "detected":
            result.append(row)
            continue
        short_gap_sec = row.get("short_gap_sec")
        if source == "missing" and isinstance(short_gap_sec, (int, float)) and float(short_gap_sec) <= MAX_TRUSTED_GAP_SEC:
            result.append(row)
            continue
        if source != "predicted" or not detected_frames:
            continue
        frame = int(row.get("frame") or 0)
        previous = max((candidate for candidate in detected_frames if candidate < frame), default=None)
        following = min((candidate for candidate in detected_frames if candidate > frame), default=None)
        fps = max(_number(row.get("_fps")), 0.001)
        if previous is not None and following is not None and (following - previous) / fps <= MAX_TRUSTED_GAP_SEC:
            result.append(row)
    return result


def _presence_rank(kind: str) -> int:
    return {
        "assigned_detected": 5,
        "assigned_ambiguous": 4,
        "assigned_predicted": 3,
        "assigned_missing": 3,
        "assignment_short_gap": 2,
        "same_subject_bridge": 1,
        "same_subject_long_bridge": 1,
    }.get(kind, 0)


def _presence_kind(row: dict[str, Any]) -> str:
    source = str(row.get("source") or row.get("status") or "")
    return {
        "detected": "assigned_detected",
        "ambiguous": "assigned_ambiguous",
        "predicted": "assigned_predicted",
        "missing": "assigned_missing",
    }.get(source, "assigned_missing")


def _put_presence(
    evidence: dict[int, dict[str, Any]],
    *,
    frame: int,
    kind: str,
    subject: str,
    confidence: float = 0.0,
) -> None:
    candidate = {
        "frame": frame,
        "kind": kind,
        "subject": subject,
        "confidence": confidence,
        "rank": _presence_rank(kind),
    }
    current = evidence.get(frame)
    if current is None or (candidate["rank"], candidate["confidence"], subject) > (
        current["rank"],
        current["confidence"],
        str(current.get("subject") or ""),
    ):
        evidence[frame] = candidate


def _fragment_subject(fragment: dict[str, Any]) -> str:
    assignment = fragment.get("assignment") if isinstance(fragment.get("assignment"), dict) else {}
    return str(assignment.get("stable_subject_id") or assignment.get("stable_player_id") or "")


def _fragment_presence_interval(fragment: dict[str, Any]) -> tuple[int, int] | None:
    assignment = fragment.get("assignment") if isinstance(fragment.get("assignment"), dict) else {}
    slot = fragment.get("slot") if isinstance(fragment.get("slot"), dict) else {}
    return _assignment_interval(assignment, slot)


def _inside_play_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("frame"), (int, float))
            and row.get("play_area_status", "inside_play") == "inside_play"
        ],
        key=lambda row: int(row.get("frame") or 0),
    )


def _has_competing_owner(
    ownership: dict[str, list[tuple[int, int, str]]],
    *,
    subject: str,
    player_id: str,
    start_frame: int,
    end_frame: int,
) -> bool:
    return any(
        owner != player_id and max(start_frame, start) <= min(end_frame, end)
        for start, end, owner in ownership.get(subject, [])
    )


def _boundary_is_continuous(left: dict[str, Any], right: dict[str, Any], fps: float) -> bool:
    frame_gap = int(right.get("frame") or 0) - int(left.get("frame") or 0)
    if frame_gap <= 0:
        return False
    distance = _distance(left.get("pitch_m"), right.get("pitch_m"))
    if distance is None:
        return False
    gap_sec = frame_gap / max(fps, 0.001)
    return distance <= max(3.0, MAX_PRESENCE_BOUNDARY_SPEED_MPS * gap_sec)


def calculate_timeline_presence(
    timeline: dict[str, Any],
    *,
    duration_sec: float | None = None,
) -> dict[str, Any]:
    """Infer on-pitch presence without adding uncertain rows to movement or heatmaps."""
    fps = max(_number(timeline.get("fps")), 0.001)
    max_frame = int(duration_sec * fps) - 1 if isinstance(duration_sec, (int, float)) and duration_sec > 0 else None
    players = timeline.get("players") if isinstance(timeline.get("players"), dict) else {}
    ownership: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    fragment_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for player_id, player in players.items():
        for fragment in player.get("fragments") or []:
            if not isinstance(fragment, dict) or fragment.get("excluded_reason"):
                continue
            interval = _fragment_presence_interval(fragment)
            subject = _fragment_subject(fragment)
            if interval is None or not subject:
                continue
            start_frame, end_frame = interval
            ownership[subject].append((start_frame, end_frame, str(player_id)))
            fragment_groups[(str(player_id), subject)].append(fragment)

    evidence_by_player: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for (player_id, subject), fragments in fragment_groups.items():
        ordered_fragments = sorted(
            fragments,
            key=lambda fragment: _fragment_presence_interval(fragment) or (0, 0),
        )
        for fragment in ordered_fragments:
            interval = _fragment_presence_interval(fragment)
            if interval is None:
                continue
            start_frame, end_frame = interval
            observed = _inside_play_rows(fragment.get("rows") or [])
            observed = [
                row
                for row in observed
                if start_frame <= int(row.get("frame") or 0) <= end_frame
                and (max_frame is None or int(row.get("frame") or 0) <= max_frame)
            ]
            for row in observed:
                frame = int(row.get("frame") or 0)
                _put_presence(
                    evidence_by_player[player_id],
                    frame=frame,
                    kind=_presence_kind(row),
                    subject=subject,
                    confidence=_number(row.get("confidence")),
                )
            max_hole_frames = int(round(MAX_PRESENCE_ASSIGNMENT_HOLE_SEC * fps))
            for left, right in zip(observed, observed[1:]):
                left_frame = int(left.get("frame") or 0)
                right_frame = int(right.get("frame") or 0)
                if right_frame - left_frame - 1 <= 0 or right_frame - left_frame - 1 > max_hole_frames:
                    continue
                if not _boundary_is_continuous(left, right, fps):
                    continue
                for frame in range(left_frame + 1, right_frame):
                    if _has_competing_owner(
                        ownership,
                        subject=subject,
                        player_id=player_id,
                        start_frame=frame,
                        end_frame=frame,
                    ):
                        continue
                    _put_presence(
                        evidence_by_player[player_id],
                        frame=frame,
                        kind="assignment_short_gap",
                        subject=subject,
                    )

        max_bridge_frames = int(round(MAX_PRESENCE_FRAGMENT_BRIDGE_SEC * fps))
        max_strong_bridge_frames = int(round(MAX_PRESENCE_STRONG_BRIDGE_SEC * fps))
        for left_fragment, right_fragment in zip(ordered_fragments, ordered_fragments[1:]):
            left_interval = _fragment_presence_interval(left_fragment)
            right_interval = _fragment_presence_interval(right_fragment)
            if left_interval is None or right_interval is None:
                continue
            gap_start = left_interval[1] + 1
            gap_end = right_interval[0] - 1
            gap_frames = gap_end - gap_start + 1
            if gap_frames <= 0 or gap_frames > max_strong_bridge_frames:
                continue
            if _has_competing_owner(
                ownership,
                subject=subject,
                player_id=player_id,
                start_frame=gap_start,
                end_frame=gap_end,
            ):
                continue
            left_rows = _inside_play_rows(left_fragment.get("rows") or [])
            right_rows = _inside_play_rows(right_fragment.get("rows") or [])
            if not left_rows or not right_rows or not _boundary_is_continuous(left_rows[-1], right_rows[0], fps):
                continue
            slot = left_fragment.get("slot") if isinstance(left_fragment.get("slot"), dict) else {}
            gap_rows = [
                row
                for row in _inside_play_rows(slot.get("overlay_positions") or [])
                if gap_start <= int(row.get("frame") or 0) <= gap_end
                and (max_frame is None or int(row.get("frame") or 0) <= max_frame)
            ]
            gap_row_frames = {int(row.get("frame") or 0) for row in gap_rows}
            bridge_coverage = len(gap_row_frames) / max(1, gap_frames)
            required_coverage = (
                MIN_PRESENCE_BRIDGE_ROW_COVERAGE
                if gap_frames <= max_bridge_frames
                else MIN_PRESENCE_STRONG_BRIDGE_ROW_COVERAGE
            )
            if bridge_coverage < required_coverage:
                continue
            bridge_kind = "same_subject_bridge" if gap_frames <= max_bridge_frames else "same_subject_long_bridge"
            for frame in range(gap_start, gap_end + 1):
                if max_frame is not None and frame > max_frame:
                    continue
                _put_presence(
                    evidence_by_player[player_id],
                    frame=frame,
                    kind=bridge_kind,
                    subject=subject,
                )

    subject_frame_groups: dict[tuple[str, int], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for player_id, evidence in evidence_by_player.items():
        for frame, item in evidence.items():
            subject_frame_groups[(str(item.get("subject") or ""), frame)].append((player_id, item))
    subject_conflicts_removed = 0
    for group in subject_frame_groups.values():
        if len(group) <= 1:
            continue
        ordered = sorted(
            group,
            key=lambda pair: (pair[1]["rank"], pair[1]["confidence"], pair[0]),
            reverse=True,
        )
        for player_id, item in ordered[1:]:
            evidence_by_player[player_id].pop(int(item["frame"]), None)
            subject_conflicts_removed += 1

    parameters = timeline.get("parameters") if isinstance(timeline.get("parameters"), dict) else {}
    players_per_team = int(parameters.get("players_per_team") or 7)
    team_frame_groups: dict[tuple[str, int], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for player_id, evidence in evidence_by_player.items():
        player = players.get(player_id) if isinstance(players.get(player_id), dict) else {}
        metadata = player.get("metadata") if isinstance(player.get("metadata"), dict) else {}
        team_label = str(metadata.get("team_label") or "U")
        for frame, item in evidence.items():
            team_frame_groups[(team_label, frame)].append((player_id, item))
    team_capacity_removed = 0
    for (team_label, _frame), group in team_frame_groups.items():
        if team_label == "U" or len(group) <= players_per_team:
            continue
        ordered = sorted(
            group,
            key=lambda pair: (pair[1]["rank"], pair[1]["confidence"], pair[0]),
            reverse=True,
        )
        for player_id, item in ordered[players_per_team:]:
            evidence_by_player[player_id].pop(int(item["frame"]), None)
            team_capacity_removed += 1

    result_players: dict[str, dict[str, Any]] = {}
    for player_id in players:
        evidence = evidence_by_player.get(str(player_id), {})
        counts: dict[str, int] = defaultdict(int)
        for item in evidence.values():
            counts[str(item.get("kind") or "unknown")] += 1
        presence_frames = len(evidence)
        observed_detected_frames = counts["assigned_detected"]
        ambiguous_frames = counts["assigned_ambiguous"]
        inferred_frames = max(0, presence_frames - observed_detected_frames)
        result_players[str(player_id)] = {
            "presence_frames": presence_frames,
            "playing_time_sec": round(presence_frames / fps, 3),
            "observed_detected_frames": observed_detected_frames,
            "ambiguous_presence_frames": ambiguous_frames,
            "inferred_presence_frames": inferred_frames,
            "assignment_short_gap_frames": counts["assignment_short_gap"],
            "same_subject_bridge_frames": counts["same_subject_bridge"],
            "same_subject_long_bridge_frames": counts["same_subject_long_bridge"],
            "evidence_counts": dict(sorted(counts.items())),
            "frame_numbers": sorted(evidence),
        }
    return {
        "method": "exact_assignment_with_continuity",
        "fps": fps,
        "players": result_players,
        "quality": {
            "subject_presence_conflicts_removed": subject_conflicts_removed,
            "team_capacity_presence_frames_removed": team_capacity_removed,
        },
    }


def _boundary_jump(fragment: dict[str, Any], others: list[dict[str, Any]], fps: float) -> bool:
    detected = _trusted_detected_rows(fragment.get("rows") or [])
    if not detected:
        return False
    start = detected[0]
    end = detected[-1]
    for other in others:
        if other is fragment:
            continue
        other_detected = _trusted_detected_rows(other.get("rows") or [])
        if not other_detected:
            continue
        for left, right in [(other_detected[-1], start), (end, other_detected[0])]:
            frame_gap = int(right.get("frame") or 0) - int(left.get("frame") or 0)
            if frame_gap < 0 or frame_gap / max(fps, 0.001) > GOALKEEPER_JUMP_MAX_GAP_SEC:
                continue
            distance = _distance(left.get("pitch_m"), right.get("pitch_m"))
            if distance is not None and distance > GOALKEEPER_JUMP_DISTANCE_M:
                return True
    return False


def _filter_goalkeeper_anomalies(
    players: dict[str, dict[str, Any]],
    *,
    pitch_length_m: float,
    fps: float,
) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    for player_id, player in players.items():
        if not _is_goalkeeper(player.get("metadata") or {}):
            continue
        fragments = player.get("fragments") or []
        all_rows = _trusted_detected_rows([row for fragment in fragments for row in fragment.get("rows") or []])
        y_values = [float(row["pitch_m"][1]) for row in all_rows if len(row.get("pitch_m") or []) >= 2]
        if not y_values:
            continue
        near_count = sum(value >= pitch_length_m * 0.7 for value in y_values)
        far_count = sum(value <= pitch_length_m * 0.3 for value in y_values)
        home_end = "near" if near_count >= far_count else "far"
        player["goalkeeper_home_end"] = home_end

        for fragment in fragments:
            detected = _trusted_detected_rows(fragment.get("rows") or [])
            fragment_y = [float(row["pitch_m"][1]) for row in detected if len(row.get("pitch_m") or []) >= 2]
            if not fragment_y:
                continue
            median_y = median(fragment_y)
            opposite = (
                median_y <= pitch_length_m * GOALKEEPER_OPPOSITE_ZONE_RATIO
                if home_end == "near"
                else median_y >= pitch_length_m * (1.0 - GOALKEEPER_OPPOSITE_ZONE_RATIO)
            )
            assignment = fragment.get("assignment") or {}
            slot = fragment.get("slot") or {}
            team_mismatch = bool(
                assignment.get("team_label")
                and slot.get("team_label")
                and str(assignment.get("team_label")) != str(slot.get("team_label"))
            )
            goal_end_conflict = str(slot.get("goal_end") or "") in {"near", "far"} and str(slot.get("goal_end")) != home_end
            warning_conflict = any(
                "identity" in str(value) or "team" in str(value)
                for value in assignment.get("review_warnings") or []
            )
            jump_conflict = _boundary_jump(fragment, fragments, fps)
            if not opposite or not (team_mismatch or goal_end_conflict or warning_conflict or jump_conflict):
                continue
            excluded = len(fragment.get("rows") or [])
            fragment["excluded_reason"] = "goalkeeper_opposite_end_identity_anomaly"
            fragment["rows"] = []
            rejected.append(
                {
                    "player_id": player_id,
                    "stint_id": assignment.get("stint_id"),
                    "stable_subject_id": assignment.get("stable_subject_id"),
                    "start_frame": assignment.get("start_frame"),
                    "end_frame": assignment.get("end_frame"),
                    "median_pitch_y_m": round(median_y, 3),
                    "home_end": home_end,
                    "excluded_frames": excluded,
                    "evidence": {
                        "team_mismatch": team_mismatch,
                        "goal_end_conflict": goal_end_conflict,
                        "review_warning_conflict": warning_conflict,
                        "boundary_jump": jump_conflict,
                    },
                }
            )
    return rejected


def _enforce_team_frame_capacity(players: dict[str, dict[str, Any]], players_per_team: int) -> int:
    if players_per_team <= 0:
        return 0
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for player in players.values():
        team_label = str((player.get("metadata") or {}).get("team_label") or "U")
        for fragment in player.get("fragments") or []:
            for row in fragment.get("rows") or []:
                groups[(team_label, int(row.get("frame") or 0))].append(row)
    removed_ids: set[int] = set()
    for (team_label, _frame), rows in groups.items():
        if team_label == "U" or len(rows) <= players_per_team:
            continue
        ordered = sorted(rows, key=_candidate_rank, reverse=True)
        removed_ids.update(id(row) for row in ordered[players_per_team:])
    if not removed_ids:
        return 0
    for player in players.values():
        for fragment in player.get("fragments") or []:
            fragment["rows"] = [row for row in fragment.get("rows") or [] if id(row) not in removed_ids]
    return len(removed_ids)


def build_resolved_player_timeline(
    *,
    global_identity: dict[str, Any],
    identity_assignments: dict[str, Any],
    fps: float,
) -> dict[str, Any]:
    slots = _slot_lookup(global_identity)
    pitch = global_identity.get("pitch_dimensions_m") if isinstance(global_identity.get("pitch_dimensions_m"), dict) else {}
    pitch_length_m = _number(pitch.get("length_m")) or 47.4
    players: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    fragments_by_key: dict[str, dict[str, Any]] = {}

    assignments = identity_assignments.get("assignments") if isinstance(identity_assignments.get("assignments"), list) else []
    assigned = [row for row in assignments if isinstance(row, dict) and row.get("status") == "assigned" and row.get("player_id")]
    for index, assignment in enumerate(assigned):
        subject = str(assignment.get("stable_subject_id") or assignment.get("stable_player_id") or "")
        slot = slots.get(subject)
        assignment_key = f"{index}:{assignment.get('stint_id') or subject}"
        if slot is None:
            unresolved.append({"assignment_key": assignment_key, "stint_id": assignment.get("stint_id"), "stable_subject_id": subject, "reason": "missing_global_identity_slot"})
            continue
        interval = _assignment_interval(assignment, slot)
        if interval is None:
            unresolved.append({"assignment_key": assignment_key, "stint_id": assignment.get("stint_id"), "stable_subject_id": subject, "reason": "missing_exact_frame_interval"})
            continue
        start_frame, end_frame = interval
        source_rows = [
            row
            for row in slot.get("overlay_positions") or []
            if isinstance(row, dict)
            and isinstance(row.get("frame"), (int, float))
            and start_frame <= int(row["frame"]) <= end_frame
        ]
        if not source_rows:
            unresolved.append({"assignment_key": assignment_key, "stint_id": assignment.get("stint_id"), "stable_subject_id": subject, "reason": "empty_assignment_interval"})
            continue
        player_id = str(assignment["player_id"])
        player = players.setdefault(
            player_id,
            {
                "player_id": player_id,
                "metadata": dict(assignment),
                "fragments": [],
                "rows": [],
                "quality_flags": [],
            },
        )
        fragment = {"assignment_key": assignment_key, "assignment": dict(assignment), "slot": slot, "rows": []}
        player["fragments"].append(fragment)
        fragments_by_key[assignment_key] = fragment
        for source_row in source_rows:
            row = dict(source_row)
            row["_assignment_key"] = assignment_key
            row["_assignment_confidence"] = _number(assignment.get("anchor_confidence"))
            row["_player_id"] = player_id
            row["_stable_subject_id"] = subject
            row["_fps"] = fps
            candidates.append(row)

    stable_frame_groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        stable_frame_groups[(str(row.get("_stable_subject_id")), int(row.get("frame") or 0))].append(row)
    after_subject_conflicts: list[dict[str, Any]] = []
    cross_player_conflicts = 0
    for group in stable_frame_groups.values():
        player_ids = {str(row.get("_player_id")) for row in group}
        if len(player_ids) > 1:
            cross_player_conflicts += len(group) - 1
        after_subject_conflicts.append(max(group, key=_candidate_rank))

    player_frame_groups: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in after_subject_conflicts:
        player_frame_groups[str(row.get("_player_id"))][int(row.get("frame") or 0)].append(row)
    duplicate_frames_removed = 0
    for player_id, frame_groups in player_frame_groups.items():
        previous_pitch = None
        for frame in sorted(frame_groups):
            group = frame_groups[frame]
            selected = max(group, key=lambda row: _candidate_rank(row, previous_pitch))
            duplicate_frames_removed += len(group) - 1
            fragments_by_key[str(selected["_assignment_key"])]["rows"].append(selected)
            players[player_id]["rows"].append(selected)
            if selected.get("pitch_m"):
                previous_pitch = selected.get("pitch_m")

    goalkeeper_rejections = _filter_goalkeeper_anomalies(players, pitch_length_m=pitch_length_m, fps=fps)
    parameters = global_identity.get("parameters") if isinstance(global_identity.get("parameters"), dict) else {}
    team_capacity_frames_removed = _enforce_team_frame_capacity(
        players,
        int(parameters.get("players_per_team") or 7),
    )
    for player in players.values():
        player["rows"] = sorted(
            [row for fragment in player.get("fragments") or [] for row in fragment.get("rows") or []],
            key=lambda row: int(row.get("frame") or 0),
        )
        if goalkeeper_rejections and any(item["player_id"] == player["player_id"] for item in goalkeeper_rejections):
            player["quality_flags"].append("goalkeeper_anomalous_fragment_excluded")

    resolved_assignments = sum(1 for player in players.values() for fragment in player.get("fragments") or [] if not fragment.get("excluded_reason"))
    return {
        "calculation_method": "exact_identity_coverage",
        "fps": fps,
        "pitch_dimensions_m": pitch,
        "parameters": parameters,
        "players": players,
        "quality": {
            "assignments_total": len(assigned),
            "assignments_resolved": resolved_assignments,
            "assignments_unresolved": len(unresolved),
            "unresolved_assignments": unresolved,
            "duplicate_frames_removed": duplicate_frames_removed,
            "cross_player_conflicts_removed": cross_player_conflicts,
            "team_capacity_frames_removed": team_capacity_frames_removed,
            "goalkeeper_anomalous_fragments_excluded": goalkeeper_rejections,
        },
    }


def build_resolved_player_timeline_from_files(path: Path) -> dict[str, Any]:
    import json

    global_identity = json.loads((path / "global_identity.json").read_text(encoding="utf-8"))
    identity_assignments = json.loads((path / "player_identity_assignments.json").read_text(encoding="utf-8"))
    match = json.loads((path / "match.json").read_text(encoding="utf-8")) if (path / "match.json").exists() else {}
    video = match.get("video") if isinstance(match.get("video"), dict) else {}
    fps = _number(video.get("fps"))
    if fps <= 0:
        frame_rows = global_identity.get("frames") if isinstance(global_identity.get("frames"), list) else []
        fps = 30.0
        if len(frame_rows) >= 2:
            frame_delta = _number(frame_rows[-1].get("frame")) - _number(frame_rows[0].get("frame"))
            time_delta = _number(frame_rows[-1].get("time_sec")) - _number(frame_rows[0].get("time_sec"))
            if frame_delta > 0 and time_delta > 0:
                fps = frame_delta / time_delta
    return build_resolved_player_timeline(
        global_identity=global_identity,
        identity_assignments=identity_assignments,
        fps=fps,
    )
