from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import STORAGE_DIR, WRITE_DEBUG_VIDEO_ARTIFACTS
from app.services.analysis import (
    _build_ball_possession_artifacts,
    _cleanup_debug_video_artifacts,
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
from app.services.camera_motion import CameraMotionModel, write_camera_motion_report
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
    pitch = load_pitch_config(output_dir)
    tracks = _load_tracks(output_dir / "tracks.json")
    camera_motion = CameraMotionModel.disabled(
        fps=float(metadata.get("fps") or 0.0),
        frame_count=int(metadata.get("frame_count") or 0),
    )
    write_camera_motion_report(output_dir, camera_motion)
    if not WRITE_DEBUG_VIDEO_ARTIFACTS:
        _cleanup_debug_video_artifacts(output_dir)

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
            camera_motion=None,
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
        camera_motion=None,
        ball_tracks_doc=(ball_tracking or {}).get("ball_tracks"),
        ball_candidates_doc=(ball_tracking or {}).get("ball_candidates"),
        write_debug_overlay=write_debug_overlay,
    )
    artifacts.update(stabilization["artifacts"])

    if ball_tracking is not None:
        _merge_refined_ball_tracking(output_dir, ball_tracking, stabilization)
        artifacts.update(ball_tracking["artifacts"])

    possession: dict[str, Any] | None = None
    warnings: list[str] = []
    if ball_tracking is not None and build_possession:
        try:
            possession = _build_ball_possession_artifacts(
                output_dir,
                video_path,
                pitch,
                metadata,
                ball_tracking,
                stable_players_doc=stabilization["stable_players"],
                write_overlay_video=WRITE_DEBUG_VIDEO_ARTIFACTS,
            )
            artifacts.update(possession["artifacts"])
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
            "camera_motion_compensation": False,
            "yolo_skipped": True,
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


def _load_tracks(path: Path) -> list[dict[str, Any]]:
    tracks = _load_json(path)
    if not isinstance(tracks, list):
        raise ValueError("tracks.json must contain a list")
    return tracks


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
