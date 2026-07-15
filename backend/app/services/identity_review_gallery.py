from __future__ import annotations

import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_review_segments import (
    load_identity_review_splits,
    review_segments_for_player,
)


DEFAULT_SAMPLES_PER_STINT = 8
MAX_SAMPLES_PER_STINT = 24
GALLERY_FILENAME = "identity_review_gallery.json"
GALLERY_DIRNAME = "identity_review"
ASSIGNMENTS_FILENAME = "identity_crop_assignments.json"
MIN_TRACK_RUN_FRAMES = 5
APPEARANCE_CLUSTER_DISTANCE = 0.5


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
    previous_gallery = None
    previous_assignments = None
    if force and (path / GALLERY_FILENAME).exists():
        previous_gallery = load_identity_review_gallery(path)
        previous_assignments = _load_optional_json(path / ASSIGNMENTS_FILENAME)
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
    split_doc = load_identity_review_splits(path)
    crop_root = path / GALLERY_DIRNAME / "crops"
    if force and crop_root.exists():
        shutil.rmtree(crop_root)
    crop_root.mkdir(parents=True, exist_ok=True)

    players_out: list[dict[str, Any]] = []
    crops_total = 0
    stints_total = 0
    stints_with_crops = 0
    mixed_segments = 0
    automatic_splits = 0
    manual_splits = 0

    cap = _open_video(video_path)
    try:
        for player in _stable_players(stable_doc):
            stable_subject_id = _stable_subject_id(player)
            if not stable_subject_id:
                continue
            player_out = _player_header(player)
            player_stints: list[dict[str, Any]] = []
            for index, stint in enumerate(review_segments_for_player(player, split_doc)):
                stints_total += 1
                stint_id = str(stint.get("stint_id") or f"{player_out['stable_player_id']}-S{index + 1:02d}")
                candidates = _candidate_positions(player, stint, tracks_by_id)
                selected = _select_representative_positions(cap, candidates, stint)
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
                crops, appearance = _appearance_diagnostics(crops)
                if appearance["appearance_purity"] == "mixed":
                    mixed_segments += 1
                split_reasons = _string_list(stint.get("split_reasons"))
                automatic_splits += sum(
                    reason in {"confirmed_identity_switch", "tracklet_boundary", "detection_gap"}
                    for reason in split_reasons
                )
                manual_splits += sum(reason == "manual_crop_range" for reason in split_reasons)
                player_stints.append(
                    {
                        "stint_id": stint_id,
                        "parent_stint_id": stint.get("parent_stint_id") or stint_id,
                        "review_segment_index": _int_or_none(stint.get("review_segment_index")),
                        "review_segment_count": _int_or_none(stint.get("review_segment_count")),
                        "split_reasons": split_reasons,
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
                        "representative_clusters": len(selected),
                        "represented_intervals": sum(
                            len(crop.get("coverage_intervals") or []) for crop in crops
                        ),
                        "crops": crops,
                        **appearance,
                    }
                )
            _deduplicate_player_crops(path, player_stints)
            player_out["stints"] = player_stints
            player_out["stint_count"] = len(player_stints)
            player_out["crop_count"] = sum(len(stint["crops"]) for stint in player_stints)
            players_out.append(player_out)
    finally:
        cap.release()

    all_stints = [stint for player in players_out for stint in player["stints"]]
    crops_total = sum(len(stint["crops"]) for stint in all_stints)
    stints_with_crops = sum(1 for stint in all_stints if stint["crops"])

    gallery = {
        "schema_version": "0.4.0",
        "generated_at": now_iso(),
        "source": "stable_players_tracks_adaptive_representatives",
        "identity_semantics": stable_doc.get("identity_semantics") or "stint_first",
        "parameters": {
            "samples_per_stint": samples_per_stint,
            "max_samples_per_stint": MAX_SAMPLES_PER_STINT,
            "crop_margin_ratio": 0.2,
            "crop_min_margin_px": 8,
            "sampling_strategy": "atomic_identity_fragment_track_clusters",
            "min_track_run_frames": MIN_TRACK_RUN_FRAMES,
            "appearance_cluster_distance": APPEARANCE_CLUSTER_DISTANCE,
        },
        "summary": {
            "stable_players": len(players_out),
            "stints": stints_total,
            "stints_with_crops": stints_with_crops,
            "players_with_crops": sum(1 for player in players_out if player["crop_count"] > 0),
            "crops": crops_total,
            "automatic_splits": automatic_splits,
            "manual_splits": manual_splits,
            "mixed_segments": mixed_segments,
        },
        "players": players_out,
    }
    migration = _migrate_crop_assignments(
        path,
        previous_gallery=previous_gallery,
        previous_assignments=previous_assignments,
        gallery=gallery,
    )
    gallery["summary"]["migrated_assignments"] = migration["migrated"]
    gallery["summary"]["unmatched_previous_assignments"] = migration["unmatched"]
    (path / GALLERY_FILENAME).write_text(json.dumps(gallery, indent=2), encoding="utf-8")
    _attach_gallery_to_analysis_report(path)
    return gallery


