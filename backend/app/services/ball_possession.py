from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.ball_tracking import _BallOverlayWriter, _draw_ball_position, _draw_frame_stamp

POSSESSION_SOURCE = "ball_possession_candidates_v1"

DEFAULT_CONTROL_DISTANCE_M = 1.8
DEFAULT_CONTESTED_DISTANCE_M = 2.4
DEFAULT_CONTESTED_DISTANCE_DELTA_M = 0.8
DEFAULT_FREE_DISTANCE_M = 4.0
DEFAULT_NEAREST_PLAYERS_LIMIT = 4
DEFAULT_MAX_PLAYER_INTERPOLATION_GAP_SEC = 0.6
DEFAULT_MAX_PLAYER_INTERPOLATION_SPEED_MPS = 9.5
DEFAULT_INTERPOLATED_PLAYER_CONFIDENCE = 0.55


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_ball_possession_analysis(
    match_dir: Path,
    video_path: Path,
    pitch: Any,
    video_metadata: dict[str, Any],
    ball_tracks_doc: dict[str, Any],
    stable_players_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    fps = float(video_metadata.get("fps") or 0.0)
    width = int(video_metadata.get("width") or 0)
    height = int(video_metadata.get("height") or 0)
    if fps <= 0 or width <= 0 or height <= 0:
        raise ValueError("Video metadata is missing fps/width/height for ball possession analysis.")

    parameters = {
        "source": POSSESSION_SOURCE,
        "control_distance_m": DEFAULT_CONTROL_DISTANCE_M,
        "contested_distance_m": DEFAULT_CONTESTED_DISTANCE_M,
        "contested_distance_delta_m": DEFAULT_CONTESTED_DISTANCE_DELTA_M,
        "free_distance_m": DEFAULT_FREE_DISTANCE_M,
        "nearest_players_limit": DEFAULT_NEAREST_PLAYERS_LIMIT,
        "trusted_player_sources": ["detected", "short_gap_interpolated"],
        "max_player_interpolation_gap_sec": DEFAULT_MAX_PLAYER_INTERPOLATION_GAP_SEC,
        "max_player_interpolation_speed_mps": DEFAULT_MAX_PLAYER_INTERPOLATION_SPEED_MPS,
        "interpolated_player_confidence": DEFAULT_INTERPOLATED_PLAYER_CONFIDENCE,
        "ball_sources": ["detected", "interpolated", "unknown"],
        "semantics": "candidate_layer_not_final_stats",
    }
    candidates_doc = build_possession_candidates_document(
        ball_tracks_doc,
        stable_players_doc or {},
        fps=fps,
        parameters=parameters,
    )
    segments_doc = build_possession_segments_document(candidates_doc, fps=fps)
    contact_doc = build_contact_candidates_document(candidates_doc, segments_doc)
    report_doc = build_possession_report(candidates_doc, segments_doc, contact_doc)
    _write_possession_artifacts(match_dir, candidates_doc, segments_doc, contact_doc, report_doc)
    write_possession_overlay(
        video_path,
        match_dir,
        candidates_doc,
        report_doc,
        pitch.polygon_np,
        pitch.homography(),
        fps=fps,
        frame_size=(width, height),
    )
    return {
        "possession_candidates": candidates_doc,
        "possession_segments": segments_doc,
        "contact_candidates": contact_doc,
        "possession_report": report_doc,
        "artifacts": {
            "possession_candidates": "possession_candidates.json",
            "possession_segments": "possession_segments.json",
            "contact_candidates": "contact_candidates.json",
            "possession_report": "possession_report.json",
            "possession_overlay_preview": "possession_overlay_preview.mp4",
        },
    }


def build_possession_candidates_document(
    ball_tracks_doc: dict[str, Any],
    stable_players_doc: dict[str, Any],
    *,
    fps: float,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_parameters = dict(parameters or {})
    event_players_by_frame = _event_players_by_frame(stable_players_doc, fps=fps, parameters=final_parameters)
    positions = ball_tracks_doc.get("positions") if isinstance(ball_tracks_doc.get("positions"), list) else []
    frames = [
        _classify_possession_frame(
            position,
            event_players_by_frame.get(int(position.get("frame") or 0), []),
            parameters=final_parameters,
        )
        for position in positions
        if isinstance(position, dict)
    ]
    summary = _possession_summary(frames, fps=fps)
    if not event_players_by_frame:
        summary["warnings"] = ["No trusted player positions were available; possession is unknown."]
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": POSSESSION_SOURCE,
        "experimental": True,
        "status_semantics": "controlled_contested_free_unknown",
        "parameters": final_parameters,
        "summary": summary,
        "frames": frames,
    }


def build_possession_segments_document(candidates_doc: dict[str, Any], *, fps: float) -> dict[str, Any]:
    frames = candidates_doc.get("frames") if isinstance(candidates_doc.get("frames"), list) else []
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    interval_sec = _frame_interval_sec(frames, fps=fps)
    for frame in frames:
        key = _segment_key(frame)
        if current is None or current.get("_key") != key:
            if current is not None:
                segments.append(_finalize_segment(current, interval_sec))
            current = _new_segment(frame, key)
            continue
        _extend_segment(current, frame)
    if current is not None:
        segments.append(_finalize_segment(current, interval_sec))

    status_counts = Counter(str(segment.get("status") or "unknown") for segment in segments)
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": POSSESSION_SOURCE,
        "experimental": True,
        "summary": {
            "segments_total": len(segments),
            "status_segments": dict(status_counts),
            "frame_interval_sec": round(interval_sec, 4),
        },
        "segments": segments,
    }


