from __future__ import annotations

from typing import Any


UNRESOLVED_OVERLAY_SOURCES = frozenset(
    {
        "unmatched_raw",
        "unrepresented_tracklet",
    }
)


def build_unrepresented_tracklet_observations(
    tracklets: list[dict[str, Any]],
    global_identity: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return clean observations that have no bbox representation in identity overlay.

    These rows are visual diagnostics only. They intentionally do not change slot
    membership, identity assignments, or any trusted statistics.
    """

    represented_keys = _represented_tracklet_frame_keys(global_identity)
    existing_unmatched_keys = {
        _observation_key(row)
        for row in global_identity.get("unmatched_observations") or []
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for tracklet in tracklets:
        tracklet_id = str(tracklet.get("tracklet_id") or "")
        if not tracklet_id:
            continue
        raw_track_id = tracklet.get("source_tracker_id")
        if raw_track_id is None:
            raw_track_id = tracklet.get("source_track_id")
        for position in tracklet.get("positions_m") or tracklet.get("positions") or []:
            if not isinstance(position, dict) or not position.get("bbox_xyxy"):
                continue
            if str(position.get("play_area_status") or "inside_play") != "inside_play":
                continue
            frame = int(position.get("frame") or 0)
            key = (frame, tracklet_id)
            if key in represented_keys or key in existing_unmatched_keys:
                continue
            row = {
                "frame": frame,
                "time_sec": round(float(position.get("time_sec") or 0.0), 3),
                "bbox_xyxy": list(position["bbox_xyxy"]),
                "pitch_m": position.get("pitch_m"),
                "play_area_status": "inside_play",
                "tracklet_id": tracklet_id,
                "raw_track_id": raw_track_id,
                "confidence": round(
                    float(position.get("confidence") or 0.0),
                    4,
                ),
                "team_label": str(tracklet.get("team_label") or "U"),
                "team_id": tracklet.get("team_id"),
                "team_name": tracklet.get("team_name"),
                "team_confidence": round(
                    float(tracklet.get("team_confidence") or 0.0),
                    4,
                ),
                "source": "unrepresented_tracklet",
                "status": "unresolved_visual_only",
                "visual_trusted": False,
            }
            rows.append(row)
    return rows


def select_unresolved_overlay_rows(
    existing_rows: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    *,
    duplicate_iou: float = 0.55,
    duplicate_containment: float = 0.80,
) -> list[dict[str, Any]]:
    """Keep visible unresolved detections without drawing duplicate boxes.

    A hard player-count cap is deliberately not used here. These boxes are
    explicitly untrusted and excluded from statistics; hiding them because a
    slot count reached seven would conceal the exact identity-coverage failures
    that an operator needs to see.
    """

    occupied_boxes = [
        row["bbox_xyxy"]
        for row in existing_rows
        if isinstance(row, dict) and _valid_bbox(row.get("bbox_xyxy"))
    ]
    candidates = sorted(
        (
            row
            for row in observations
            if isinstance(row, dict) and _valid_bbox(row.get("bbox_xyxy"))
        ),
        key=_unresolved_priority,
    )
    selected: list[dict[str, Any]] = []
    for observation in candidates:
        bbox = observation["bbox_xyxy"]
        if any(
            _bbox_iou(bbox, occupied) >= duplicate_iou
            or _bbox_containment(bbox, occupied) >= duplicate_containment
            for occupied in occupied_boxes
        ):
            continue
        selected.append(observation)
        occupied_boxes.append(bbox)
    return selected


def is_unresolved_overlay_row(row: dict[str, Any]) -> bool:
    return str(row.get("source") or "") in UNRESOLVED_OVERLAY_SOURCES


def _represented_tracklet_frame_keys(
    global_identity: dict[str, Any],
) -> set[tuple[int, str]]:
    keys: set[tuple[int, str]] = set()
    for slot in global_identity.get("slots") or []:
        for row in slot.get("overlay_positions") or []:
            if not isinstance(row, dict) or not row.get("bbox_xyxy"):
                continue
            tracklet_id = row.get("tracklet_id") or row.get("candidate_tracklet_id")
            if tracklet_id:
                keys.add((int(row.get("frame") or 0), str(tracklet_id)))
    return keys


def _observation_key(row: dict[str, Any]) -> tuple[int, str]:
    return (
        int(row.get("frame") or 0),
        str(row.get("tracklet_id") or row.get("candidate_tracklet_id") or ""),
    )


def _unresolved_priority(row: dict[str, Any]) -> tuple[int, int, float, str]:
    source = str(row.get("source") or "")
    team_label = str(row.get("team_label") or "U")
    return (
        0 if source == "unrepresented_tracklet" else 1,
        0 if team_label in {"A", "B"} else 1,
        -float(row.get("confidence") or 0.0),
        str(row.get("tracklet_id") or row.get("raw_track_id") or ""),
    )


def _valid_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    x1, y1, x2, y2 = [float(item) for item in value]
    return x2 > x1 and y2 > y1


def _bbox_iou(first: list[float], second: list[float]) -> float:
    intersection, first_area, second_area = _bbox_areas(first, second)
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def _bbox_containment(first: list[float], second: list[float]) -> float:
    intersection, first_area, second_area = _bbox_areas(first, second)
    smaller_area = min(first_area, second_area)
    return intersection / smaller_area if smaller_area > 0 else 0.0


def _bbox_areas(
    first: list[float],
    second: list[float],
) -> tuple[float, float, float]:
    ax1, ay1, ax2, ay2 = [float(value) for value in first]
    bx1, by1, bx2, by2 = [float(value) for value in second]
    intersection_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    first_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    second_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return intersection, first_area, second_area
