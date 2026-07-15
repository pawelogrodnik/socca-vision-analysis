from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SPLITS_FILENAME = "identity_review_splits.json"
MIN_REVIEW_SEGMENT_FRAMES = 12
IDENTITY_FRAGMENT_GAP_FRAMES = 18


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_identity_review_splits(path: Path) -> dict[str, Any]:
    split_path = path / SPLITS_FILENAME
    if not split_path.exists():
        return {
            "schema_version": "0.1.0",
            "updated_at": None,
            "source": "identity_review_manual_splits",
            "splits": [],
        }
    data = json.loads(split_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{SPLITS_FILENAME} must contain an object")
    splits = data.get("splits")
    if not isinstance(splits, list):
        raise ValueError(f"{SPLITS_FILENAME}.splits must contain a list")
    return data


def save_identity_review_splits(path: Path, splits: list[dict[str, Any]]) -> dict[str, Any]:
    current = load_identity_review_splits(path)
    merged: dict[tuple[str, str, int], dict[str, Any]] = {}
    for item in [*(current.get("splits") or []), *splits]:
        normalized = _normalize_split(item)
        if normalized is None:
            continue
        key = (
            normalized["stable_subject_id"],
            normalized["parent_stint_id"],
            normalized["frame"],
        )
        merged[key] = normalized
    doc = {
        "schema_version": "0.1.0",
        "updated_at": now_iso(),
        "source": "identity_review_manual_splits",
        "splits": sorted(
            merged.values(),
            key=lambda item: (
                item["stable_subject_id"],
                item["parent_stint_id"],
                item["frame"],
            ),
        ),
    }
    (path / SPLITS_FILENAME).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def review_segments_for_player(
    player: dict[str, Any],
    split_doc: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    stable_subject_id = str(
        player.get("stable_subject_id")
        or player.get("stable_player_id")
        or player.get("slot_id")
        or ""
    )
    manual_by_parent = _manual_splits_for_subject(split_doc or {}, stable_subject_id)
    automatic_by_parent = _automatic_splits_for_player(player)
    segments: list[dict[str, Any]] = []
    for index, stint in enumerate(_base_stints(player), start=1):
        parent_stint_id = str(
            stint.get("stint_id")
            or f"{player.get('stable_player_id') or player.get('slot_id') or 'slot'}-S{index:02d}"
        )
        split_reasons: dict[int, set[str]] = {}
        for frame, reason in automatic_by_parent.get(parent_stint_id, []):
            split_reasons.setdefault(frame, set()).add(reason)
        for frame in manual_by_parent.get(parent_stint_id, []):
            split_reasons.setdefault(frame, set()).add("manual_crop_range")
        segments.extend(_split_stint(stint, parent_stint_id, split_reasons))
    return segments


def gallery_stints_by_subject(gallery: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(gallery, dict):
        return {}
    players = gallery.get("players")
    if not isinstance(players, list):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for player in players:
        if not isinstance(player, dict):
            continue
        stable_subject_id = str(player.get("stable_subject_id") or "")
        stints = player.get("stints")
        if stable_subject_id and isinstance(stints, list):
            result[stable_subject_id] = [stint for stint in stints if isinstance(stint, dict)]
    return result


def _normalize_split(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    stable_subject_id = str(item.get("stable_subject_id") or "")
    parent_stint_id = str(item.get("parent_stint_id") or "")
    frame = item.get("frame")
    if not stable_subject_id or not parent_stint_id or isinstance(frame, bool) or not isinstance(frame, int | float):
        return None
    return {
        "stable_subject_id": stable_subject_id,
        "parent_stint_id": parent_stint_id,
        "frame": int(frame),
        "reason": str(item.get("reason") or "manual_crop_range"),
    }


def _manual_splits_for_subject(split_doc: dict[str, Any], stable_subject_id: str) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for item in split_doc.get("splits") or []:
        normalized = _normalize_split(item)
        if normalized is None or normalized["stable_subject_id"] != stable_subject_id:
            continue
        result.setdefault(normalized["parent_stint_id"], []).append(normalized["frame"])
    return result


def _automatic_splits_for_player(player: dict[str, Any]) -> dict[str, list[tuple[int, str]]]:
    stints = _base_stints(player)
    result: dict[str, list[tuple[int, str]]] = {}
    for event in player.get("identity_events") or []:
        if not isinstance(event, dict) or event.get("type") != "confirmed_switch_with_competitor_accepted":
            continue
        frame = event.get("frame")
        if isinstance(frame, bool) or not isinstance(frame, int | float):
            continue
        frame_int = int(frame)
        for index, stint in enumerate(stints, start=1):
            start_frame = _int_or_none(stint.get("start_frame"))
            end_frame = _int_or_none(stint.get("end_frame"))
            if start_frame is None or end_frame is None or not (start_frame < frame_int <= end_frame):
                continue
            parent_stint_id = str(
                stint.get("stint_id")
                or f"{player.get('stable_player_id') or player.get('slot_id') or 'slot'}-S{index:02d}"
            )
            result.setdefault(parent_stint_id, []).append((frame_int, "confirmed_identity_switch"))
            break
    for index, stint in enumerate(stints, start=1):
        parent_stint_id = str(
            stint.get("stint_id")
            or f"{player.get('stable_player_id') or player.get('slot_id') or 'slot'}-S{index:02d}"
        )
        for frame, reason in _history_fragment_splits(player, stint):
            result.setdefault(parent_stint_id, []).append((frame, reason))
    return result


def _history_fragment_splits(
    player: dict[str, Any],
    stint: dict[str, Any],
) -> list[tuple[int, str]]:
    start_frame = _int_or_none(stint.get("start_frame"))
    end_frame = _int_or_none(stint.get("end_frame"))
    if start_frame is None or end_frame is None:
        return []
    rows = sorted(
        (
            row
            for row in player.get("overlay_positions") or []
            if isinstance(row, dict)
            and (_int_or_none(row.get("frame")) is not None)
            and start_frame <= int(row["frame"]) <= end_frame
        ),
        key=lambda row: int(row["frame"]),
    )
    detected = [
        row
        for row in rows
        if row.get("source") == "detected" and row.get("tracklet_id")
    ]
    splits: dict[int, str] = {}
    previous: dict[str, Any] | None = None
    for row in detected:
        frame = int(row["frame"])
        if previous is not None:
            previous_frame = int(previous["frame"])
            previous_tracklet = str(previous.get("tracklet_id") or "")
            tracklet = str(row.get("tracklet_id") or "")
            if tracklet != previous_tracklet:
                splits[frame] = "tracklet_boundary"
            elif frame - previous_frame > IDENTITY_FRAGMENT_GAP_FRAMES:
                splits[frame] = "detection_gap"
        previous = row
    return sorted(splits.items())


def _split_stint(
    stint: dict[str, Any],
    parent_stint_id: str,
    split_reasons: dict[int, set[str]],
) -> list[dict[str, Any]]:
    start_frame = _int_or_none(stint.get("start_frame"))
    end_frame = _int_or_none(stint.get("end_frame"))
    if start_frame is None or end_frame is None or end_frame < start_frame:
        return [{**stint, "stint_id": parent_stint_id, "parent_stint_id": parent_stint_id}]

    valid_splits: list[int] = []
    for frame in sorted(split_reasons):
        if frame - start_frame < MIN_REVIEW_SEGMENT_FRAMES:
            continue
        if end_frame - frame + 1 < MIN_REVIEW_SEGMENT_FRAMES:
            continue
        if valid_splits and frame - valid_splits[-1] < MIN_REVIEW_SEGMENT_FRAMES:
            continue
        valid_splits.append(frame)

    if not valid_splits:
        return [
            {
                **stint,
                "stint_id": parent_stint_id,
                "parent_stint_id": parent_stint_id,
                "review_segment_index": 1,
                "review_segment_count": 1,
                "split_reasons": [],
            }
        ]

    boundaries = [start_frame, *valid_splits, end_frame + 1]
    parent_start_time = _float_or_none(stint.get("start_time_sec"))
    parent_end_time = _float_or_none(stint.get("end_time_sec"))
    seconds_per_frame = None
    if parent_start_time is not None and parent_end_time is not None and end_frame > start_frame:
        seconds_per_frame = (parent_end_time - parent_start_time) / (end_frame - start_frame)

    output: list[dict[str, Any]] = []
    segment_count = len(boundaries) - 1
    for index in range(segment_count):
        segment_start = boundaries[index]
        segment_end = boundaries[index + 1] - 1
        segment_id = f"{parent_stint_id}-R{index + 1:02d}"
        frame_ratio = (segment_end - segment_start + 1) / max(1, end_frame - start_frame + 1)
        segment = {
            **stint,
            "stint_id": segment_id,
            "parent_stint_id": parent_stint_id,
            "review_segment_index": index + 1,
            "review_segment_count": segment_count,
            "start_frame": segment_start,
            "end_frame": segment_end,
            "split_reasons": sorted(split_reasons.get(segment_start, set())),
        }
        segment_tracklets, segment_raw_ids = _segment_track_ids(
            stint,
            segment_start,
            segment_end,
        )
        if segment_tracklets:
            segment["tracklet_ids"] = segment_tracklets
        if segment_raw_ids:
            segment["raw_track_ids"] = segment_raw_ids
        if seconds_per_frame is not None and parent_start_time is not None:
            segment_start_time = parent_start_time + (segment_start - start_frame) * seconds_per_frame
            segment_end_time = parent_start_time + (segment_end - start_frame) * seconds_per_frame
            segment["start_time_sec"] = round(segment_start_time, 3)
            segment["end_time_sec"] = round(segment_end_time, 3)
            segment["duration_sec"] = round(max(0.0, segment_end_time - segment_start_time), 3)
        for key in ["detected_frames", "predicted_frames", "missing_frames", "ambiguous_frames"]:
            value = stint.get(key)
            if isinstance(value, int | float):
                segment[key] = int(round(float(value) * frame_ratio))
        output.append(segment)
    return output


def _segment_track_ids(
    stint: dict[str, Any],
    start_frame: int,
    end_frame: int,
) -> tuple[list[str], list[int]]:
    rows = stint.get("_identity_history")
    if not isinstance(rows, list):
        return [], []
    tracklets: set[str] = set()
    raw_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        frame = _int_or_none(row.get("frame"))
        if frame is None or not (start_frame <= frame <= end_frame):
            continue
        tracklet_id = row.get("tracklet_id")
        raw_track_id = row.get("raw_track_id")
        if tracklet_id:
            tracklets.add(str(tracklet_id))
        if isinstance(raw_track_id, int) and not isinstance(raw_track_id, bool):
            raw_ids.add(raw_track_id)
    return sorted(tracklets), sorted(raw_ids)


def _base_stints(player: dict[str, Any]) -> list[dict[str, Any]]:
    stints = [stint for stint in player.get("stints") or [] if isinstance(stint, dict)]
    if stints:
        history = [row for row in player.get("overlay_positions") or [] if isinstance(row, dict)]
        return [{**stint, "_identity_history": history} for stint in stints]
    return [
        {
            "stint_id": f"{player.get('stable_player_id') or player.get('slot_id') or 'slot'}-S01",
            "slot_id": player.get("slot_id"),
            "start_frame": player.get("start_frame"),
            "end_frame": player.get("end_frame"),
            "start_time_sec": player.get("start_time_sec"),
            "end_time_sec": player.get("end_time_sec"),
            "duration_sec": player.get("duration_sec"),
            "tracklet_ids": player.get("tracklet_ids") or [],
            "raw_track_ids": player.get("raw_track_ids") or [],
        }
    ]


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
