from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.ball_tracking import _BallOverlayWriter, _draw_ball_position, _draw_frame_stamp
from app.services.contact_auto_review import apply_auto_contact_review
from app.services.event_candidates import build_event_candidate_artifacts
from app.services.match_phase_config import load_match_phase_config
from app.services.pass_candidates import build_pass_review_report, update_pass_candidate_summary

POSSESSION_SOURCE = "ball_possession_candidates_v1"
RESTART_SOURCE = "ground_restart_candidates_v1"

DEFAULT_CONTROL_DISTANCE_M = 1.8
DEFAULT_CONTESTED_DISTANCE_M = 2.4
DEFAULT_CONTESTED_DISTANCE_DELTA_M = 0.8
DEFAULT_FREE_DISTANCE_M = 4.0
DEFAULT_NEAREST_PLAYERS_LIMIT = 4
DEFAULT_MAX_PLAYER_INTERPOLATION_GAP_SEC = 0.6
DEFAULT_MAX_PLAYER_INTERPOLATION_SPEED_MPS = 9.5
DEFAULT_INTERPOLATED_PLAYER_CONFIDENCE = 0.55
DEFAULT_FLY_THROUGH_CLOSE_DISTANCE_M = 0.95
DEFAULT_FLY_THROUGH_MIN_SPEED_MPS = 7.5
DEFAULT_FLY_THROUGH_MIN_STRAIGHTNESS = 0.85
DEFAULT_FLY_THROUGH_MIN_PATH_DISTANCE_M = 1.5
DEFAULT_FLY_THROUGH_MIN_FRAMES = 3
DEFAULT_RESTART_BOUNDARY_DISTANCE_M = 0.8
DEFAULT_RESTART_CORNER_ZONE_M = 5.0
DEFAULT_RESTART_SETUP_MIN_FRAMES = 8
DEFAULT_RESTART_SETUP_MAX_SPEED_MPS = 0.9
DEFAULT_RESTART_SETUP_MAX_DISPLACEMENT_M = 0.7
DEFAULT_RESTART_RELEASE_MIN_SPEED_MPS = 2.0
DEFAULT_RESTART_RELEASE_MIN_DISPLACEMENT_M = 0.9
DEFAULT_RESTART_RELEASE_LOOKAHEAD_FRAMES = 45
DEFAULT_RESTART_RECEIVER_LOOKAHEAD_FRAMES = 120
DEFAULT_RESTART_ACTOR_DISTANCE_M = 2.6
DEFAULT_RESTART_LAST_TOUCH_LOOKBACK_FRAMES = 180
DEFAULT_RESTART_OUT_OF_PLAY_MARGIN_M = 0.25


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_ball_possession_analysis(
    match_dir: Path,
    video_path: Path,
    pitch: Any,
    video_metadata: dict[str, Any],
    ball_tracks_doc: dict[str, Any],
    stable_players_doc: dict[str, Any] | None,
    *,
    write_overlay_video: bool = True,
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
        "fly_through_close_distance_m": DEFAULT_FLY_THROUGH_CLOSE_DISTANCE_M,
        "fly_through_min_speed_mps": DEFAULT_FLY_THROUGH_MIN_SPEED_MPS,
        "fly_through_min_straightness": DEFAULT_FLY_THROUGH_MIN_STRAIGHTNESS,
        "fly_through_min_path_distance_m": DEFAULT_FLY_THROUGH_MIN_PATH_DISTANCE_M,
        "fly_through_min_frames": DEFAULT_FLY_THROUGH_MIN_FRAMES,
        "restart_boundary_distance_m": DEFAULT_RESTART_BOUNDARY_DISTANCE_M,
        "restart_corner_zone_m": DEFAULT_RESTART_CORNER_ZONE_M,
        "restart_setup_min_frames": DEFAULT_RESTART_SETUP_MIN_FRAMES,
        "restart_setup_max_speed_mps": DEFAULT_RESTART_SETUP_MAX_SPEED_MPS,
        "restart_setup_max_displacement_m": DEFAULT_RESTART_SETUP_MAX_DISPLACEMENT_M,
        "restart_release_min_speed_mps": DEFAULT_RESTART_RELEASE_MIN_SPEED_MPS,
        "restart_release_min_displacement_m": DEFAULT_RESTART_RELEASE_MIN_DISPLACEMENT_M,
        "restart_release_lookahead_frames": DEFAULT_RESTART_RELEASE_LOOKAHEAD_FRAMES,
        "restart_receiver_lookahead_frames": DEFAULT_RESTART_RECEIVER_LOOKAHEAD_FRAMES,
        "restart_actor_distance_m": DEFAULT_RESTART_ACTOR_DISTANCE_M,
        "restart_last_touch_lookback_frames": DEFAULT_RESTART_LAST_TOUCH_LOOKBACK_FRAMES,
        "restart_out_of_play_margin_m": DEFAULT_RESTART_OUT_OF_PLAY_MARGIN_M,
        "pitch_width_m": float(getattr(pitch, "width_m", 30.0) or 30.0),
        "pitch_length_m": float(getattr(pitch, "length_m", 47.4) or 47.4),
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
    match_phase_config = load_match_phase_config(match_dir, {"video": video_metadata})
    event_docs = build_event_candidate_artifacts(contact_doc, match_phase_config, candidates_doc)
    restart_doc = build_restart_candidates_document(
        ball_tracks_doc,
        candidates_doc,
        stable_players_doc or {},
        fps=fps,
        parameters=parameters,
    )
    _append_restart_pass_candidates(event_docs["pass_candidates"], restart_doc)
    event_docs["pass_review_report"] = build_pass_review_report(event_docs["pass_candidates"])
    event_docs["restart_candidates"] = restart_doc
    event_docs.setdefault("artifacts", {})["restart_candidates"] = "restart_candidates.json"
    report_doc = build_possession_report(candidates_doc, segments_doc, contact_doc, restart_doc)
    _write_possession_artifacts(match_dir, candidates_doc, segments_doc, contact_doc, event_docs, report_doc)
    artifacts = {
        "possession_candidates": "possession_candidates.json",
        "possession_segments": "possession_segments.json",
        "contact_candidates": "contact_candidates.json",
        "match_phase_config": "match_phase_config.json",
        **event_docs["artifacts"],
        "possession_report": "possession_report.json",
    }
    if write_overlay_video:
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
        artifacts["possession_overlay_preview"] = "possession_overlay_preview.mp4"
    return {
        "possession_candidates": candidates_doc,
        "possession_segments": segments_doc,
        "contact_candidates": contact_doc,
        "event_candidates": event_docs["event_candidates"],
        "event_review_report": event_docs["event_review_report"],
        "restart_candidates": restart_doc,
        "pass_candidates": event_docs["pass_candidates"],
        "pass_review_report": event_docs["pass_review_report"],
        "possession_report": report_doc,
        "artifacts": artifacts,
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
    frames = _suppress_fly_through_control_segments(frames, fps=fps, parameters=final_parameters)
    summary = _possession_summary(frames, fps=fps)
    if not event_players_by_frame:
        summary["warnings"] = ["No trusted player positions were available; possession is unknown."]
    document = {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": POSSESSION_SOURCE,
        "experimental": True,
        "status_semantics": "controlled_contested_free_unknown",
        "parameters": final_parameters,
        "summary": summary,
        "frames": frames,
    }
    return document


def _suppress_fly_through_control_segments(
    frames: list[dict[str, Any]],
    *,
    fps: float,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_key: tuple[Any, ...] | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        if _controlled_segment_is_fly_through(current, fps=fps, parameters=parameters):
            metrics = _ball_motion_metrics(current)
            updated.extend(_fly_through_frame(frame, metrics) for frame in current)
        else:
            updated.extend(current)
        current = []

    for frame in frames:
        key = _segment_key(frame)
        if current and key != current_key:
            flush()
        current.append(frame)
        current_key = key
    flush()
    return updated


def _controlled_segment_is_fly_through(
    segment_frames: list[dict[str, Any]],
    *,
    fps: float,
    parameters: dict[str, Any],
) -> bool:
    if not segment_frames or segment_frames[0].get("status") != "controlled":
        return False
    if len(segment_frames) < int(parameters.get("fly_through_min_frames") or DEFAULT_FLY_THROUGH_MIN_FRAMES):
        return False
    distances = [
        float(frame.get("nearest_distance_m"))
        for frame in segment_frames
        if frame.get("nearest_distance_m") is not None
    ]
    if not distances:
        return False
    if min(distances) <= float(parameters.get("fly_through_close_distance_m") or DEFAULT_FLY_THROUGH_CLOSE_DISTANCE_M):
        return False
    metrics = _ball_motion_metrics(segment_frames)
    mean_speed = float(metrics.get("mean_ball_speed_mps") or 0.0)
    straightness = float(metrics.get("ball_path_straightness") or 0.0)
    path_distance = float(metrics.get("ball_path_distance_m") or 0.0)
    if path_distance < float(parameters.get("fly_through_min_path_distance_m") or DEFAULT_FLY_THROUGH_MIN_PATH_DISTANCE_M):
        return False
    if mean_speed < float(parameters.get("fly_through_min_speed_mps") or DEFAULT_FLY_THROUGH_MIN_SPEED_MPS):
        return False
    if straightness < float(parameters.get("fly_through_min_straightness") or DEFAULT_FLY_THROUGH_MIN_STRAIGHTNESS):
        return False
    return True


def _fly_through_frame(frame: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    output = dict(frame)
    output["status"] = "free"
    output["reason"] = "fly_through_no_close_control"
    output["confidence"] = round(min(float(frame.get("confidence") or 0.0), 0.45), 4)
    output["suppressed_stable_player_id"] = frame.get("stable_player_id")
    output["suppressed_stable_subject_id"] = frame.get("stable_subject_id")
    output["suppressed_team_label"] = frame.get("team_label")
    output["stable_player_id"] = None
    output["stable_subject_id"] = None
    output["slot_id"] = None
    output["team_label"] = None
    output["team_id"] = None
    output["team_name"] = None
    output.update(metrics)
    return output


def _ball_motion_metrics(frames: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        frame
        for frame in sorted(frames, key=lambda item: int(item.get("frame") or 0))
        if _valid_pair(frame.get("ball_position_m"))
    ]
    if len(rows) < 2:
        return {
            "ball_path_distance_m": 0.0,
            "ball_displacement_m": 0.0,
            "mean_ball_speed_mps": 0.0,
            "max_step_ball_speed_mps": 0.0,
            "ball_path_straightness": 0.0,
        }
    points = [row["ball_position_m"] for row in rows]
    distances = [_distance_m(points[index - 1], points[index]) for index in range(1, len(points))]
    path_distance = sum(float(distance) for distance in distances)
    displacement = _distance_m(points[0], points[-1])
    start_time = _frame_time_sec(rows[0])
    end_time = _frame_time_sec(rows[-1])
    duration = max(0.0, end_time - start_time)
    if duration <= 0:
        duration = max(0.0, (int(rows[-1].get("frame") or 0) - int(rows[0].get("frame") or 0)) / 30.0)
    step_speeds = []
    for previous, current, distance in zip(rows, rows[1:], distances):
        dt = _frame_time_sec(current) - _frame_time_sec(previous)
        if dt > 0:
            step_speeds.append(float(distance) / dt)
    return {
        "ball_path_distance_m": round(path_distance, 3),
        "ball_displacement_m": round(float(displacement), 3),
        "mean_ball_speed_mps": round(path_distance / max(duration, 0.001), 3),
        "max_step_ball_speed_mps": round(max(step_speeds, default=0.0), 3),
        "ball_path_straightness": round(float(displacement) / max(path_distance, 0.001), 4),
    }


def _frame_time_sec(frame: dict[str, Any]) -> float:
    if frame.get("time_sec") is not None:
        return float(frame.get("time_sec") or 0.0)
    return int(frame.get("frame") or 0) / 30.0


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
    document = {
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
    return document


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
        ball_motion = _ball_motion_metrics(detected_frames)
        confidences = [float(frame.get("confidence") or 0.0) for frame in detected_frames]
        player_source_counts = Counter(str(frame.get("nearest_player_source")) for frame in segment_frames if frame.get("nearest_player_source"))
        start_detected = detected_frames[0]
        end_detected = detected_frames[-1]
        stable_player_id = segment.get("stable_player_id")
        candidate_id = f"contact-{len(candidates) + 1:04d}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "stable_player_id": stable_player_id,
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
                **ball_motion,
                "start_ball_position_m": _rounded_pair(start_detected.get("ball_position_m")),
                "end_ball_position_m": _rounded_pair(end_detected.get("ball_position_m")),
                "start_ball_position_px": _rounded_pair(start_detected.get("ball_position_px")),
                "end_ball_position_px": _rounded_pair(end_detected.get("ball_position_px")),
                "start_player_position_m": _nearest_player_position_m(start_detected, stable_player_id),
                "end_player_position_m": _nearest_player_position_m(end_detected, stable_player_id),
                "mean_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
                "source": "controlled_ball_nearest_player",
                "status": "needs_review",
            }
        )
    document = {
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
    return apply_auto_contact_review(document, preserve_manual=False)


def build_restart_candidates_document(
    ball_tracks_doc: dict[str, Any],
    possession_doc: dict[str, Any],
    stable_players_doc: dict[str, Any],
    *,
    fps: float,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_parameters = dict(parameters or {})
    ball_rows = _restart_ball_rows(ball_tracks_doc)
    possession_frames = [
        frame
        for frame in possession_doc.get("frames") or []
        if isinstance(frame, dict) and frame.get("frame") is not None
    ]
    possession_by_frame = {int(frame.get("frame") or 0): frame for frame in possession_frames}
    players_by_frame = _event_players_by_frame(stable_players_doc, fps=fps, parameters=final_parameters)
    candidates: list[dict[str, Any]] = []
    ignored_reasons: Counter[str] = Counter()
    skip_until_frame = -1

    for setup_rows, boundary in _restart_setup_runs(ball_rows, final_parameters):
        setup_start = int(setup_rows[0]["frame"])
        setup_end = int(setup_rows[-1]["frame"])
        if setup_start <= skip_until_frame:
            continue
        release = _find_restart_release(ball_rows, setup_rows, final_parameters, fps=fps)
        if release is None:
            ignored_reasons["release_not_found"] += 1
            continue
        release_frame = int(release["frame"])
        setup_position = _mean_position_m(setup_rows)
        actor = _find_restart_actor(players_by_frame, setup_rows, setup_position, final_parameters)
        fallback = _infer_restart_team_from_last_out(
            possession_frames,
            setup_start,
            setup_position,
            final_parameters,
        )
        receiver = _find_restart_receiver(
            possession_by_frame,
            release_frame,
            final_parameters,
        )
        actor_team_label = actor.get("team_label") if actor else fallback.get("restart_team_label")
        actor_team_id = actor.get("team_id") if actor else fallback.get("restart_team_id")
        actor_team_name = actor.get("team_name") if actor else fallback.get("restart_team_name")
        actor_source = "nearest_setup_player" if actor else fallback.get("restart_team_source")
        result_type = _restart_result_type(actor_team_label, receiver)
        candidate_id = f"restart-{len(candidates) + 1:04d}"
        candidate = {
            "candidate_id": candidate_id,
            "event_type": "ground_restart_candidate",
            "restart_type": boundary["restart_type"],
            "source": RESTART_SOURCE,
            "setup_start_frame": setup_start,
            "setup_end_frame": setup_end,
            "release_frame": release_frame,
            "receiver_frame": receiver.get("frame") if receiver else None,
            "start_frame": setup_end,
            "end_frame": receiver.get("frame") if receiver else release_frame,
            "start_time_sec": _frame_time_sec(setup_rows[0]),
            "end_time_sec": receiver.get("time_sec") if receiver else release.get("time_sec"),
            "setup_position_m": _rounded_pair(setup_position),
            "release_position_m": _rounded_pair(release.get("position_m")),
            "receiver_ball_position_m": _rounded_pair(receiver.get("ball_position_m")) if receiver else None,
            "boundary_line": boundary["boundary_line"],
            "corner": boundary.get("corner"),
            "boundary_distance_m": boundary["boundary_distance_m"],
            "stationary_frames": len(setup_rows),
            "stationary_duration_sec": round(len(setup_rows) / max(fps, 0.001), 3),
            **_ball_motion_metrics(_restart_rows_as_motion_frames(setup_rows)),
            "release_speed_mps": release.get("release_speed_mps"),
            "release_displacement_m": release.get("release_displacement_m"),
            "actor_stable_player_id": actor.get("stable_player_id") if actor else None,
            "actor_stable_subject_id": actor.get("stable_subject_id") if actor else None,
            "actor_team_label": actor_team_label,
            "actor_team_id": actor_team_id,
            "actor_team_name": actor_team_name,
            "actor_position_m": _rounded_pair(actor.get("position_m")) if actor else None,
            "actor_distance_m": actor.get("distance_m") if actor else None,
            "actor_source": actor_source,
            "last_touch_stable_player_id": fallback.get("last_touch_stable_player_id"),
            "last_touch_team_label": fallback.get("last_touch_team_label"),
            "last_touch_frame": fallback.get("last_touch_frame"),
            "out_of_play_evidence": fallback.get("out_of_play_evidence"),
            "receiver_stable_player_id": receiver.get("stable_player_id") if receiver else None,
            "receiver_stable_subject_id": receiver.get("stable_subject_id") if receiver else None,
            "receiver_team_label": receiver.get("team_label") if receiver else None,
            "receiver_team_id": receiver.get("team_id") if receiver else None,
            "receiver_team_name": receiver.get("team_name") if receiver else None,
            "receiver_confidence": receiver.get("confidence") if receiver else None,
            "result_type": result_type,
            "confidence": _restart_confidence(actor, fallback, receiver, release, setup_rows, fps=fps),
            "review_status": "needs_review",
            "review_source": "generated",
            "final_stat_eligible": False,
            "notes": [
                "Ground restart candidate only. Goal kicks are intentionally ignored in this detector.",
            ],
        }
        candidates.append(candidate)
        skip_until_frame = max(release_frame, int(candidate["end_frame"] or release_frame))

    summary = _restart_summary(candidates, ignored_reasons)
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": RESTART_SOURCE,
        "experimental": True,
        "candidate_semantics": "ground_restart_candidates_from_static_boundary_ball_not_final_stats",
        "parameters": {
            key: final_parameters.get(key)
            for key in [
                "restart_boundary_distance_m",
                "restart_corner_zone_m",
                "restart_setup_min_frames",
                "restart_setup_max_speed_mps",
                "restart_setup_max_displacement_m",
                "restart_release_min_speed_mps",
                "restart_release_min_displacement_m",
                "restart_release_lookahead_frames",
                "restart_receiver_lookahead_frames",
                "restart_actor_distance_m",
                "restart_last_touch_lookback_frames",
                "restart_out_of_play_margin_m",
                "pitch_width_m",
                "pitch_length_m",
            ]
        },
        "summary": summary,
        "candidates": candidates,
    }


def _append_restart_pass_candidates(pass_candidates_doc: dict[str, Any], restart_doc: dict[str, Any]) -> None:
    candidates = pass_candidates_doc.setdefault("candidates", [])
    if not isinstance(candidates, list):
        pass_candidates_doc["candidates"] = []
        candidates = pass_candidates_doc["candidates"]
    next_index = len(candidates) + 1
    appended = 0
    for restart in restart_doc.get("candidates") or []:
        if not isinstance(restart, dict):
            continue
        actor_team = restart.get("actor_team_label")
        receiver_team = restart.get("receiver_team_label")
        receiver_player = restart.get("receiver_stable_player_id")
        if not actor_team or not receiver_team or not receiver_player:
            continue
        if restart.get("actor_stable_player_id") and restart.get("actor_stable_player_id") == receiver_player:
            continue
        pass_type = "same_team_pass" if actor_team == receiver_team else "turnover_or_interception"
        outcome = "completed_pass" if pass_type == "same_team_pass" else "failed_pass"
        start_position = restart.get("setup_position_m") or restart.get("release_position_m")
        end_position = restart.get("receiver_ball_position_m") or restart.get("release_position_m")
        start_time = restart.get("start_time_sec")
        end_time = restart.get("end_time_sec")
        candidate = {
            "candidate_id": f"pass-{next_index:04d}",
            "event_type": "pass_candidate",
            "pass_type": pass_type,
            "outcome": outcome,
            "count_for_team_label": actor_team,
            "completed": outcome == "completed_pass",
            "failed": outcome == "failed_pass",
            "from_restart": True,
            "excluded_reason": None,
            "source": "ground_restart_candidates_to_pass_candidates_v1",
            "source_event_id": restart.get("candidate_id"),
            "target_event_id": f"{restart.get('candidate_id')}:receiver",
            "source_candidate_id": restart.get("candidate_id"),
            "target_candidate_id": restart.get("candidate_id"),
            "from_stable_player_id": restart.get("actor_stable_player_id"),
            "from_stable_subject_id": restart.get("actor_stable_subject_id"),
            "from_team_label": actor_team,
            "from_team_id": restart.get("actor_team_id"),
            "from_team_name": restart.get("actor_team_name"),
            "to_stable_player_id": receiver_player,
            "to_stable_subject_id": restart.get("receiver_stable_subject_id"),
            "to_team_label": receiver_team,
            "to_team_id": restart.get("receiver_team_id"),
            "to_team_name": restart.get("receiver_team_name"),
            "start_frame": restart.get("release_frame"),
            "end_frame": restart.get("receiver_frame") or restart.get("end_frame"),
            "start_time_sec": start_time,
            "end_time_sec": end_time,
            "duration_sec": round(max(0.0, float(end_time or 0.0) - float(start_time or 0.0)), 3),
            "start_position_m": _rounded_pair(start_position),
            "end_position_m": _rounded_pair(end_position),
            "displacement_m": round(_distance_m(start_position, end_position), 3) if _valid_pair(start_position) and _valid_pair(end_position) else None,
            "distance_m": round(_distance_m(start_position, end_position), 3) if _valid_pair(start_position) and _valid_pair(end_position) else None,
            "match_phase_period_id": None,
            "attack_direction": "unknown",
            "direction_source": "restart_candidate",
            "forward_progress_m": None,
            "direction": "unknown",
            "is_progressive": False,
            "confidence": round(float(restart.get("confidence") or 0.0), 4),
            "auto_review_status": "restart_candidate",
            "review_status": "needs_review",
            "review_source": "generated",
            "review_notes": "",
            "final_stat_eligible": False,
            "restart_candidate_id": restart.get("candidate_id"),
            "restart_type": restart.get("restart_type"),
            "restart_boundary_line": restart.get("boundary_line"),
            "restart_actor_source": restart.get("actor_source"),
            "release_evidence": {
                "method": "ground_restart",
                "stationary_frames": restart.get("stationary_frames"),
                "stationary_duration_sec": restart.get("stationary_duration_sec"),
                "release_speed_mps": restart.get("release_speed_mps"),
                "release_displacement_m": restart.get("release_displacement_m"),
            },
            "receiver_evidence": {
                "stable_player_id": receiver_player,
                "team_label": receiver_team,
                "confidence": restart.get("receiver_confidence"),
            },
            "trajectory_evidence": {
                "ball_path_distance_m": restart.get("ball_path_distance_m"),
                "ball_displacement_m": restart.get("ball_displacement_m"),
                "mean_ball_speed_mps": restart.get("mean_ball_speed_mps"),
                "ball_path_straightness": restart.get("ball_path_straightness"),
            },
            "rejection_reasons": [],
            "source_event_review_statuses": [restart.get("review_status")],
            "notes": [
                "Candidate generated from a detected ground restart. Do not count as final pass statistic until reviewed.",
            ],
        }
        candidates.append(candidate)
        next_index += 1
        appended += 1
    if appended:
        summary = dict(restart_doc.get("summary") or {})
        summary["restart_pass_candidates_appended"] = int(summary.get("restart_pass_candidates_appended") or 0) + appended
        restart_doc["summary"] = summary
    update_pass_candidate_summary(pass_candidates_doc)


def _restart_ball_rows(ball_tracks_doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in ball_tracks_doc.get("positions") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "frame": int(row.get("frame") or 0),
                "time_sec": row.get("time_sec"),
                "position_m": row.get("position_m"),
                "position_px": row.get("position_px"),
                "bbox_xyxy": row.get("bbox_xyxy"),
                "source": row.get("source") or "unknown",
                "confidence": row.get("confidence"),
            }
        )
    return sorted(rows, key=lambda item: int(item.get("frame") or 0))


def _restart_setup_runs(
    ball_rows: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
    segments: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    current_rows: list[dict[str, Any]] = []
    current_boundary: dict[str, Any] | None = None
    previous_frame: int | None = None

    def flush() -> None:
        nonlocal current_rows, current_boundary
        if current_rows and current_boundary:
            segments.append((current_rows, current_boundary))
        current_rows = []
        current_boundary = None

    for row in ball_rows:
        frame_idx = int(row.get("frame") or 0)
        boundary = _restart_boundary_info(row.get("position_m"), parameters)
        if boundary is None or boundary.get("restart_type") == "ignored_goal_line_restart" or row.get("source") == "unknown":
            flush()
            previous_frame = frame_idx
            continue
        boundary_key = (boundary.get("restart_type"), boundary.get("boundary_line"), boundary.get("corner"))
        current_key = (
            current_boundary.get("restart_type"),
            current_boundary.get("boundary_line"),
            current_boundary.get("corner"),
        ) if current_boundary else None
        has_gap = previous_frame is not None and frame_idx > previous_frame + 2
        if current_rows and (boundary_key != current_key or has_gap):
            flush()
        current_rows.append(row)
        current_boundary = boundary
        previous_frame = frame_idx
    flush()
    runs: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for rows, boundary in segments:
        runs.extend(_static_restart_windows(rows, boundary, parameters))
    return runs


def _static_restart_windows(
    rows: list[dict[str, Any]],
    boundary: dict[str, Any],
    parameters: dict[str, Any],
) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
    min_frames = int(parameters.get("restart_setup_min_frames") or DEFAULT_RESTART_SETUP_MIN_FRAMES)
    windows: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    start = 0
    while start <= len(rows) - min_frames:
        end = start + min_frames
        window = rows[start:end]
        if not _restart_setup_run_is_static(window, parameters):
            start += 1
            continue
        while end < len(rows) and _restart_setup_run_is_static(rows[start : end + 1], parameters):
            end += 1
        windows.append((rows[start:end], boundary))
        start = end
    return windows


def _restart_setup_run_is_static(rows: list[dict[str, Any]], parameters: dict[str, Any]) -> bool:
    if len(rows) < int(parameters.get("restart_setup_min_frames") or DEFAULT_RESTART_SETUP_MIN_FRAMES):
        return False
    metrics = _ball_motion_metrics(_restart_rows_as_motion_frames(rows))
    if float(metrics.get("max_step_ball_speed_mps") or 0.0) > float(parameters.get("restart_setup_max_speed_mps") or DEFAULT_RESTART_SETUP_MAX_SPEED_MPS):
        return False
    if float(metrics.get("ball_displacement_m") or 0.0) > float(parameters.get("restart_setup_max_displacement_m") or DEFAULT_RESTART_SETUP_MAX_DISPLACEMENT_M):
        return False
    return True


def _restart_rows_as_motion_frames(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "frame": row.get("frame"),
            "time_sec": row.get("time_sec"),
            "ball_position_m": row.get("position_m"),
        }
        for row in rows
    ]


def _find_restart_release(
    ball_rows: list[dict[str, Any]],
    setup_rows: list[dict[str, Any]],
    parameters: dict[str, Any],
    *,
    fps: float,
) -> dict[str, Any] | None:
    setup_position = _mean_position_m(setup_rows)
    setup_end = int(setup_rows[-1]["frame"])
    lookahead = int(parameters.get("restart_release_lookahead_frames") or DEFAULT_RESTART_RELEASE_LOOKAHEAD_FRAMES)
    min_speed = float(parameters.get("restart_release_min_speed_mps") or DEFAULT_RESTART_RELEASE_MIN_SPEED_MPS)
    min_displacement = float(parameters.get("restart_release_min_displacement_m") or DEFAULT_RESTART_RELEASE_MIN_DISPLACEMENT_M)
    previous = setup_rows[-1]
    for row in ball_rows:
        frame_idx = int(row.get("frame") or 0)
        if frame_idx <= setup_end:
            continue
        if frame_idx > setup_end + lookahead:
            break
        if row.get("source") == "unknown" or not _valid_pair(row.get("position_m")):
            continue
        displacement = _distance_m(setup_position, row["position_m"])
        step_distance = _distance_m(previous["position_m"], row["position_m"]) if _valid_pair(previous.get("position_m")) else displacement
        dt = _row_dt_sec(previous, row, fps=fps)
        speed = step_distance / max(dt, 0.001)
        previous = row
        if displacement >= min_displacement and speed >= min_speed:
            return {
                **row,
                "release_displacement_m": round(displacement, 3),
                "release_speed_mps": round(speed, 3),
            }
    return None


def _find_restart_receiver(
    possession_by_frame: dict[int, dict[str, Any]],
    release_frame: int,
    parameters: dict[str, Any],
) -> dict[str, Any] | None:
    lookahead = int(parameters.get("restart_receiver_lookahead_frames") or DEFAULT_RESTART_RECEIVER_LOOKAHEAD_FRAMES)
    for frame_idx in range(release_frame, release_frame + lookahead + 1):
        row = possession_by_frame.get(frame_idx)
        if not row or row.get("status") != "controlled":
            continue
        if not row.get("stable_player_id") or not row.get("team_label"):
            continue
        return row
    return None


def _find_restart_actor(
    players_by_frame: dict[int, list[dict[str, Any]]],
    setup_rows: list[dict[str, Any]],
    setup_position: list[float],
    parameters: dict[str, Any],
) -> dict[str, Any] | None:
    max_distance = float(parameters.get("restart_actor_distance_m") or DEFAULT_RESTART_ACTOR_DISTANCE_M)
    candidates: list[dict[str, Any]] = []
    for row in setup_rows:
        for player in players_by_frame.get(int(row.get("frame") or 0), []):
            if not _valid_pair(player.get("position_m")):
                continue
            distance = _distance_m(setup_position, player["position_m"])
            if distance <= max_distance:
                candidates.append(
                    {
                        **player,
                        "distance_m": round(distance, 3),
                    }
                )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (float(item.get("distance_m") or 0.0), str(item.get("stable_player_id") or "")))
    return candidates[0]


def _infer_restart_team_from_last_out(
    possession_frames: list[dict[str, Any]],
    setup_start_frame: int,
    setup_position: list[float],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    lookback = int(parameters.get("restart_last_touch_lookback_frames") or DEFAULT_RESTART_LAST_TOUCH_LOOKBACK_FRAMES)
    start = setup_start_frame - lookback
    previous_frames = [
        frame
        for frame in possession_frames
        if start <= int(frame.get("frame") or 0) < setup_start_frame
    ]
    last_control = next(
        (
            frame
            for frame in reversed(previous_frames)
            if frame.get("status") == "controlled" and str(frame.get("team_label") or "") in {"A", "B"}
        ),
        None,
    )
    if not last_control:
        return {}
    last_team = str(last_control.get("team_label") or "")
    restart_team = _opponent_team_label(last_team)
    if not restart_team:
        return {}
    evidence = _out_of_play_evidence(previous_frames, int(last_control.get("frame") or 0), setup_position, parameters)
    if not evidence:
        return {}
    return {
        "restart_team_label": restart_team,
        "restart_team_id": None,
        "restart_team_name": None,
        "restart_team_source": "last_touch_out_of_play_opponent",
        "last_touch_stable_player_id": last_control.get("stable_player_id"),
        "last_touch_team_label": last_team,
        "last_touch_frame": int(last_control.get("frame") or 0),
        "out_of_play_evidence": evidence,
    }


def _out_of_play_evidence(
    frames: list[dict[str, Any]],
    last_touch_frame: int,
    setup_position: list[float],
    parameters: dict[str, Any],
) -> dict[str, Any] | None:
    width = float(parameters.get("pitch_width_m") or 30.0)
    length = float(parameters.get("pitch_length_m") or 47.4)
    margin = float(parameters.get("restart_out_of_play_margin_m") or DEFAULT_RESTART_OUT_OF_PLAY_MARGIN_M)
    between = [frame for frame in frames if int(frame.get("frame") or 0) >= last_touch_frame]
    outside = [
        frame
        for frame in between
        if _valid_pair(frame.get("ball_position_m"))
        and not _point_inside_pitch(frame["ball_position_m"], width, length, margin=-margin)
    ]
    unknown = [
        frame
        for frame in between
        if frame.get("ball_source") == "unknown" or not _valid_pair(frame.get("ball_position_m"))
    ]
    last_ball = next((frame for frame in reversed(between) if _valid_pair(frame.get("ball_position_m"))), None)
    boundary = _restart_boundary_info(setup_position, parameters)
    if outside:
        return {"method": "detected_outside_pitch", "frames": len(outside), "boundary_line": boundary.get("boundary_line") if boundary else None}
    if len(unknown) >= 3 and boundary is not None:
        return {"method": "ball_missing_before_static_boundary_restart", "frames": len(unknown), "boundary_line": boundary.get("boundary_line")}
    if last_ball and boundary and _distance_m(last_ball.get("ball_position_m"), setup_position) <= 2.0:
        return {"method": "last_ball_near_restart_boundary", "frames": 1, "boundary_line": boundary.get("boundary_line")}
    return None


def _restart_boundary_info(position_m: Any, parameters: dict[str, Any]) -> dict[str, Any] | None:
    if not _valid_pair(position_m):
        return None
    x, y = float(position_m[0]), float(position_m[1])
    width = float(parameters.get("pitch_width_m") or 30.0)
    length = float(parameters.get("pitch_length_m") or 47.4)
    boundary_distance = float(parameters.get("restart_boundary_distance_m") or DEFAULT_RESTART_BOUNDARY_DISTANCE_M)
    corner_zone = float(parameters.get("restart_corner_zone_m") or DEFAULT_RESTART_CORNER_ZONE_M)
    distances = {
        "left_touchline": abs(x),
        "right_touchline": abs(width - x),
        "top_goal_line": abs(y),
        "bottom_goal_line": abs(length - y),
    }
    line, distance = min(distances.items(), key=lambda item: item[1])
    if distance > boundary_distance:
        return None
    near_left_corner = x <= corner_zone
    near_right_corner = x >= width - corner_zone
    near_top_corner = y <= corner_zone
    near_bottom_corner = y >= length - corner_zone
    if line in {"left_touchline", "right_touchline"}:
        corner = None
        if near_top_corner:
            corner = "top_left" if line == "left_touchline" else "top_right"
        elif near_bottom_corner:
            corner = "bottom_left" if line == "left_touchline" else "bottom_right"
        return {
            "restart_type": "kick_in",
            "boundary_line": line,
            "corner": corner,
            "boundary_distance_m": round(float(distance), 3),
        }
    if line in {"top_goal_line", "bottom_goal_line"} and (near_left_corner or near_right_corner):
        corner = f"{'top' if line == 'top_goal_line' else 'bottom'}_{'left' if near_left_corner else 'right'}"
        return {
            "restart_type": "corner",
            "boundary_line": line,
            "corner": corner,
            "boundary_distance_m": round(float(distance), 3),
        }
    return {
        "restart_type": "ignored_goal_line_restart",
        "boundary_line": line,
        "corner": None,
        "boundary_distance_m": round(float(distance), 3),
    }


def _mean_position_m(rows: list[dict[str, Any]]) -> list[float]:
    valid = [row.get("position_m") or row.get("ball_position_m") for row in rows if _valid_pair(row.get("position_m") or row.get("ball_position_m"))]
    if not valid:
        return [0.0, 0.0]
    return [
        sum(float(point[0]) for point in valid) / len(valid),
        sum(float(point[1]) for point in valid) / len(valid),
    ]


def _restart_result_type(actor_team_label: Any, receiver: dict[str, Any] | None) -> str:
    if not actor_team_label:
        return "restart_unknown_actor_team"
    if not receiver:
        return "restart_no_receiver"
    if actor_team_label == receiver.get("team_label"):
        return "restart_pass"
    return "restart_turnover_or_interception"


def _restart_confidence(
    actor: dict[str, Any] | None,
    fallback: dict[str, Any],
    receiver: dict[str, Any] | None,
    release: dict[str, Any],
    setup_rows: list[dict[str, Any]],
    *,
    fps: float,
) -> float:
    setup_confidences = [float(row.get("confidence") or 0.0) for row in setup_rows]
    setup_confidence = sum(setup_confidences) / max(len(setup_confidences), 1)
    release_confidence = float(release.get("confidence") or 0.0)
    confidence = 0.25 + min(setup_confidence, release_confidence) * 0.25
    confidence += min(0.12, len(setup_rows) / max(fps, 1.0) * 0.08)
    confidence += min(0.12, float(release.get("release_speed_mps") or 0.0) / 12.0)
    if actor:
        confidence += 0.2
    elif fallback:
        confidence += 0.08
    if receiver:
        confidence += min(0.13, float(receiver.get("confidence") or 0.0) * 0.13)
    return round(min(0.88, max(0.05, confidence)), 4)


def _restart_summary(candidates: list[dict[str, Any]], ignored_reasons: Counter[str]) -> dict[str, Any]:
    type_counts = Counter(str(candidate.get("restart_type") or "unknown") for candidate in candidates)
    result_counts = Counter(str(candidate.get("result_type") or "unknown") for candidate in candidates)
    actor_source_counts = Counter(str(candidate.get("actor_source") or "unknown") for candidate in candidates)
    return {
        "restart_candidates": len(candidates),
        "kick_in_candidates": type_counts.get("kick_in", 0),
        "corner_candidates": type_counts.get("corner", 0),
        "result_type_counts": dict(sorted(result_counts.items())),
        "restart_pass_candidates": result_counts.get("restart_pass", 0),
        "restart_turnover_or_interception_candidates": result_counts.get("restart_turnover_or_interception", 0),
        "restart_unknown_actor_team_candidates": result_counts.get("restart_unknown_actor_team", 0),
        "restart_no_receiver_candidates": result_counts.get("restart_no_receiver", 0),
        "actor_source_counts": dict(sorted(actor_source_counts.items())),
        "visible_actor_candidates": actor_source_counts.get("nearest_setup_player", 0),
        "last_touch_fallback_candidates": actor_source_counts.get("last_touch_out_of_play_opponent", 0),
        "ignored_reasons": dict(sorted(ignored_reasons.items())),
    }


def _opponent_team_label(team_label: str) -> str | None:
    if team_label == "A":
        return "B"
    if team_label == "B":
        return "A"
    return None


def _point_inside_pitch(point: Any, width: float, length: float, *, margin: float = 0.0) -> bool:
    if not _valid_pair(point):
        return False
    x, y = float(point[0]), float(point[1])
    return margin <= x <= width - margin and margin <= y <= length - margin


def _row_dt_sec(previous: dict[str, Any], current: dict[str, Any], *, fps: float) -> float:
    previous_time = previous.get("time_sec")
    current_time = current.get("time_sec")
    if previous_time is not None and current_time is not None:
        return float(current_time) - float(previous_time)
    return (int(current.get("frame") or 0) - int(previous.get("frame") or 0)) / max(fps, 0.001)


def build_possession_report(
    candidates_doc: dict[str, Any],
    segments_doc: dict[str, Any],
    contact_doc: dict[str, Any],
    restart_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(candidates_doc.get("summary") or {})
    warnings = list(summary.pop("warnings", []) or [])
    restart_summary = dict((restart_doc or {}).get("summary") or {})
    possession_timeline = build_possession_timeline(candidates_doc)
    if float(summary.get("known_possession_coverage") or 0.0) < 0.5:
        warnings.append("Known possession coverage is below 50%; do not use this as a final possession statistic.")
    if int(summary.get("contested_frames") or 0) > 0:
        warnings.append("Contested frames exist and require review before player/team possession stats.")
    if int(contact_doc.get("summary", {}).get("contact_candidates") or 0) == 0:
        warnings.append("No contact candidates were found with conservative thresholds.")
    if int(contact_doc.get("summary", {}).get("candidates_with_interpolated_player_positions") or 0) > 0:
        warnings.append("Some contact candidates use short-gap interpolated player positions and need review.")
    if int(restart_summary.get("last_touch_fallback_candidates") or 0) > 0:
        warnings.append("Some restart candidates infer the restart team from last-touch out-of-play evidence.")
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
            "restart_candidates": restart_summary.get("restart_candidates", 0),
            "kick_in_candidates": restart_summary.get("kick_in_candidates", 0),
            "corner_candidates": restart_summary.get("corner_candidates", 0),
            "restart_pass_candidates": restart_summary.get("restart_pass_candidates", 0),
            "restart_turnover_or_interception_candidates": restart_summary.get(
                "restart_turnover_or_interception_candidates", 0
            ),
            "possession_timeline_points": len(possession_timeline),
        },
        "possession_timeline": possession_timeline,
        "warnings": warnings,
        "notes": [
            "This is a conservative candidate layer, not a final possession or pass statistic.",
            "Unknown means the ball or trusted player position was missing; it is intentionally not guessed.",
        ],
    }


def build_possession_timeline(candidates_doc: dict[str, Any]) -> list[dict[str, Any]]:
    frames = [
        frame
        for frame in candidates_doc.get("frames") or []
        if isinstance(frame, dict) and frame.get("frame") is not None
    ]
    if not frames:
        return []
    frames.sort(key=lambda frame: (float(frame.get("time_sec") or 0.0), int(frame.get("frame") or 0)))
    summary = candidates_doc.get("summary") if isinstance(candidates_doc.get("summary"), dict) else {}
    interval_sec = float(summary.get("frame_interval_sec") or _frame_interval_sec(frames, fps=30.0) or (1.0 / 30.0))
    start_time = _timeline_frame_time_sec(frames[0], interval_sec)
    end_time = _timeline_frame_time_sec(frames[-1], interval_sec)
    duration_sec = max(interval_sec, end_time - start_time + interval_sec)
    target_points = max(10, min(40, int(math.ceil(duration_sec / 60.0))))
    if len(frames) < target_points:
        target_points = max(1, len(frames))
    bucket_sec = duration_sec / max(target_points, 1)
    timeline: list[dict[str, Any]] = []
    cursor = 0
    for index in range(target_points):
        bucket_start = start_time + index * bucket_sec
        bucket_end = start_time + (index + 1) * bucket_sec
        bucket_frames: list[dict[str, Any]] = []
        while cursor < len(frames):
            current_time = _timeline_frame_time_sec(frames[cursor], interval_sec)
            if current_time < bucket_start and index > 0:
                cursor += 1
                continue
            if current_time >= bucket_end and index < target_points - 1:
                break
            bucket_frames.append(frames[cursor])
            cursor += 1
            if index == target_points - 1:
                continue
        if not bucket_frames:
            timeline.append(_empty_possession_timeline_point(index, bucket_start, bucket_end))
            continue
        timeline.append(_possession_timeline_point(index, bucket_start, bucket_end, bucket_frames))
    return timeline


def _timeline_frame_time_sec(frame: dict[str, Any], interval_sec: float) -> float:
    if frame.get("time_sec") is not None:
        return float(frame.get("time_sec") or 0.0)
    return int(frame.get("frame") or 0) * interval_sec


def _empty_possession_timeline_point(index: int, start_time: float, end_time: float) -> dict[str, Any]:
    return {
        "index": index,
        "time_sec": round((start_time + end_time) / 2.0, 3),
        "start_time_sec": round(start_time, 3),
        "end_time_sec": round(end_time, 3),
        "frames": 0,
        "controlled_frames": 0,
        "contested_frames": 0,
        "free_frames": 0,
        "unknown_frames": 0,
        "team_controlled_frames": {"A": 0, "B": 0},
        "team_a_share": None,
        "team_b_share": None,
        "controlled_coverage": 0.0,
        "unknown_coverage": 1.0,
    }


def _possession_timeline_point(
    index: int,
    start_time: float,
    end_time: float,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts = Counter(str(frame.get("status") or "unknown") for frame in frames)
    team_counts = Counter(
        str(frame.get("team_label"))
        for frame in frames
        if frame.get("status") == "controlled" and frame.get("team_label") in {"A", "B"}
    )
    total = len(frames)
    controlled = int(status_counts.get("controlled", 0))
    team_a = int(team_counts.get("A", 0))
    team_b = int(team_counts.get("B", 0))
    team_total = team_a + team_b
    return {
        "index": index,
        "time_sec": round((start_time + end_time) / 2.0, 3),
        "start_time_sec": round(start_time, 3),
        "end_time_sec": round(end_time, 3),
        "frames": total,
        "controlled_frames": controlled,
        "contested_frames": int(status_counts.get("contested", 0)),
        "free_frames": int(status_counts.get("free", 0)),
        "unknown_frames": int(status_counts.get("unknown", 0)),
        "team_controlled_frames": {"A": team_a, "B": team_b},
        "team_a_share": round(team_a / team_total, 4) if team_total else None,
        "team_b_share": round(team_b / team_total, 4) if team_total else None,
        "controlled_coverage": round(controlled / max(total, 1), 4),
        "unknown_coverage": round(int(status_counts.get("unknown", 0)) / max(total, 1), 4),
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
        rows = _player_event_rows(player)
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
        "display_label": player.get("display_label"),
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


def _player_event_rows(player: dict[str, Any]) -> list[dict[str, Any]]:
    overlay_positions = [
        row
        for row in player.get("overlay_positions") or []
        if isinstance(row, dict) and row.get("frame") is not None and _valid_pair(row.get("pitch_m"))
    ]
    if overlay_positions:
        return overlay_positions
    return [
        row
        for row in player.get("trajectory_m") or []
        if isinstance(row, dict) and row.get("frame") is not None and _valid_pair(row.get("pitch_m"))
    ]


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
    fly_through_suppressed = sum(1 for frame in frames if frame.get("reason") == "fly_through_no_close_control")
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
        "fly_through_suppressed_frames": fly_through_suppressed,
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


def _rounded_pair(value: Any) -> list[float] | None:
    if not _valid_pair(value):
        return None
    return [round(float(value[0]), 3), round(float(value[1]), 3)]


def _nearest_player_position_m(frame: dict[str, Any], stable_player_id: Any) -> list[float] | None:
    nearest_players = frame.get("nearest_players") if isinstance(frame.get("nearest_players"), list) else []
    for player in nearest_players:
        if not isinstance(player, dict):
            continue
        if stable_player_id and player.get("stable_player_id") != stable_player_id:
            continue
        return _rounded_pair(player.get("position_m"))
    if nearest_players and isinstance(nearest_players[0], dict):
        return _rounded_pair(nearest_players[0].get("position_m"))
    return None


def _write_possession_artifacts(
    match_dir: Path,
    candidates_doc: dict[str, Any],
    segments_doc: dict[str, Any],
    contact_doc: dict[str, Any],
    event_docs: dict[str, Any],
    report_doc: dict[str, Any],
) -> None:
    (match_dir / "possession_candidates.json").write_text(json.dumps(candidates_doc, indent=2), encoding="utf-8")
    (match_dir / "possession_segments.json").write_text(json.dumps(segments_doc, indent=2), encoding="utf-8")
    (match_dir / "contact_candidates.json").write_text(json.dumps(contact_doc, indent=2), encoding="utf-8")
    for doc_key, filename in event_docs.get("artifacts", {}).items():
        if doc_key in event_docs:
            (match_dir / filename).write_text(json.dumps(event_docs[doc_key], indent=2), encoding="utf-8")
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
