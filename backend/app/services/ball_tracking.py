from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BALL_COCO_CLASS_ID = 32
BALL_COCO_CLASS_NAME = "sports ball"
BALL_SOURCE = "ultralytics_yolo_ball_v1"
BALL_COCO_SOURCE = "ultralytics_yolo_coco_sports_ball_v1"
BALL_CUSTOM_SOURCE = "ultralytics_yolo_custom_ball_v1"

DEFAULT_BALL_CONF = 0.03
DEFAULT_BALL_IOU = 0.45
DEFAULT_MAX_INTERPOLATION_GAP_SEC = 0.5
DEFAULT_MAX_LINK_SPEED_MPS = 35.0
DEFAULT_MAX_INTERPOLATION_SPEED_MPS = 35.0
DEFAULT_MIN_START_CONF = 0.02
DEFAULT_MIN_BALL_AREA_PX = 4.0
DEFAULT_MAX_BALL_AREA_RATIO = 0.003


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_ball_yolo_coco(
    match_dir: Path,
    video_path: Path,
    pitch: Any,
    video_metadata: dict[str, Any],
    *,
    model: Any,
    max_seconds: float,
    frame_stride: int,
    yolo_imgsz: int,
    yolo_device: str | None,
    ball_conf: float = DEFAULT_BALL_CONF,
    max_interpolation_gap_sec: float = DEFAULT_MAX_INTERPOLATION_GAP_SEC,
) -> dict[str, Any]:
    import cv2
    import numpy as np

    fps = float(video_metadata.get("fps") or 0.0)
    width = int(video_metadata.get("width") or 0)
    height = int(video_metadata.get("height") or 0)
    frame_count = int(video_metadata.get("frame_count") or 0)
    if fps <= 0 or width <= 0 or height <= 0:
        raise ValueError("Video metadata is missing fps/width/height for ball tracking.")

    parameters = {
        "ball_conf": float(ball_conf),
        "ball_iou": DEFAULT_BALL_IOU,
        "imgsz": int(yolo_imgsz),
        "frame_stride": int(frame_stride),
        "max_seconds": float(max_seconds),
        "max_interpolation_gap_sec": float(max_interpolation_gap_sec),
        "max_link_speed_mps": DEFAULT_MAX_LINK_SPEED_MPS,
        "max_interpolation_speed_mps": DEFAULT_MAX_INTERPOLATION_SPEED_MPS,
        "min_start_conf": DEFAULT_MIN_START_CONF,
        "pitch_filter": "center_in_pitch_polygon",
        "size_filter": {
            "min_area_px": DEFAULT_MIN_BALL_AREA_PX,
            "max_area_ratio": DEFAULT_MAX_BALL_AREA_RATIO,
        },
        "device": yolo_device or "auto",
    }
    warnings: list[str] = []
    class_config = _resolve_ball_model_classes(model)
    parameters.update(
        {
            "detector": class_config["source"],
            "ball_class_ids": class_config["class_ids"],
            "ball_class_names": class_config["class_names"],
            "ball_class_resolution": class_config["resolution"],
            "model_classes": class_config["model_classes"],
        }
    )
    if not class_config["class_ids"]:
        warnings.append(
            "Selected YOLO model does not expose a class named ball/sports ball and is not a single-class model, "
            "so ball detection was skipped. Use a COCO model or a custom one-class ball detector."
        )
        processed_frames = _processed_frame_indices(frame_count, fps, max_seconds, frame_stride)
        candidates_doc = build_ball_candidates_document(
            [],
            processed_frames=processed_frames,
            rejected_summary={},
            parameters=parameters,
        )
        tracks_doc = build_ball_tracks_document(
            [],
            processed_frames=processed_frames,
            fps=fps,
            parameters=parameters,
        )
        report = build_ball_tracking_report(
            tracks_doc,
            candidates_doc,
            parameters=parameters,
            warnings=warnings,
        )
        quality_report = build_ball_quality_report(tracks_doc, candidates_doc, report)
        _write_ball_artifacts(match_dir, candidates_doc, tracks_doc, report, quality_report)
        write_ball_overlay(
            video_path,
            match_dir,
            tracks_doc,
            candidates_doc,
            pitch.polygon_np,
            fps=fps,
            frame_size=(width, height),
        )
        return _ball_result(candidates_doc, tracks_doc, report, quality_report)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video for ball tracking: {video_path}")

    H = pitch.homography()
    pitch_polygon = pitch.polygon_np
    max_frame = int(max_seconds * fps) if max_seconds > 0 else frame_count - 1
    max_frame = min(max_frame, frame_count - 1) if frame_count > 0 else max_frame

    processed_frames: list[int] = []
    frames: list[dict[str, Any]] = []
    rejected_summary: Counter[str] = Counter()
    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame_idx > max_frame:
                break
            if frame_idx % max(1, frame_stride) != 0:
                frame_idx += 1
                continue

            processed_frames.append(frame_idx)
            kwargs: dict[str, Any] = {
                "source": frame,
                "classes": class_config["class_ids"],
                "conf": float(ball_conf),
                "iou": DEFAULT_BALL_IOU,
                "imgsz": int(yolo_imgsz),
                "verbose": False,
            }
            if yolo_device:
                kwargs["device"] = yolo_device

            results = model.predict(**kwargs)
            raw_predictions = 0
            candidates: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            if results:
                boxes = results[0].boxes
                if boxes is not None:
                    xyxy = boxes.xyxy.cpu().numpy()
                    confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(xyxy))
                    classes = boxes.cls.cpu().numpy() if boxes.cls is not None else None
                    raw_predictions = len(xyxy)
                    candidates, rejected = extract_ball_candidates(
                        xyxy,
                        confs,
                        class_ids=classes,
                        class_names=class_config["class_name_by_id"],
                        frame_idx=frame_idx,
                        fps=fps,
                        pitch_polygon=pitch_polygon,
                        homography=H,
                        frame_size=(width, height),
                    )
            for item in rejected:
                rejected_summary[str(item.get("reason") or "unknown")] += 1
            frames.append(
                {
                    "frame": frame_idx,
                    "time_sec": round(frame_idx / fps, 3),
                    "raw_predictions": raw_predictions,
                    "candidates": candidates,
                    "rejected_candidates": rejected,
                    "rejected_counts": dict(Counter(str(item.get("reason") or "unknown") for item in rejected)),
                }
            )
            frame_idx += 1
    finally:
        cap.release()

    candidates_doc = build_ball_candidates_document(
        frames,
        processed_frames=processed_frames,
        rejected_summary=dict(rejected_summary),
        parameters=parameters,
    )
    tracks_doc = build_ball_tracks_document(
        frames,
        processed_frames=processed_frames,
        fps=fps,
        parameters=parameters,
    )
    report = build_ball_tracking_report(
        tracks_doc,
        candidates_doc,
        parameters=parameters,
        warnings=warnings,
    )
    quality_report = build_ball_quality_report(tracks_doc, candidates_doc, report)
    _write_ball_artifacts(match_dir, candidates_doc, tracks_doc, report, quality_report)
    write_ball_overlay(
        video_path,
        match_dir,
        tracks_doc,
        candidates_doc,
        pitch_polygon,
        fps=fps,
        frame_size=(width, height),
    )
    return _ball_result(candidates_doc, tracks_doc, report, quality_report)