def _load_json(path: Path, label: str) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found. Run analysis first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _migrate_crop_assignments(
    path: Path,
    *,
    previous_gallery: dict[str, Any] | None,
    previous_assignments: dict[str, Any] | None,
    gallery: dict[str, Any],
) -> dict[str, int]:
    if not previous_gallery or not previous_assignments:
        return {"migrated": 0, "unmatched": 0}

    old_crops = {
        str(crop.get("artifact")): (player, stint, crop)
        for player in previous_gallery.get("players") or []
        if isinstance(player, dict)
        for stint in player.get("stints") or []
        if isinstance(stint, dict)
        for crop in stint.get("crops") or []
        if isinstance(crop, dict) and crop.get("artifact")
    }
    new_rows = [
        (player, stint, crop)
        for player in gallery.get("players") or []
        if isinstance(player, dict)
        for stint in player.get("stints") or []
        if isinstance(stint, dict)
        for crop in stint.get("crops") or []
        if isinstance(crop, dict) and crop.get("artifact")
    ]
    used: set[str] = set()
    migrated: list[dict[str, Any]] = []
    unmatched = 0
    for assignment in previous_assignments.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        old = old_crops.get(str(assignment.get("artifact") or ""))
        if old is None:
            unmatched += 1
            continue
        old_player, _, old_crop = old
        old_subject = str(old_player.get("stable_subject_id") or "")
        old_frame = _int_or_none(old_crop.get("frame"))
        old_track = str(old_crop.get("track_id") or "")
        same_subject_candidates = [
            row
            for row in new_rows
            if str(row[0].get("stable_subject_id") or "") == old_subject
            and str(row[2].get("artifact")) not in used
            and (
                _int_or_none(row[2].get("frame")) == old_frame
                or (
                    str(row[2].get("track_id") or "") == old_track
                    and _crop_time_distance(old_crop, row[2]) <= 1.0
                )
            )
        ]
        track_coverage_candidates = [
            row
            for row in new_rows
            if str(row[2].get("artifact")) not in used
            and old_track
            and str(row[2].get("track_id") or "") == old_track
            and _crop_covers_frame(row[2], old_frame)
        ]
        candidates = same_subject_candidates or track_coverage_candidates
        if not candidates:
            unmatched += 1
            continue
        player, stint, crop = min(
            candidates,
            key=lambda row: (
                0 if _int_or_none(row[2].get("frame")) == old_frame else 1,
                0 if _crop_covers_frame(row[2], old_frame) else 1,
                _crop_time_distance(old_crop, row[2]),
            ),
        )
        artifact = str(crop["artifact"])
        used.add(artifact)
        migrated.append(
            {
                **assignment,
                "artifact": artifact,
                "frame": crop.get("frame"),
                "time_sec": crop.get("time_sec"),
                "stable_subject_id": player.get("stable_subject_id"),
                "stable_player_id": player.get("stable_player_id"),
                "stint_id": stint.get("stint_id"),
                "parent_stint_id": stint.get("parent_stint_id") or stint.get("stint_id"),
                "team_label": player.get("team_label") or "U",
                "migrated_from_artifact": assignment.get("artifact"),
            }
        )
    doc = {
        "schema_version": "0.2.0",
        "updated_at": now_iso(),
        "source": "manual_crop_identity_review",
        "assignments": sorted(migrated, key=lambda item: str(item.get("artifact") or "")),
        "derived_summary": {
            "migration_pending_refresh": True,
            "migrated_assignments": len(migrated),
            "unmatched_previous_assignments": unmatched,
        },
    }
    (path / ASSIGNMENTS_FILENAME).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return {"migrated": len(migrated), "unmatched": unmatched}


def _crop_time_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_time = _float_or_none(left.get("time_sec"))
    right_time = _float_or_none(right.get("time_sec"))
    if left_time is None or right_time is None:
        return 1_000_000.0
    return abs(left_time - right_time)