def build_contact_candidates_document(
    candidates_doc: dict[str, Any],
    segments_doc: dict[str, Any],
) -> dict[str, Any]:
    frames_by_index = {
        int(frame.get("frame") or 0): frame
        for frame in candidates_doc.get("frames", [])
        if isinstance(frame, dict)
    }
    candidates: list[dict[str, Any]] = []
    for segment in segments_doc.get("segments", []):
        if not isinstance(segment, dict) or segment.get("status") != "controlled":
            continue
        segment_frames = [
            frames_by_index[frame_idx]
            for frame_idx in range(int(segment["start_frame"]), int(segment["end_frame"]) + 1)
            if frame_idx in frames_by_index
        ]
        detected_frames = [frame for frame in segment_frames if frame.get("ball_source") == "detected"]
        if not detected_frames:
            continue
        distances = [
            float(frame.get("nearest_distance_m"))
            for frame in detected_frames
            if frame.get("nearest_distance_m") is not None
        ]
        confidences = [float(frame.get("confidence") or 0.0) for frame in detected_frames]
        player_source_counts = Counter(str(frame.get("nearest_player_source")) for frame in segment_frames if frame.get("nearest_player_source"))
        candidate_id = f"contact-{len(candidates) + 1:04d}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "stable_player_id": segment.get("stable_player_id"),
                "stable_subject_id": segment.get("stable_subject_id"),
                "team_label": segment.get("team_label"),
                "team_id": segment.get("team_id"),
                "team_name": segment.get("team_name"),
                "start_frame": segment.get("start_frame"),
                "end_frame": segment.get("end_frame"),
                "start_time_sec": segment.get("start_time_sec"),
                "end_time_sec": segment.get("end_time_sec"),
                "duration_sec": segment.get("duration_sec"),
                "frames": segment.get("frames"),
                "detected_ball_frames": len(detected_frames),
                "player_source_counts": dict(player_source_counts),
                "detected_player_frames": player_source_counts.get("detected", 0),
                "interpolated_player_frames": player_source_counts.get("short_gap_interpolated", 0),
                "mean_distance_m": round(sum(distances) / len(distances), 3) if distances else None,
                "min_distance_m": round(min(distances), 3) if distances else None,
                "mean_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
                "source": "controlled_ball_nearest_player",
                "status": "needs_review",
            }
        )
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": POSSESSION_SOURCE,
        "experimental": True,
        "summary": {
            "contact_candidates": len(candidates),
            "players_with_candidates": len({item.get("stable_player_id") for item in candidates if item.get("stable_player_id")}),
            "candidates_with_interpolated_player_positions": sum(
                1 for item in candidates if int(item.get("interpolated_player_frames") or 0) > 0
            ),
        },
        "candidates": candidates,
    }