def collect_ball_candidates_range(
    video_path: Path,
    pitch: Any,
    video_metadata: dict[str, Any],
    *,
    model: Any,
    start_time_sec: float,
    end_time_sec: float,
    frame_stride: int,
    yolo_imgsz: int,
    yolo_device: str | None,
    ball_conf: float = DEFAULT_BALL_CONF,
    max_interpolation_gap_sec: float = DEFAULT_MAX_INTERPOLATION_GAP_SEC,
) -> dict[str, Any]:
    import cv2
    import numpy as np

    fps = float(video_metadata.get("fps") or 0.0)
    width = int(video_metadata.get("width") or 0)
    height = int(video_metadata.get("height") or 0)
    frame_count = int(video_metadata.get("frame_count") or 0)
    if fps <= 0 or width <= 0 or height <= 0:
        raise ValueError("Video metadata is missing fps/width/height for ball tracking.")

    class_config = _resolve_ball_model_classes(model)
    parameters = ball_tracking_parameters(
        class_config=class_config,
        ball_conf=ball_conf,
        yolo_imgsz=yolo_imgsz,
        frame_stride=frame_stride,
        max_seconds=0.0,
        yolo_device=yolo_device,
        max_interpolation_gap_sec=max_interpolation_gap_sec,
    )
    start_frame = max(0, int(round(max(0.0, start_time_sec) * fps)))
    end_frame = max(start_frame, int(round(max(start_time_sec, end_time_sec) * fps)))
    if frame_count > 0:
        end_frame = min(end_frame, frame_count - 1)
    stride = max(1, int(frame_stride))
    processed_frames = [frame for frame in range(start_frame, end_frame + 1) if frame % stride == 0]
    warnings: list[str] = []
    if not class_config["class_ids"]:
        warnings.append(
            "Selected ball YOLO model does not expose a ball/sports ball class and is not a single-class model."
        )
        return {
            "frames": [],
            "processed_frames": processed_frames,
            "rejected_summary": {},
            "parameters": parameters,
            "warnings": warnings,
            "metrics": {
                "start_time_sec": round(float(start_time_sec), 3),
                "end_time_sec": round(float(end_time_sec), 3),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "processed_frames": len(processed_frames),
                "frames_with_candidates": 0,
                "candidate_count": 0,
                "rejected_candidate_count": 0,
            },
        }

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video for ball tracking: {video_path}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    H = pitch.homography()
    pitch_polygon = pitch.polygon_np
    frames: list[dict[str, Any]] = []
    rejected_summary: Counter[str] = Counter()
    frame_idx = start_frame
    try:
        while frame_idx <= end_frame:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % stride != 0:
                frame_idx += 1
                continue

            kwargs: dict[str, Any] = {
                "source": frame,
                "classes": class_config["class_ids"],
                "conf": float(ball_conf),
                "iou": DEFAULT_BALL_IOU,
                "imgsz": int(yolo_imgsz),
                "verbose": False,
            }
            if yolo_device:
                kwargs["device"] = yolo_device

            raw_predictions = 0
            candidates: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            results = model.predict(**kwargs)
            if results:
                boxes = results[0].boxes
                if boxes is not None:
                    xyxy = boxes.xyxy.cpu().numpy()
                    confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(xyxy))
                    classes = boxes.cls.cpu().numpy() if boxes.cls is not None else None
                    raw_predictions = len(xyxy)
                    candidates, rejected = extract_ball_candidates(
                        xyxy,
                        confs,
                        class_ids=classes,
                        class_names=class_config["class_name_by_id"],
                        frame_idx=frame_idx,
                        fps=fps,
                        pitch_polygon=pitch_polygon,
                        homography=H,
                        frame_size=(width, height),
                    )
            for item in rejected:
                rejected_summary[str(item.get("reason") or "unknown")] += 1
            frames.append(
                {
                    "frame": frame_idx,
                    "time_sec": round(frame_idx / fps, 3),
                    "raw_predictions": raw_predictions,
                    "candidates": candidates,
                    "rejected_candidates": rejected,
                    "rejected_counts": dict(Counter(str(item.get("reason") or "unknown") for item in rejected)),
                }
            )
            frame_idx += 1
    finally:
        cap.release()

    return {
        "frames": frames,
        "processed_frames": processed_frames,
        "rejected_summary": dict(rejected_summary),
        "parameters": parameters,
        "warnings": warnings,
        "metrics": {
            "start_time_sec": round(float(start_time_sec), 3),
            "end_time_sec": round(float(end_time_sec), 3),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "processed_frames": len(processed_frames),
            "frames_with_candidates": sum(1 for frame in frames if frame.get("candidates")),
            "candidate_count": sum(len(frame.get("candidates") or []) for frame in frames),
            "rejected_candidate_count": sum(len(frame.get("rejected_candidates") or []) for frame in frames),
            "rejected_summary": dict(rejected_summary),
        },
    }


def ball_tracking_parameters(
    *,
    class_config: dict[str, Any],
    ball_conf: float,
    yolo_imgsz: int,
    frame_stride: int,
    max_seconds: float,
    yolo_device: str | None,
    max_interpolation_gap_sec: float,
) -> dict[str, Any]:
    parameters = {
        "ball_conf": float(ball_conf),
        "ball_iou": DEFAULT_BALL_IOU,
        "imgsz": int(yolo_imgsz),
        "frame_stride": int(frame_stride),
        "max_seconds": float(max_seconds),
        "max_interpolation_gap_sec": float(max_interpolation_gap_sec),
        "max_link_speed_mps": DEFAULT_MAX_LINK_SPEED_MPS,
        "max_interpolation_speed_mps": DEFAULT_MAX_INTERPOLATION_SPEED_MPS,
        "min_start_conf": DEFAULT_MIN_START_CONF,
        "pitch_filter": "center_in_pitch_polygon",
        "size_filter": {
            "min_area_px": DEFAULT_MIN_BALL_AREA_PX,
            "max_area_ratio": DEFAULT_MAX_BALL_AREA_RATIO,
        },
        "device": yolo_device or "auto",
    }
    parameters.update(
        {
            "detector": class_config["source"],
            "ball_class_ids": class_config["class_ids"],
            "ball_class_names": class_config["class_names"],
            "ball_class_resolution": class_config["resolution"],
            "model_classes": class_config["model_classes"],
        }
    )
    return parameters