def _crop_covers_frame(crop: dict[str, Any], frame: int | None) -> bool:
    if frame is None:
        return False
    for interval in crop.get("coverage_intervals") or []:
        if not isinstance(interval, dict):
            continue
        start = _int_or_none(interval.get("start_frame"))
        end = _int_or_none(interval.get("end_frame"))
        if start is not None and end is not None and start <= frame <= end:
            return True
    return False


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
    for tracklet_id in _string_list(stint.get("tracklet_ids")):
        match = re.match(r"^(\d+)", tracklet_id)
        if match:
            ids.add(match.group(1))
    if ids:
        return sorted(ids)
    ids.update(str(item) for item in _int_list(player.get("raw_track_ids")))
    for tracklet_id in _string_list(player.get("tracklet_ids")):
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


def _select_representative_positions(
    cap: Any,
    positions: list[dict[str, Any]],
    stint: dict[str, Any],
) -> list[dict[str, Any]]:
    runs = _smoothed_track_runs(positions)
    if not runs:
        return []
    _attach_run_coverage(runs, stint)
    clusters_by_track: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        representative = _run_representative(run["positions"])
        signature = _read_position_signature(cap, representative)
        track_id = str(run["track_id"])
        clusters = clusters_by_track.setdefault(track_id, [])
        cluster = next(
            (
                candidate
                for candidate in clusters
                if _signature_distance(candidate["signature"], signature) <= APPEARANCE_CLUSTER_DISTANCE
            ),
            None,
        )
        if cluster is None:
            cluster = {
                "signature": signature,
                "positions": [],
                "coverage_intervals": [],
            }
            clusters.append(cluster)
        cluster["positions"].append(representative)
        cluster["coverage_intervals"].append(run["coverage"])

    selected: list[dict[str, Any]] = []
    for track_id, clusters in clusters_by_track.items():
        for cluster_index, cluster in enumerate(clusters, start=1):
            representative = max(cluster["positions"], key=_confidence)
            selected.append(
                {
                    **representative,
                    "_coverage_intervals": sorted(
                        cluster["coverage_intervals"],
                        key=lambda interval: int(interval["start_frame"]),
                    ),
                    "_representative_reason": "track_appearance_cluster",
                    "_appearance_cluster_id": f"{track_id}:{cluster_index}",
                }
            )
    return sorted(selected, key=lambda item: _int_or_none(item.get("frame")) or 0)