def build_possession_report(
    candidates_doc: dict[str, Any],
    segments_doc: dict[str, Any],
    contact_doc: dict[str, Any],
) -> dict[str, Any]:
    summary = dict(candidates_doc.get("summary") or {})
    warnings = list(summary.pop("warnings", []) or [])
    if float(summary.get("known_possession_coverage") or 0.0) < 0.5:
        warnings.append("Known possession coverage is below 50%; do not use this as a final possession statistic.")
    if int(summary.get("contested_frames") or 0) > 0:
        warnings.append("Contested frames exist and require review before player/team possession stats.")
    if int(contact_doc.get("summary", {}).get("contact_candidates") or 0) == 0:
        warnings.append("No contact candidates were found with conservative thresholds.")
    if int(contact_doc.get("summary", {}).get("candidates_with_interpolated_player_positions") or 0) > 0:
        warnings.append("Some contact candidates use short-gap interpolated player positions and need review.")
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": POSSESSION_SOURCE,
        "status": "completed",
        "experimental": True,
        "summary": {
            **summary,
            "segments_total": (segments_doc.get("summary") or {}).get("segments_total", 0),
            "contact_candidates": (contact_doc.get("summary") or {}).get("contact_candidates", 0),
            "contact_candidates_with_interpolated_player_positions": (contact_doc.get("summary") or {}).get(
                "candidates_with_interpolated_player_positions", 0
            ),
        },
        "warnings": warnings,
        "notes": [
            "This is a conservative candidate layer, not a final possession or pass statistic.",
            "Unknown means the ball or trusted player position was missing; it is intentionally not guessed.",
        ],
    }


def write_possession_overlay(
    video_path: Path,
    match_dir: Path,
    candidates_doc: dict[str, Any],
    report_doc: dict[str, Any],
    pitch_polygon: Any,
    homography: Any,
    *,
    fps: float,
    frame_size: tuple[int, int],
    output_name: str = "possession_overlay_preview.mp4",
) -> Path:
    import cv2
    import numpy as np

    frame_rows = {
        int(frame.get("frame") or 0): frame
        for frame in candidates_doc.get("frames", [])
        if isinstance(frame, dict)
    }
    if not frame_rows:
        raise RuntimeError("Possession overlay was not generated because no possession frames exist.")
    max_frame = max(frame_rows)
    frame_stride = max(1, int((candidates_doc.get("parameters") or {}).get("frame_stride") or 1))
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video for possession overlay: {video_path}")
    writer = _BallOverlayWriter(match_dir, output_name, fps=max(fps / frame_stride, 1.0), frame_size=frame_size)
    inverse_h = np.linalg.inv(np.asarray(homography, dtype=np.float32))
    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame_idx > max_frame:
                break
            if frame_idx not in frame_rows:
                frame_idx += 1
                continue
            overlay = frame.copy()
            cv2.polylines(overlay, [pitch_polygon.astype(np.int32)], isClosed=True, color=(0, 255, 255), thickness=2)
            row = frame_rows[frame_idx]
            _draw_ball_position(overlay, _row_to_ball_position(row))
            _draw_nearest_players(overlay, row, inverse_h)
            _draw_possession_hud(overlay, row, report_doc.get("summary") or {}, fps=fps)
            _draw_frame_stamp(overlay, frame_idx)
            writer.write(overlay)
            frame_idx += 1
    finally:
        cap.release()
    return writer.close()