def extract_ball_candidates(
    xyxy: Any,
    confs: Any,
    *,
    class_ids: Any | None = None,
    class_names: dict[int, str] | None = None,
    frame_idx: int,
    fps: float,
    pitch_polygon: Any,
    homography: Any,
    frame_size: tuple[int, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    width, height = frame_size
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, (bbox, conf) in enumerate(zip(xyxy, confs)):
        detected_class_id = _class_id_at(class_ids, index)
        detected_class_name = (class_names or {}).get(detected_class_id) or _default_ball_class_name(detected_class_id)
        x1, y1, x2, y2 = [float(value) for value in bbox]
        box_width = max(0.0, x2 - x1)
        box_height = max(0.0, y2 - y1)
        area = box_width * box_height
        center = [float((x1 + x2) / 2.0), float((y1 + y2) / 2.0)]
        base = {
            "candidate_id": f"ball-f{frame_idx:06d}-c{index:02d}",
            "frame": int(frame_idx),
            "time_sec": round(float(frame_idx / max(fps, 0.001)), 3),
            "bbox_xyxy": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
            "position_px": [round(center[0], 2), round(center[1], 2)],
            "confidence": round(float(conf), 4),
            "class_id": detected_class_id,
            "class_name": detected_class_name,
            "width_px": round(box_width, 2),
            "height_px": round(box_height, 2),
            "area_px": round(area, 2),
        }
        reject_reason = _ball_candidate_reject_reason(
            base,
            pitch_polygon=pitch_polygon,
            frame_size=frame_size,
        )
        if reject_reason:
            rejected.append({**base, "reason": reject_reason})
            continue
        pitch_m = _image_to_pitch_m(center[0], center[1], homography)
        candidates.append(
            {
                **base,
                "position_m": [round(float(pitch_m[0]), 3), round(float(pitch_m[1]), 3)],
                "source": "detected",
            }
        )
    return candidates, rejected


def build_ball_candidates_document(
    frames: list[dict[str, Any]],
    *,
    processed_frames: list[int],
    rejected_summary: dict[str, int],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    frames_with_candidates = sum(1 for frame in frames if frame.get("candidates"))
    total_candidates = sum(len(frame.get("candidates") or []) for frame in frames)
    total_rejected = sum(len(frame.get("rejected_candidates") or []) for frame in frames)
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": parameters.get("detector") or BALL_SOURCE,
        "parameters": parameters,
        "summary": {
            "processed_frames": len(processed_frames),
            "frames_with_candidates": frames_with_candidates,
            "candidate_count": total_candidates,
            "rejected_candidate_count": total_rejected,
            "rejected_summary": rejected_summary,
        },
        "frames": frames,
    }


def build_ball_tracks_document(
    frames: list[dict[str, Any]],
    *,
    processed_frames: list[int],
    fps: float,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    selected = select_ball_detections(
        frames,
        fps=fps,
        max_link_speed_mps=float(parameters.get("max_link_speed_mps") or DEFAULT_MAX_LINK_SPEED_MPS),
        min_start_conf=float(parameters.get("min_start_conf") or DEFAULT_MIN_START_CONF),
    )
    positions, interpolation_gaps = build_ball_positions(
        selected,
        processed_frames=processed_frames,
        fps=fps,
        max_interpolation_gap_sec=float(parameters.get("max_interpolation_gap_sec") or DEFAULT_MAX_INTERPOLATION_GAP_SEC),
        max_interpolation_speed_mps=float(parameters.get("max_interpolation_speed_mps") or DEFAULT_MAX_INTERPOLATION_SPEED_MPS),
    )
    detected_frames = sum(1 for item in positions if item["source"] == "detected")
    interpolated_frames = sum(1 for item in positions if item["source"] == "interpolated")
    unknown_frames = sum(1 for item in positions if item["source"] == "unknown")
    total_frames = len(positions)
    detected_confidences = [float(item["confidence"]) for item in positions if item["source"] == "detected"]
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": parameters.get("detector") or BALL_SOURCE,
        "track_id": "ball-main",
        "status_semantics": "detected_interpolated_unknown",
        "units": {
            "position_px": "image_pixels",
            "position_m": "pitch_meters",
            "speed": "meters_per_second",
        },
        "parameters": parameters,
        "summary": {
            "processed_frames": total_frames,
            "detected_frames": detected_frames,
            "interpolated_frames": interpolated_frames,
            "unknown_frames": unknown_frames,
            "detected_coverage": _ratio(detected_frames, total_frames),
            "interpolated_coverage": _ratio(interpolated_frames, total_frames),
            "known_coverage": _ratio(detected_frames + interpolated_frames, total_frames),
            "mean_detected_confidence": round(sum(detected_confidences) / len(detected_confidences), 4) if detected_confidences else None,
            "interpolation_gaps": len(interpolation_gaps),
        },
        "positions": positions,
        "interpolation_gaps": interpolation_gaps,
    }


def select_ball_detections(
    frames: list[dict[str, Any]],
    *,
    fps: float,
    max_link_speed_mps: float,
    min_start_conf: float,
) -> dict[int, dict[str, Any]]:
    selected: dict[int, dict[str, Any]] = {}
    last: dict[str, Any] | None = None
    for frame in frames:
        frame_idx = int(frame.get("frame") or 0)
        candidates = sorted(frame.get("candidates") or [], key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
        if not candidates:
            continue

        if last is None:
            best = candidates[0]
            if float(best.get("confidence") or 0.0) >= min_start_conf:
                selected[frame_idx] = best
                last = best
            continue

        scored: list[tuple[float, dict[str, Any], float]] = []
        previous_frame = int(last.get("frame") or 0)
        dt = max((frame_idx - previous_frame) / max(fps, 0.001), 1.0 / max(fps, 0.001))
        for candidate in candidates:
            distance = _distance_m(last.get("position_m"), candidate.get("position_m"))
            if distance is None:
                continue
            speed = distance / dt
            if speed <= max_link_speed_mps:
                confidence = float(candidate.get("confidence") or 0.0)
                cost = speed / max(max_link_speed_mps, 0.001) + (1.0 - confidence) * 0.3
                scored.append((cost, candidate, speed))
        if scored:
            scored.sort(key=lambda item: item[0])
            best = scored[0][1]
            selected[frame_idx] = best
            last = best
            continue

        if dt > 1.0 and float(candidates[0].get("confidence") or 0.0) >= min_start_conf:
            best = {**candidates[0], "segment_start_reason": "after_impossible_or_long_gap"}
            selected[frame_idx] = best
            last = best
        elif dt > 1.0:
            last = None
    return selected


def build_ball_positions(
    selected_by_frame: dict[int, dict[str, Any]],
    *,
    processed_frames: list[int],
    fps: float,
    max_interpolation_gap_sec: float,
    max_interpolation_speed_mps: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    processed_set = set(processed_frames)
    positions_by_frame: dict[int, dict[str, Any]] = {}
    for frame_idx, candidate in selected_by_frame.items():
        positions_by_frame[int(frame_idx)] = _candidate_to_position(candidate, source="detected")

    interpolation_gaps: list[dict[str, Any]] = []
    selected_frames = sorted(frame for frame in selected_by_frame if frame in processed_set)
    for start_frame, end_frame in zip(selected_frames, selected_frames[1:]):
        gap_frames = [frame for frame in processed_frames if start_frame < frame < end_frame]
        if not gap_frames:
            continue
        start = selected_by_frame[start_frame]
        end = selected_by_frame[end_frame]
        dt = max((end_frame - start_frame) / max(fps, 0.001), 1.0 / max(fps, 0.001))
        distance = _distance_m(start.get("position_m"), end.get("position_m"))
        speed = distance / dt if distance is not None else None
        if dt > max_interpolation_gap_sec or speed is None or speed > max_interpolation_speed_mps:
            continue
        gap_rows = []
        for frame_idx in gap_frames:
            alpha = (frame_idx - start_frame) / max(end_frame - start_frame, 1)
            position_px = _lerp_pair(start.get("position_px"), end.get("position_px"), alpha)
            position_m = _lerp_pair(start.get("position_m"), end.get("position_m"), alpha)
            confidence = min(float(start.get("confidence") or 0.0), float(end.get("confidence") or 0.0)) * 0.55
            row = {
                "frame": int(frame_idx),
                "time_sec": round(frame_idx / max(fps, 0.001), 3),
                "position_px": [round(position_px[0], 2), round(position_px[1], 2)] if position_px else None,
                "position_m": [round(position_m[0], 3), round(position_m[1], 3)] if position_m else None,
                "bbox_xyxy": None,
                "source": "interpolated",
                "confidence": round(confidence, 4),
                "interpolated_from": [int(start_frame), int(end_frame)],
            }
            positions_by_frame[frame_idx] = row
            gap_rows.append(frame_idx)
        interpolation_gaps.append(
            {
                "start_frame": int(start_frame),
                "end_frame": int(end_frame),
                "interpolated_frames": gap_rows,
                "duration_sec": round(dt, 3),
                "required_speed_mps": round(speed, 3),
            }
        )

    positions = []
    for frame_idx in processed_frames:
        if frame_idx in positions_by_frame:
            positions.append(positions_by_frame[frame_idx])
            continue
        positions.append(
            {
                "frame": int(frame_idx),
                "time_sec": round(frame_idx / max(fps, 0.001), 3),
                "position_px": None,
                "position_m": None,
                "bbox_xyxy": None,
                "source": "unknown",
                "confidence": 0.0,
            }
        )
    return positions, interpolation_gaps


def build_ball_tracking_report(
    tracks_doc: dict[str, Any],
    candidates_doc: dict[str, Any],
    *,
    parameters: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    summary = dict(tracks_doc.get("summary") or {})
    candidates_summary = candidates_doc.get("summary") or {}
    summary.update(
        {
            "candidate_count": int(candidates_summary.get("candidate_count") or 0),
            "frames_with_candidates": int(candidates_summary.get("frames_with_candidates") or 0),
            "rejected_candidate_count": int(candidates_summary.get("rejected_candidate_count") or 0),
            "rejected_summary": candidates_summary.get("rejected_summary") or {},
        }
    )
    final_warnings = list(warnings or [])
    if summary.get("detected_frames", 0) == 0:
        final_warnings.append(
            "Selected ball detector did not produce accepted ball detections. "
            "This clip likely needs higher-resolution input, larger imgsz, a lower threshold, or more custom ball labels."
        )
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": parameters.get("detector") or BALL_SOURCE,
        "status": "completed",
        "experimental": True,
        "summary": summary,
        "parameters": parameters,
        "warnings": final_warnings,
    }


def build_ball_quality_report(
    tracks_doc: dict[str, Any],
    candidates_doc: dict[str, Any],
    tracking_report: dict[str, Any],
) -> dict[str, Any]:
    track_summary = tracks_doc.get("summary") if isinstance(tracks_doc.get("summary"), dict) else {}
    report_summary = tracking_report.get("summary") if isinstance(tracking_report.get("summary"), dict) else {}
    candidates_summary = candidates_doc.get("summary") if isinstance(candidates_doc.get("summary"), dict) else {}
    candidate_frames = candidates_doc.get("frames") if isinstance(candidates_doc.get("frames"), list) else []
    processed_frames = int(track_summary.get("processed_frames") or 0)
    positions = tracks_doc.get("positions") if isinstance(tracks_doc.get("positions"), list) else []
    unknown_ranges = _source_ranges(positions, "unknown")
    detected_ranges = _source_ranges(positions, "detected")
    candidate_count = int(report_summary.get("candidate_count") or candidates_summary.get("candidate_count") or 0)
    rejected_count = int(
        report_summary.get("rejected_candidate_count") or candidates_summary.get("rejected_candidate_count") or 0
    )
    frames_with_candidates = int(
        report_summary.get("frames_with_candidates") or candidates_summary.get("frames_with_candidates") or 0
    )
    multi_candidate_frames = sum(1 for frame in candidate_frames if len(frame.get("candidates") or []) > 1)
    known_coverage = float(track_summary.get("known_coverage") or 0.0)
    detected_coverage = float(track_summary.get("detected_coverage") or 0.0)
    mean_confidence = track_summary.get("mean_detected_confidence")
    mean_confidence_value = float(mean_confidence) if mean_confidence is not None else 0.0
    rejected_rate = rejected_count / max(candidate_count + rejected_count, 1)
    multi_candidate_ratio = multi_candidate_frames / max(processed_frames, 1)
    candidate_frame_ratio = frames_with_candidates / max(processed_frames, 1)
    longest_unknown = max((int(item["frames"]) for item in unknown_ranges), default=0)
    recommendation = _ball_quality_recommendation(
        processed_frames=processed_frames,
        known_coverage=known_coverage,
        detected_coverage=detected_coverage,
        mean_confidence=mean_confidence_value,
        multi_candidate_ratio=multi_candidate_ratio,
        longest_unknown_frames=longest_unknown,
    )
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": (tracking_report.get("parameters") or {}).get("detector") or BALL_SOURCE,
        "experimental": True,
        "summary": {
            "processed_frames": processed_frames,
            "candidate_count": candidate_count,
            "frames_with_candidates": frames_with_candidates,
            "candidate_frame_ratio": round(candidate_frame_ratio, 4),
            "multi_candidate_frames": multi_candidate_frames,
            "multi_candidate_ratio": round(multi_candidate_ratio, 4),
            "rejected_candidate_count": rejected_count,
            "rejected_rate": round(rejected_rate, 4),
            "known_coverage": known_coverage,
            "detected_coverage": detected_coverage,
            "interpolated_coverage": float(track_summary.get("interpolated_coverage") or 0.0),
            "unknown_coverage": round(1.0 - known_coverage, 4) if processed_frames else 0.0,
            "mean_detected_confidence": mean_confidence,
            "longest_unknown_streak_frames": longest_unknown,
            "longest_unknown_streak_sec": round(longest_unknown * _frame_interval_sec(tracks_doc), 3),
            "detected_ranges_count": len(detected_ranges),
            "unknown_ranges_count": len(unknown_ranges),
        },
        "recommendation": recommendation,
        "diagnostics": {
            "unknown_ranges": unknown_ranges[:20],
            "detected_ranges": detected_ranges[:20],
            "tracking_warnings": tracking_report.get("warnings") or [],
        },
        "notes": [
            "This report is diagnostic and does not measure true precision without manual labels.",
            "Use the overlay to verify whether accepted candidates are the real ball before building possession/pass logic.",
        ],
    }


def _ball_quality_recommendation(
    *,
    processed_frames: int,
    known_coverage: float,
    detected_coverage: float,
    mean_confidence: float,
    multi_candidate_ratio: float,
    longest_unknown_frames: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    if processed_frames < 50:
        reasons.append("Sample is short; run a longer ball-only test before final model choice.")
    if known_coverage < 0.55:
        reasons.append("Known ball coverage is below 55%, too low for possession or pass analytics.")
    if detected_coverage < 0.45:
        reasons.append("Detected-only coverage is below 45%, so interpolation would hide too many misses.")
    if mean_confidence < 0.2:
        reasons.append("Mean detection confidence is low; the selected detector is not strongly recognizing this ball/view.")
    if multi_candidate_ratio > 0.2:
        reasons.append("Many frames have multiple ball candidates, increasing false-positive risk.")
    if longest_unknown_frames >= 30:
        reasons.append("There are long unknown streaks that would break possession continuity.")

    if known_coverage >= 0.75 and mean_confidence >= 0.25 and multi_candidate_ratio <= 0.1:
        decision = "ball_detector_usable_for_next_experiments"
        custom_dataset_recommended = False
        next_step = "Validate precision manually on full clips, then test simple possession candidates."
    elif known_coverage >= 0.55 and mean_confidence >= 0.18:
        decision = "ball_detector_promising_but_not_enough_for_events"
        custom_dataset_recommended = False
        next_step = "Run higher imgsz/longer clips and inspect false positives before collecting a custom dataset."
    else:
        decision = "custom_dataset_likely_needed"
        custom_dataset_recommended = True
        next_step = "Collect labeled frames from these camera angles and train/fine-tune a dedicated ball detector."

    return {
        "decision": decision,
        "custom_dataset_recommended": custom_dataset_recommended,
        "confidence": "low" if processed_frames < 50 else "medium",
        "reasons": reasons,
        "next_step": next_step,
    }


def write_ball_overlay(
    video_path: Path,
    match_dir: Path,
    tracks_doc: dict[str, Any],
    candidates_doc: dict[str, Any],
    pitch_polygon: Any,
    *,
    fps: float,
    frame_size: tuple[int, int],
    output_name: str = "ball_overlay_preview.mp4",
) -> Path:
    import cv2
    import numpy as np

    position_by_frame = {int(item.get("frame") or 0): item for item in tracks_doc.get("positions", [])}
    candidates_by_frame = {
        int(frame.get("frame") or 0): frame.get("candidates") or []
        for frame in candidates_doc.get("frames", [])
    }
    if not position_by_frame:
        max_frame = int(candidates_doc.get("summary", {}).get("processed_frames") or 0) - 1
    else:
        max_frame = max(position_by_frame)
    frame_stride = max(1, int((tracks_doc.get("parameters") or {}).get("frame_stride") or 1))
    summary = tracks_doc.get("summary") or {}

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video for ball overlay: {video_path}")
    writer = _BallOverlayWriter(match_dir, output_name, fps=max(fps / frame_stride, 1.0), frame_size=frame_size)
    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame_idx > max_frame:
                break
            if frame_idx not in position_by_frame:
                frame_idx += 1
                continue
            overlay = frame.copy()
            cv2.polylines(overlay, [pitch_polygon.astype(np.int32)], isClosed=True, color=(0, 255, 255), thickness=2)
            _draw_ball_candidates(overlay, candidates_by_frame.get(frame_idx, []))
            _draw_ball_position(overlay, position_by_frame[frame_idx])
            _draw_ball_hud(
                overlay,
                frame_idx,
                fps=fps,
                position=position_by_frame[frame_idx],
                candidate_count=len(candidates_by_frame.get(frame_idx, [])),
                summary=summary,
            )
            _draw_frame_stamp(overlay, frame_idx)
            writer.write(overlay)
            frame_idx += 1
    finally:
        cap.release()
    return writer.close()


def _write_ball_artifacts(
    match_dir: Path,
    candidates_doc: dict[str, Any],
    tracks_doc: dict[str, Any],
    report: dict[str, Any],
    quality_report: dict[str, Any],
) -> None:
    (match_dir / "ball_candidates.json").write_text(json.dumps(candidates_doc, indent=2), encoding="utf-8")
    (match_dir / "ball_tracks.json").write_text(json.dumps(tracks_doc, indent=2), encoding="utf-8")
    (match_dir / "ball_tracking_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (match_dir / "ball_quality_report.json").write_text(json.dumps(quality_report, indent=2), encoding="utf-8")


def _ball_result(
    candidates_doc: dict[str, Any],
    tracks_doc: dict[str, Any],
    report: dict[str, Any],
    quality_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ball_candidates": candidates_doc,
        "ball_tracks": tracks_doc,
        "ball_tracking_report": report,
        "ball_quality_report": quality_report,
        "artifacts": {
            "ball_candidates": "ball_candidates.json",
            "ball_tracks": "ball_tracks.json",
            "ball_tracking_report": "ball_tracking_report.json",
            "ball_quality_report": "ball_quality_report.json",
            "ball_overlay_preview": "ball_overlay_preview.mp4",
        },
    }


def _ball_candidate_reject_reason(
    candidate: dict[str, Any],
    *,
    pitch_polygon: Any,
    frame_size: tuple[int, int],
) -> str | None:
    width, height = frame_size
    frame_area = max(1.0, float(width * height))
    box_width = float(candidate.get("width_px") or 0.0)
    box_height = float(candidate.get("height_px") or 0.0)
    area = float(candidate.get("area_px") or 0.0)
    if box_width <= 0 or box_height <= 0:
        return "degenerate_bbox"
    if area < DEFAULT_MIN_BALL_AREA_PX:
        return "too_small"
    if area > frame_area * DEFAULT_MAX_BALL_AREA_RATIO:
        return "too_large"
    aspect = box_width / max(box_height, 0.001)
    if aspect < 0.25 or aspect > 4.0:
        return "bad_aspect_ratio"
    center = candidate.get("position_px")
    if not center or len(center) != 2:
        return "missing_center"
    if not _point_in_polygon((float(center[0]), float(center[1])), pitch_polygon):
        return "outside_pitch"
    return None


def _candidate_to_position(candidate: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "frame": int(candidate.get("frame") or 0),
        "time_sec": candidate.get("time_sec"),
        "position_px": candidate.get("position_px"),
        "position_m": candidate.get("position_m"),
        "bbox_xyxy": candidate.get("bbox_xyxy"),
        "source": source,
        "confidence": candidate.get("confidence"),
        "candidate_id": candidate.get("candidate_id"),
        "segment_start_reason": candidate.get("segment_start_reason"),
    }


def _resolve_ball_model_classes(model: Any) -> dict[str, Any]:
    model_classes = _model_classes(model)
    matches = {
        class_id: name
        for class_id, name in model_classes.items()
        if _is_ball_class_name(name)
    }
    source = BALL_SOURCE
    selected = matches
    resolution = "class_name_match"
    if selected and set(selected) == {BALL_COCO_CLASS_ID}:
        source = BALL_COCO_SOURCE
    elif selected:
        source = BALL_CUSTOM_SOURCE
    elif len(model_classes) == 1:
        selected = dict(model_classes)
        source = BALL_CUSTOM_SOURCE
        resolution = "single_class_model"
    else:
        selected = {}
        resolution = "no_ball_class"

    class_ids = sorted(selected)
    class_names = [selected[class_id] for class_id in class_ids]
    return {
        "source": source,
        "class_ids": class_ids,
        "class_names": class_names,
        "class_name_by_id": selected,
        "model_classes": model_classes,
        "resolution": resolution,
    }


def _model_classes(model: Any) -> dict[int, str]:
    names = getattr(model, "names", None)
    normalized: dict[int, str] = {}
    if isinstance(names, dict):
        for key, value in names.items():
            try:
                class_id = int(key)
            except (TypeError, ValueError):
                continue
            normalized[class_id] = str(value)
        return normalized
    if isinstance(names, list):
        return {index: str(value) for index, value in enumerate(names)}
    return {BALL_COCO_CLASS_ID: BALL_COCO_CLASS_NAME}


def _is_ball_class_name(name: str) -> bool:
    normalized = str(name).lower().replace("_", " ").replace("-", " ").strip()
    return normalized in {"ball", "sports ball", "soccer ball", "football"} or normalized.endswith(" ball")


def _class_id_at(class_ids: Any | None, index: int) -> int:
    if class_ids is None:
        return BALL_COCO_CLASS_ID
    try:
        return int(class_ids[index])
    except (IndexError, TypeError, ValueError):
        return BALL_COCO_CLASS_ID


def _default_ball_class_name(class_id: int) -> str:
    if class_id == BALL_COCO_CLASS_ID:
        return BALL_COCO_CLASS_NAME
    return "ball"


def _processed_frame_indices(frame_count: int, fps: float, max_seconds: float, frame_stride: int) -> list[int]:
    max_frame = int(max_seconds * fps) if max_seconds > 0 else frame_count - 1
    max_frame = min(max_frame, frame_count - 1) if frame_count > 0 else max_frame
    if max_frame < 0:
        return []
    return list(range(0, max_frame + 1, max(1, frame_stride)))


def _distance_m(a: Any, b: Any) -> float | None:
    if not a or not b or len(a) != 2 or len(b) != 2:
        return None
    return float(((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2) ** 0.5)


def _lerp_pair(a: Any, b: Any, alpha: float) -> list[float] | None:
    if not a or not b or len(a) != 2 or len(b) != 2:
        return None
    return [
        float(a[0]) + (float(b[0]) - float(a[0])) * alpha,
        float(a[1]) + (float(b[1]) - float(a[1])) * alpha,
    ]


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _source_ranges(positions: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for position in positions:
        if position.get("source") != source:
            if current is not None:
                ranges.append(current)
                current = None
            continue
        frame = int(position.get("frame") or 0)
        time_sec = float(position.get("time_sec") or 0.0)
        if current is None:
            current = {
                "start_frame": frame,
                "end_frame": frame,
                "start_time_sec": round(time_sec, 3),
                "end_time_sec": round(time_sec, 3),
                "frames": 1,
            }
        else:
            current["end_frame"] = frame
            current["end_time_sec"] = round(time_sec, 3)
            current["frames"] = int(current["frames"]) + 1
    if current is not None:
        ranges.append(current)
    for item in ranges:
        item["duration_sec"] = round(float(item["end_time_sec"]) - float(item["start_time_sec"]), 3)
    return ranges


def _frame_interval_sec(tracks_doc: dict[str, Any]) -> float:
    positions = tracks_doc.get("positions") if isinstance(tracks_doc.get("positions"), list) else []
    previous: dict[str, Any] | None = None
    for position in positions:
        if previous is not None:
            dt = float(position.get("time_sec") or 0.0) - float(previous.get("time_sec") or 0.0)
            if dt > 0:
                return dt
        previous = position
    return 0.0


def _image_to_pitch_m(x: float, y: float, homography: Any) -> tuple[float, float]:
    h = homography
    denominator = float(h[2][0]) * x + float(h[2][1]) * y + float(h[2][2])
    if abs(denominator) < 1e-9:
        return (0.0, 0.0)
    mapped_x = (float(h[0][0]) * x + float(h[0][1]) * y + float(h[0][2])) / denominator
    mapped_y = (float(h[1][0]) * x + float(h[1][1]) * y + float(h[1][2])) / denominator
    return (mapped_x, mapped_y)


def _point_in_polygon(point: tuple[float, float], polygon: Any) -> bool:
    x, y = point
    points = [(float(row[0]), float(row[1])) for row in polygon]
    if len(points) < 3:
        return False
    inside = False
    previous_x, previous_y = points[-1]
    for current_x, current_y in points:
        on_horizontal = abs(current_y - previous_y) < 1e-9
        if on_horizontal and abs(y - current_y) < 1e-9 and min(previous_x, current_x) <= x <= max(previous_x, current_x):
            return True
        intersects = (current_y > y) != (previous_y > y)
        if intersects:
            crossing_x = (previous_x - current_x) * (y - current_y) / (previous_y - current_y) + current_x
            if abs(crossing_x - x) < 1e-9:
                return True
            if x < crossing_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


class _BallOverlayWriter:
    def __init__(self, match_dir: Path, output_name: str, fps: float, frame_size: tuple[int, int]) -> None:
        import cv2

        self.frame_size = frame_size
        self.fps = max(1.0, float(fps))
        self.final_path = match_dir / output_name
        self.temp_path = match_dir / f"{output_name}.raw.avi"
        self.frames_written = 0
        self._writer = cv2.VideoWriter(str(self.temp_path), cv2.VideoWriter_fourcc(*"MJPG"), self.fps, frame_size)
        if not self._writer.isOpened():
            raise RuntimeError(f"Could not open OpenCV VideoWriter for {self.temp_path.name}.")

    def write(self, frame: Any) -> None:
        import cv2

        expected_w, expected_h = self.frame_size
        if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
            frame = cv2.resize(frame, (expected_w, expected_h))
        self._writer.write(frame)
        self.frames_written += 1

    def close(self) -> Path:
        self._writer.release()
        if self.frames_written == 0:
            self.temp_path.unlink(missing_ok=True)
            raise RuntimeError("Ball overlay was not generated because zero frames were processed.")
        if self.final_path.exists():
            self.final_path.unlink()
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is not available, so ball overlay could not be converted to MP4.")
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
            raise RuntimeError(f"ffmpeg failed while converting ball overlay: {completed.stderr.strip()}")
        return self.final_path


def _draw_ball_candidates(frame: Any, candidates: list[dict[str, Any]]) -> None:
    import cv2

    for candidate in candidates:
        position = candidate.get("position_px")
        if not position or len(position) != 2:
            continue
        x, y = int(round(float(position[0]))), int(round(float(position[1])))
        cv2.circle(frame, (x, y), 5, (160, 160, 160), 1, cv2.LINE_AA)


def _draw_ball_position(frame: Any, position: dict[str, Any]) -> None:
    import cv2

    point = position.get("position_px")
    if not point or len(point) != 2:
        return
    x, y = int(round(float(point[0]))), int(round(float(point[1])))
    source = position.get("source")
    color = (0, 255, 255) if source == "detected" else (255, 255, 0)
    radius = 9 if source == "detected" else 7
    cv2.circle(frame, (x, y), radius, color, 2, cv2.LINE_AA)
    cv2.line(frame, (x - radius - 3, y), (x + radius + 3, y), color, 1, cv2.LINE_AA)
    cv2.line(frame, (x, y - radius - 3), (x, y + radius + 3), color, 1, cv2.LINE_AA)
    label = f"BALL {source} {float(position.get('confidence') or 0.0):.2f}"
    cv2.putText(frame, label, (max(0, x + 10), max(16, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(frame, label, (max(0, x + 10), max(16, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)


def _draw_ball_hud(
    frame: Any,
    frame_idx: int,
    *,
    fps: float,
    position: dict[str, Any],
    candidate_count: int,
    summary: dict[str, Any],
) -> None:
    import cv2

    lines = [
        f"ball yolo | frame={frame_idx} t={frame_idx / max(fps, 0.001):.1f}s",
        f"status={position.get('source')} cand={candidate_count} conf={float(position.get('confidence') or 0.0):.2f}",
        f"coverage det={_percent(summary.get('detected_coverage'))} int={_percent(summary.get('interpolated_coverage'))} known={_percent(summary.get('known_coverage'))}",
        f"frames det={summary.get('detected_frames', 0)} int={summary.get('interpolated_frames', 0)} unk={summary.get('unknown_frames', 0)}",
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


def _draw_frame_stamp(frame: Any, frame_idx: int) -> None:
    import cv2

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


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "--"
