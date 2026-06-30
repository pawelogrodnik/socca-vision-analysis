from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.ball_tracking import _resolve_ball_model_classes

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_BALL_PRELABEL_CONF = 0.05
DEFAULT_BALL_PRELABEL_IOU = 0.45
DEFAULT_BALL_PRELABEL_IMGSZ = 1280
DEFAULT_MAX_DETECTIONS_PER_IMAGE = 3


def prelabel_ball_frames(
    frames_dir: Path,
    output_dir: Path | None,
    *,
    model: Any,
    model_name: str,
    conf: float = DEFAULT_BALL_PRELABEL_CONF,
    iou: float = DEFAULT_BALL_PRELABEL_IOU,
    imgsz: int = DEFAULT_BALL_PRELABEL_IMGSZ,
    device: str | None = None,
    max_detections_per_image: int = DEFAULT_MAX_DETECTIONS_PER_IMAGE,
    overwrite: bool = False,
    create_zip: bool = True,
) -> dict[str, Any]:
    import cv2

    source_dir = frames_dir.resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise ValueError(f"Frames directory does not exist or is not a directory: {source_dir}")

    image_paths = list_image_paths(source_dir)
    if not image_paths:
        raise ValueError(f"No supported image files found in: {source_dir}")

    target_dir = (output_dir or default_output_dir(source_dir)).resolve()
    _prepare_output_dir(source_dir, target_dir, overwrite=overwrite)

    roboflow_dir = target_dir / "roboflow_yolo"
    images_dir = roboflow_dir / "train" / "images"
    labels_dir = roboflow_dir / "train" / "labels"
    preview_dir = target_dir / "annotated_preview"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    class_config = _resolve_ball_model_classes(model)
    if not class_config["class_ids"]:
        raise ValueError(
            "Selected YOLO model does not expose a class named ball/sports ball and is not a single-class model. "
            "Use a COCO model or a custom one-class ball detector."
        )

    rows: list[dict[str, Any]] = []
    total_detections = 0
    images_with_detections = 0
    for image_path in image_paths:
        frame = cv2.imread(str(image_path))
        if frame is None:
            rows.append(
                {
                    "image": image_path.name,
                    "status": "skipped_unreadable_image",
                    "detections": [],
                }
            )
            continue

        height, width = frame.shape[:2]
        detections = _predict_ball_boxes(
            model,
            image_path,
            class_ids=class_config["class_ids"],
            class_name_by_id=class_config["class_name_by_id"],
            conf=conf,
            iou=iou,
            imgsz=imgsz,
            device=device,
            max_detections_per_image=max_detections_per_image,
        )
        label_lines = [
            line
            for detection in detections
            if (line := bbox_xyxy_to_yolo_line(detection["bbox_xyxy"], frame_size=(width, height))) is not None
        ]

        shutil.copy2(image_path, images_dir / image_path.name)
        (labels_dir / f"{image_path.stem}.txt").write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
        annotated = draw_ball_detections(frame, detections)
        cv2.imwrite(str(preview_dir / image_path.name), annotated)

        total_detections += len(label_lines)
        if label_lines:
            images_with_detections += 1
        rows.append(
            {
                "image": image_path.name,
                "width": width,
                "height": height,
                "status": "processed",
                "detections": detections,
                "label_count": len(label_lines),
            }
        )

    write_yolo_data_yaml(roboflow_dir / "data.yaml")
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "output_dir": str(target_dir),
        "roboflow_yolo_dir": str(roboflow_dir),
        "annotated_preview_dir": str(preview_dir),
        "model": model_name,
        "parameters": {
            "conf": float(conf),
            "iou": float(iou),
            "imgsz": int(imgsz),
            "device": device or "auto",
            "max_detections_per_image": int(max_detections_per_image),
        },
        "class_resolution": {
            "source": class_config["source"],
            "class_ids": class_config["class_ids"],
            "class_names": class_config["class_names"],
            "resolution": class_config["resolution"],
            "model_classes": class_config["model_classes"],
            "exported_dataset_class": {"id": 0, "name": "ball"},
        },
        "summary": {
            "input_images": len(image_paths),
            "processed_images": sum(1 for row in rows if row["status"] == "processed"),
            "skipped_images": sum(1 for row in rows if row["status"] != "processed"),
            "images_with_detections": images_with_detections,
            "label_count": total_detections,
        },
        "images": rows,
    }
    (target_dir / "predictions.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if create_zip:
        zip_path = target_dir / "roboflow_yolo.zip"
        zip_directory(roboflow_dir, zip_path)
        summary["roboflow_yolo_zip"] = str(zip_path)
        (target_dir / "predictions.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


def list_image_paths(frames_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in frames_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )


def default_output_dir(frames_dir: Path) -> Path:
    return frames_dir.parent / f"{frames_dir.name}_ball_prelabels"


def bbox_xyxy_to_yolo_line(bbox_xyxy: list[float], *, frame_size: tuple[int, int]) -> str | None:
    width, height = frame_size
    if width <= 0 or height <= 0 or len(bbox_xyxy) != 4:
        return None

    x1, y1, x2, y2 = [float(value) for value in bbox_xyxy]
    x1 = _clamp(x1, 0.0, float(width))
    x2 = _clamp(x2, 0.0, float(width))
    y1 = _clamp(y1, 0.0, float(height))
    y2 = _clamp(y2, 0.0, float(height))
    if x2 <= x1 or y2 <= y1:
        return None

    box_width = x2 - x1
    box_height = y2 - y1
    x_center = x1 + box_width / 2.0
    y_center = y1 + box_height / 2.0
    values = [
        0,
        x_center / width,
        y_center / height,
        box_width / width,
        box_height / height,
    ]
    return f"{values[0]} " + " ".join(f"{value:.6f}" for value in values[1:])


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


def zip_directory(source_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir).as_posix())


def draw_ball_detections(frame: Any, detections: list[dict[str, Any]]) -> Any:
    import cv2

    annotated = frame.copy()
    for index, detection in enumerate(detections, start=1):
        x1, y1, x2, y2 = [int(round(float(value))) for value in detection["bbox_xyxy"]]
        color = (0, 255, 255)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        label = f"ball {index} {float(detection.get('confidence') or 0.0):.2f}"
        _draw_label(annotated, label, x1, y1, color)
    if not detections:
        _draw_label(annotated, "no ball detection", 12, 24, (180, 180, 180))
    return annotated


def _predict_ball_boxes(
    model: Any,
    image_path: Path,
    *,
    class_ids: list[int],
    class_name_by_id: dict[int, str],
    conf: float,
    iou: float,
    imgsz: int,
    device: str | None,
    max_detections_per_image: int,
) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {
        "source": str(image_path),
        "classes": class_ids,
        "conf": float(conf),
        "iou": float(iou),
        "imgsz": int(imgsz),
        "verbose": False,
    }
    if device:
        kwargs["device"] = device

    results = model.predict(**kwargs)
    if not results or results[0].boxes is None:
        return []

    boxes = results[0].boxes
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy() if boxes.conf is not None else [1.0] * len(xyxy)
    classes = boxes.cls.cpu().numpy() if boxes.cls is not None else [class_ids[0]] * len(xyxy)
    detections = [
        {
            "bbox_xyxy": [round(float(value), 2) for value in bbox],
            "confidence": round(float(score), 4),
            "model_class_id": int(model_class_id),
            "model_class_name": class_name_by_id.get(int(model_class_id), "ball"),
            "export_class_id": 0,
            "export_class_name": "ball",
        }
        for bbox, score, model_class_id in zip(xyxy, confs, classes)
    ]
    detections.sort(key=lambda item: float(item["confidence"]), reverse=True)
    if max_detections_per_image > 0:
        return detections[:max_detections_per_image]
    return detections


def _prepare_output_dir(source_dir: Path, target_dir: Path, *, overwrite: bool) -> None:
    if target_dir == source_dir or _is_relative_to(target_dir, source_dir):
        raise ValueError("Output directory must not be the same as, or inside, the input frames directory.")
    if target_dir.exists():
        if not overwrite:
            raise ValueError(f"Output directory already exists. Use --overwrite to replace it: {target_dir}")
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)


def _draw_label(frame: Any, label: str, x: int, y: int, color: tuple[int, int, int]) -> None:
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.45
    thickness = 1
    (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
    top = max(0, y - text_height - baseline - 8)
    left = max(0, x)
    right = min(frame.shape[1] - 1, left + text_width + 10)
    bottom = min(frame.shape[0] - 1, top + text_height + baseline + 8)
    cv2.rectangle(frame, (left, top), (right, bottom), (20, 20, 20), -1)
    cv2.putText(frame, label, (left + 5, bottom - baseline - 4), font, font_scale, color, thickness, cv2.LINE_AA)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