def _classify_possession_frame(
    ball_position: dict[str, Any],
    players: list[dict[str, Any]],
    *,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    frame_idx = int(ball_position.get("frame") or 0)
    base = {
        "frame": frame_idx,
        "time_sec": ball_position.get("time_sec"),
        "ball_source": ball_position.get("source") or "unknown",
        "ball_confidence": round(float(ball_position.get("confidence") or 0.0), 4),
        "ball_position_m": ball_position.get("position_m"),
        "ball_position_px": ball_position.get("position_px"),
        "ball_bbox_xyxy": ball_position.get("bbox_xyxy"),
    }
    if ball_position.get("source") == "unknown" or not _valid_pair(ball_position.get("position_m")):
        return _unknown_frame(base, "ball_unknown")
    if not players:
        return _unknown_frame(base, "no_trusted_detected_player_positions")

    ball_m = [float(ball_position["position_m"][0]), float(ball_position["position_m"][1])]
    nearest_players = sorted(
        [
            {
                **player,
                "distance_m": round(_distance_m(ball_m, player["position_m"]), 3),
            }
            for player in players
            if _valid_pair(player.get("position_m"))
        ],
        key=lambda item: float(item["distance_m"]),
    )
    if not nearest_players:
        return _unknown_frame(base, "no_valid_trusted_player_positions")

    limit = int(parameters.get("nearest_players_limit") or DEFAULT_NEAREST_PLAYERS_LIMIT)
    nearest = nearest_players[0]
    second = nearest_players[1] if len(nearest_players) > 1 else None
    nearest_distance = float(nearest["distance_m"])
    control_distance = float(parameters.get("control_distance_m") or DEFAULT_CONTROL_DISTANCE_M)
    contested_distance = float(parameters.get("contested_distance_m") or DEFAULT_CONTESTED_DISTANCE_M)
    contested_delta = float(parameters.get("contested_distance_delta_m") or DEFAULT_CONTESTED_DISTANCE_DELTA_M)
    free_distance = float(parameters.get("free_distance_m") or DEFAULT_FREE_DISTANCE_M)

    is_contested = bool(
        second
        and nearest_distance <= contested_distance
        and float(second["distance_m"]) <= contested_distance
        and float(second["distance_m"]) - nearest_distance <= contested_delta
    )
    if is_contested:
        status = "contested"
        reason = "multiple_players_close_to_ball"
        confidence = _contested_confidence(ball_position, nearest, second, contested_distance)
        owner = {}
    elif nearest_distance <= control_distance:
        status = "controlled"
        reason = "nearest_player_within_control_distance"
        confidence = _controlled_confidence(ball_position, nearest, control_distance)
        owner = _owner_fields(nearest)
    elif nearest_distance >= free_distance:
        status = "free"
        reason = "nearest_player_beyond_free_distance"
        confidence = _free_confidence(ball_position, nearest_distance, free_distance)
        owner = {}
    else:
        status = "free"
        reason = "nearest_player_outside_control_distance"
        confidence = min(_free_confidence(ball_position, nearest_distance, free_distance), 0.55)
        owner = {}

    return {
        **base,
        "status": status,
        "confidence": round(confidence, 4),
        "reason": reason,
        **owner,
        "nearest_distance_m": round(nearest_distance, 3),
        "second_distance_m": round(float(second["distance_m"]), 3) if second else None,
        "nearest_player_source": nearest.get("player_source"),
        "nearest_player_position_confidence": nearest.get("player_position_confidence"),
        "nearest_players": [_public_player_distance(player) for player in nearest_players[:limit]],
    }


def _event_players_by_frame(
    stable_players_doc: dict[str, Any],
    *,
    fps: float,
    parameters: dict[str, Any],
) -> dict[int, list[dict[str, Any]]]:
    players_by_frame: dict[int, list[dict[str, Any]]] = {}
    for player in stable_players_doc.get("players") or stable_players_doc.get("slots") or []:
        if not isinstance(player, dict):
            continue
        rows = [
            row
            for row in player.get("trajectory_m") or []
            if isinstance(row, dict) and row.get("frame") is not None and _valid_pair(row.get("pitch_m"))
        ]
        rows.sort(key=lambda item: int(item.get("frame") or 0))
        row_by_frame = {int(row.get("frame") or 0): row for row in rows}
        detected_rows = [
            row
            for row in rows
            if row.get("source") == "detected" or row.get("status") == "detected"
        ]
        for row in detected_rows:
            _add_player_position(
                players_by_frame,
                player,
                row,
                player_source="detected",
                player_position_confidence=1.0,
            )
        for start, end in zip(detected_rows, detected_rows[1:]):
            start_frame = int(start.get("frame") or 0)
            end_frame = int(end.get("frame") or 0)
            if end_frame <= start_frame + 1:
                continue
            dt = (end_frame - start_frame) / max(fps, 0.001)
            if dt > float(parameters.get("max_player_interpolation_gap_sec") or DEFAULT_MAX_PLAYER_INTERPOLATION_GAP_SEC):
                continue
            distance = _distance_m(start.get("pitch_m"), end.get("pitch_m"))
            speed = distance / max(dt, 0.001)
            if speed > float(parameters.get("max_player_interpolation_speed_mps") or DEFAULT_MAX_PLAYER_INTERPOLATION_SPEED_MPS):
                continue
            for frame_idx in range(start_frame + 1, end_frame):
                row = row_by_frame.get(frame_idx)
                if not row or not _valid_pair(row.get("pitch_m")):
                    continue
                if row.get("source") == "ambiguous" or row.get("status") == "ambiguous":
                    continue
                _add_player_position(
                    players_by_frame,
                    player,
                    row,
                    player_source="short_gap_interpolated",
                    player_position_confidence=float(
                        parameters.get("interpolated_player_confidence") or DEFAULT_INTERPOLATED_PLAYER_CONFIDENCE
                    ),
                    interpolated_from=[start_frame, end_frame],
                    interpolation_gap_sec=dt,
                )
    return players_by_frame


def _add_player_position(
    players_by_frame: dict[int, list[dict[str, Any]]],
    player: dict[str, Any],
    row: dict[str, Any],
    *,
    player_source: str,
    player_position_confidence: float,
    interpolated_from: list[int] | None = None,
    interpolation_gap_sec: float | None = None,
) -> None:
    frame_idx = int(row.get("frame") or 0)
    item = {
        "stable_player_id": player.get("stable_player_id") or player.get("slot_id"),
        "stable_subject_id": player.get("stable_subject_id"),
        "slot_id": player.get("slot_id"),
        "team_label": player.get("team_label") or "unknown",
        "team_id": player.get("team_id"),
        "team_name": player.get("team_name"),
        "confidence_score": player.get("confidence_score"),
        "position_m": [float(row["pitch_m"][0]), float(row["pitch_m"][1])],
        "player_source": player_source,
        "player_position_confidence": round(float(player_position_confidence), 4),
    }
    if interpolated_from is not None:
        item["interpolated_from"] = interpolated_from
    if interpolation_gap_sec is not None:
        item["interpolation_gap_sec"] = round(float(interpolation_gap_sec), 3)
    players_by_frame.setdefault(frame_idx, []).append(item)


def _unknown_frame(base: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        **base,
        "status": "unknown",
        "confidence": 0.0,
        "reason": reason,
        "stable_player_id": None,
        "stable_subject_id": None,
        "team_label": None,
        "team_id": None,
        "team_name": None,
        "nearest_distance_m": None,
        "second_distance_m": None,
        "nearest_player_source": None,
        "nearest_player_position_confidence": None,
        "nearest_players": [],
    }


def _owner_fields(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_player_id": player.get("stable_player_id"),
        "stable_subject_id": player.get("stable_subject_id"),
        "slot_id": player.get("slot_id"),
        "team_label": player.get("team_label"),
        "team_id": player.get("team_id"),
        "team_name": player.get("team_name"),
    }


def _public_player_distance(player: dict[str, Any]) -> dict[str, Any]:
    output = {
        "stable_player_id": player.get("stable_player_id"),
        "stable_subject_id": player.get("stable_subject_id"),
        "slot_id": player.get("slot_id"),
        "team_label": player.get("team_label"),
        "team_id": player.get("team_id"),
        "team_name": player.get("team_name"),
        "position_m": [round(float(player["position_m"][0]), 3), round(float(player["position_m"][1]), 3)],
        "distance_m": player.get("distance_m"),
        "player_source": player.get("player_source"),
        "player_position_confidence": player.get("player_position_confidence"),
    }
    if player.get("interpolated_from") is not None:
        output["interpolated_from"] = player.get("interpolated_from")
    if player.get("interpolation_gap_sec") is not None:
        output["interpolation_gap_sec"] = player.get("interpolation_gap_sec")
    return output


def _possession_summary(frames: list[dict[str, Any]], *, fps: float) -> dict[str, Any]:
    total = len(frames)
    status_counts = Counter(str(frame.get("status") or "unknown") for frame in frames)
    team_counts = Counter(str(frame.get("team_label")) for frame in frames if frame.get("status") == "controlled" and frame.get("team_label"))
    player_counts = Counter(str(frame.get("stable_player_id")) for frame in frames if frame.get("status") == "controlled" and frame.get("stable_player_id"))
    nearest_source_counts = Counter(str(frame.get("nearest_player_source")) for frame in frames if frame.get("nearest_player_source"))
    interval_sec = _frame_interval_sec(frames, fps=fps)
    return {
        "processed_frames": total,
        "frame_interval_sec": round(interval_sec, 4),
        "controlled_frames": status_counts.get("controlled", 0),
        "contested_frames": status_counts.get("contested", 0),
        "free_frames": status_counts.get("free", 0),
        "unknown_frames": status_counts.get("unknown", 0),
        "known_possession_frames": total - status_counts.get("unknown", 0),
        "controlled_coverage": _ratio(status_counts.get("controlled", 0), total),
        "contested_coverage": _ratio(status_counts.get("contested", 0), total),
        "free_coverage": _ratio(status_counts.get("free", 0), total),
        "unknown_coverage": _ratio(status_counts.get("unknown", 0), total),
        "known_possession_coverage": _ratio(total - status_counts.get("unknown", 0), total),
        "team_controlled_frames": dict(team_counts),
        "player_controlled_frames": dict(player_counts),
        "nearest_player_source_frames": dict(nearest_source_counts),
        "detected_player_position_frames": nearest_source_counts.get("detected", 0),
        "interpolated_player_position_frames": nearest_source_counts.get("short_gap_interpolated", 0),
        "longest_unknown_streak_frames": _longest_status_streak(frames, "unknown"),
    }


def _new_segment(frame: dict[str, Any], key: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "_key": key,
        "status": frame.get("status"),
        "stable_player_id": frame.get("stable_player_id"),
        "stable_subject_id": frame.get("stable_subject_id"),
        "team_label": frame.get("team_label"),
        "team_id": frame.get("team_id"),
        "team_name": frame.get("team_name"),
        "start_frame": int(frame.get("frame") or 0),
        "end_frame": int(frame.get("frame") or 0),
        "start_time_sec": frame.get("time_sec"),
        "end_time_sec": frame.get("time_sec"),
        "frames": 1,
        "confidence_sum": float(frame.get("confidence") or 0.0),
        "ball_source_counts": _counter_from_value(frame.get("ball_source")),
        "player_source_counts": _counter_from_value(frame.get("nearest_player_source")),
        "nearest_distance_values": [
            float(frame["nearest_distance_m"])
        ] if frame.get("nearest_distance_m") is not None else [],
    }


def _extend_segment(segment: dict[str, Any], frame: dict[str, Any]) -> None:
    segment["end_frame"] = int(frame.get("frame") or 0)
    segment["end_time_sec"] = frame.get("time_sec")
    segment["frames"] = int(segment.get("frames") or 0) + 1
    segment["confidence_sum"] = float(segment.get("confidence_sum") or 0.0) + float(frame.get("confidence") or 0.0)
    _counter_increment(segment.setdefault("ball_source_counts", {}), frame.get("ball_source"))
    _counter_increment(segment.setdefault("player_source_counts", {}), frame.get("nearest_player_source"))
    if frame.get("nearest_distance_m") is not None:
        segment.setdefault("nearest_distance_values", []).append(float(frame["nearest_distance_m"]))


def _finalize_segment(segment: dict[str, Any], interval_sec: float) -> dict[str, Any]:
    frames = int(segment.get("frames") or 0)
    distances = segment.get("nearest_distance_values") or []
    segment_id = f"pos-{int(segment['start_frame']):06d}-{int(segment['end_frame']):06d}-{segment.get('status')}"
    output = {
        key: value
        for key, value in segment.items()
        if key not in {"_key", "confidence_sum", "nearest_distance_values"}
    }
    output.update(
        {
            "segment_id": segment_id,
            "duration_sec": round(frames * interval_sec, 3),
            "mean_confidence": round(float(segment.get("confidence_sum") or 0.0) / max(frames, 1), 4),
            "mean_nearest_distance_m": round(sum(distances) / len(distances), 3) if distances else None,
            "min_nearest_distance_m": round(min(distances), 3) if distances else None,
        }
    )
    return output


def _segment_key(frame: dict[str, Any]) -> tuple[Any, ...]:
    status = frame.get("status") or "unknown"
    if status == "controlled":
        return (status, frame.get("stable_player_id"), frame.get("team_label"))
    return (status,)


def _controlled_confidence(ball_position: dict[str, Any], nearest_player: dict[str, Any], control_distance: float) -> float:
    ball_conf = float(ball_position.get("confidence") or 0.0)
    source_factor = 1.0 if ball_position.get("source") == "detected" else 0.65
    player_factor = _player_source_factor(nearest_player)
    nearest_distance = float(nearest_player.get("distance_m") or 0.0)
    distance_score = max(0.0, 1.0 - nearest_distance / max(control_distance, 0.001))
    return min(0.95, (0.45 + ball_conf * 0.35 + distance_score * 0.2) * source_factor * player_factor)


def _contested_confidence(
    ball_position: dict[str, Any],
    nearest_player: dict[str, Any],
    second_player: dict[str, Any],
    contested_distance: float,
) -> float:
    ball_conf = float(ball_position.get("confidence") or 0.0)
    nearest_distance = float(nearest_player.get("distance_m") or 0.0)
    second_distance = float(second_player.get("distance_m") or 0.0)
    player_factor = min(_player_source_factor(nearest_player), _player_source_factor(second_player))
    proximity = max(0.0, 1.0 - ((nearest_distance + second_distance) / 2.0) / max(contested_distance, 0.001))
    return min(0.9, (0.35 + ball_conf * 0.35 + proximity * 0.2) * player_factor)


def _free_confidence(ball_position: dict[str, Any], nearest_distance: float, free_distance: float) -> float:
    ball_conf = float(ball_position.get("confidence") or 0.0)
    distance_score = min(1.0, nearest_distance / max(free_distance, 0.001))
    return min(0.85, 0.2 + ball_conf * 0.35 + distance_score * 0.25)


def _player_source_factor(player: dict[str, Any]) -> float:
    if player.get("player_source") == "detected":
        return 1.0
    confidence = float(player.get("player_position_confidence") or DEFAULT_INTERPOLATED_PLAYER_CONFIDENCE)
    return max(0.3, min(0.75, confidence))


def _row_to_ball_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "frame": row.get("frame"),
        "time_sec": row.get("time_sec"),
        "position_px": row.get("ball_position_px"),
        "position_m": row.get("ball_position_m"),
        "bbox_xyxy": row.get("ball_bbox_xyxy"),
        "source": row.get("ball_source"),
        "confidence": row.get("ball_confidence"),
    }


