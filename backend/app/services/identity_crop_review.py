from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_review_gallery import load_identity_review_gallery
from app.services.player_identity import replace_player_identity_assignments


ASSIGNMENTS_FILENAME = "identity_crop_assignments.json"
ALLOWED_STATUSES = {"unassigned", "assigned", "unknown", "wrong_team", "false_positive"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_identity_crop_review(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    gallery = load_identity_review_gallery(path)
    assignment_doc = _load_assignments(path)
    assignment_by_artifact = {
        str(item.get("artifact")): item
        for item in assignment_doc.get("assignments") or []
        if isinstance(item, dict) and item.get("artifact")
    }
    crops = []
    for player, stint, crop in _gallery_crops(gallery):
        artifact = str(crop.get("artifact") or "")
        assignment = assignment_by_artifact.get(artifact) or {}
        crops.append(
            {
                **crop,
                "artifact": artifact,
                "stable_subject_id": player.get("stable_subject_id"),
                "stable_player_id": player.get("stable_player_id"),
                "slot_id": player.get("slot_id"),
                "team_label": player.get("team_label") or "U",
                "team_id": player.get("team_id"),
                "team_name": player.get("team_name"),
                "stint_id": stint.get("stint_id"),
                "parent_stint_id": stint.get("parent_stint_id") or stint.get("stint_id"),
                "stint_start_frame": stint.get("start_frame"),
                "stint_end_frame": stint.get("end_frame"),
                "status": assignment.get("status") or "unassigned",
                "player_id": assignment.get("player_id"),
                "player_name": assignment.get("player_name"),
                "updated_at": assignment.get("updated_at"),
            }
        )
    summary = _review_summary(crops, assignment_doc.get("derived_summary") or {})
    return {
        "schema_version": "0.1.0",
        "updated_at": assignment_doc.get("updated_at"),
        "source": "identity_review_gallery_crop_annotations",
        "summary": summary,
        "crops": sorted(crops, key=_crop_sort_key),
        "roster": meta.get("teams") or [],
    }


def save_identity_crop_assignments(
    path: Path,
    meta: dict[str, Any],
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    gallery = load_identity_review_gallery(path)
    crop_lookup = {
        str(crop.get("artifact")): (player, stint, crop)
        for player, stint, crop in _gallery_crops(gallery)
        if crop.get("artifact")
    }
    roster = _roster_lookup(meta)
    current = _load_assignments(path)
    by_artifact = {
        str(item.get("artifact")): item
        for item in current.get("assignments") or []
        if isinstance(item, dict) and item.get("artifact")
    }
    for update in updates:
        normalized = _normalize_update(update, crop_lookup, roster)
        if normalized is None:
            continue
        if normalized["status"] == "unassigned":
            by_artifact.pop(normalized["artifact"], None)
        else:
            by_artifact[normalized["artifact"]] = normalized

    assignments = sorted(by_artifact.values(), key=lambda item: str(item["artifact"]))
    derived, derived_summary = _derive_player_assignments(gallery, assignments, roster)
    replace_player_identity_assignments(path, meta, derived)
    doc = {
        "schema_version": "0.1.0",
        "updated_at": now_iso(),
        "source": "manual_crop_identity_review",
        "assignments": assignments,
        "derived_summary": derived_summary,
    }
    (path / ASSIGNMENTS_FILENAME).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return build_identity_crop_review(path, meta)


def reset_identity_crop_review(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    doc = {
        "schema_version": "0.1.0",
        "updated_at": now_iso(),
        "source": "manual_crop_identity_review",
        "assignments": [],
        "derived_summary": {"derived_stints": 0, "overlap_clipped": 0},
    }
    (path / ASSIGNMENTS_FILENAME).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    replace_player_identity_assignments(path, meta, [])
    return build_identity_crop_review(path, meta)


def refresh_identity_crop_assignments(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    gallery = load_identity_review_gallery(path)
    crop_artifacts = {
        str(crop.get("artifact"))
        for _, _, crop in _gallery_crops(gallery)
        if crop.get("artifact")
    }
    roster = _roster_lookup(meta)
    current = _load_assignments(path)
    assignments = [
        item
        for item in current.get("assignments") or []
        if isinstance(item, dict) and str(item.get("artifact") or "") in crop_artifacts
    ]
    derived, derived_summary = _derive_player_assignments(gallery, assignments, roster)
    replace_player_identity_assignments(path, meta, derived)
    doc = {
        "schema_version": "0.2.0",
        "updated_at": now_iso(),
        "source": "manual_crop_identity_review",
        "assignments": assignments,
        "derived_summary": derived_summary,
    }
    (path / ASSIGNMENTS_FILENAME).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return build_identity_crop_review(path, meta)


def _load_assignments(path: Path) -> dict[str, Any]:
    assignment_path = path / ASSIGNMENTS_FILENAME
    if not assignment_path.exists():
        return {
            "schema_version": "0.1.0",
            "updated_at": None,
            "assignments": [],
            "derived_summary": {},
        }
    data = json.loads(assignment_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("assignments"), list):
        raise ValueError(f"{ASSIGNMENTS_FILENAME} must contain an assignments list")
    return data


def _gallery_crops(gallery: dict[str, Any]):
    for player in gallery.get("players") or []:
        if not isinstance(player, dict):
            continue
        for stint in player.get("stints") or []:
            if not isinstance(stint, dict):
                continue
            for crop in stint.get("crops") or []:
                if isinstance(crop, dict):
                    yield player, stint, crop


def _roster_lookup(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for team_index, team in enumerate(meta.get("teams") or []):
        if not isinstance(team, dict):
            continue
        team_label = "A" if team_index == 0 else "B" if team_index == 1 else "U"
        for player in team.get("players") or []:
            if not isinstance(player, dict) or not player.get("id"):
                continue
            player_id = str(player["id"])
            result[player_id] = {
                "player_id": player_id,
                "player_name": str(player.get("name") or player_id),
                "player_number": player.get("number"),
                "player_role": player.get("role") or "player",
                "team_id": team.get("id"),
                "team_name": team.get("name"),
                "team_label": team_label,
            }
    return result


def _normalize_update(
    update: Any,
    crop_lookup: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    roster: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(update, dict):
        return None
    artifact = str(update.get("artifact") or "")
    if artifact not in crop_lookup:
        raise ValueError(f"Unknown identity crop artifact={artifact!r}")
    status = str(update.get("status") or "unassigned")
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Unsupported crop assignment status={status!r}")
    player, stint, crop = crop_lookup[artifact]
    normalized = {
        "artifact": artifact,
        "frame": crop.get("frame"),
        "time_sec": crop.get("time_sec"),
        "stable_subject_id": player.get("stable_subject_id"),
        "stable_player_id": player.get("stable_player_id"),
        "stint_id": stint.get("stint_id"),
        "parent_stint_id": stint.get("parent_stint_id") or stint.get("stint_id"),
        "team_label": player.get("team_label") or "U",
        "status": status,
        "player_id": None,
        "player_name": None,
        "updated_at": now_iso(),
    }
    if status == "assigned":
        player_id = str(update.get("player_id") or "")
        roster_player = roster.get(player_id)
        if roster_player is None:
            raise ValueError(f"Unknown player_id={player_id!r}")
        normalized["player_id"] = player_id
        normalized["player_name"] = roster_player["player_name"]
    return normalized


def _derive_player_assignments(
    gallery: dict[str, Any],
    assignments: list[dict[str, Any]],
    roster: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    label_by_artifact = {
        str(item.get("artifact")): item
        for item in assignments
        if item.get("status") == "assigned" and item.get("player_id")
    }
    candidates: list[dict[str, Any]] = []
    for player in gallery.get("players") or []:
        if not isinstance(player, dict):
            continue
        for stint in player.get("stints") or []:
            if not isinstance(stint, dict):
                continue
            cells = _crop_cells(stint)
            current: dict[str, Any] | None = None
            for cell in cells:
                label = label_by_artifact.get(cell["artifact"])
                if label is None:
                    if current is not None:
                        candidates.append(current)
                        current = None
                    continue
                player_id = str(label["player_id"])
                if current is not None and current["player_id"] == player_id and current["end_frame"] + 1 >= cell["start_frame"]:
                    current["end_frame"] = cell["end_frame"]
                    current["end_time_sec"] = cell["end_time_sec"]
                    current["duration_sec"] = round(current["end_time_sec"] - current["start_time_sec"], 3)
                    current["detected_frames"] = current["end_frame"] - current["start_frame"] + 1
                    current["anchor_artifacts"].append(cell["artifact"])
                    current["anchor_confidence"] = max(current["anchor_confidence"], cell["confidence"])
                    continue
                if current is not None:
                    candidates.append(current)
                current = _candidate_from_cell(player, stint, cell, roster[player_id])
            if current is not None:
                candidates.append(current)

    accepted, clipped = _remove_player_overlaps(candidates)
    for index, item in enumerate(sorted(accepted, key=_derived_sort_key), start=1):
        digest = hashlib.sha1(
            f"{item['stable_subject_id']}:{item['player_id']}:{item['start_frame']}:{item['end_frame']}".encode()
        ).hexdigest()[:8]
        item["stint_id"] = f"{item['stable_player_id']}-C{index:03d}-{digest}"
    return accepted, {
        "derived_stints": len(accepted),
        "assigned_crops": len(label_by_artifact),
        "overlap_clipped": clipped,
        "covered_frames": sum(item["end_frame"] - item["start_frame"] + 1 for item in accepted),
    }


def _crop_cells(stint: dict[str, Any]) -> list[dict[str, Any]]:
    crops = sorted(
        [crop for crop in stint.get("crops") or [] if isinstance(crop, dict) and isinstance(crop.get("frame"), int)],
        key=lambda crop: int(crop["frame"]),
    )
    if not crops:
        return []
    adaptive_cells = []
    for crop in crops:
        intervals = crop.get("coverage_intervals")
        if not isinstance(intervals, list) or not intervals:
            adaptive_cells = []
            break
        for interval in intervals:
            if not isinstance(interval, dict):
                continue
            start_frame = interval.get("start_frame")
            end_frame = interval.get("end_frame")
            if not isinstance(start_frame, int) or not isinstance(end_frame, int) or end_frame < start_frame:
                continue
            start_time = interval.get("start_time_sec")
            end_time = interval.get("end_time_sec")
            adaptive_cells.append(
                {
                    "artifact": str(crop.get("artifact")),
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_time_sec": float(start_time) if isinstance(start_time, (int, float)) else _frame_time(stint, start_frame, crop),
                    "end_time_sec": float(end_time) if isinstance(end_time, (int, float)) else _frame_time(stint, end_frame, crop),
                    "confidence": float(crop.get("confidence") or 0.0),
                }
            )
    if adaptive_cells:
        return sorted(adaptive_cells, key=lambda cell: (cell["start_frame"], cell["end_frame"]))
    stint_start = _int(stint.get("start_frame"), int(crops[0]["frame"]))
    stint_end = _int(stint.get("end_frame"), int(crops[-1]["frame"]))
    cells = []
    for index, crop in enumerate(crops):
        frame = int(crop["frame"])
        start_frame = stint_start if index == 0 else ((int(crops[index - 1]["frame"]) + frame) // 2) + 1
        end_frame = stint_end if index == len(crops) - 1 else (frame + int(crops[index + 1]["frame"])) // 2
        start_time = _frame_time(stint, start_frame, crop)
        end_time = _frame_time(stint, end_frame, crop)
        cells.append(
            {
                "artifact": str(crop.get("artifact")),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "start_time_sec": start_time,
                "end_time_sec": end_time,
                "confidence": float(crop.get("confidence") or 0.0),
            }
        )
    return cells


def _candidate_from_cell(
    stable_player: dict[str, Any],
    stint: dict[str, Any],
    cell: dict[str, Any],
    roster_player: dict[str, Any],
) -> dict[str, Any]:
    return {
        "stable_subject_id": stable_player.get("stable_subject_id"),
        "stable_player_id": stable_player.get("stable_player_id"),
        "slot_id": stable_player.get("slot_id"),
        "stint_id": None,
        "parent_stint_id": stint.get("parent_stint_id") or stint.get("stint_id"),
        "assignment_scope": "crop_derived_stint",
        "assignment_source": "identity_crop_gallery",
        "status": "assigned",
        **roster_player,
        "start_frame": cell["start_frame"],
        "end_frame": cell["end_frame"],
        "start_time_sec": cell["start_time_sec"],
        "end_time_sec": cell["end_time_sec"],
        "duration_sec": round(cell["end_time_sec"] - cell["start_time_sec"], 3),
        "detected_frames": cell["end_frame"] - cell["start_frame"] + 1,
        "predicted_frames": 0,
        "missing_frames": 0,
        "ambiguous_frames": 0,
        "anchor_artifacts": [cell["artifact"]],
        "anchor_confidence": cell["confidence"],
    }


def _remove_player_overlaps(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    accepted: list[dict[str, Any]] = []
    clipped = 0
    by_player: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        by_player.setdefault(str(item["player_id"]), []).append(item)
    for rows in by_player.values():
        occupied: list[tuple[int, int]] = []
        for item in sorted(rows, key=lambda row: (-float(row["anchor_confidence"]), row["start_frame"])):
            pieces = [(int(item["start_frame"]), int(item["end_frame"]))]
            for occupied_start, occupied_end in occupied:
                pieces = _subtract_intervals(pieces, occupied_start, occupied_end)
            if not pieces:
                clipped += 1
                continue
            for start_frame, end_frame in pieces:
                piece = dict(item)
                piece["start_frame"] = start_frame
                piece["end_frame"] = end_frame
                piece["start_time_sec"] = _interpolate_piece_time(item, start_frame)
                piece["end_time_sec"] = _interpolate_piece_time(item, end_frame)
                piece["duration_sec"] = round(piece["end_time_sec"] - piece["start_time_sec"], 3)
                piece["detected_frames"] = end_frame - start_frame + 1
                accepted.append(piece)
                occupied.append((start_frame, end_frame))
            if len(pieces) != 1 or pieces[0] != (item["start_frame"], item["end_frame"]):
                clipped += 1
        occupied.sort()
    return accepted, clipped


def _subtract_intervals(
    pieces: list[tuple[int, int]],
    occupied_start: int,
    occupied_end: int,
) -> list[tuple[int, int]]:
    result = []
    for start, end in pieces:
        if occupied_end < start or occupied_start > end:
            result.append((start, end))
            continue
        if start < occupied_start:
            result.append((start, occupied_start - 1))
        if end > occupied_end:
            result.append((occupied_end + 1, end))
    return result


def _interpolate_piece_time(item: dict[str, Any], frame: int) -> float:
    start_frame = int(item["start_frame"])
    end_frame = int(item["end_frame"])
    if end_frame <= start_frame:
        return float(item["start_time_sec"])
    ratio = (frame - start_frame) / (end_frame - start_frame)
    return round(float(item["start_time_sec"]) + ratio * (float(item["end_time_sec"]) - float(item["start_time_sec"])), 3)


def _frame_time(stint: dict[str, Any], frame: int, crop: dict[str, Any]) -> float:
    start_frame = stint.get("start_frame")
    end_frame = stint.get("end_frame")
    start_time = stint.get("start_time_sec")
    end_time = stint.get("end_time_sec")
    if all(isinstance(value, (int, float)) for value in [start_frame, end_frame, start_time, end_time]) and end_frame > start_frame:
        ratio = (frame - float(start_frame)) / (float(end_frame) - float(start_frame))
        return round(float(start_time) + ratio * (float(end_time) - float(start_time)), 3)
    return round(float(crop.get("time_sec") or 0.0), 3)


def _review_summary(crops: list[dict[str, Any]], derived_summary: dict[str, Any]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    players: dict[str, int] = {}
    for crop in crops:
        status = str(crop.get("status") or "unassigned")
        statuses[status] = statuses.get(status, 0) + 1
        if crop.get("player_id"):
            players[str(crop["player_id"])] = players.get(str(crop["player_id"]), 0) + 1
    return {
        "crops_total": len(crops),
        "reviewed": len(crops) - statuses.get("unassigned", 0),
        "remaining": statuses.get("unassigned", 0),
        "by_status": statuses,
        "by_player": players,
        **derived_summary,
    }


def _crop_sort_key(crop: dict[str, Any]) -> tuple[int, str, str]:
    return _int(crop.get("frame"), 0), str(crop.get("stable_player_id") or ""), str(crop.get("artifact") or "")


def _derived_sort_key(item: dict[str, Any]) -> tuple[str, int, int]:
    return str(item.get("stable_subject_id") or ""), int(item.get("start_frame") or 0), int(item.get("end_frame") or 0)


def _int(value: Any, fallback: int) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback
