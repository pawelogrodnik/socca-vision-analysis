from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.model_defaults import DEFAULT_BALL_YOLO_MODEL
from app.services.ball_tracking import _resolve_ball_model_classes


DEFAULT_VIDEO = REPO_ROOT / "matches_video" / "corgi_verisk_2_3.mp4"
DEFAULT_PITCH_CONFIG = BACKEND_DIR / "storage" / "matches" / "682c5606" / "pitch_config.json"
DEFAULT_TEACHER_MODEL = DEFAULT_BALL_YOLO_MODEL
DEFAULT_CUSTOM_MODEL = "models/best-model-with-ball-and-players-500-frames.pt"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "training_frames"

CATEGORY_WEIGHTS = {
    "teacher_detected_custom_missing": 1.2,
    "custom_detected_teacher_missing": 0.9,
    "model_disagreement": 1.0,
    "teacher_low_confidence": 1.0,
    "multi_candidate_noise": 0.7,
    "outside_pitch_projection": 1.0,
    "no_ball_model_gap": 1.2,
    "agreement_high_confidence": 0.25,
}


@dataclass(frozen=True)
class Detection:
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    model_class_id: int
    model_class_name: str
    center: tuple[float, float]
    area_px: float
    pitch_distance_px: float | None
    inside_pitch_polygon: bool | None
    inside_expanded_roi: bool


