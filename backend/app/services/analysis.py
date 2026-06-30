from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np

from app.config import ROOT_DIR
from app.services.analysis_runs import finalize_analysis_report, new_analysis_run_id, now_iso
from app.services.ball_tracking import DEFAULT_BALL_CONF, detect_ball_yolo_coco
from app.services.ball_possession import build_ball_possession_analysis
from app.services.pitch import PitchConfig, create_pitch_mask, image_to_pitch_m, point_in_polygon
from app.services.stabilization import stabilize_match
from app.services.tracker import CentroidTracker
from app.services.video import read_video_metadata

AnalysisAdapter = Literal["motion", "yolo"]

BALL_ARTIFACT_FILENAMES = {
    "ball_candidates": "ball_candidates.json",
    "ball_tracks": "ball_tracks.json",
    "ball_tracking_report": "ball_tracking_report.json",
    "ball_quality_report": "ball_quality_report.json",
    "ball_overlay_preview": "ball_overlay_preview.mp4",
    "possession_candidates": "possession_candidates.json",
    "possession_segments": "possession_segments.json",
    "contact_candidates": "contact_candidates.json",
    "match_phase_config": "match_phase_config.json",
    "event_candidates": "event_candidates.json",
    "event_review_report": "event_review_report.json",
    "pass_candidates": "pass_candidates.json",
    "pass_review_report": "pass_review_report.json",
    "possession_report": "possession_report.json",
    "possession_overlay_preview": "possession_overlay_preview.mp4",
}


class OverlayWriter:
    """Write browser-playable overlay previews.

    OpenCV's mp4v output often creates files that exist but do not play in Chrome.
    We therefore write an intermediate MJPEG AVI and transcode it to H.264 MP4
    with ffmpeg, which is installed in the Docker image.
    """

    def __init__(self, match_dir: Path, fps: float, frame_size: tuple[int, int]) -> None:
        self.match_dir = match_dir
        self.frame_size = frame_size
        self.fps = max(1.0, float(fps))
        self.final_path = match_dir / "overlay_preview.mp4"
        self.temp_path = match_dir / "overlay_preview.raw.avi"
        self.frames_written = 0
        self._writer = cv2.VideoWriter(
            str(self.temp_path),
            cv2.VideoWriter_fourcc(*"MJPG"),
            self.fps,
            frame_size,
        )
        if not self._writer.isOpened():
            raise RuntimeError(
                "Could not open OpenCV VideoWriter for overlay_preview.raw.avi. "
                "Check ffmpeg/OpenCV installation and write permissions in backend/storage."
            )

    def write(self, frame: np.ndarray) -> None:
        expected_w, expected_h = self.frame_size
        if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
            frame = cv2.resize(frame, (expected_w, expected_h))
        self._writer.write(frame)
        self.frames_written += 1

    def close(self) -> Path:
        self._writer.release()
        if self.frames_written == 0:
            self.temp_path.unlink(missing_ok=True)
            raise RuntimeError("Overlay preview was not generated because zero frames were processed.")
        if not self.temp_path.exists() or self.temp_path.stat().st_size == 0:
            raise RuntimeError("OpenCV created an empty overlay preview file.")

        if self.final_path.exists():
            self.final_path.unlink()

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            # Last-resort fallback: leave the AVI and report a clear error rather than a broken MP4.
            raise RuntimeError(
                "ffmpeg is not available, so the overlay could not be converted to browser-playable MP4. "
                "Install ffmpeg or use Docker, where ffmpeg is included."
            )

        cmd = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(self.temp_path),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(self.final_path),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.temp_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            raise RuntimeError(
                "ffmpeg failed while converting overlay preview to H.264 MP4: "
                f"{completed.stderr.strip() or completed.stdout.strip() or 'unknown error'}"
            )
        if not self.final_path.exists() or self.final_path.stat().st_size == 0:
            raise RuntimeError("ffmpeg finished but overlay_preview.mp4 is missing or empty.")
        return self.final_path


