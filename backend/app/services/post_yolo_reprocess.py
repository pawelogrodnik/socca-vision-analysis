from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.config import STORAGE_DIR, WRITE_DEBUG_VIDEO_ARTIFACTS
from app.services.analysis import (
    _build_ball_possession_artifacts,
    _cleanup_debug_video_artifacts,
    _rewrite_stable_overlay_with_possession,
    _write_outputs,
    load_pitch_config,
    write_raw_overlay_from_tracks,
)
from app.services.analysis_runs import finalize_analysis_report, now_iso
from app.services.ball_tracking import (
    build_ball_quality_report,
    build_ball_tracking_report,
    build_ball_tracks_document,
)
from app.services.camera_motion import CameraMotionModel, build_camera_motion_model, write_camera_motion_report
from app.services.pitch import PitchConfig, image_to_pitch_m
from app.services.stabilization import stabilize_match
from app.services.video import read_video_metadata


REPROCESS_OPTIONAL_INPUTS = (
    "match.json",
    "team_config.json",
    "benchmark_input.json",
    "ball_candidates.json",
    "ball_tracks.json",
    "ball_tracking_report.json",
    "ball_quality_report.json",
)


def default_reprocess_output_dir(source_dir: Path, *, label: str = "") -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _slug(label or source_dir.name or "match")
    return STORAGE_DIR / "reprocess" / f"{timestamp}-{slug}"