def _smoothed_track_runs(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(positions, key=lambda item: _int_or_none(item.get("frame")) or 0)
    runs: list[dict[str, Any]] = []
    for position in ordered:
        frame = _int_or_none(position.get("frame"))
        if frame is None:
            continue
        track_id = str(position.get("track_id") or "unknown")
        if runs and runs[-1]["track_id"] == track_id and frame - runs[-1]["end_frame"] <= 3:
            runs[-1]["positions"].append(position)
            runs[-1]["end_frame"] = frame
        else:
            runs.append(
                {
                    "track_id": track_id,
                    "start_frame": frame,
                    "end_frame": frame,
                    "positions": [position],
                }
            )

    for index, run in enumerate(runs):
        if len(run["positions"]) >= MIN_TRACK_RUN_FRAMES or len(runs) == 1:
            continue
        previous = runs[index - 1] if index > 0 else None
        following = runs[index + 1] if index + 1 < len(runs) else None
        if previous and following and previous["track_id"] == following["track_id"]:
            run["track_id"] = previous["track_id"]
        elif previous and following:
            run["track_id"] = (
                previous["track_id"]
                if len(previous["positions"]) >= len(following["positions"])
                else following["track_id"]
            )
        elif previous:
            run["track_id"] = previous["track_id"]
        elif following:
            run["track_id"] = following["track_id"]

    compressed: list[dict[str, Any]] = []
    for run in runs:
        if compressed and compressed[-1]["track_id"] == run["track_id"]:
            compressed[-1]["positions"].extend(run["positions"])
            compressed[-1]["end_frame"] = run["end_frame"]
        else:
            compressed.append(run)
    return compressed


def _attach_run_coverage(runs: list[dict[str, Any]], stint: dict[str, Any]) -> None:
    for run in runs:
        start_frame = int(run["start_frame"])
        end_frame = int(run["end_frame"])
        run["coverage"] = {
            "start_frame": start_frame,
            "end_frame": end_frame,
            "start_time_sec": _time_for_stint_frame(stint, start_frame),
            "end_time_sec": _time_for_stint_frame(stint, end_frame),
            "coverage_source": "observed_track_run",
        }


def _run_representative(positions: list[dict[str, Any]]) -> dict[str, Any]:
    if len(positions) <= 2:
        return max(positions, key=_confidence)
    start = len(positions) // 4
    end = max(start + 1, len(positions) - start)
    return max(positions[start:end], key=_confidence)


def _read_position_signature(cap: Any, position: dict[str, Any]) -> list[float]:
    import cv2

    frame_idx = _int_or_none(position.get("frame"))
    bbox = _bbox(position.get("bbox_xyxy"))
    if frame_idx is None or bbox is None:
        return []
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    return _appearance_signature(frame, bbox) if ok and frame is not None else []


def _signature_distance(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 1.0
    import cv2
    import numpy as np

    return float(
        cv2.compareHist(
            np.asarray(left, dtype="float32"),
            np.asarray(right, dtype="float32"),
            cv2.HISTCMP_BHATTACHARYYA,
        )
    )


def _time_for_stint_frame(stint: dict[str, Any], frame: int) -> float | None:
    start_frame = _int_or_none(stint.get("start_frame"))
    end_frame = _int_or_none(stint.get("end_frame"))
    start_time = _float_or_none(stint.get("start_time_sec"))
    end_time = _float_or_none(stint.get("end_time_sec"))
    if None in {start_frame, end_frame, start_time, end_time} or end_frame == start_frame:
        return None
    ratio = (frame - start_frame) / (end_frame - start_frame)
    return round(start_time + ratio * (end_time - start_time), 3)


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
    crop = frame[y1:y2, x1:x2].copy()
    signature = _appearance_signature(frame, bbox)
    similarity_descriptor = _similarity_descriptor(frame, bbox)
    target_left = max(0, int(round(bbox[0])) - x1)
    target_top = max(0, int(round(bbox[1])) - y1)
    target_right = min(crop.shape[1] - 1, int(round(bbox[2])) - x1)
    target_bottom = min(crop.shape[0] - 1, int(round(bbox[3])) - y1)
    cv2.rectangle(crop, (target_left, target_top), (target_right, target_bottom), (0, 220, 255), 2)
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
        "coverage_intervals": position.get("_coverage_intervals") or [],
        "coverage_frames": sum(
            int(interval["end_frame"]) - int(interval["start_frame"]) + 1
            for interval in position.get("_coverage_intervals") or []
        ),
        "representative_reason": position.get("_representative_reason"),
        "appearance_cluster_id": position.get("_appearance_cluster_id"),
        "similarity_descriptor": similarity_descriptor,
        "_appearance_signature": signature,
    }


def _appearance_signature(frame: Any, bbox: list[float]) -> list[float]:
    import cv2

    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    torso_left = max(0, min(width - 1, int(round(x1 + (x2 - x1) * 0.15))))
    torso_right = max(torso_left + 1, min(width, int(round(x2 - (x2 - x1) * 0.15))))
    torso_top = max(0, min(height - 1, int(round(y1 + (y2 - y1) * 0.16))))
    torso_bottom = max(torso_top + 1, min(height, int(round(y1 + (y2 - y1) * 0.62))))
    torso = frame[torso_top:torso_bottom, torso_left:torso_right]
    if torso.size == 0:
        return []
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return [round(float(value), 6) for value in hist.flatten()]


def _similarity_descriptor(frame: Any, bbox: list[float]) -> list[float]:
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    left = max(0, min(width - 1, int(round(x1 + (x2 - x1) * 0.08))))
    right = max(left + 1, min(width, int(round(x2 - (x2 - x1) * 0.08))))
    top = max(0, min(height - 1, int(round(y1 + (y2 - y1) * 0.04))))
    bottom = max(top + 1, min(height, int(round(y2))))
    person = frame[top:bottom, left:right]
    if person.size == 0:
        return []
    resized = cv2.resize(person, (4, 8), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB).astype("float32")
    lightness = lab[:, :, 0]
    lightness = (lightness - float(lightness.mean())) / max(float(lightness.std()), 12.0)
    channel_a = (lab[:, :, 1] - 128.0) / 64.0
    channel_b = (lab[:, :, 2] - 128.0) / 64.0
    descriptor = np.stack([lightness, channel_a, channel_b], axis=2).flatten()
    return [round(float(max(-3.0, min(3.0, value))), 4) for value in descriptor]


def _appearance_diagnostics(
    crops: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import cv2
    import numpy as np

    clean: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    previous_signature: list[float] | None = None
    max_change = 0.0
    for crop in crops:
        signature = crop.get("_appearance_signature", [])
        score = None
        if signature and previous_signature and len(signature) == len(previous_signature):
            score = float(
                cv2.compareHist(
                    np.asarray(previous_signature, dtype="float32"),
                    np.asarray(signature, dtype="float32"),
                    cv2.HISTCMP_BHATTACHARYYA,
                )
            )
            max_change = max(max_change, score)
            crop["appearance_change_from_previous"] = round(score, 3)
            if score >= 0.46:
                changes.append({"frame": crop.get("frame"), "score": round(score, 3)})
        if signature:
            previous_signature = signature
        clean.append(crop)
    purity = "mixed" if max_change >= 0.62 else "review" if max_change >= 0.46 else "consistent"
    return clean, {
        "appearance_purity": purity,
        "appearance_max_change": round(max_change, 3),
        "appearance_change_candidates": changes,
    }


def _deduplicate_player_crops(match_root: Path, stints: list[dict[str, Any]]) -> None:
    entries = [
        (stint, crop)
        for stint in stints
        for crop in stint.get("crops") or []
        if isinstance(crop, dict)
    ]
    clusters_by_fragment_track: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for stint, crop in sorted(entries, key=lambda item: _int_or_none(item[1].get("frame")) or 0):
        track_id = str(crop.get("track_id") or "unknown")
        fragment_id = str(stint.get("stint_id") or "unknown")
        signature = crop.get("_appearance_signature") or []
        clusters = clusters_by_fragment_track.setdefault((fragment_id, track_id), [])
        cluster = next(
            (
                candidate
                for candidate in clusters
                if _signature_distance(candidate["signature"], signature) <= APPEARANCE_CLUSTER_DISTANCE
            ),
            None,
        )
        if cluster is None:
            cluster = {"signature": signature, "entries": []}
            clusters.append(cluster)
        cluster["entries"].append((stint, crop))

    retained: set[str] = set()
    for (fragment_id, track_id), clusters in clusters_by_fragment_track.items():
        for cluster_index, cluster in enumerate(clusters, start=1):
            representative_stint, representative = max(
                cluster["entries"],
                key=lambda item: _confidence(item[1]),
            )
            intervals = [
                interval
                for _, crop in cluster["entries"]
                for interval in crop.get("coverage_intervals") or []
                if isinstance(interval, dict)
            ]
            representative["coverage_intervals"] = _merge_coverage_intervals(intervals)
            representative["coverage_frames"] = sum(
                int(interval["end_frame"]) - int(interval["start_frame"]) + 1
                for interval in representative["coverage_intervals"]
            )
            representative["representative_reason"] = "atomic_identity_fragment_track_cluster"
            representative["identity_fragment_id"] = fragment_id
            representative["appearance_cluster_id"] = f"{fragment_id}:{track_id}:{cluster_index}"
            retained.add(str(representative["artifact"]))
            for stint, crop in cluster["entries"]:
                if crop is representative and stint is representative_stint:
                    continue
                artifact = crop.get("artifact")
                if artifact:
                    try:
                        (match_root / str(artifact)).unlink()
                    except FileNotFoundError:
                        pass

    for stint in stints:
        stint["crops"] = [
            crop for crop in stint.get("crops") or [] if str(crop.get("artifact")) in retained
        ]
        for crop in stint["crops"]:
            crop["appearance_signature"] = crop.pop("_appearance_signature", [])
        stint["representative_clusters"] = len(stint["crops"])
        stint["represented_intervals"] = sum(
            len(crop.get("coverage_intervals") or []) for crop in stint["crops"]
        )


def _merge_coverage_intervals(intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        (
            dict(interval)
            for interval in intervals
            if isinstance(interval.get("start_frame"), int)
            and isinstance(interval.get("end_frame"), int)
        ),
        key=lambda interval: (interval["start_frame"], interval["end_frame"]),
    )
    merged: list[dict[str, Any]] = []
    for interval in ordered:
        if not merged or int(interval["start_frame"]) > int(merged[-1]["end_frame"]) + 1:
            merged.append(interval)
            continue
        merged[-1]["end_frame"] = max(int(merged[-1]["end_frame"]), int(interval["end_frame"]))
        if isinstance(interval.get("end_time_sec"), (int, float)):
            merged[-1]["end_time_sec"] = max(
                float(merged[-1].get("end_time_sec") or 0.0),
                float(interval["end_time_sec"]),
            )
    return merged


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
