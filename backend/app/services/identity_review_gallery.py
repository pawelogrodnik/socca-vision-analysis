from __future__ import annotations

import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SAMPLES_PER_STINT = 8
MAX_SAMPLES_PER_STINT = 24
GALLERY_FILENAME = "identity_review_gallery.json"
GALLERY_DIRNAME = "identity_review"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_identity_review_gallery(path: Path) -> dict[str, Any]:
    gallery_path = path / GALLERY_FILENAME
    if not gallery_path.exists():
        raise FileNotFoundError(f"{GALLERY_FILENAME} not found. Generate identity review crops first.")
    data = json.loads(gallery_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{GALLERY_FILENAME} must contain an object")
    return data


def build_identity_review_gallery(
    path: Path,
    video_path: Path,
    *,
    samples_per_stint: int = DEFAULT_SAMPLES_PER_STINT,
    force: bool = False,
) -> dict[str, Any]:
    if not force and (path / GALLERY_FILENAME).exists():
        existing = load_identity_review_gallery(path)
        existing_params = existing.get("parameters") if isinstance(existing.get("parameters"), dict) else {}
        if int(existing_params.get("samples_per_stint") or 0) == samples_per_stint:
            return existing

    stable_doc = _load_json(path / "stable_players.json", "stable_players.json")
    tracks_doc = _load_json(path / "tracks.json", "tracks.json")
    tracks = tracks_doc if isinstance(tracks_doc, list) else tracks_doc.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError("tracks.json must contain a list of tracks")

    samples_per_stint = max(1, min(int(samples_per_stint or DEFAULT_SAMPLES_PER_STINT), MAX_SAMPLES_PER_STINT))
    tracks_by_id = _tracks_by_id(tracks)
    crop_root = path / GALLERY_DIRNAME / "crops"
    if force and crop_root.exists():
        shutil.rmtree(crop_root)
    crop_root.mkdir(parents=True, exist_ok=True)

    players_out: list[dict[str, Any]] = []
    crops_total = 0
    stints_total = 0
    stints_with_crops = 0

    cap = _open_video(video_path)
    try:
        for player in _stable_players(stable_doc):
            stable_subject_id = _stable_subject_id(player)
            if not stable_subject_id:
                continue
            player_out = _player_header(player)
            player_stints: list[dict[str, Any]] = []
            for index, stint in enumerate(_stints_for_player(player)):
                stints_total += 1
                stint_id = str(stint.get("stint_id") or f"{player_out['stable_player_id']}-S{index + 1:02d}")
                candidates = _candidate_positions(player, stint, tracks_by_id)
                selected = _select_positions(candidates, samples_per_stint)
                crops = []
                for sample_index, position in enumerate(selected, start=1):
                    crop = _write_crop(
                        cap,
                        path,
                        crop_root,
                        stable_subject_id=stable_subject_id,
                        stint_id=stint_id,
                        position=position,
                        sample_index=sample_index,
                    )
                    if crop:
                        crops.append(crop)
                if crops:
                    stints_with_crops += 1
                    crops_total += len(crops)
                player_stints.append(
                    {
                        "stint_id": stint_id,
                        "slot_id": stint.get("slot_id") or player.get("slot_id"),
                        "start_frame": _int_or_none(stint.get("start_frame")),
                        "end_frame": _int_or_none(stint.get("end_frame")),
                        "start_time_sec": _float_or_none(stint.get("start_time_sec")),
                        "end_time_sec": _float_or_none(stint.get("end_time_sec")),
                        "duration_sec": _float_or_none(stint.get("duration_sec")),
                        "status": stint.get("status"),
                        "detected_frames": _int_or_none(stint.get("detected_frames")),
                        "predicted_frames": _int_or_none(stint.get("predicted_frames")),
                        "missing_frames": _int_or_none(stint.get("missing_frames")),
                        "ambiguous_frames": _int_or_none(stint.get("ambiguous_frames")),
                        "tracklet_ids": _string_list(stint.get("tracklet_ids")),
                        "raw_track_ids": _int_list(stint.get("raw_track_ids")),
                        "candidate_positions": len(candidates),
                        "crops": crops,
                    }
                )
            player_out["stints"] = player_stints
            player_out["stint_count"] = len(player_stints)
            player_out["crop_count"] = sum(len(stint["crops"]) for stint in player_stints)
            players_out.append(player_out)
    finally:
        cap.release()

    gallery = {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": "stable_players_tracks_sparse_crops",
        "identity_semantics": stable_doc.get("identity_semantics") or "stint_first",
        "parameters": {
            "samples_per_stint": samples_per_stint,
            "max_samples_per_stint": MAX_SAMPLES_PER_STINT,
            "crop_margin_ratio": 0.2,
            "crop_min_margin_px": 8,
        },
        "summary": {
            "stable_players": len(players_out),
            "stints": stints_total,
            "stints_with_crops": stints_with_crops,
            "players_with_crops": sum(1 for player in players_out if player["crop_count"] > 0),
            "crops": crops_total,
        },
        "players": players_out,
    }
    (path / GALLERY_FILENAME).write_text(json.dumps(gallery, indent=2), encoding="utf-8")
    _attach_gallery_to_analysis_report(path)
    return gallery


def _load_json(path: Path, label: str) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found. Run analysis first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _open_video(video_path: Path):
    if not video_path.exists():
        raise FileNotFoundError("Match video not found.")
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    return cap


def _stable_players(stable_doc: dict[str, Any]) -> list[dict[str, Any]]:
    players = stable_doc.get("players")
    return [player for player in players if isinstance(player, dict)] if isinstance(players, list) else []


def _stable_subject_id(player: dict[str, Any]) -> str:
    return str(player.get("stable_subject_id") or player.get("stable_player_id") or player.get("slot_id") or "")


def _player_header(player: dict[str, Any]) -> dict[str, Any]:
    stable_subject_id = _stable_subject_id(player)
    return {
        "stable_subject_id": stable_subject_id,
        "stable_player_id": str(player.get("stable_player_id") or stable_subject_id),
        "slot_id": player.get("slot_id"),
        "team_label": player.get("team_label") or "U",
        "team_id": player.get("team_id"),
        "team_name": player.get("team_name"),
        "status": player.get("status") or "active",
        "confidence": player.get("confidence"),
        "confidence_score": player.get("confidence_score"),
        "tracklet_ids": _string_list(player.get("tracklet_ids")),
        "raw_track_ids": _int_list(player.get("raw_track_ids")),
        "start_time_sec": _float_or_none(player.get("start_time_sec")),
        "end_time_sec": _float_or_none(player.get("end_time_sec")),
        "duration_sec": _float_or_none(player.get("duration_sec")),
    }


def _stints_for_player(player: dict[str, Any]) -> list[dict[str, Any]]:
    stints = [stint for stint in player.get("stints") or [] if isinstance(stint, dict)]
    if stints:
        return stints
    return [
        {
            "stint_id": f"{player.get('stable_player_id') or player.get('slot_id') or 'slot'}-S01",
            "slot_id": player.get("slot_id"),
            "start_time_sec": player.get("start_time_sec"),
            "end_time_sec": player.get("end_time_sec"),
            "duration_sec": player.get("duration_sec"),
            "tracklet_ids": player.get("tracklet_ids") or [],
            "raw_track_ids": player.get("raw_track_ids") or [],
        }
    ]


def _tracks_by_id(tracks: list[Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = track.get("track_id")
        if track_id is None:
            continue
        by_id[str(track_id)] = track
    return by_id


def _candidate_positions(
    player: dict[str, Any],
    stint: dict[str, Any],
    tracks_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    track_ids = _track_ids_for_stint(player, stint)
    start_frame = _int_or_none(stint.get("start_frame"))
    end_frame = _int_or_none(stint.get("end_frame"))
    start_time = _float_or_none(stint.get("start_time_sec"))
    end_time = _float_or_none(stint.get("end_time_sec"))
    by_frame: dict[int, dict[str, Any]] = {}
    for track_id in track_ids:
        track = tracks_by_id.get(str(track_id))
        if not track:
            continue
        positions = track.get("positions")
        if not isinstance(positions, list):
            continue
        for position in positions:
            if not isinstance(position, dict) or not position.get("bbox_xyxy"):
                continue
            frame = _int_or_none(position.get("frame"))
            if frame is None:
                continue
            time_sec = _float_or_none(position.get("time_sec"))
            if not _inside_stint(frame, time_sec, start_frame, end_frame, start_time, end_time):
                continue
            current = by_frame.get(frame)
            if current is None or _confidence(position) > _confidence(current):
                by_frame[frame] = {**position, "track_id": position.get("track_id") or track.get("track_id")}
    return sorted(by_frame.values(), key=lambda item: (_int_or_none(item.get("frame")) or 0, _confidence(item)))


def _track_ids_for_stint(player: dict[str, Any], stint: dict[str, Any]) -> list[str]:
    ids = {str(item) for item in _int_list(stint.get("raw_track_ids"))}
    ids.update(str(item) for item in _int_list(player.get("raw_track_ids")))
    for tracklet_id in _string_list(stint.get("tracklet_ids")) + _string_list(player.get("tracklet_ids")):
        match = re.match(r"^(\d+)", tracklet_id)
        if match:
            ids.add(match.group(1))
    return sorted(ids)


def _inside_stint(
    frame: int,
    time_sec: float | None,
    start_frame: int | None,
    end_frame: int | None,
    start_time: float | None,
    end_time: float | None,
) -> bool:
    if start_frame is not None and frame < start_frame:
        return False
    if end_frame is not None and frame > end_frame:
        return False
    if time_sec is not None and start_time is not None and time_sec < start_time - 0.05:
        return False
    if time_sec is not None and end_time is not None and time_sec > end_time + 0.05:
        return False
    return True


def _select_positions(positions: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(positions) <= limit:
        return positions
    if limit <= 1:
        return [max(positions, key=_confidence)]
    selected: list[dict[str, Any]] = []
    last_index = len(positions) - 1
    for sample_index in range(limit):
        index = round((sample_index / (limit - 1)) * last_index)
        selected.append(positions[index])
    return selected


def _write_crop(
    cap: Any,
    match_root: Path,
    crop_root: Path,
    *,
    stable_subject_id: str,
    stint_id: str,
    position: dict[str, Any],
    sample_index: int,
) -> dict[str, Any] | None:
    import cv2

    frame_idx = _int_or_none(position.get("frame"))
    bbox = _bbox(position.get("bbox_xyxy"))
    if frame_idx is None or bbox is None:
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = _expand_bbox(bbox, width, height)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    slot_dir = crop_root / _slug(stable_subject_id) / _slug(stint_id)
    slot_dir.mkdir(parents=True, exist_ok=True)
    crop_path = slot_dir / f"{sample_index:02d}_f{frame_idx:06d}.jpg"
    if not cv2.imwrite(str(crop_path), crop):
        return None
    return {
        "artifact": crop_path.relative_to(match_root).as_posix(),
        "frame": frame_idx,
        "time_sec": _float_or_none(position.get("time_sec")),
        "bbox_xyxy": [round(value, 2) for value in bbox],
        "crop_bbox_xyxy": [x1, y1, x2, y2],
        "confidence": _float_or_none(position.get("confidence")),
        "track_id": position.get("track_id"),
        "source": position.get("source"),
    }


def _expand_bbox(bbox: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    margin = max(8.0, max(box_w, box_h) * 0.2)
    left = max(0, math.floor(x1 - margin))
    top = max(0, math.floor(y1 - margin))
    right = min(width, math.ceil(x2 + margin))
    bottom = min(height, math.ceil(y2 + margin))
    return left, top, right, bottom


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _confidence(position: dict[str, Any]) -> float:
    value = position.get("confidence")
    return float(value) if isinstance(value, int | float) else 0.0


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, str) and item.isdigit():
            out.append(int(item))
    return out


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "item"


def _attach_gallery_to_analysis_report(path: Path) -> None:
    report_path = path / "analysis_report.json"
    if not report_path.exists():
        return
    data = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        data["artifacts"] = artifacts
    artifacts["identity_review_gallery"] = GALLERY_FILENAME
    data["updated_at"] = now_iso()
    report_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