def _draw_nearest_players(frame: Any, row: dict[str, Any], inverse_homography: Any) -> None:
    import cv2

    ball_px = row.get("ball_position_px")
    nearest_players = row.get("nearest_players") if isinstance(row.get("nearest_players"), list) else []
    status = row.get("status")
    colors = {
        "controlled": (60, 220, 60),
        "contested": (0, 180, 255),
        "free": (180, 180, 180),
        "unknown": (120, 120, 120),
    }
    color = colors.get(str(status), (180, 180, 180))
    for index, player in enumerate(nearest_players[:3]):
        point = _pitch_to_image_px(player.get("position_m"), inverse_homography)
        if point is None:
            continue
        radius = 7 if index == 0 else 5
        cv2.circle(frame, point, radius, color if index == 0 else (160, 160, 160), 2, cv2.LINE_AA)
        source_mark = "~" if player.get("player_source") == "short_gap_interpolated" else ""
        label = f"{player.get('stable_player_id')}{source_mark} {float(player.get('distance_m') or 0.0):.1f}m"
        cv2.putText(frame, label, (point[0] + 8, max(16, point[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(frame, label, (point[0] + 8, max(16, point[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
        if index == 0 and _valid_pair(ball_px):
            ball_point = (int(round(float(ball_px[0]))), int(round(float(ball_px[1]))))
            cv2.line(frame, ball_point, point, color, 2, cv2.LINE_AA)


def _draw_possession_hud(frame: Any, row: dict[str, Any], summary: dict[str, Any], *, fps: float) -> None:
    import cv2

    holder = row.get("stable_player_id") or row.get("team_label") or "n/a"
    player_source = row.get("nearest_player_source") or "n/a"
    lines = [
        f"possession v1 | frame={int(row.get('frame') or 0)} t={float(row.get('time_sec') or 0.0):.1f}s",
        f"status={row.get('status')} holder={holder} conf={float(row.get('confidence') or 0.0):.2f}",
        f"ball={row.get('ball_source')} ball_conf={float(row.get('ball_confidence') or 0.0):.2f} nearest={_format_distance(row.get('nearest_distance_m'))} player={player_source}",
        f"coverage ctrl={_percent(summary.get('controlled_coverage'))} cont={_percent(summary.get('contested_coverage'))} unk={_percent(summary.get('unknown_coverage'))}",
    ]
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.42
    thickness = 1
    line_height = 16
    widths = [cv2.getTextSize(line, font, font_scale, thickness)[0][0] for line in lines]
    width = max(widths, default=360) + 18
    height = line_height * len(lines) + 14
    x1 = 12
    y1 = 12
    x2 = min(frame.shape[1] - 12, x1 + width)
    y2 = min(frame.shape[0] - 12, y1 + height)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 20, 20), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (230, 230, 230), 1)
    for index, line in enumerate(lines):
        y = y1 + 20 + index * line_height
        cv2.putText(frame, line, (x1 + 8, y), font, font_scale, (245, 245, 245), thickness, cv2.LINE_AA)


def _pitch_to_image_px(pitch_m: Any, inverse_homography: Any) -> tuple[int, int] | None:
    if not _valid_pair(pitch_m):
        return None
    import cv2
    import numpy as np

    src = np.array([[[float(pitch_m[0]), float(pitch_m[1])]]], dtype=np.float32)
    dst = cv2.perspectiveTransform(src, inverse_homography)
    return (int(round(float(dst[0][0][0]))), int(round(float(dst[0][0][1]))))


def _write_possession_artifacts(
    match_dir: Path,
    candidates_doc: dict[str, Any],
    segments_doc: dict[str, Any],
    contact_doc: dict[str, Any],
    report_doc: dict[str, Any],
) -> None:
    (match_dir / "possession_candidates.json").write_text(json.dumps(candidates_doc, indent=2), encoding="utf-8")
    (match_dir / "possession_segments.json").write_text(json.dumps(segments_doc, indent=2), encoding="utf-8")
    (match_dir / "contact_candidates.json").write_text(json.dumps(contact_doc, indent=2), encoding="utf-8")
    (match_dir / "possession_report.json").write_text(json.dumps(report_doc, indent=2), encoding="utf-8")


def _valid_pair(value: Any) -> bool:
    return isinstance(value, list | tuple) and len(value) == 2 and value[0] is not None and value[1] is not None


def _distance_m(a: Any, b: Any) -> float:
    return float(((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2) ** 0.5)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _longest_status_streak(frames: list[dict[str, Any]], status: str) -> int:
    best = 0
    current = 0
    for frame in frames:
        if frame.get("status") == status:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _counter_from_value(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    return {str(value): 1}


def _counter_increment(counter: dict[str, int], value: Any) -> None:
    if value is None:
        return
    key = str(value)
    counter[key] = int(counter.get(key) or 0) + 1


def _frame_interval_sec(frames: list[dict[str, Any]], *, fps: float) -> float:
    previous_frame: int | None = None
    for frame in frames:
        current = int(frame.get("frame") or 0)
        if previous_frame is not None and current > previous_frame:
            return (current - previous_frame) / max(fps, 0.001)
        previous_frame = current
    return 1.0 / max(fps, 0.001)


def _format_distance(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}m"


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "--"