def load_pitch_config(match_dir: Path) -> PitchConfig:
    path = match_dir / "pitch_config.json"
    if not path.exists():
        raise FileNotFoundError("Missing pitch_config.json. Calibrate pitch first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    pitch_dimensions = data.get("pitch_dimensions_m") if isinstance(data.get("pitch_dimensions_m"), dict) else {}
    width_m = float(data.get("width_m") or pitch_dimensions.get("width_m") or 30.0)
    length_m = float(data.get("length_m") or pitch_dimensions.get("length_m") or 47.4)
    if abs(width_m - 26.0) < 0.001 and abs(length_m - 56.0) < 0.001:
        width_m = 30.0
        length_m = 47.4
    return PitchConfig(
        image_points=data["image_points"],
        width_m=width_m,
        length_m=length_m,
    )


def detect_motion_people_like_blobs(
    frame: np.ndarray,
    fg_mask: np.ndarray,
    pitch_polygon: np.ndarray,
    min_area: int,
    max_area: int,
) -> list[dict[str, Any]]:
    contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections: list[dict[str, Any]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area or area > max_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 2 or h <= 2:
            continue
        foot = (float(x + w / 2), float(y + h))
        if not point_in_polygon(foot, pitch_polygon):
            continue
        detections.append(
            {
                "bbox_xyxy": [int(x), int(y), int(x + w), int(y + h)],
                "footpoint": [foot[0], foot[1]],
                "area_px": area,
                "confidence": 0.35,
                "source": "motion",
            }
        )
    return detections


def draw_overlay(
    frame: np.ndarray,
    pitch_polygon: np.ndarray,
    active_rows: list[dict[str, Any]],
    *,
    label_prefix: str,
    frame_idx: int | None = None,
) -> np.ndarray:
    overlay = frame.copy()
    cv2.polylines(overlay, [pitch_polygon.astype(np.int32)], isClosed=True, color=(0, 255, 255), thickness=2)
    for row in active_rows:
        x1, y1, x2, y2 = [int(v) for v in row["bbox_xyxy"]]
        track_id = row["track_id"]
        fx, fy = row["footpoint"]
        conf = row.get("confidence")
        label = f"{label_prefix}{track_id}"
        if conf is not None:
            label += f" {float(conf):.2f}"
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.circle(overlay, (int(fx), int(fy)), 4, (0, 0, 255), -1)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        top = max(0, y1 - th - 10)
        cv2.rectangle(overlay, (x1, top), (x1 + tw + 6, y1), (0, 255, 0), -1)
        cv2.putText(overlay, label, (x1 + 3, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, cv2.LINE_AA)
    if frame_idx is not None:
        _draw_frame_stamp(overlay, frame_idx)
    return overlay


def _draw_frame_stamp(frame: np.ndarray, frame_idx: int) -> None:
    label = f"FRAME {frame_idx:06d}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.9
    thickness = 2
    padding_x = 14
    padding_y = 10
    (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
    frame_height, frame_width = frame.shape[:2]
    x2 = frame_width - 18
    y2 = frame_height - 18
    x1 = max(0, x2 - text_width - padding_x * 2)
    y1 = max(0, y2 - text_height - baseline - padding_y * 2)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 20, 20), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (245, 245, 245), 1)
    cv2.putText(
        frame,
        label,
        (x1 + padding_x, y2 - padding_y - baseline),
        font,
        font_scale,
        (245, 245, 245),
        thickness,
        cv2.LINE_AA,
    )


def _tracks_with_pitch_positions(raw_tracks: list[dict[str, Any]], H: np.ndarray) -> list[dict[str, Any]]:
    tracks_json: list[dict[str, Any]] = []
    for track in raw_tracks:
        positions = []
        mapped = image_to_pitch_m([(float(p["footpoint"][0]), float(p["footpoint"][1])) for p in track["positions"]], H)
        for p, pitch_m in zip(track["positions"], mapped):
            row = dict(p)
            row["pitch_m"] = [round(float(pitch_m[0]), 3), round(float(pitch_m[1]), 3)]
            positions.append(row)
        if positions:
            tracks_json.append(
                {
                    "track_id": int(track["track_id"]),
                    "start_time_sec": positions[0]["time_sec"],
                    "end_time_sec": positions[-1]["time_sec"],
                    "duration_sec": round(float(positions[-1]["time_sec"] - positions[0]["time_sec"]), 3),
                    "positions_count": len(positions),
                    "positions": positions,
                }
            )
    return sorted(tracks_json, key=lambda item: int(item["track_id"]))


def save_heatmap(match_dir: Path, pitch: PitchConfig, tracks: list[dict[str, Any]]) -> Path:
    width_px, length_px = 360, 720
    heat = np.zeros((length_px, width_px), dtype=np.float32)
    for track in tracks:
        for pos in track["positions"]:
            pitch_m = pos.get("pitch_m")
            if not pitch_m:
                continue
            x_m, y_m = pitch_m
            x = int(np.clip(x_m / pitch.width_m * (width_px - 1), 0, width_px - 1))
            y = int(np.clip(y_m / pitch.length_m * (length_px - 1), 0, length_px - 1))
            heat[y, x] += 1.0
    if heat.max() > 0:
        heat = cv2.GaussianBlur(heat, (0, 0), sigmaX=10, sigmaY=10)
        heat = heat / heat.max() * 255
    colored = cv2.applyColorMap(heat.astype(np.uint8), cv2.COLORMAP_JET)
    cv2.rectangle(colored, (0, 0), (width_px - 1, length_px - 1), (255, 255, 255), 2)
    cv2.line(colored, (0, length_px // 2), (width_px - 1, length_px // 2), (255, 255, 255), 1)
    path = match_dir / "heatmap_all_tracks.png"
    cv2.imwrite(str(path), colored)
    return path


def _write_outputs(match_dir: Path, pitch: PitchConfig, tracks_json: list[dict[str, Any]]) -> dict[str, str]:
    tracks_path = match_dir / "tracks.json"
    tracks_path.write_text(json.dumps(tracks_json, indent=2), encoding="utf-8")
    heatmap_path = save_heatmap(match_dir, pitch, tracks_json)
    return {
        "tracks_json": tracks_path.name,
        "overlay_preview": "overlay_preview.mp4",
        "heatmap_all_tracks": heatmap_path.name,
    }


def _write_failed_report(match_dir: Path, *, adapter: str, error: Exception) -> dict[str, Any]:
    report = {
        "status": "failed",
        "analysis_type": adapter,
        "error": {
            "type": error.__class__.__name__,
            "message": str(error),
        },
        "artifacts": {},
    }
    return finalize_analysis_report(match_dir, report)


def _validate_common_video_params(metadata: dict[str, Any], frame_stride: int) -> tuple[float, int, int]:
    fps = float(metadata.get("fps") or 0)
    width = int(metadata.get("width") or 0)
    height = int(metadata.get("height") or 0)
    if fps <= 0:
        raise ValueError("Video FPS is 0 or unreadable. Try re-encoding the file before analysis.")
    if width <= 0 or height <= 0:
        raise ValueError("Video width/height is unreadable. Try re-encoding the file before analysis.")
    if frame_stride < 1:
        raise ValueError("frame_stride must be >= 1")
    return fps, width, height


def analyze_match_motion(match_dir: Path, video_path: Path, *, max_seconds: float, frame_stride: int) -> dict[str, Any]:
    adapter_name = "motion-baseline"
    try:
        metadata = read_video_metadata(video_path)
        pitch = load_pitch_config(match_dir)
        pitch_polygon = pitch.polygon_np
        H = pitch.homography()

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        fps, width, height = _validate_common_video_params(metadata, frame_stride)
        frame_area = width * height
        min_area = max(40, int(frame_area * 0.00004))
        max_area = max(800, int(frame_area * 0.006))

        pitch_mask = create_pitch_mask((height, width), pitch_polygon)
        bg = cv2.createBackgroundSubtractorMOG2(history=350, varThreshold=24, detectShadows=False)
        tracker = CentroidTracker(max_distance_px=max(35, width * 0.055), max_missing=max(8, int(fps / max(frame_stride, 1))))

        max_frames = int(max_seconds * fps) if max_seconds > 0 else int(metadata["frame_count"])
        overlay_writer = OverlayWriter(match_dir, fps=fps / frame_stride, frame_size=(width, height))

        frame_idx = 0
        processed = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame_idx > max_frames:
                    break
                if frame_idx % frame_stride != 0:
                    frame_idx += 1
                    continue
                masked_frame = cv2.bitwise_and(frame, frame, mask=pitch_mask)
                fg = bg.apply(masked_frame)
                fg = cv2.bitwise_and(fg, fg, mask=pitch_mask)
                fg = cv2.medianBlur(fg, 5)
                kernel = np.ones((5, 5), np.uint8)
                fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
                fg = cv2.morphologyEx(fg, cv2.MORPH_DILATE, kernel, iterations=1)
                detections = detect_motion_people_like_blobs(frame, fg, pitch_polygon, min_area, max_area)
                active_rows = tracker.update(detections, frame_idx, frame_idx / fps)
                overlay_writer.write(draw_overlay(frame, pitch_polygon, active_rows, label_prefix="T", frame_idx=frame_idx))
                processed += 1
                frame_idx += 1
        finally:
            cap.release()

        overlay_writer.close()

        raw_tracks = [{"track_id": t.id, "positions": t.positions} for t in tracker.all_tracks()]
        tracks_json = _tracks_with_pitch_positions(raw_tracks, H)
        artifacts = _write_outputs(match_dir, pitch, tracks_json)
        stabilization = stabilize_match(match_dir, video_path, pitch, tracks_json, metadata)
        artifacts.update(stabilization["artifacts"])

        report = {
            "status": "completed",
            "analysis_type": adapter_name,
            "note": "Fallback detector. Use yolo for real player ID flickering checks.",
            "video": metadata,
            "parameters": {"max_seconds": max_seconds, "frame_stride": frame_stride, "min_area_px": min_area, "max_area_px": max_area},
            "frames_processed": processed,
            "tracks_count": len(tracks_json),
            "stable_players_count": stabilization["stable_players"]["summary"]["stable_players"],
            "artifacts": artifacts,
            "warnings": [] if tracks_json else ["No tracks were detected. Check pitch polygon, video quality, and adapter settings."],
        }
        return finalize_analysis_report(match_dir, report)
    except Exception as exc:
        _write_failed_report(match_dir, adapter=adapter_name, error=exc)
        raise


def _load_yolo_model(model_name: str):
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("YOLO adapter requires ultralytics. Install it with: pip install ultralytics") from exc
    return YOLO(_resolve_yolo_model_name(model_name))


def _resolve_yolo_model_name(model_name: str) -> str:
    raw = str(model_name or "").strip()
    if not raw:
        raise ValueError("YOLO model name/path cannot be empty.")

    direct = Path(raw)
    if direct.is_absolute() or direct.exists():
        return str(direct)

    normalized = raw.replace("\\", "/")
    candidates = [
        ROOT_DIR / raw,
        ROOT_DIR.parent / raw,
    ]
    if normalized.startswith("backend/"):
        candidates.append(ROOT_DIR / normalized[len("backend/") :])
    if normalized.startswith("models/"):
        candidates.append(ROOT_DIR / normalized)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return raw


def _load_stable_players_doc(match_dir: Path) -> dict[str, Any]:
    stable_path = match_dir / "stable_players.json"
    if not stable_path.exists():
        return {"schema_version": "0.1.0", "players": []}
    loaded = json.loads(stable_path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {"schema_version": "0.1.0", "players": []}


def _build_ball_possession_artifacts(
    match_dir: Path,
    video_path: Path,
    pitch: PitchConfig,
    metadata: dict[str, Any],
    ball_tracking: dict[str, Any],
    *,
    stable_players_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stable_doc = stable_players_doc or _load_stable_players_doc(match_dir)
    return build_ball_possession_analysis(
        match_dir,
        video_path,
        pitch,
        metadata,
        ball_tracking.get("ball_tracks") or {},
        stable_doc,
    )


def _resolve_yolo_tracker_config(tracker_name: str) -> str:
    tracker_path = Path(tracker_name)
    if tracker_path.exists():
        return str(tracker_path)
    local_path = Path(__file__).resolve().parents[1] / "tracker_configs" / tracker_name
    if local_path.exists():
        return str(local_path)
    return tracker_name


def analyze_match_yolo(
    match_dir: Path,
    video_path: Path,
    *,
    max_seconds: float,
    frame_stride: int,
    yolo_model: str,
    yolo_conf: float,
    yolo_imgsz: int,
    yolo_tracker: str,
    yolo_device: str | None,
) -> dict[str, Any]:
    adapter_name = "yolo-ultralytics"
    try:
        metadata = read_video_metadata(video_path)
        pitch = load_pitch_config(match_dir)
        pitch_polygon = pitch.polygon_np
        H = pitch.homography()

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        fps, width, height = _validate_common_video_params(metadata, frame_stride)
        max_frames = int(max_seconds * fps) if max_seconds > 0 else int(metadata["frame_count"])
        overlay_writer = OverlayWriter(match_dir, fps=fps / frame_stride, frame_size=(width, height))

        resolved_yolo_model = _resolve_yolo_model_name(yolo_model)
        model = _load_yolo_model(resolved_yolo_model)
        use_centroid_tracker = yolo_tracker == "centroid_high_recall"
        tracker_config = _resolve_yolo_tracker_config(yolo_tracker)
        centroid_tracker = CentroidTracker(max_distance_px=max(45, width * 0.04), max_missing=max(12, int(fps * 0.6 / max(frame_stride, 1))))
        tracks: dict[int, list[dict[str, Any]]] = defaultdict(list)

        frame_idx = 0
        processed = 0
        detections_kept = 0
        detections_rejected_outside_pitch = 0
        yolo_frames_with_results = 0

        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame_idx > max_frames:
                    break
                if frame_idx % frame_stride != 0:
                    frame_idx += 1
                    continue

                active_rows: list[dict[str, Any]] = []
                kwargs: dict[str, Any] = {
                    "source": frame,
                    "classes": [0],
                    "conf": yolo_conf,
                    "iou": 0.45,
                    "imgsz": yolo_imgsz,
                    "verbose": False,
                }
                if yolo_device:
                    kwargs["device"] = yolo_device

                if use_centroid_tracker:
                    results = model.predict(**kwargs)
                    detections: list[dict[str, Any]] = []
                    if results:
                        boxes = results[0].boxes
                        if boxes is not None:
                            yolo_frames_with_results += 1
                            xyxy = boxes.xyxy.cpu().numpy()
                            confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(xyxy))
                            for bbox, conf in zip(xyxy, confs):
                                x1, y1, x2, y2 = [float(v) for v in bbox]
                                foot = [float((x1 + x2) / 2), float(y2)]
                                if not point_in_polygon((foot[0], foot[1]), pitch_polygon):
                                    detections_rejected_outside_pitch += 1
                                    continue
                                detections.append(
                                    {
                                        "bbox_xyxy": [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))],
                                        "footpoint": [round(foot[0], 2), round(foot[1], 2)],
                                        "area_px": round(float((x2 - x1) * (y2 - y1)), 2),
                                        "confidence": round(float(conf), 4),
                                        "source": "yolo-person-centroid",
                                    }
                                )
                    detections_kept += len(detections)
                    active_rows = centroid_tracker.update(detections, frame_idx, frame_idx / fps)
                else:
                    track_kwargs = {
                        **kwargs,
                        "persist": True,
                        "tracker": tracker_config,
                    }
                    results = model.track(**track_kwargs)
                    if results:
                        boxes = results[0].boxes
                        if boxes is not None and boxes.id is not None:
                            yolo_frames_with_results += 1
                            xyxy = boxes.xyxy.cpu().numpy()
                            ids = boxes.id.cpu().numpy().astype(int)
                            confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(ids))
                            for bbox, track_id, conf in zip(xyxy, ids, confs):
                                x1, y1, x2, y2 = [float(v) for v in bbox]
                                foot = [float((x1 + x2) / 2), float(y2)]
                                if not point_in_polygon((foot[0], foot[1]), pitch_polygon):
                                    detections_rejected_outside_pitch += 1
                                    continue
                                row = {
                                    "track_id": int(track_id),
                                    "frame": int(frame_idx),
                                    "time_sec": round(float(frame_idx / fps), 3),
                                    "bbox_xyxy": [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))],
                                    "footpoint": [round(foot[0], 2), round(foot[1], 2)],
                                    "area_px": round(float((x2 - x1) * (y2 - y1)), 2),
                                    "confidence": round(float(conf), 4),
                                    "source": "yolo-person",
                                }
                                tracks[int(track_id)].append(row)
                                active_rows.append(row)
                                detections_kept += 1

                overlay_writer.write(draw_overlay(frame, pitch_polygon, active_rows, label_prefix="P", frame_idx=frame_idx))
                processed += 1
                frame_idx += 1
        finally:
            cap.release()

        overlay_writer.close()

        if use_centroid_tracker:
            raw_tracks = [{"track_id": track.id, "positions": track.positions} for track in centroid_tracker.all_tracks()]
        else:
            raw_tracks = [{"track_id": tid, "positions": positions} for tid, positions in sorted(tracks.items()) if positions]
        tracks_json = _tracks_with_pitch_positions(raw_tracks, H)
        warnings: list[str] = []
        if processed == 0:
            warnings.append("No frames were processed.")
        if detections_kept == 0:
            warnings.append("YOLO did not keep any person detections inside the pitch polygon. Check pitch points, confidence, imgsz, and model.")
        elif len(tracks_json) == 0:
            warnings.append("Detections were found, but no track positions were exported.")

        artifacts = _write_outputs(match_dir, pitch, tracks_json)
        stabilization = stabilize_match(match_dir, video_path, pitch, tracks_json, metadata)
        artifacts.update(stabilization["artifacts"])
        ball_tracking: dict[str, Any] | None = None
        possession: dict[str, Any] | None = None
        try:
            ball_tracking = detect_ball_yolo_coco(
                match_dir,
                video_path,
                pitch,
                metadata,
                model=model,
                max_seconds=max_seconds,
                frame_stride=frame_stride,
                yolo_imgsz=int(yolo_imgsz),
                yolo_device=yolo_device,
                ball_conf=min(float(yolo_conf), DEFAULT_BALL_CONF),
            )
            artifacts.update(ball_tracking["artifacts"])
        except Exception as exc:
            warnings.append(f"Experimental ball detection failed: {exc}")
        if ball_tracking is not None:
            try:
                possession = _build_ball_possession_artifacts(
                    match_dir,
                    video_path,
                    pitch,
                    metadata,
                    ball_tracking,
                    stable_players_doc=stabilization["stable_players"],
                )
                artifacts.update(possession["artifacts"])
                warnings.extend(possession["possession_report"].get("warnings") or [])
            except Exception as exc:
                warnings.append(f"Experimental possession candidate layer failed: {exc}")

        report = {
            "status": "completed",
            "analysis_type": adapter_name,
            "note": "Overlay labels are raw tracker IDs from the selected tracking backend. Stable A##/B## IDs are generated in stable_overlay_preview.mp4.",
            "video": metadata,
            "parameters": {
                "max_seconds": max_seconds,
                "frame_stride": frame_stride,
                "yolo_model": yolo_model,
                "yolo_model_resolved": resolved_yolo_model,
                "yolo_conf": yolo_conf,
                "yolo_iou": 0.45,
                "yolo_imgsz": yolo_imgsz,
                "yolo_tracker": yolo_tracker,
                "yolo_tracker_resolved": tracker_config if not use_centroid_tracker else "internal_centroid_tracker",
                "yolo_device": yolo_device or "auto",
                "classes": ["person"],
                "pitch_mask_before_yolo": False,
                "pitch_filter": "footpoint_in_pitch_polygon",
                "tracking_backend": "centroid" if use_centroid_tracker else "ultralytics",
            },
            "frames_processed": processed,
            "yolo_frames_with_results": yolo_frames_with_results,
            "detections_kept": detections_kept,
            "detections_rejected_outside_pitch": detections_rejected_outside_pitch,
            "tracks_count": len(tracks_json),
            "stable_players_count": stabilization["stable_players"]["summary"]["stable_players"],
            "ball_tracking_summary": (ball_tracking or {}).get("ball_tracking_report", {}).get("summary"),
            "ball_quality_summary": (ball_tracking or {}).get("ball_quality_report", {}).get("summary"),
            "possession_summary": (possession or {}).get("possession_report", {}).get("summary"),
            "warnings": warnings,
            "artifacts": artifacts,
        }
        return finalize_analysis_report(match_dir, report)
    except Exception as exc:
        _write_failed_report(match_dir, adapter=adapter_name, error=exc)
        raise