@dataclass(frozen=True)
class FrameCandidate:
    frame: int
    time_sec: float
    category: str
    score: float
    reason: str
    teacher_detections: list[Detection]
    custom_detections: list[Detection]
    label_source: str
    label_detections: list[Detection]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a match video with two ball-capable models, select problematic active-learning frames, "
            "and export a Roboflow-ready one-class ball dataset with prelabels."
        )
    )
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--pitch-config", type=Path, default=DEFAULT_PITCH_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--name", default=None, help="Optional output folder name.")
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--end-sec", type=float, default=18.0 * 60.0)
    parser.add_argument("--sample-every-sec", type=float, default=2.0)
    parser.add_argument("--target-count", type=int, default=500)
    parser.add_argument("--teacher-model", default=DEFAULT_TEACHER_MODEL)
    parser.add_argument("--custom-model", default=DEFAULT_CUSTOM_MODEL)
    parser.add_argument("--teacher-conf", type=float, default=0.02)
    parser.add_argument("--custom-conf", type=float, default=0.02)
    parser.add_argument("--low-confidence-threshold", type=float, default=0.16)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--device", default=None, help='Ultralytics device, for example "cpu", "0" or "cuda".')
    parser.add_argument("--max-detections-per-model", type=int, default=5)
    parser.add_argument("--max-prelabels-per-image", type=int, default=1)
    parser.add_argument("--agreement-distance-px", type=float, default=70.0)
    parser.add_argument("--min-ball-area-px", type=float, default=2.0)
    parser.add_argument("--max-ball-area-ratio", type=float, default=0.004)
    parser.add_argument("--roi-side-margin-px", type=float, default=160.0)
    parser.add_argument("--roi-top-margin-px", type=float, default=420.0)
    parser.add_argument("--roi-bottom-margin-px", type=float, default=100.0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    if args.sample_every_sec <= 0:
        raise ValueError("--sample-every-sec must be greater than 0.")
    if args.target_count <= 0:
        raise ValueError("--target-count must be greater than 0.")

    import cv2

    video_path = args.video.resolve()
    pitch_config_path = args.pitch_config.resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not pitch_config_path.exists():
        raise FileNotFoundError(f"Pitch config not found: {pitch_config_path}")

    pitch_polygon = load_pitch_polygon(pitch_config_path)
    pitch_bounds = polygon_bounds(pitch_polygon)
    teacher_model = load_yolo_model(args.teacher_model)
    custom_model = load_yolo_model(args.custom_model)
    teacher_classes = resolve_ball_classes(teacher_model, "teacher")
    custom_classes = resolve_ball_classes(custom_model, "custom")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
        raise ValueError("Video metadata is incomplete.")

    start_frame = max(0, int(round(args.start_sec * fps)))
    requested_end_frame = int(round(args.end_sec * fps)) if args.end_sec > 0 else frame_count - 1
    end_frame = min(frame_count - 1, max(start_frame, requested_end_frame))
    sample_step_frames = max(1, int(round(args.sample_every_sec * fps)))
    sample_frames = list(range(start_frame, end_frame + 1, sample_step_frames))

    output_dir = build_output_dir(args.output_root, args.name, video_path, args.start_sec, args.end_sec)
    prepare_output_dir(output_dir, overwrite=args.overwrite)

    candidates: list[FrameCandidate] = []
    try:
        for index, frame_idx in enumerate(sample_frames, start=1):
            ok, frame = read_frame(cap, frame_idx)
            if not ok or frame is None:
                continue
            teacher_detections = predict_ball_detections(
                teacher_model,
                frame,
                class_ids=teacher_classes["class_ids"],
                class_name_by_id=teacher_classes["class_name_by_id"],
                conf=args.teacher_conf,
                iou=args.iou,
                imgsz=args.imgsz,
                device=args.device,
                max_detections=args.max_detections_per_model,
                frame_size=(width, height),
                pitch_polygon=pitch_polygon,
                pitch_bounds=pitch_bounds,
                roi_side_margin_px=args.roi_side_margin_px,
                roi_top_margin_px=args.roi_top_margin_px,
                roi_bottom_margin_px=args.roi_bottom_margin_px,
                min_ball_area_px=args.min_ball_area_px,
                max_ball_area_ratio=args.max_ball_area_ratio,
            )
            custom_detections = predict_ball_detections(
                custom_model,
                frame,
                class_ids=custom_classes["class_ids"],
                class_name_by_id=custom_classes["class_name_by_id"],
                conf=args.custom_conf,
                iou=args.iou,
                imgsz=args.imgsz,
                device=args.device,
                max_detections=args.max_detections_per_model,
                frame_size=(width, height),
                pitch_polygon=pitch_polygon,
                pitch_bounds=pitch_bounds,
                roi_side_margin_px=args.roi_side_margin_px,
                roi_top_margin_px=args.roi_top_margin_px,
                roi_bottom_margin_px=args.roi_bottom_margin_px,
                min_ball_area_px=args.min_ball_area_px,
                max_ball_area_ratio=args.max_ball_area_ratio,
            )
            candidates.append(
                classify_frame(
                    frame_idx,
                    frame_idx / fps,
                    teacher_detections,
                    custom_detections,
                    low_confidence_threshold=args.low_confidence_threshold,
                    agreement_distance_px=args.agreement_distance_px,
                    max_prelabels_per_image=args.max_prelabels_per_image,
                )
            )
            if index % 50 == 0:
                print(
                    json.dumps(
                        {
                            "status": "scanning",
                            "sampled": index,
                            "total_samples": len(sample_frames),
                            "frame": frame_idx,
                            "time_sec": round(frame_idx / fps, 2),
                        }
                    ),
                    flush=True,
                )
    finally:
        cap.release()

    selected = select_active_learning_frames(candidates, target_count=args.target_count)
    export_selected_frames(
        video_path,
        output_dir,
        selected,
        frame_size=(width, height),
        pitch_polygon=pitch_polygon,
        pitch_bounds=pitch_bounds,
        roi_side_margin_px=args.roi_side_margin_px,
        roi_top_margin_px=args.roi_top_margin_px,
        roi_bottom_margin_px=args.roi_bottom_margin_px,
        create_zip=not args.no_zip,
    )
    summary = build_summary(
        output_dir,
        video_path,
        pitch_config_path,
        fps=fps,
        frame_count=frame_count,
        frame_size=(width, height),
        sampled_count=len(candidates),
        selected=selected,
        args=args,
        teacher_classes=teacher_classes,
        custom_classes=custom_classes,
    )
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def load_yolo_model(model_name: str) -> Any:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("This exporter requires ultralytics. Run it in the backend environment.") from exc
    return YOLO(resolve_yolo_model_name(model_name))


def resolve_yolo_model_name(model_name: str) -> str:
    raw = str(model_name or "").strip()
    if not raw:
        raise ValueError("YOLO model name/path cannot be empty.")

    direct = Path(raw)
    if direct.is_absolute() or direct.exists():
        return str(direct)

    normalized = raw.replace("\\", "/")
    candidates = [BACKEND_DIR / raw, BACKEND_DIR.parent / raw]
    if normalized.startswith("backend/"):
        candidates.append(BACKEND_DIR / normalized[len("backend/") :])
    if normalized.startswith("models/"):
        candidates.append(BACKEND_DIR / normalized)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return raw


def resolve_ball_classes(model: Any, label: str) -> dict[str, Any]:
    class_config = _resolve_ball_model_classes(model)
    if not class_config["class_ids"]:
        raise ValueError(
            f"The {label} model does not expose a ball/sports ball class and is not a one-class detector."
        )
    return class_config


def load_pitch_polygon(path: Path) -> list[tuple[float, float]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw_points = doc.get("image_points")
    if not isinstance(raw_points, list) or len(raw_points) < 3:
        raise ValueError(f"pitch_config.image_points is missing or invalid: {path}")
    return [(float(point[0]), float(point[1])) for point in raw_points]


def polygon_bounds(polygon: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def build_output_dir(output_root: Path, name: str | None, video_path: Path, start_sec: float, end_sec: float) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if name:
        folder_name = name
    else:
        start_min = int(start_sec // 60)
        end_min = int(math.ceil(end_sec / 60.0)) if end_sec > 0 else "full"
        folder_name = f"{video_path.stem}_ball_problem_frames_m{start_min:02d}_m{end_min}_{timestamp}"
    return output_root.resolve() / folder_name


def prepare_output_dir(output_dir: Path, *, overwrite: bool) -> None:
    if output_dir.exists():
        if not overwrite:
            raise ValueError(f"Output directory already exists. Use --overwrite to replace it: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def read_frame(cap: Any, frame_idx: int) -> tuple[bool, Any | None]:
    cap.set(1, int(frame_idx))
    ok, frame = cap.read()
    return bool(ok), frame


def predict_ball_detections(
    model: Any,
    frame: Any,
    *,
    class_ids: list[int],
    class_name_by_id: dict[int, str],
    conf: float,
    iou: float,
    imgsz: int,
    device: str | None,
    max_detections: int,
    frame_size: tuple[int, int],
    pitch_polygon: list[tuple[float, float]],
    pitch_bounds: tuple[float, float, float, float],
    roi_side_margin_px: float,
    roi_top_margin_px: float,
    roi_bottom_margin_px: float,
    min_ball_area_px: float,
    max_ball_area_ratio: float,
) -> list[Detection]:
    import cv2
    import numpy as np

    kwargs: dict[str, Any] = {
        "source": frame,
        "classes": class_ids,
        "conf": float(conf),
        "iou": float(iou),
        "imgsz": int(imgsz),
        "verbose": False,
    }
    if device and str(device).lower() != "auto":
        kwargs["device"] = device

    results = model.predict(**kwargs)
    if not results or results[0].boxes is None:
        return []

    width, height = frame_size
    frame_area = float(width * height)
    polygon_np = np.array(pitch_polygon, dtype=np.float32)
    boxes = results[0].boxes
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(xyxy))
    classes = boxes.cls.cpu().numpy() if boxes.cls is not None else np.array([class_ids[0]] * len(xyxy))

    detections: list[Detection] = []
    for bbox, score, model_class_id in zip(xyxy, confs, classes):
        x1, y1, x2, y2 = [float(value) for value in bbox]
        box_width = max(0.0, x2 - x1)
        box_height = max(0.0, y2 - y1)
        area = box_width * box_height
        if area < min_ball_area_px or area > frame_area * max_ball_area_ratio:
            continue
        center = (x1 + box_width / 2.0, y1 + box_height / 2.0)
        inside_expanded_roi = point_in_expanded_roi(
            center,
            pitch_bounds=pitch_bounds,
            frame_size=frame_size,
            side_margin_px=roi_side_margin_px,
            top_margin_px=roi_top_margin_px,
            bottom_margin_px=roi_bottom_margin_px,
        )
        if not inside_expanded_roi:
            continue
        pitch_distance = float(cv2.pointPolygonTest(polygon_np, center, measureDist=True))
        detections.append(
            Detection(
                bbox_xyxy=(round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)),
                confidence=round(float(score), 4),
                model_class_id=int(model_class_id),
                model_class_name=class_name_by_id.get(int(model_class_id), "ball"),
                center=(round(center[0], 2), round(center[1], 2)),
                area_px=round(area, 2),
                pitch_distance_px=round(pitch_distance, 2),
                inside_pitch_polygon=pitch_distance >= 0,
                inside_expanded_roi=True,
            )
        )
    detections.sort(key=lambda item: item.confidence, reverse=True)
    if max_detections > 0:
        return detections[:max_detections]
    return detections


def point_in_expanded_roi(
    point: tuple[float, float],
    *,
    pitch_bounds: tuple[float, float, float, float],
    frame_size: tuple[int, int],
    side_margin_px: float,
    top_margin_px: float,
    bottom_margin_px: float,
) -> bool:
    x, y = point
    min_x, min_y, max_x, max_y = pitch_bounds
    width, height = frame_size
    return (
        max(0.0, min_x - side_margin_px) <= x <= min(float(width), max_x + side_margin_px)
        and max(0.0, min_y - top_margin_px) <= y <= min(float(height), max_y + bottom_margin_px)
    )


def classify_frame(
    frame: int,
    time_sec: float,
    teacher_detections: list[Detection],
    custom_detections: list[Detection],
    *,
    low_confidence_threshold: float,
    agreement_distance_px: float,
    max_prelabels_per_image: int,
) -> FrameCandidate:
    teacher_best = teacher_detections[0] if teacher_detections else None
    custom_best = custom_detections[0] if custom_detections else None
    distance = center_distance(teacher_best, custom_best) if teacher_best and custom_best else None
    outside_pitch = any(det.inside_pitch_polygon is False for det in teacher_detections[:1] + custom_detections[:1])
    multi_candidate = len(teacher_detections) > 1 or len(custom_detections) > 1

    if outside_pitch and (teacher_best or custom_best):
        category = "outside_pitch_projection"
        score = 95.0 + best_confidence(teacher_best, custom_best) * 10.0
        reason = "best ball candidate is outside the pitch polygon but inside expanded ball ROI"
    elif teacher_best and custom_best and distance is not None and distance > agreement_distance_px:
        category = "model_disagreement"
        score = 90.0 + min(distance / 15.0, 20.0)
        reason = f"teacher/custom ball centers differ by {distance:.1f}px"
    elif teacher_best and not custom_best:
        category = "teacher_detected_custom_missing"
        score = 85.0 + teacher_best.confidence * 10.0
        reason = "ball-only teacher found a ball candidate but custom player+ball model did not"
    elif custom_best and not teacher_best:
        category = "custom_detected_teacher_missing"
        score = 80.0 + custom_best.confidence * 10.0
        reason = "custom player+ball model found a ball candidate but ball-only teacher did not"
    elif teacher_best and teacher_best.confidence < low_confidence_threshold:
        category = "teacher_low_confidence"
        score = 76.0 + max(0.0, low_confidence_threshold - teacher_best.confidence) * 100.0
        reason = "teacher candidate exists but confidence is low"
    elif multi_candidate:
        category = "multi_candidate_noise"
        score = 70.0 + (len(teacher_detections) + len(custom_detections)) * 4.0
        reason = "one of the models produced multiple ball candidates"
    elif not teacher_best and not custom_best:
        category = "no_ball_model_gap"
        score = 52.0
        reason = "neither model found a ball candidate; useful for finding missed balls/negatives"
    else:
        category = "agreement_high_confidence"
        score = 25.0 + best_confidence(teacher_best, custom_best) * 10.0
        reason = "both models agree; kept only as a small control sample"

    label_source, label_detections = choose_label_source(
        teacher_detections,
        custom_detections,
        max_prelabels_per_image=max_prelabels_per_image,
    )
    return FrameCandidate(
        frame=int(frame),
        time_sec=round(float(time_sec), 3),
        category=category,
        score=round(score, 3),
        reason=reason,
        teacher_detections=teacher_detections,
        custom_detections=custom_detections,
        label_source=label_source,
        label_detections=label_detections,
    )


def center_distance(first: Detection | None, second: Detection | None) -> float | None:
    if first is None or second is None:
        return None
    return math.hypot(first.center[0] - second.center[0], first.center[1] - second.center[1])


def best_confidence(first: Detection | None, second: Detection | None) -> float:
    return max(first.confidence if first else 0.0, second.confidence if second else 0.0)


def choose_label_source(
    teacher_detections: list[Detection],
    custom_detections: list[Detection],
    *,
    max_prelabels_per_image: int,
) -> tuple[str, list[Detection]]:
    limit = max(0, int(max_prelabels_per_image))
    if limit == 0:
        return "none", []
    if teacher_detections:
        return "teacher", teacher_detections[:limit]
    if custom_detections:
        return "custom", custom_detections[:limit]
    return "none", []


def select_active_learning_frames(candidates: list[FrameCandidate], *, target_count: int) -> list[FrameCandidate]:
    if len(candidates) <= target_count:
        return sorted(candidates, key=lambda item: item.frame)

    grouped: dict[str, list[FrameCandidate]] = {}
    for item in candidates:
        grouped.setdefault(item.category, []).append(item)

    total_weight = sum(CATEGORY_WEIGHTS.get(category, 0.5) for category in grouped)
    selected: list[FrameCandidate] = []
    selected_frames: set[int] = set()

    for category, items in grouped.items():
        quota = max(1, int(round(target_count * (CATEGORY_WEIGHTS.get(category, 0.5) / total_weight))))
        if category in {"no_ball_model_gap", "agreement_high_confidence"}:
            picks = pick_evenly_by_time(sorted(items, key=lambda item: item.frame), quota)
        else:
            picks = sorted(items, key=lambda item: (-item.score, item.frame))[:quota]
        for item in picks:
            if item.frame not in selected_frames:
                selected.append(item)
                selected_frames.add(item.frame)

    if len(selected) < target_count:
        for item in sorted(candidates, key=lambda item: (-item.score, item.frame)):
            if item.frame not in selected_frames:
                selected.append(item)
                selected_frames.add(item.frame)
                if len(selected) >= target_count:
                    break

    return sorted(selected[:target_count], key=lambda item: item.frame)


def pick_evenly_by_time(items: list[FrameCandidate], quota: int) -> list[FrameCandidate]:
    if quota >= len(items):
        return items
    if quota <= 0:
        return []
    if quota == 1:
        return [items[len(items) // 2]]
    picks: list[FrameCandidate] = []
    used_indices: set[int] = set()
    for index in range(quota):
        raw_position = index * (len(items) - 1) / (quota - 1)
        item_index = int(round(raw_position))
        while item_index in used_indices and item_index + 1 < len(items):
            item_index += 1
        if item_index not in used_indices:
            picks.append(items[item_index])
            used_indices.add(item_index)
    return picks


def export_selected_frames(
    video_path: Path,
    output_dir: Path,
    selected: list[FrameCandidate],
    *,
    frame_size: tuple[int, int],
    pitch_polygon: list[tuple[float, float]],
    pitch_bounds: tuple[float, float, float, float],
    roi_side_margin_px: float,
    roi_top_margin_px: float,
    roi_bottom_margin_px: float,
    create_zip: bool,
) -> None:
    import cv2

    roboflow_dir = output_dir / "roboflow_yolo"
    images_dir = roboflow_dir / "train" / "images"
    labels_dir = roboflow_dir / "train" / "labels"
    preview_dir = output_dir / "annotated_preview"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video for export: {video_path}")
    rows: list[dict[str, Any]] = []
    try:
        for item in selected:
            ok, frame = read_frame(cap, item.frame)
            if not ok or frame is None:
                continue
            image_name = f"frame_{item.frame:06d}_t{int(round(item.time_sec * 1000)):09d}_{item.category}.jpg"
            image_path = images_dir / image_name
            label_path = labels_dir / f"{Path(image_name).stem}.txt"
            cv2.imwrite(str(image_path), frame)
            label_lines = [
                line
                for detection in item.label_detections
                if (line := bbox_xyxy_to_yolo_line(detection.bbox_xyxy, frame_size=frame_size)) is not None
            ]
            label_path.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
            preview = draw_preview(
                frame,
                item,
                pitch_polygon=pitch_polygon,
                pitch_bounds=pitch_bounds,
                roi_side_margin_px=roi_side_margin_px,
                roi_top_margin_px=roi_top_margin_px,
                roi_bottom_margin_px=roi_bottom_margin_px,
            )
            cv2.imwrite(str(preview_dir / image_name), preview)
            rows.append(manifest_row(item, image_name, label_path.name))
    finally:
        cap.release()

    write_yolo_data_yaml(roboflow_dir / "data.yaml")
    (roboflow_dir / "classes.txt").write_text("ball\n", encoding="utf-8")
    write_manifest_csv(output_dir / "problem_frames.csv", rows)
    (output_dir / "problem_frames.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if create_zip:
        zip_directory(roboflow_dir, output_dir / "roboflow_yolo.zip")


def manifest_row(item: FrameCandidate, image_name: str, label_name: str) -> dict[str, Any]:
    teacher_best = item.teacher_detections[0] if item.teacher_detections else None
    custom_best = item.custom_detections[0] if item.custom_detections else None
    return {
        "frame": item.frame,
        "time_sec": item.time_sec,
        "category": item.category,
        "score": item.score,
        "reason": item.reason,
        "image": f"roboflow_yolo/train/images/{image_name}",
        "label": f"roboflow_yolo/train/labels/{label_name}",
        "label_source": item.label_source,
        "prelabel_count": len(item.label_detections),
        "teacher_count": len(item.teacher_detections),
        "teacher_conf": teacher_best.confidence if teacher_best else "",
        "teacher_center_x": teacher_best.center[0] if teacher_best else "",
        "teacher_center_y": teacher_best.center[1] if teacher_best else "",
        "teacher_pitch_distance_px": teacher_best.pitch_distance_px if teacher_best else "",
        "custom_count": len(item.custom_detections),
        "custom_conf": custom_best.confidence if custom_best else "",
        "custom_center_x": custom_best.center[0] if custom_best else "",
        "custom_center_y": custom_best.center[1] if custom_best else "",
        "custom_pitch_distance_px": custom_best.pitch_distance_px if custom_best else "",
        "model_center_distance_px": (
            round(center_distance(teacher_best, custom_best) or 0.0, 2) if teacher_best and custom_best else ""
        ),
    }


def write_manifest_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_yolo_data_yaml(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "path: .",
                "train: train/images",
                "val: train/images",
                "names:",
                "  0: ball",
                "",
            ]
        ),
        encoding="utf-8",
    )


def bbox_xyxy_to_yolo_line(bbox_xyxy: tuple[float, float, float, float], *, frame_size: tuple[int, int]) -> str | None:
    width, height = frame_size
    x1, y1, x2, y2 = bbox_xyxy
    x1 = clamp(x1, 0.0, float(width))
    x2 = clamp(x2, 0.0, float(width))
    y1 = clamp(y1, 0.0, float(height))
    y2 = clamp(y2, 0.0, float(height))
    if x2 <= x1 or y2 <= y1:
        return None
    box_width = x2 - x1
    box_height = y2 - y1
    x_center = x1 + box_width / 2.0
    y_center = y1 + box_height / 2.0
    values = [0, x_center / width, y_center / height, box_width / width, box_height / height]
    return f"{values[0]} " + " ".join(f"{value:.6f}" for value in values[1:])


def draw_preview(
    frame: Any,
    item: FrameCandidate,
    *,
    pitch_polygon: list[tuple[float, float]],
    pitch_bounds: tuple[float, float, float, float],
    roi_side_margin_px: float,
    roi_top_margin_px: float,
    roi_bottom_margin_px: float,
) -> Any:
    import cv2
    import numpy as np

    annotated = frame.copy()
    polygon_np = np.array(pitch_polygon, dtype=np.int32)
    cv2.polylines(annotated, [polygon_np], isClosed=True, color=(0, 255, 255), thickness=2, lineType=cv2.LINE_AA)
    min_x, min_y, max_x, max_y = pitch_bounds
    left = int(round(max(0.0, min_x - roi_side_margin_px)))
    top = int(round(max(0.0, min_y - roi_top_margin_px)))
    right = int(round(min(float(frame.shape[1]), max_x + roi_side_margin_px)))
    bottom = int(round(min(float(frame.shape[0]), max_y + roi_bottom_margin_px)))
    cv2.rectangle(annotated, (left, top), (right, bottom), (90, 90, 90), 1, cv2.LINE_AA)
    for detection in item.teacher_detections:
        draw_detection(annotated, detection, "teacher", (0, 255, 255))
    for detection in item.custom_detections:
        draw_detection(annotated, detection, "custom", (255, 0, 255))
    for detection in item.label_detections:
        draw_detection(annotated, detection, "PRELABEL", (0, 255, 0), thickness=3)
    header = f"frame {item.frame} | t={item.time_sec:.2f}s | {item.category} | label={item.label_source}"
    draw_label(annotated, header, 12, 28, (240, 240, 240), background=(20, 20, 20))
    draw_label(annotated, item.reason[:120], 12, 58, (220, 220, 220), background=(20, 20, 20))
    return annotated


def draw_detection(
    frame: Any,
    detection: Detection,
    label_prefix: str,
    color: tuple[int, int, int],
    *,
    thickness: int = 2,
) -> None:
    import cv2

    x1, y1, x2, y2 = [int(round(value)) for value in detection.bbox_xyxy]
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
    label = f"{label_prefix} {detection.confidence:.2f}"
    draw_label(frame, label, x1, max(18, y1 - 2), color, background=(20, 20, 20))


def draw_label(
    frame: Any,
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
    *,
    background: tuple[int, int, int],
) -> None:
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.48
    thickness = 1
    (text_width, text_height), baseline = cv2.getTextSize(text, font, font_scale, thickness)
    left = max(0, x)
    top = max(0, y - text_height - baseline - 4)
    right = min(frame.shape[1] - 1, left + text_width + 8)
    bottom = min(frame.shape[0] - 1, y + 4)
    cv2.rectangle(frame, (left, top), (right, bottom), background, -1)
    cv2.putText(frame, text, (left + 4, bottom - baseline - 2), font, font_scale, color, thickness, cv2.LINE_AA)


def zip_directory(source_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir).as_posix())


def build_summary(
    output_dir: Path,
    video_path: Path,
    pitch_config_path: Path,
    *,
    fps: float,
    frame_count: int,
    frame_size: tuple[int, int],
    sampled_count: int,
    selected: list[FrameCandidate],
    args: argparse.Namespace,
    teacher_classes: dict[str, Any],
    custom_classes: dict[str, Any],
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    prelabel_count = 0
    for item in selected:
        counts[item.category] = counts.get(item.category, 0) + 1
        prelabel_count += len(item.label_detections)
    return {
        "schema_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "roboflow_yolo_dir": str(output_dir / "roboflow_yolo"),
        "roboflow_yolo_zip": str(output_dir / "roboflow_yolo.zip"),
        "annotated_preview_dir": str(output_dir / "annotated_preview"),
        "problem_frames_csv": str(output_dir / "problem_frames.csv"),
        "problem_frames_json": str(output_dir / "problem_frames.json"),
        "source": {
            "video": str(video_path),
            "pitch_config": str(pitch_config_path),
            "fps": round(fps, 4),
            "frame_count": frame_count,
            "width": frame_size[0],
            "height": frame_size[1],
        },
        "models": {
            "teacher": args.teacher_model,
            "custom": args.custom_model,
        },
        "parameters": {
            "start_sec": float(args.start_sec),
            "end_sec": float(args.end_sec),
            "sample_every_sec": float(args.sample_every_sec),
            "target_count": int(args.target_count),
            "teacher_conf": float(args.teacher_conf),
            "custom_conf": float(args.custom_conf),
            "low_confidence_threshold": float(args.low_confidence_threshold),
            "iou": float(args.iou),
            "imgsz": int(args.imgsz),
            "device": args.device or "auto",
            "max_detections_per_model": int(args.max_detections_per_model),
            "max_prelabels_per_image": int(args.max_prelabels_per_image),
            "agreement_distance_px": float(args.agreement_distance_px),
            "min_ball_area_px": float(args.min_ball_area_px),
            "max_ball_area_ratio": float(args.max_ball_area_ratio),
            "roi_side_margin_px": float(args.roi_side_margin_px),
            "roi_top_margin_px": float(args.roi_top_margin_px),
            "roi_bottom_margin_px": float(args.roi_bottom_margin_px),
        },
        "class_resolution": {
            "teacher": {
                "source": teacher_classes["source"],
                "class_ids": teacher_classes["class_ids"],
                "class_names": teacher_classes["class_names"],
                "resolution": teacher_classes["resolution"],
            },
            "custom": {
                "source": custom_classes["source"],
                "class_ids": custom_classes["class_ids"],
                "class_names": custom_classes["class_names"],
                "resolution": custom_classes["resolution"],
            },
        },
        "summary": {
            "sampled_frames": sampled_count,
            "selected_frames": len(selected),
            "prelabel_count": prelabel_count,
            "empty_label_files": sum(1 for item in selected if not item.label_detections),
            "category_counts": dict(sorted(counts.items())),
        },
    }


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


if __name__ == "__main__":
    main()
