from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import WRITE_DEBUG_VIDEO_ARTIFACTS
from app.model_defaults import DEFAULT_BALL_YOLO_MODEL, DEFAULT_PLAYER_YOLO_MODEL
from app.services.analysis_runs import finalize_analysis_report, new_analysis_run_id
from app.services.runtime import collect_runtime_info, normalize_yolo_device, requested_device_label, resolve_yolo_device


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_analysis_chunk_manifest(
    *,
    video_metadata: dict[str, Any],
    payload: dict[str, Any],
    job_id: str | None = None,
) -> dict[str, Any]:
    fps = float(video_metadata.get("fps") or 0.0)
    duration_sec = float(video_metadata.get("duration_sec") or 0.0)
    frame_count = int(video_metadata.get("frame_count") or 0)
    max_seconds = float(payload.get("max_seconds") or 0.0)
    analysis_duration = duration_sec if max_seconds <= 0 else min(duration_sec, max_seconds)
    if analysis_duration <= 0 and fps > 0 and frame_count > 0:
        analysis_duration = frame_count / fps
    chunk_duration_sec = max(1.0, float(payload.get("chunk_duration_sec") or 120.0))
    overlap_sec = max(0.0, min(float(payload.get("chunk_overlap_sec") or 2.0), chunk_duration_sec / 2.0))
    chunks = []
    start = 0.0
    index = 1
    while start < analysis_duration - 1e-6:
        end = min(analysis_duration, start + chunk_duration_sec)
        chunks.append(
            {
                "chunk_id": f"chunk-{index:04d}",
                "index": index,
                "start_time_sec": round(start, 3),
                "end_time_sec": round(end, 3),
                "duration_sec": round(max(0.0, end - start), 3),
                "start_frame": int(round(start * fps)) if fps > 0 else None,
                "end_frame": int(round(end * fps)) if fps > 0 else None,
                "status": "planned",
                "artifacts": {},
            }
        )
        if end >= analysis_duration:
            break
        start = max(0.0, end - overlap_sec)
        index += 1

    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "job_id": job_id,
        "status": "planned",
        "execution_mode": "chunked_planned",
        "note": "Chunk boundaries are planned and persisted. Real per-chunk execution can resume completed chunks.",
        "video": {
            "fps": fps,
            "frame_count": frame_count,
            "duration_sec": round(duration_sec, 3),
        },
        "parameters": {
            "max_seconds": max_seconds,
            "analysis_duration_sec": round(analysis_duration, 3),
            "chunk_duration_sec": chunk_duration_sec,
            "chunk_overlap_sec": overlap_sec,
            "frame_stride": payload.get("frame_stride"),
        },
        "summary": {
            "chunks": len(chunks),
            "analysis_duration_sec": round(analysis_duration, 3),
            "chunk_duration_sec": chunk_duration_sec,
            "chunk_overlap_sec": overlap_sec,
        },
        "chunks": chunks,
    }