def analyze_match_ball_yolo(
    match_dir: Path,
    video_path: Path,
    *,
    max_seconds: float,
    frame_stride: int,
    yolo_model: str,
    yolo_conf: float,
    yolo_imgsz: int,
    yolo_device: str | None,
) -> dict[str, Any]:
    adapter_name = "ball-yolo"
    try:
        metadata = read_video_metadata(video_path)
        pitch = load_pitch_config(match_dir)
        resolved_yolo_model = _resolve_yolo_model_name(yolo_model)
        model = _load_yolo_model(resolved_yolo_model)
        ball_tracking = detect_ball_yolo_coco(
            match_dir,
            video_path,
            pitch,
            metadata,
            model=model,
            max_seconds=max_seconds,
            frame_stride=max(1, frame_stride),
            yolo_imgsz=int(yolo_imgsz),
            yolo_device=yolo_device,
            ball_conf=min(float(yolo_conf), DEFAULT_BALL_CONF),
        )
        possession = _build_ball_possession_artifacts(
            match_dir,
            video_path,
            pitch,
            metadata,
            ball_tracking,
        )
        ball_parameters = ball_tracking["ball_tracking_report"].get("parameters") or {}
        run_id = new_analysis_run_id(adapter_name)
        generated_at = now_iso()
        run_dir = match_dir / "analysis_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": "0.1.0",
            "status": "completed",
            "analysis_type": adapter_name,
            "experimental": True,
            "run_id": run_id,
            "generated_at": generated_at,
            "run_directory": f"analysis_runs/{run_id}",
            "run_manifest": f"analysis_runs/{run_id}/run_metadata.json",
            "video": metadata,
            "parameters": {
                "max_seconds": max_seconds,
                "frame_stride": max(1, frame_stride),
                "yolo_model": yolo_model,
                "yolo_model_resolved": resolved_yolo_model,
                "yolo_conf": yolo_conf,
                "yolo_imgsz": yolo_imgsz,
                "yolo_device": yolo_device or "auto",
                "detector": ball_parameters.get("detector"),
                "classes": ball_parameters.get("ball_class_names") or ["ball"],
                "class_ids": ball_parameters.get("ball_class_ids") or [],
                "class_resolution": ball_parameters.get("ball_class_resolution"),
                "pitch_filter": "center_in_pitch_polygon",
            },
            "frames_processed": ball_tracking["ball_tracking_report"]["summary"]["processed_frames"],
            "ball_tracking_summary": ball_tracking["ball_tracking_report"]["summary"],
            "ball_quality_summary": ball_tracking["ball_quality_report"]["summary"],
            "ball_quality_recommendation": ball_tracking["ball_quality_report"]["recommendation"],
            "possession_summary": possession["possession_report"]["summary"],
            "warnings": [
                *(ball_tracking["ball_tracking_report"].get("warnings") or []),
                *(possession["possession_report"].get("warnings") or []),
            ],
            "artifacts": {**ball_tracking["artifacts"], **possession["artifacts"]},
        }
        report["run_artifacts"] = {
            key: f"analysis_runs/{run_id}/{Path(filename).name}"
            for key, filename in report["artifacts"].items()
        }
        (match_dir / "ball_analysis_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (run_dir / "ball_analysis_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (run_dir / "run_metadata.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1.0",
                    "run_id": run_id,
                    "generated_at": generated_at,
                    "status": report["status"],
                    "analysis_type": adapter_name,
                    "parameters": report["parameters"],
                    "artifacts": report["artifacts"],
                    "run_artifacts": report["run_artifacts"],
                    "report": "ball_analysis_report.json",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        for filename in ["pitch_config.json", "ball_analysis_report.json", *BALL_ARTIFACT_FILENAMES.values()]:
            source = match_dir / filename
            if source.exists() and source.is_file():
                shutil.copy2(source, run_dir / filename)
        _merge_ball_analysis_into_main_report(match_dir, report)
        return report
    except Exception as exc:
        failed = {
            "schema_version": "0.1.0",
            "status": "failed",
            "analysis_type": adapter_name,
            "generated_at": now_iso(),
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
            "artifacts": {},
        }
        (match_dir / "ball_analysis_report.json").write_text(json.dumps(failed, indent=2), encoding="utf-8")
        raise


def _merge_ball_analysis_into_main_report(match_dir: Path, ball_report: dict[str, Any]) -> None:
    report_path = match_dir / "analysis_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {
            "status": "completed",
            "analysis_type": "ball-yolo-only",
            "generated_at": ball_report.get("generated_at") or now_iso(),
            "artifacts": {},
        }
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), dict) else {}
    artifacts.update(ball_report.get("artifacts") or {})
    artifacts["ball_analysis_report"] = "ball_analysis_report.json"
    report["artifacts"] = artifacts
    report["ball_tracking_summary"] = ball_report.get("ball_tracking_summary")
    report["ball_quality_summary"] = ball_report.get("ball_quality_summary")
    report["ball_quality_recommendation"] = ball_report.get("ball_quality_recommendation")
    report["latest_ball_analysis_run"] = {
        "run_id": ball_report.get("run_id"),
        "generated_at": ball_report.get("generated_at"),
        "run_directory": ball_report.get("run_directory"),
        "run_manifest": ball_report.get("run_manifest"),
        "parameters": ball_report.get("parameters") or {},
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def analyze_match(
    match_dir: Path,
    video_path: Path,
    *,
    adapter: AnalysisAdapter,
    max_seconds: float,
    frame_stride: int,
    yolo_model: str,
    yolo_conf: float,
    yolo_imgsz: int,
    yolo_tracker: str,
    yolo_device: str | None,
) -> dict[str, Any]:
    if adapter == "motion":
        return analyze_match_motion(match_dir, video_path, max_seconds=max_seconds, frame_stride=frame_stride)
    if adapter == "yolo":
        return analyze_match_yolo(
            match_dir,
            video_path,
            max_seconds=max_seconds,
            frame_stride=frame_stride,
            yolo_model=yolo_model,
            yolo_conf=yolo_conf,
            yolo_imgsz=yolo_imgsz,
            yolo_tracker=yolo_tracker,
            yolo_device=yolo_device,
        )
    raise ValueError(f"Unknown analysis adapter: {adapter}")