def reprocess_match_from_artifacts(
    source_dir: Path,
    video_path: Path | None = None,
    *,
    output_dir: Path | None = None,
    label: str = "",
    include_ball: bool | None = None,
    build_possession: bool = True,
    write_raw_overlay: bool = False,
    write_debug_overlay: bool = WRITE_DEBUG_VIDEO_ARTIFACTS,
    render_stable_overlay: bool = True,
    player_label_overrides: dict[str, str] | None = None,
    start_sec: float = 0.0,
    max_seconds: float | None = None,
    progress: Callable[[str, float, str, dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    """Run all post-YOLO analysis from stored raw artifacts.

    This intentionally skips player and ball YOLO inference. It is meant for
    debugging resolver/stat/stat-preview changes against a frozen tracks.json
    and optional ball_candidates.json/ball_tracks.json.
    """
    source_dir = source_dir.resolve()
    if not source_dir.exists():
        raise FileNotFoundError(f"Source artifact directory not found: {source_dir}")
    video_path = resolve_reprocess_video(source_dir, video_path)
    output_dir = (output_dir or default_reprocess_output_dir(source_dir, label=label)).resolve()
    _prepare_reprocess_directory(source_dir, output_dir)

    metadata = read_video_metadata(video_path)
    time_window = _time_window(start_sec=start_sec, max_seconds=max_seconds)
    if time_window is not None:
        _trim_reprocess_artifacts(output_dir, metadata, time_window=time_window)
    pitch = load_pitch_config(output_dir)
    tracks = _load_tracks(output_dir / "tracks.json")
    warnings: list[str] = []
    try:
        camera_motion = build_camera_motion_model(
            video_path,
            metadata,
            calibration_frame_time_sec=float(getattr(pitch, "calibration_frame_time_sec", 0.0) or 0.0),
            start_time_sec=0.0,
            end_time_sec=_max_track_time_sec(tracks),
            enabled=True,
        )
    except Exception as exc:
        camera_motion = CameraMotionModel.disabled(
            fps=float(metadata.get("fps") or 0.0),
            frame_count=int(metadata.get("frame_count") or 0),
        )
        warnings.append(f"Camera motion compensation disabled after estimator failure: {exc}")
    write_camera_motion_report(output_dir, camera_motion)
    if not WRITE_DEBUG_VIDEO_ARTIFACTS:
        _cleanup_debug_video_artifacts(output_dir)

    tracks = _recalibrate_tracks_for_camera_motion(tracks, pitch, camera_motion)
    artifacts = _write_outputs(output_dir, pitch, tracks, include_overlay=False)
    artifacts["camera_motion_report"] = "camera_motion_report.json"
    if write_raw_overlay:
        max_seconds = _max_track_time_sec(tracks)
        write_raw_overlay_from_tracks(
            output_dir,
            video_path,
            pitch,
            tracks,
            metadata,
            frame_stride=_infer_frame_stride(tracks),
            max_seconds=max_seconds,
            camera_motion=camera_motion,
        )
        artifacts["overlay_preview"] = "overlay_preview.mp4"

    ball_tracking = _load_or_rebuild_ball_tracking(output_dir, metadata, include_ball=include_ball)
    if ball_tracking is not None:
        artifacts.update(ball_tracking["artifacts"])

    stabilization = stabilize_match(
        output_dir,
        video_path,
        pitch,
        tracks,
        metadata,
        camera_motion=camera_motion,
        ball_tracks_doc=(ball_tracking or {}).get("ball_tracks"),
        ball_candidates_doc=(ball_tracking or {}).get("ball_candidates"),
        write_debug_overlay=write_debug_overlay,
        render_stable_overlay=render_stable_overlay,
        player_label_overrides=player_label_overrides,
        progress=progress,
    )
    artifacts.update(stabilization["artifacts"])

    if ball_tracking is not None:
        _merge_refined_ball_tracking(output_dir, ball_tracking, stabilization)
        artifacts.update(ball_tracking["artifacts"])

    possession: dict[str, Any] | None = None
    if ball_tracking is not None and build_possession:
        try:
            possession = _build_ball_possession_artifacts(
                output_dir,
                video_path,
                pitch,
                metadata,
                ball_tracking,
                stable_players_doc=stabilization.get("stable_players_overlay_doc") or stabilization["stable_players"],
                write_overlay_video=WRITE_DEBUG_VIDEO_ARTIFACTS,
            )
            artifacts.update(possession["artifacts"])
            if render_stable_overlay:
                try:
                    _rewrite_stable_overlay_with_possession(
                        output_dir,
                        video_path,
                        pitch,
                        metadata,
                        stabilization,
                        ball_tracking,
                        possession,
                        camera_motion=camera_motion,
                    )
                except Exception as exc:
                    warnings.append(f"Stable overlay possession/pass layer failed: {exc}")
        except Exception as exc:
            warnings.append(f"Post-YOLO possession candidate layer failed: {exc}")

    report = {
        "schema_version": "0.1.0",
        "status": "completed",
        "analysis_type": "post-yolo-reprocess",
        "note": "Developer reprocess run: uses stored tracks/ball artifacts and skips YOLO inference.",
        "generated_at": now_iso(),
        "video": metadata,
        "parameters": {
            "source_dir": str(source_dir),
            "video_path": str(video_path),
            "label": label,
            "include_ball": ball_tracking is not None,
            "ball_input": (ball_tracking or {}).get("input_source"),
            "build_possession": bool(build_possession),
            "write_raw_overlay": bool(write_raw_overlay),
            "write_debug_overlay": bool(write_debug_overlay),
            "render_stable_overlay": bool(render_stable_overlay),
            "camera_motion_compensation": bool(getattr(camera_motion, "enabled", False)),
            "camera_motion_reference_frame": getattr(camera_motion, "reference_frame", None),
            "player_label_overrides": player_label_overrides or {},
            "yolo_skipped": True,
            "time_window": (
                {"start_sec": time_window[0], "end_sec": time_window[1], "max_seconds": max_seconds}
                if time_window is not None
                else None
            ),
        },
        "frames_processed": _count_unique_track_frames(tracks),
        "tracks_count": len(tracks),
        "stable_players_count": stabilization["stable_players"]["summary"]["stable_players"],
        "ball_tracking_summary": (ball_tracking or {}).get("ball_tracking_report", {}).get("summary"),
        "ball_quality_summary": (ball_tracking or {}).get("ball_quality_report", {}).get("summary"),
        "possession_summary": (possession or {}).get("possession_report", {}).get("summary"),
        "warnings": warnings,
        "artifacts": artifacts,
    }
    return finalize_analysis_report(output_dir, report)


def resolve_reprocess_video(source_dir: Path, video_path: Path | None = None) -> Path:
    if video_path is not None:
        resolved = video_path.expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Video file not found: {resolved}")
        return resolved

    for candidate in sorted(source_dir.glob("video.*")):
        if candidate.is_file():
            return candidate.resolve()

    benchmark_input_path = source_dir / "benchmark_input.json"
    if benchmark_input_path.exists():
        benchmark_input = _load_json(benchmark_input_path)
        candidate = benchmark_input.get("video_path") if isinstance(benchmark_input, dict) else None
        if candidate:
            resolved = Path(str(candidate)).expanduser().resolve()
            if resolved.exists():
                return resolved

    raise FileNotFoundError(
        f"Could not infer source video from {source_dir}. Pass --video explicitly."
    )


def _prepare_reprocess_directory(source_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_required(source_dir / "pitch_config.json", output_dir / "pitch_config.json")
    _copy_required(source_dir / "tracks.json", output_dir / "tracks.json")
    for filename in REPROCESS_OPTIONAL_INPUTS:
        source = source_dir / filename
        if source.exists() and source.is_file():
            _copy_if_different(source, output_dir / filename)


def _copy_required(source: Path, target: Path) -> None:
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Required reprocess artifact not found: {source}")
    _copy_if_different(source, target)


def _copy_if_different(source: Path, target: Path) -> None:
    if source.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _load_or_rebuild_ball_tracking(
    output_dir: Path,
    metadata: dict[str, Any],
    *,
    include_ball: bool | None,
) -> dict[str, Any] | None:
    candidates_path = output_dir / "ball_candidates.json"
    tracks_path = output_dir / "ball_tracks.json"
    if include_ball is False:
        return None
    if not candidates_path.exists() and not tracks_path.exists():
        return None

    candidates_doc = _load_json(candidates_path) if candidates_path.exists() else _empty_ball_candidates_doc()
    existing_report = _load_json(output_dir / "ball_tracking_report.json") if (output_dir / "ball_tracking_report.json").exists() else {}
    input_source = "ball_tracks"

    if candidates_path.exists():
        parameters = _ball_parameters(candidates_doc, existing_report)
        tracks_doc = build_ball_tracks_document(
            candidates_doc.get("frames") if isinstance(candidates_doc.get("frames"), list) else [],
            processed_frames=candidates_doc.get("processed_frames") if isinstance(candidates_doc.get("processed_frames"), list) else [],
            fps=float(metadata.get("fps") or 0.0),
            parameters=parameters,
        )
        report = build_ball_tracking_report(
            tracks_doc,
            candidates_doc,
            parameters=parameters,
            warnings=list(existing_report.get("warnings") or []),
        )
        input_source = "ball_candidates"
    else:
        tracks_doc = _load_json(tracks_path)
        report = existing_report if isinstance(existing_report, dict) and existing_report else _ball_report_from_tracks(tracks_doc)

    quality_report = build_ball_quality_report(tracks_doc, candidates_doc, report)
    tracks_path.write_text(json.dumps(tracks_doc, indent=2), encoding="utf-8")
    (output_dir / "ball_tracking_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output_dir / "ball_quality_report.json").write_text(json.dumps(quality_report, indent=2), encoding="utf-8")

    artifacts = {
        "ball_tracks": "ball_tracks.json",
        "ball_tracking_report": "ball_tracking_report.json",
        "ball_quality_report": "ball_quality_report.json",
    }
    if candidates_path.exists():
        artifacts["ball_candidates"] = "ball_candidates.json"
    return {
        "input_source": input_source,
        "ball_candidates": candidates_doc,
        "ball_tracks": tracks_doc,
        "ball_tracking_report": report,
        "ball_quality_report": quality_report,
        "artifacts": artifacts,
    }


def _merge_refined_ball_tracking(
    output_dir: Path,
    ball_tracking: dict[str, Any],
    stabilization: dict[str, Any],
) -> None:
    refined_ball_tracks = stabilization.get("refined_ball_tracks")
    if refined_ball_tracks is None:
        return
    ball_tracking["ball_tracks"] = refined_ball_tracks
    ball_report = ball_tracking.get("ball_tracking_report") or {}
    ball_report["summary"] = {
        **(ball_report.get("summary") or {}),
        **(refined_ball_tracks.get("summary") or {}),
    }
    ball_tracking["ball_tracking_report"] = ball_report
    ball_tracking["ball_quality_report"] = build_ball_quality_report(
        refined_ball_tracks,
        ball_tracking.get("ball_candidates") or _empty_ball_candidates_doc(),
        ball_report,
    )
    (output_dir / "ball_tracking_report.json").write_text(json.dumps(ball_report, indent=2), encoding="utf-8")
    (output_dir / "ball_quality_report.json").write_text(
        json.dumps(ball_tracking["ball_quality_report"], indent=2),
        encoding="utf-8",
    )


def _time_window(*, start_sec: float, max_seconds: float | None) -> tuple[float, float | None] | None:
    start = max(0.0, float(start_sec or 0.0))
    duration = float(max_seconds or 0.0)
    if start <= 0.0 and duration <= 0.0:
        return None
    end = start + duration if duration > 0.0 else None
    return (start, end)


def _trim_reprocess_artifacts(
    output_dir: Path,
    metadata: dict[str, Any],
    *,
    time_window: tuple[float, float | None],
) -> None:
    fps = float(metadata.get("fps") or 0.0)
    tracks_path = output_dir / "tracks.json"
    tracks = _load_tracks(tracks_path)
    tracks_path.write_text(
        json.dumps(_filter_tracks_by_time(tracks, fps=fps, time_window=time_window), indent=2),
        encoding="utf-8",
    )
    _trim_ball_candidates_file(output_dir / "ball_candidates.json", fps=fps, time_window=time_window)
    _trim_ball_tracks_file(output_dir / "ball_tracks.json", fps=fps, time_window=time_window)


def _filter_tracks_by_time(
    tracks: list[dict[str, Any]],
    *,
    fps: float,
    time_window: tuple[float, float | None],
) -> list[dict[str, Any]]:
    filtered_tracks: list[dict[str, Any]] = []
    for track in tracks:
        positions = [
            position
            for position in (track.get("positions") or [])
            if isinstance(position, dict) and _is_row_in_time_window(position, fps=fps, time_window=time_window)
        ]
        if not positions:
            continue
        next_track = dict(track)
        next_track["positions"] = positions
        next_track["positions_count"] = len(positions)
        times = [_row_time_sec(position, fps=fps) for position in positions]
        known_times = [time for time in times if time is not None]
        if known_times:
            next_track["start_time_sec"] = round(min(known_times), 3)
            next_track["end_time_sec"] = round(max(known_times), 3)
            next_track["duration_sec"] = round(max(0.0, max(known_times) - min(known_times)), 3)
        filtered_tracks.append(next_track)
    return filtered_tracks


def _trim_ball_candidates_file(
    path: Path,
    *,
    fps: float,
    time_window: tuple[float, float | None],
) -> None:
    if not path.exists():
        return
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return
    frames = doc.get("frames") if isinstance(doc.get("frames"), list) else []
    filtered_frames = [
        frame
        for frame in frames
        if isinstance(frame, dict) and _is_row_in_time_window(frame, fps=fps, time_window=time_window)
    ]
    doc["frames"] = filtered_frames
    processed_frames = doc.get("processed_frames") if isinstance(doc.get("processed_frames"), list) else []
    doc["processed_frames"] = [
        frame
        for frame in processed_frames
        if _is_processed_frame_in_time_window(frame, fps=fps, time_window=time_window)
    ]
    summary = dict(doc.get("summary") or {})
    candidate_count = sum(len(frame.get("candidates") or []) for frame in filtered_frames if isinstance(frame, dict))
    summary.update(
        {
            "processed_frames": len(doc["processed_frames"]) or len(filtered_frames),
            "frames_with_candidates": sum(1 for frame in filtered_frames if frame.get("candidates")),
            "candidate_count": candidate_count,
        }
    )
    doc["summary"] = summary
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _trim_ball_tracks_file(
    path: Path,
    *,
    fps: float,
    time_window: tuple[float, float | None],
) -> None:
    if not path.exists():
        return
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return
    positions = doc.get("positions") if isinstance(doc.get("positions"), list) else []
    filtered_positions = [
        position
        for position in positions
        if isinstance(position, dict) and _is_row_in_time_window(position, fps=fps, time_window=time_window)
    ]
    doc["positions"] = filtered_positions
    summary = dict(doc.get("summary") or {})
    processed = len(filtered_positions)
    detected = sum(1 for position in filtered_positions if position.get("status") == "detected")
    interpolated = sum(1 for position in filtered_positions if position.get("status") == "interpolated")
    unknown = sum(1 for position in filtered_positions if position.get("status") == "unknown")
    summary.update(
        {
            "processed_frames": processed,
            "detected_frames": detected,
            "interpolated_frames": interpolated,
            "unknown_frames": unknown,
            "detected_coverage": round(detected / processed, 4) if processed else 0.0,
            "interpolated_coverage": round(interpolated / processed, 4) if processed else 0.0,
            "known_coverage": round((detected + interpolated) / processed, 4) if processed else 0.0,
        }
    )
    doc["summary"] = summary
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _is_processed_frame_in_time_window(
    value: Any,
    *,
    fps: float,
    time_window: tuple[float, float | None],
) -> bool:
    if isinstance(value, dict):
        return _is_row_in_time_window(value, fps=fps, time_window=time_window)
    if isinstance(value, (int, float)) and fps > 0:
        return _is_time_in_window(float(value) / fps, time_window)
    return True


def _is_row_in_time_window(row: dict[str, Any], *, fps: float, time_window: tuple[float, float | None]) -> bool:
    time_sec = _row_time_sec(row, fps=fps)
    if time_sec is None:
        return True
    return _is_time_in_window(time_sec, time_window)


def _row_time_sec(row: dict[str, Any], *, fps: float) -> float | None:
    if row.get("time_sec") is not None:
        try:
            return float(row["time_sec"])
        except (TypeError, ValueError):
            return None
    if row.get("frame") is not None and fps > 0:
        try:
            return float(row["frame"]) / fps
        except (TypeError, ValueError):
            return None
    return None


def _is_time_in_window(time_sec: float, time_window: tuple[float, float | None]) -> bool:
    start, end = time_window
    if time_sec < start:
        return False
    if end is not None and time_sec > end:
        return False
    return True


def _load_tracks(path: Path) -> list[dict[str, Any]]:
    tracks = _load_json(path)
    if not isinstance(tracks, list):
        raise ValueError("tracks.json must contain a list")
    return tracks


def _recalibrate_tracks_for_camera_motion(
    tracks: list[dict[str, Any]],
    pitch: PitchConfig,
    camera_motion: CameraMotionModel,
) -> list[dict[str, Any]]:
    if not getattr(camera_motion, "enabled", False):
        return tracks
    homography = pitch.homography()
    recalibrated: list[dict[str, Any]] = []
    for track in tracks:
        positions: list[dict[str, Any]] = []
        for position in track.get("positions") or []:
            if not isinstance(position, dict):
                continue
            row = dict(position)
            footpoint = row.get("footpoint")
            if _valid_point(footpoint):
                frame = int(row.get("frame") or 0)
                calibrated = camera_motion.transform_point(frame, [float(footpoint[0]), float(footpoint[1])])
                row["calibrated_footpoint"] = calibrated
                row["tracking_footpoint"] = calibrated
                row.update(camera_motion.metadata_for_frame(frame))
                mapped = image_to_pitch_m([(float(calibrated[0]), float(calibrated[1]))], homography)
                if mapped:
                    x_m = float(mapped[0][0])
                    y_m = float(mapped[0][1])
                    clamped_x = float(np.clip(x_m, 0.0, pitch.width_m))
                    clamped_y = float(np.clip(y_m, 0.0, pitch.length_m))
                    row["pitch_m_clamped"] = abs(clamped_x - x_m) > 1e-6 or abs(clamped_y - y_m) > 1e-6
                    row["pitch_m"] = [round(clamped_x, 3), round(clamped_y, 3)]
                    row["pitch_m_source"] = "reprocess_camera_motion_calibrated_footpoint"
            positions.append(row)
        if not positions:
            continue
        next_track = dict(track)
        next_track["positions"] = positions
        next_track["positions_count"] = len(positions)
        times = [float(row.get("time_sec") or 0.0) for row in positions]
        next_track["start_time_sec"] = round(min(times), 3)
        next_track["end_time_sec"] = round(max(times), 3)
        next_track["duration_sec"] = round(max(0.0, max(times) - min(times)), 3)
        recalibrated.append(next_track)
    return sorted(recalibrated, key=lambda item: int(item.get("track_id") or 0))


def _valid_point(value: Any) -> bool:
    return isinstance(value, list | tuple) and len(value) >= 2 and value[0] is not None and value[1] is not None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_ball_candidates_doc() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "frames": [],
        "processed_frames": [],
        "summary": {
            "candidate_count": 0,
            "frames_with_candidates": 0,
            "rejected_candidate_count": 0,
            "rejected_summary": {},
        },
        "parameters": {},
        "warnings": [],
    }


def _ball_parameters(candidates_doc: dict[str, Any], existing_report: dict[str, Any]) -> dict[str, Any]:
    parameters = candidates_doc.get("parameters") if isinstance(candidates_doc.get("parameters"), dict) else {}
    if not parameters:
        parameters = existing_report.get("parameters") if isinstance(existing_report.get("parameters"), dict) else {}
    return dict(parameters)


def _ball_report_from_tracks(tracks_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": (tracks_doc.get("parameters") or {}).get("detector") or tracks_doc.get("source") or "stored_ball_tracks",
        "status": "completed",
        "experimental": True,
        "summary": dict(tracks_doc.get("summary") or {}),
        "parameters": dict(tracks_doc.get("parameters") or {}),
        "warnings": ["ball_tracking_report.json was rebuilt from stored ball_tracks.json."],
    }


def _count_unique_track_frames(tracks: list[dict[str, Any]]) -> int:
    return len(
        {
            int(position.get("frame"))
            for track in tracks
            for position in (track.get("positions") or [])
            if isinstance(position, dict) and position.get("frame") is not None
        }
    )


def _max_track_time_sec(tracks: list[dict[str, Any]]) -> float:
    times = [
        float(position.get("time_sec") or 0.0)
        for track in tracks
        for position in (track.get("positions") or [])
        if isinstance(position, dict)
    ]
    return max(times, default=0.0)


def _infer_frame_stride(tracks: list[dict[str, Any]]) -> int:
    frames = sorted(
        {
            int(position.get("frame"))
            for track in tracks
            for position in (track.get("positions") or [])
            if isinstance(position, dict) and position.get("frame") is not None
        }
    )
    if len(frames) < 2:
        return 1
    gaps = [frames[index] - frames[index - 1] for index in range(1, len(frames)) if frames[index] > frames[index - 1]]
    return max(1, min(gaps, default=1))


def _slug(value: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")
    return safe or "reprocess"