def write_analysis_chunk_manifest(match_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    (match_path / "analysis_chunk_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return manifest


def mark_chunk_manifest_single_pass_completed(match_path: Path, report: dict[str, Any]) -> dict[str, Any] | None:
    path = match_path / "analysis_chunk_manifest.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        return None
    manifest["status"] = "single_pass_completed"
    manifest["updated_at"] = now_iso()
    manifest["single_pass_run"] = {
        "run_id": report.get("run_id"),
        "status": report.get("status"),
        "frames_processed": report.get("frames_processed"),
        "run_directory": report.get("run_directory"),
        "run_manifest": report.get("run_manifest"),
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def analyze_match_chunked_yolo(
    match_dir: Path,
    video_path: Path,
    *,
    payload: dict[str, Any],
    job_id: str | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    adapter = str(payload.get("adapter") or "yolo")
    if adapter != "yolo":
        raise ValueError("Real chunked analysis currently supports the yolo adapter only.")

    from app.services.analysis import (
        DEFAULT_CLAMP_POSITIONS_TO_PITCH,
        DEFAULT_PITCH_FILTER_MARGIN_PX,
        _cleanup_debug_video_artifacts,
        _load_yolo_model,
        _render_final_stable_overlay,
        _write_outputs,
        collect_yolo_tracks_range,
        load_pitch_config,
        write_raw_overlay_from_tracks,
    )
    from app.services.ball_possession import build_ball_possession_analysis
    from app.services.camera_motion import (
        DEFAULT_CAMERA_MOTION_COMPENSATION,
        DEFAULT_CAMERA_MOTION_INTERVAL_SEC,
        DEFAULT_CAMERA_MOTION_MIN_INLIER_RATIO,
        CameraMotionModel,
        build_camera_motion_model,
        write_camera_motion_overlay,
        write_camera_motion_report,
    )
    from app.services.ball_tracking import (
        DEFAULT_BALL_CONF,
        build_ball_candidates_document,
        build_ball_quality_report,
        build_ball_tracking_report,
        build_ball_tracks_document,
        collect_ball_candidates_range,
        write_ball_overlay,
    )
    from app.services.stabilization import stabilize_match
    from app.services.video import read_video_metadata

    metadata = read_video_metadata(video_path)
    manifest = _load_or_create_real_manifest(match_dir, metadata, payload, job_id)
    chunks = manifest.get("chunks") if isinstance(manifest.get("chunks"), list) else []
    if not chunks:
        raise ValueError("Chunk manifest does not contain any chunks.")
    pitch = load_pitch_config(match_dir)
    if not WRITE_DEBUG_VIDEO_ARTIFACTS:
        _cleanup_debug_video_artifacts(match_dir)
    frame_stride = max(1, int(payload.get("frame_stride") or 1))
    yolo_model = str(payload.get("yolo_model") or DEFAULT_PLAYER_YOLO_MODEL)
    yolo_tracker = str(payload.get("yolo_tracker") or "centroid_high_recall")
    yolo_device = normalize_yolo_device(payload.get("yolo_device"))
    yolo_conf = float(payload.get("yolo_conf") or 0.05)
    yolo_imgsz = int(payload.get("yolo_imgsz") or 960)
    include_ball = bool(payload.get("include_ball"))
    render_stable_overlay = bool(payload.get("render_stable_overlay", True))
    ball_yolo_model = str(payload.get("ball_yolo_model") or DEFAULT_BALL_YOLO_MODEL)
    ball_yolo_conf = float(payload.get("ball_yolo_conf") or DEFAULT_BALL_CONF)
    ball_yolo_imgsz = int(payload.get("ball_yolo_imgsz") or yolo_imgsz)
    ball_yolo_device = normalize_yolo_device(payload.get("ball_yolo_device") or payload.get("yolo_device"))
    camera_motion_compensation = bool(payload.get("camera_motion_compensation", DEFAULT_CAMERA_MOTION_COMPENSATION))
    camera_motion_interval_sec = float(payload.get("camera_motion_interval_sec") or DEFAULT_CAMERA_MOTION_INTERVAL_SEC)
    camera_motion_min_inlier_ratio = float(payload.get("camera_motion_min_inlier_ratio") or DEFAULT_CAMERA_MOTION_MIN_INLIER_RATIO)
    runtime_info = collect_runtime_info()
    yolo_device = resolve_yolo_device(
        yolo_device,
        runtime_info=runtime_info,
        context="player YOLO chunked",
    )
    if include_ball:
        ball_yolo_device = resolve_yolo_device(
            ball_yolo_device,
            runtime_info=runtime_info,
            context="ball YOLO chunked",
        )
    analysis_end_sec = float(payload.get("max_seconds") or 0.0) or None
    camera_motion_warnings: list[str] = []
    if progress:
        progress(
            "camera_motion",
            10.0,
            "Building camera motion compensation model.",
            {"current": 1, "total": len(chunks), "unit": "setup"},
        )
    try:
        camera_motion = build_camera_motion_model(
            video_path,
            metadata,
            calibration_frame_time_sec=pitch.calibration_frame_time_sec,
            start_time_sec=0.0,
            end_time_sec=analysis_end_sec,
            interval_sec=camera_motion_interval_sec,
            min_inlier_ratio=camera_motion_min_inlier_ratio,
            enabled=camera_motion_compensation,
            reference_pitch_polygon=pitch.polygon_np,
        )
    except Exception as exc:
        camera_motion = CameraMotionModel.disabled(
            fps=float(metadata.get("fps") or 0.0),
            frame_count=int(metadata.get("frame_count") or 0),
        )
        camera_motion_warnings.append(f"Camera motion compensation disabled after estimator failure: {exc}")
    shared_model = _load_yolo_model(yolo_model) if yolo_tracker == "centroid_high_recall" else None
    ball_model = _load_yolo_model(ball_yolo_model) if include_ball else None

    manifest["status"] = "running"
    manifest["execution_mode"] = "chunked_real_v1"
    manifest["updated_at"] = now_iso()
    manifest["runtime"] = runtime_info
    manifest["parameters"]["yolo_model"] = yolo_model
    manifest["parameters"]["yolo_conf"] = yolo_conf
    manifest["parameters"]["yolo_imgsz"] = yolo_imgsz
    manifest["parameters"]["yolo_tracker"] = yolo_tracker
    manifest["parameters"]["yolo_device"] = yolo_device or "auto"
    manifest["parameters"]["yolo_device_requested"] = requested_device_label(payload.get("yolo_device"))
    manifest["parameters"]["camera_motion_compensation"] = camera_motion.enabled
    manifest["parameters"]["camera_motion_interval_sec"] = camera_motion_interval_sec
    manifest["parameters"]["camera_motion_min_inlier_ratio"] = camera_motion_min_inlier_ratio
    manifest["parameters"]["camera_motion_reference_frame"] = camera_motion.reference_frame
    manifest["parameters"]["include_ball"] = include_ball
    manifest["parameters"]["render_stable_overlay"] = render_stable_overlay
    if include_ball:
        manifest["parameters"]["ball_yolo_model"] = ball_yolo_model
        manifest["parameters"]["ball_yolo_conf"] = ball_yolo_conf
        manifest["parameters"]["ball_yolo_imgsz"] = ball_yolo_imgsz
        manifest["parameters"]["ball_yolo_device"] = ball_yolo_device or "auto"
        manifest["parameters"]["ball_yolo_device_requested"] = requested_device_label(payload.get("ball_yolo_device") or payload.get("yolo_device"))
    write_analysis_chunk_manifest(match_dir, manifest)

    completed_chunks = 0
    for chunk in chunks:
        chunk_id = str(chunk["chunk_id"])
        chunk_dir = match_dir / "analysis_chunks" / chunk_id
        report_path = chunk_dir / "chunk_analysis_report.json"
        tracks_path = chunk_dir / "tracks.json"
        if _chunk_completed_for_payload(chunk, chunk_dir, include_ball=include_ball):
            completed_chunks += 1
            if progress:
                progress(
                    "chunk_resume",
                    _chunk_progress(completed_chunks, len(chunks), base=15.0, span=65.0),
                    f"Skipping completed {chunk_id}.",
                    {"chunk_count": len(chunks), "chunk_manifest": "analysis_chunk_manifest.json"},
                )
            continue

        chunk_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(match_dir / "pitch_config.json", chunk_dir / "pitch_config.json")
        _update_chunk(manifest, chunk_id, status="running", started_at=now_iso(), error=None)
        write_analysis_chunk_manifest(match_dir, manifest)
        if progress:
            progress(
                "chunk_analyzing",
                _chunk_progress(completed_chunks, len(chunks), base=15.0, span=65.0),
                f"Analyzing {chunk_id} ({chunk['start_time_sec']}s -> {chunk['end_time_sec']}s).",
                {"chunk_count": len(chunks), "chunk_manifest": "analysis_chunk_manifest.json"},
            )

        try:
            chunk_result = collect_yolo_tracks_range(
                match_dir,
                video_path,
                pitch,
                metadata,
                start_time_sec=float(chunk["start_time_sec"]),
                end_time_sec=float(chunk["end_time_sec"]),
                frame_stride=frame_stride,
                yolo_model=yolo_model,
                yolo_conf=yolo_conf,
                yolo_imgsz=yolo_imgsz,
                yolo_tracker=yolo_tracker,
                yolo_device=yolo_device,
                track_id_offset=int(chunk["index"]) * 100000,
                model=shared_model,
                camera_motion=camera_motion,
            )
            tracks_path.write_text(json.dumps(chunk_result["tracks"], indent=2), encoding="utf-8")
            chunk_artifacts = {
                "chunk_analysis_report": f"analysis_chunks/{chunk_id}/chunk_analysis_report.json",
                "tracks_json": f"analysis_chunks/{chunk_id}/tracks.json",
            }
            chunk_metrics = dict(chunk_result["metrics"])
            chunk_warnings = list(chunk_result["warnings"])
            if include_ball and ball_model is not None:
                ball_result = collect_ball_candidates_range(
                    video_path,
                    pitch,
                    metadata,
                    model=ball_model,
                    start_time_sec=float(chunk["start_time_sec"]),
                    end_time_sec=float(chunk["end_time_sec"]),
                    frame_stride=frame_stride,
                    yolo_imgsz=ball_yolo_imgsz,
                    yolo_device=ball_yolo_device,
                    ball_conf=ball_yolo_conf,
                    camera_motion=camera_motion,
                )
                ball_observations = {
                    "schema_version": "0.1.0",
                    "chunk_id": chunk_id,
                    "generated_at": now_iso(),
                    "frames": ball_result["frames"],
                    "processed_frames": ball_result["processed_frames"],
                    "rejected_summary": ball_result["rejected_summary"],
                    "parameters": ball_result["parameters"],
                    "warnings": ball_result["warnings"],
                    "metrics": ball_result["metrics"],
                }
                (chunk_dir / "ball_observations.json").write_text(
                    json.dumps(ball_observations, indent=2),
                    encoding="utf-8",
                )
                chunk_artifacts["ball_observations"] = f"analysis_chunks/{chunk_id}/ball_observations.json"
                chunk_metrics["ball"] = ball_result["metrics"]
                chunk_warnings.extend(ball_result["warnings"])
            chunk_report = {
                "schema_version": "0.1.0",
                "status": "completed",
                "chunk_id": chunk_id,
                "generated_at": now_iso(),
                "range": {
                    "start_time_sec": chunk.get("start_time_sec"),
                    "end_time_sec": chunk.get("end_time_sec"),
                    "start_frame": chunk.get("start_frame"),
                    "end_frame": chunk.get("end_frame"),
                },
                "metrics": chunk_metrics,
                "warnings": chunk_warnings,
                "artifacts": {
                    key: Path(value).name
                    for key, value in chunk_artifacts.items()
                    if isinstance(value, str)
                },
            }
            report_path.write_text(json.dumps(chunk_report, indent=2), encoding="utf-8")
            _update_chunk(
                manifest,
                chunk_id,
                status="completed",
                finished_at=now_iso(),
                metrics=chunk_metrics,
                warnings=chunk_warnings,
                artifacts=chunk_artifacts,
                error=None,
            )
        except Exception as exc:
            _update_chunk(
                manifest,
                chunk_id,
                status="failed",
                finished_at=now_iso(),
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            manifest["status"] = "failed"
            manifest["updated_at"] = now_iso()
            write_analysis_chunk_manifest(match_dir, manifest)
            raise
        completed_chunks += 1
        manifest["status"] = "partial" if completed_chunks < len(chunks) else "merging"
        manifest["updated_at"] = now_iso()
        write_analysis_chunk_manifest(match_dir, manifest)

    if progress:
        progress("chunk_merging", 82.0, "Merging chunk tracks and generating final artifacts.", {"chunk_count": len(chunks)})
    merged_tracks = merge_completed_chunk_tracks(match_dir, manifest)
    if progress:
        progress(
            "chunk_merging",
            84.0,
            f"Merged {len(merged_tracks)} player tracks from {len(chunks)} chunks.",
            {"current": len(chunks), "total": len(chunks), "unit": "chunks", "artifact": "tracks.json"},
        )
    if WRITE_DEBUG_VIDEO_ARTIFACTS:
        write_raw_overlay_from_tracks(
            match_dir,
            video_path,
            pitch,
            merged_tracks,
            metadata,
            frame_stride=frame_stride,
            max_seconds=float(payload.get("max_seconds") or 0.0),
            camera_motion=camera_motion,
        )
    artifacts = _write_outputs(match_dir, pitch, merged_tracks, include_overlay=WRITE_DEBUG_VIDEO_ARTIFACTS)
    write_camera_motion_report(match_dir, camera_motion)
    artifacts["camera_motion_report"] = "camera_motion_report.json"
    ball_tracking: dict[str, Any] | None = None
    ball_tracks_doc: dict[str, Any] | None = None
    ball_candidates_doc: dict[str, Any] | None = None
    ball_report: dict[str, Any] | None = None
    possession: dict[str, Any] | None = None
    if include_ball:
        if progress:
            progress("chunk_ball_merge", 86.0, "Merging ball observations from chunks.", {"chunk_count": len(chunks)})
        ball_merge = merge_completed_chunk_ball_observations(match_dir, manifest)
        ball_parameters = ball_merge["parameters"]
        if progress:
            progress(
                "ball_tracking",
                88.0,
                f"Building ball tracks from {len(ball_merge['frames'])} candidate frames.",
                {"current": len(ball_merge["processed_frames"]), "total": int(metadata.get("frame_count") or 0), "unit": "frames"},
            )
        ball_candidates_doc = build_ball_candidates_document(
            ball_merge["frames"],
            processed_frames=ball_merge["processed_frames"],
            rejected_summary=ball_merge["rejected_summary"],
            parameters=ball_parameters,
        )
        ball_tracks_doc = build_ball_tracks_document(
            ball_merge["frames"],
            processed_frames=ball_merge["processed_frames"],
            fps=float(metadata.get("fps") or 0.0),
            parameters=ball_parameters,
        )
        ball_report = build_ball_tracking_report(
            ball_tracks_doc,
            ball_candidates_doc,
            parameters=ball_parameters,
            warnings=ball_merge["warnings"],
        )
        ball_quality_report = build_ball_quality_report(ball_tracks_doc, ball_candidates_doc, ball_report)
        manifest["summary"]["ball_merged_processed_frames"] = len(ball_merge["processed_frames"])
        manifest["summary"]["ball_merged_candidate_count"] = int((ball_candidates_doc.get("summary") or {}).get("candidate_count") or 0)
        (match_dir / "ball_candidates.json").write_text(json.dumps(ball_candidates_doc, indent=2), encoding="utf-8")
        (match_dir / "ball_tracks.json").write_text(json.dumps(ball_tracks_doc, indent=2), encoding="utf-8")
        (match_dir / "ball_tracking_report.json").write_text(json.dumps(ball_report, indent=2), encoding="utf-8")
        (match_dir / "ball_quality_report.json").write_text(json.dumps(ball_quality_report, indent=2), encoding="utf-8")
        ball_artifacts = {
            "ball_candidates": "ball_candidates.json",
            "ball_tracks": "ball_tracks.json",
            "ball_tracking_report": "ball_tracking_report.json",
            "ball_quality_report": "ball_quality_report.json",
        }
        if WRITE_DEBUG_VIDEO_ARTIFACTS:
            write_ball_overlay(
                video_path,
                match_dir,
                ball_tracks_doc,
                ball_candidates_doc,
                pitch.polygon_np,
                fps=float(metadata.get("fps") or 0.0),
                frame_size=(int(metadata.get("width") or 0), int(metadata.get("height") or 0)),
                camera_motion=camera_motion,
            )
            ball_artifacts["ball_overlay_preview"] = "ball_overlay_preview.mp4"
        ball_tracking = {
            "ball_candidates": ball_candidates_doc,
            "ball_tracks": ball_tracks_doc,
            "ball_tracking_report": ball_report,
            "ball_quality_report": ball_quality_report,
            "artifacts": ball_artifacts,
        }
        artifacts.update(ball_tracking["artifacts"])
        if progress:
            progress(
                "ball_tracking",
                90.0,
                "Ball tracking artifacts written.",
                {"artifact": "ball_tracks.json"},
            )

    if progress:
        progress(
            "stabilization",
            91.0,
            "Stabilizing player identities, stats, and stable overlay.",
            {"current": len(merged_tracks), "unit": "tracks"},
        )
    stabilization = stabilize_match(
        match_dir,
        video_path,
        pitch,
        merged_tracks,
        metadata,
        camera_motion=camera_motion,
        ball_tracks_doc=ball_tracks_doc,
        ball_candidates_doc=ball_candidates_doc,
        write_debug_overlay=WRITE_DEBUG_VIDEO_ARTIFACTS,
        render_stable_overlay=render_stable_overlay,
        defer_stable_overlay_render=render_stable_overlay,
        progress=progress,
    )
    refined_ball_tracks = stabilization.get("refined_ball_tracks")
    if refined_ball_tracks is not None:
        ball_tracks_doc = refined_ball_tracks
        if ball_tracking is not None:
            ball_tracking["ball_tracks"] = refined_ball_tracks
        if ball_report is not None:
            ball_report["summary"] = {
                **(ball_report.get("summary") or {}),
                **(refined_ball_tracks.get("summary") or {}),
            }
            if ball_tracking is not None:
                ball_tracking["ball_tracking_report"] = ball_report
        if ball_tracking is not None and ball_report is not None:
            ball_quality_report = build_ball_quality_report(refined_ball_tracks, ball_candidates_doc, ball_report)
            ball_tracking["ball_quality_report"] = ball_quality_report
            (match_dir / "ball_tracking_report.json").write_text(json.dumps(ball_report, indent=2), encoding="utf-8")
            (match_dir / "ball_quality_report.json").write_text(json.dumps(ball_quality_report, indent=2), encoding="utf-8")
    artifacts.update(stabilization["artifacts"])
    artifacts["analysis_chunk_manifest"] = "analysis_chunk_manifest.json"
    if WRITE_DEBUG_VIDEO_ARTIFACTS:
        try:
            write_camera_motion_overlay(
                video_path,
                match_dir,
                camera_motion,
                pitch.polygon_np,
                metadata,
                frame_stride=frame_stride,
                max_seconds=float(payload.get("max_seconds") or 0.0),
            )
            artifacts["camera_motion_overlay"] = "camera_motion_overlay.mp4"
        except Exception as exc:
            camera_motion_warnings.append(f"Camera motion debug overlay failed: {exc}")
    if ball_tracking is not None and ball_report is not None:
        try:
            if progress:
                progress("possession_pass_candidates", 96.0, "Building possession and pass candidate layers.", None)
            possession = build_ball_possession_analysis(
                match_dir,
                video_path,
                pitch,
                metadata,
                ball_tracks_doc,
                stabilization.get("stable_players_overlay_doc") or stabilization["stable_players"],
                write_overlay_video=WRITE_DEBUG_VIDEO_ARTIFACTS,
            )
            artifacts.update(possession["artifacts"])
        except Exception as exc:
            ball_report.setdefault("warnings", []).append(f"Chunked possession candidate layer failed: {exc}")
            (match_dir / "ball_tracking_report.json").write_text(json.dumps(ball_report, indent=2), encoding="utf-8")
    if render_stable_overlay and "stable_overlay_preview" not in artifacts:
        try:
            artifacts.update(
                _render_final_stable_overlay(
                    match_dir,
                    video_path,
                    pitch,
                    metadata,
                    stabilization,
                    ball_tracking,
                    possession,
                    camera_motion=camera_motion,
                    progress=progress,
                    progress_percent=98.0 if possession is not None else 94.0,
                )
            )
        except Exception as exc:
            camera_motion_warnings.append(f"Stable overlay render failed: {exc}")
    chunk_warnings = [
        warning
        for chunk in manifest.get("chunks", [])
        for warning in (chunk.get("warnings") or [])
        if isinstance(warning, str)
    ]
    warnings = list(dict.fromkeys([*camera_motion_warnings, *chunk_warnings]))
    if not include_ball:
        warnings.append("Chunked run skipped ball/possession analysis because include_ball=false.")
    if progress:
        progress("final_reports", 99.0, "Writing final reports and analysis metadata.", None)
    manifest["status"] = "completed"
    manifest["updated_at"] = now_iso()
    manifest["summary"]["completed_chunks"] = len(chunks)
    manifest["summary"]["merged_tracks"] = len(merged_tracks)
    manifest["summary"]["frames_processed"] = sum(int(((chunk.get("metrics") or {}).get("frames_processed") or 0)) for chunk in chunks)
    if include_ball:
        manifest["summary"]["ball_processed_frames"] = sum(
            int((((chunk.get("metrics") or {}).get("ball") or {}).get("processed_frames") or 0))
            for chunk in chunks
        )
        manifest["summary"]["ball_candidate_count"] = sum(
            int((((chunk.get("metrics") or {}).get("ball") or {}).get("candidate_count") or 0))
            for chunk in chunks
        )
    run_id = new_analysis_run_id("yolo-ultralytics-chunked")
    manifest["final_run"] = {
        "run_id": run_id,
        "status": "completed",
        "tracks_count": len(merged_tracks),
        "run_directory": f"analysis_runs/{run_id}",
        "run_manifest": f"analysis_runs/{run_id}/run_metadata.json",
    }
    write_analysis_chunk_manifest(match_dir, manifest)
    first_player_metrics = next(
        (
            chunk.get("metrics") or {}
            for chunk in chunks
            if (chunk.get("metrics") or {}).get("player_class_ids") is not None
        ),
        {},
    )
    report = {
        "schema_version": "0.1.0",
        "run_id": run_id,
        "status": "completed",
        "analysis_type": "yolo-ultralytics-chunked",
        "note": "Real chunked runner v1: chunks are processed independently, completed chunks are skipped on retry, then tracks are merged into one stable analysis.",
        "video": metadata,
        "runtime": runtime_info,
        "requested_device": requested_device_label(payload.get("yolo_device")),
        "normalized_yolo_device": yolo_device or "auto",
        "cuda_available": bool((runtime_info.get("torch") or {}).get("cuda_available")),
        "cuda_device_names": (runtime_info.get("torch") or {}).get("cuda_device_names") or [],
        "parameters": {
            "adapter": "yolo",
            "chunked": True,
            "max_seconds": float(payload.get("max_seconds") or 0.0),
            "frame_stride": frame_stride,
            "chunk_duration_sec": manifest["parameters"]["chunk_duration_sec"],
            "chunk_overlap_sec": manifest["parameters"]["chunk_overlap_sec"],
            "include_ball": include_ball,
            "render_stable_overlay": render_stable_overlay,
            "yolo_model": yolo_model,
            "yolo_conf": yolo_conf,
            "yolo_iou": 0.45,
            "yolo_imgsz": yolo_imgsz,
            "yolo_tracker": yolo_tracker,
            "yolo_device": yolo_device or "auto",
            "yolo_device_requested": requested_device_label(payload.get("yolo_device")),
            "classes": first_player_metrics.get("player_class_names") or ["person"],
            "player_class_ids": first_player_metrics.get("player_class_ids") or [0],
            "player_class_names": first_player_metrics.get("player_class_names") or ["person"],
            "player_class_resolution": first_player_metrics.get("player_class_resolution") or "fallback_class_0",
            "model_classes": first_player_metrics.get("model_classes"),
            "pitch_filter": "footpoint_in_pitch_polygon_with_margin",
            "pitch_filter_margin_px": DEFAULT_PITCH_FILTER_MARGIN_PX,
            "clamp_positions_to_pitch": DEFAULT_CLAMP_POSITIONS_TO_PITCH,
            "camera_motion_compensation": camera_motion.enabled,
            "camera_motion_interval_sec": camera_motion_interval_sec,
            "camera_motion_min_inlier_ratio": camera_motion_min_inlier_ratio,
            "camera_motion_reference_frame": camera_motion.reference_frame,
            "camera_motion_reference_time_sec": round(camera_motion.reference_time_sec, 3),
            "tracking_backend": "centroid" if yolo_tracker == "centroid_high_recall" else "ultralytics",
            "ball_yolo_model": ball_yolo_model if include_ball else None,
            "ball_yolo_conf": ball_yolo_conf if include_ball else None,
            "ball_yolo_imgsz": ball_yolo_imgsz if include_ball else None,
            "ball_yolo_device": (ball_yolo_device or "auto") if include_ball else None,
        },
        "chunked_summary": manifest["summary"],
        "frames_processed": manifest["summary"]["frames_processed"],
        "detections_kept": sum(int(((chunk.get("metrics") or {}).get("detections_kept") or 0)) for chunk in chunks),
        "detections_rejected_outside_pitch": sum(
            int(((chunk.get("metrics") or {}).get("detections_rejected_outside_pitch") or 0)) for chunk in chunks
        ),
        "detections_accepted_by_pitch_margin": sum(
            int(((chunk.get("metrics") or {}).get("detections_accepted_by_pitch_margin") or 0)) for chunk in chunks
        ),
        "positions_clamped_to_pitch": sum(
            int(((chunk.get("metrics") or {}).get("positions_clamped_to_pitch") or 0)) for chunk in chunks
        ),
        "pitch_filter_margin_px": DEFAULT_PITCH_FILTER_MARGIN_PX,
        "clamp_positions_to_pitch": DEFAULT_CLAMP_POSITIONS_TO_PITCH,
        "camera_motion_summary": camera_motion.report()["summary"],
        "tracks_count": len(merged_tracks),
        "stable_players_count": stabilization["stable_players"]["summary"]["stable_players"],
        "ball_tracking_summary": (ball_tracking or {}).get("ball_tracking_report", {}).get("summary"),
        "ball_quality_summary": (ball_tracking or {}).get("ball_quality_report", {}).get("summary"),
        "possession_summary": (possession or {}).get("possession_report", {}).get("summary"),
        "warnings": warnings,
        "artifacts": artifacts,
    }
    return finalize_analysis_report(match_dir, report)


def merge_completed_chunk_tracks(match_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = [chunk for chunk in manifest.get("chunks", []) if chunk.get("status") == "completed"]
    chunks = sorted(chunks, key=lambda item: int(item.get("index") or 0))
    merged: list[dict[str, Any]] = []
    previous_end_time: float | None = None
    for chunk in chunks:
        chunk_id = str(chunk["chunk_id"])
        path = match_dir / "analysis_chunks" / chunk_id / "tracks.json"
        if not path.exists():
            raise FileNotFoundError(f"Missing chunk tracks for completed chunk: {chunk_id}")
        tracks = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(tracks, list):
            raise ValueError(f"Chunk tracks must be a list: {chunk_id}")
        cutoff = previous_end_time
        for track in tracks:
            positions = [
                dict(position)
                for position in (track.get("positions") or [])
                if isinstance(position, dict)
                and _position_in_chunk_merge_window(position, chunk, cutoff)
            ]
            if not positions:
                continue
            positions = sorted(positions, key=lambda item: (int(item.get("frame") or 0), float(item.get("time_sec") or 0.0)))
            merged.append(
                {
                    "track_id": int(track.get("track_id")),
                    "start_time_sec": positions[0]["time_sec"],
                    "end_time_sec": positions[-1]["time_sec"],
                    "duration_sec": round(float(positions[-1]["time_sec"]) - float(positions[0]["time_sec"]), 3),
                    "positions_count": len(positions),
                    "chunk_id": chunk_id,
                    "source": "chunked_merge",
                    "positions": positions,
                }
            )
        previous_end_time = float(chunk.get("end_time_sec") or 0.0)
    return sorted(merged, key=lambda item: int(item["track_id"]))


def merge_completed_chunk_ball_observations(match_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    chunks = [chunk for chunk in manifest.get("chunks", []) if chunk.get("status") == "completed"]
    chunks = sorted(chunks, key=lambda item: int(item.get("index") or 0))
    frames_by_index: dict[int, dict[str, Any]] = {}
    processed_frames: set[int] = set()
    rejected_summary: dict[str, int] = {}
    parameters: dict[str, Any] | None = None
    warnings: list[str] = []
    previous_end_time: float | None = None
    for chunk in chunks:
        chunk_id = str(chunk["chunk_id"])
        path = match_dir / "analysis_chunks" / chunk_id / "ball_observations.json"
        if not path.exists():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            continue
        parameters = parameters or dict(document.get("parameters") or {})
        warnings.extend([str(item) for item in document.get("warnings") or []])
        for reason, count in (document.get("rejected_summary") or {}).items():
            rejected_summary[str(reason)] = rejected_summary.get(str(reason), 0) + int(count or 0)
        cutoff = previous_end_time
        for frame in document.get("frames") or []:
            if not isinstance(frame, dict) or not _position_in_chunk_merge_window(frame, chunk, cutoff):
                continue
            frames_by_index[int(frame.get("frame") or 0)] = frame
        for frame_idx in document.get("processed_frames") or []:
            synthetic = {"time_sec": float(frame_idx) / max(float((manifest.get("video") or {}).get("fps") or 0.0), 0.001)}
            if _position_in_chunk_merge_window(synthetic, chunk, cutoff):
                processed_frames.add(int(frame_idx))
        previous_end_time = float(chunk.get("end_time_sec") or 0.0)
    return {
        "frames": [frames_by_index[frame] for frame in sorted(frames_by_index)],
        "processed_frames": sorted(processed_frames),
        "rejected_summary": rejected_summary,
        "parameters": parameters or {},
        "warnings": list(dict.fromkeys(warnings)),
    }


def _load_or_create_real_manifest(
    match_dir: Path,
    metadata: dict[str, Any],
    payload: dict[str, Any],
    job_id: str | None,
) -> dict[str, Any]:
    path = match_dir / "analysis_chunk_manifest.json"
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and _manifest_matches_payload(loaded, payload):
            loaded["job_id"] = job_id or loaded.get("job_id")
            return loaded
    manifest = build_analysis_chunk_manifest(video_metadata=metadata, payload=payload, job_id=job_id)
    manifest["execution_mode"] = "chunked_real_v1"
    write_analysis_chunk_manifest(match_dir, manifest)
    return manifest


def _manifest_matches_payload(manifest: dict[str, Any], payload: dict[str, Any]) -> bool:
    parameters = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
    if not manifest.get("chunks"):
        return False
    if bool(parameters.get("include_ball")) != bool(payload.get("include_ball")):
        return False
    numeric_keys = [
        "max_seconds",
        "frame_stride",
        "chunk_duration_sec",
        "chunk_overlap_sec",
        "camera_motion_interval_sec",
        "camera_motion_min_inlier_ratio",
    ]
    for key in numeric_keys:
        if abs(float(parameters.get(key) or 0.0) - float(payload.get(key) or 0.0)) > 1e-6:
            return False
    yolo_keys = ["yolo_model", "yolo_conf", "yolo_imgsz", "yolo_tracker"]
    for key in yolo_keys:
        if key in parameters and str(parameters.get(key)) != str(payload.get(key)):
            return False
    if bool(parameters.get("camera_motion_compensation")) != bool(payload.get("camera_motion_compensation", True)):
        return False
    if bool(payload.get("include_ball")):
        ball_numeric_keys = ["ball_yolo_conf", "ball_yolo_imgsz"]
        for key in ball_numeric_keys:
            if key in parameters and abs(float(parameters.get(key) or 0.0) - float(payload.get(key) or 0.0)) > 1e-6:
                return False
        for key in ["ball_yolo_model"]:
            if key in parameters and str(parameters.get(key)) != str(payload.get(key)):
                return False
    return True


def _update_chunk(manifest: dict[str, Any], chunk_id: str, **updates: Any) -> None:
    for chunk in manifest.get("chunks", []):
        if chunk.get("chunk_id") == chunk_id:
            chunk.update({key: value for key, value in updates.items() if value is not None})
            chunk["updated_at"] = now_iso()
            return
    raise KeyError(f"Unknown chunk_id: {chunk_id}")


def _chunk_completed_for_payload(chunk: dict[str, Any], chunk_dir: Path, *, include_ball: bool) -> bool:
    if chunk.get("status") != "completed":
        return False
    if not (chunk_dir / "chunk_analysis_report.json").exists():
        return False
    if not (chunk_dir / "tracks.json").exists():
        return False
    if include_ball and not (chunk_dir / "ball_observations.json").exists():
        return False
    return True


def _position_in_chunk_merge_window(position: dict[str, Any], chunk: dict[str, Any], cutoff: float | None) -> bool:
    time_sec = float(position.get("time_sec") or 0.0)
    if cutoff is not None and time_sec < cutoff - 1e-6:
        return False
    end_time = float(chunk.get("end_time_sec") or time_sec)
    return time_sec <= end_time + 1e-6


def _chunk_progress(completed: int, total: int, *, base: float, span: float) -> float:
    if total <= 0:
        return base
    return round(base + span * (completed / total), 2)
