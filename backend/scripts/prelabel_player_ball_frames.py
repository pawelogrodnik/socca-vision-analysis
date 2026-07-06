from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.model_defaults import DEFAULT_BALL_YOLO_MODEL, DEFAULT_PLAYER_YOLO_MODEL
from app.services.ball_tracking import _resolve_ball_model_classes


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_PLAYER_MODEL = DEFAULT_PLAYER_YOLO_MODEL
DEFAULT_BALL_MODEL = DEFAULT_BALL_YOLO_MODEL
DEFAULT_PLAYER_CONF = 0.05
DEFAULT_BALL_CONF = 0.03
DEFAULT_IOU = 0.45
DEFAULT_PLAYER_IMGSZ = 1280
DEFAULT_BALL_IMGSZ = 960
DEFAULT_MAX_BALL_DETECTIONS = 3

EXPORT_CLASSES = {
    "player": 0,
    "ball": 1,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Roboflow-ready YOLO pre-labels for player + ball from extracted match frames.",
    )
    parser.add_argument("frames_dir", nargs="?", type=Path, help="Folder with extracted training frame images.")
    parser.add_argument("--frames-dir", dest="frames_dir_option", type=Path, help="Folder with extracted frame images.")
    parser.add_argument("--out", "--output-dir", dest="output_dir", type=Path, default=None)
    parser.add_argument("--player-model", default=DEFAULT_PLAYER_MODEL)
    parser.add_argument("--ball-model", default=DEFAULT_BALL_MODEL)
    parser.add_argument("--player-conf", type=float, default=DEFAULT_PLAYER_CONF)
    parser.add_argument("--ball-conf", type=float, default=DEFAULT_BALL_CONF)
    parser.add_argument("--iou", type=float, default=DEFAULT_IOU)
    parser.add_argument("--player-imgsz", type=int, default=DEFAULT_PLAYER_IMGSZ)
    parser.add_argument("--ball-imgsz", type=int, default=DEFAULT_BALL_IMGSZ)
    parser.add_argument("--device", default=None, help='Ultralytics device, for example "cpu" or "0". Default: auto.')
    parser.add_argument("--max-ball-detections", type=int, default=DEFAULT_MAX_BALL_DETECTIONS)
    parser.add_argument(
        "--roi-polygon",
        default=None,
        help='Optional pitch ROI polygon in image pixels, for example "120,180;1650,170;1850,1030;80,1040".',
    )
    parser.add_argument(
        "--roi-margin-px",
        type=float,
        default=0.0,
        help=(
            "ROI margin in pixels. Positive accepts detections slightly outside the polygon; "
            "negative shrinks the accepted area inward. Default: 0."
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    frames_dir = args.frames_dir_option or args.frames_dir
    if frames_dir is None:
        parser.error("Missing frames directory. Pass it positionally or with --frames-dir.")

    roi_polygon = parse_roi_polygon(args.roi_polygon)
    player_model = load_yolo_model(args.player_model)
    ball_model = load_yolo_model(args.ball_model)
    result = prelabel_player_ball_frames(
        frames_dir=frames_dir,
        output_dir=args.output_dir,
        player_model=player_model,
        ball_model=ball_model,
        player_model_name=args.player_model,
        ball_model_name=args.ball_model,
        player_conf=args.player_conf,
        ball_conf=args.ball_conf,
        iou=args.iou,
        player_imgsz=args.player_imgsz,
        ball_imgsz=args.ball_imgsz,
        device=args.device,
        max_ball_detections=args.max_ball_detections,
        roi_polygon=roi_polygon,
        roi_margin_px=args.roi_margin_px,
        overwrite=args.overwrite,
        create_zip=not args.no_zip,
    )
    print(json.dumps(cli_summary(result), indent=2))


def prelabel_player_ball_frames(
    *,
    frames_dir: Path,
    output_dir: Path | None,
    player_model: Any,
    ball_model: Any,
    player_model_name: str,
    ball_model_name: str,
    player_conf: float,
    ball_conf: float,
    iou: float,
    player_imgsz: int,
    ball_imgsz: int,
    device: str | None,
    max_ball_detections: int,
    roi_polygon: list[tuple[float, float]] | None,
    roi_margin_px: float,
    overwrite: bool,
    create_zip: bool,
) -> dict[str, Any]:
    import cv2

    source_dir = frames_dir.resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise ValueError(f"Frames directory does not exist or is not a directory: {source_dir}")

    image_paths = list_image_paths(source_dir)
    if not image_paths:
        raise ValueError(f"No supported image files found in: {source_dir}")

    target_dir = (output_dir or default_output_dir(source_dir)).resolve()
    prepare_output_dir(source_dir, target_dir, overwrite=overwrite)

    roboflow_dir = target_dir / "roboflow_yolo"
    images_dir = roboflow_dir / "train" / "images"
    labels_dir = roboflow_dir / "train" / "labels"
    preview_dir = target_dir / "annotated_preview"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    player_class_ids = resolve_player_class_ids(player_model)
    ball_class_config = _resolve_ball_model_classes(ball_model)
    if not ball_class_config["class_ids"]:
        raise ValueError(
            "Selected ball model does not expose a class named ball/sports ball and is not a single-class model."
        )

    rows: list[dict[str, Any]] = []
    total_player_labels = 0
    total_ball_labels = 0
    total_player_rejected_outside_roi = 0
    total_ball_rejected_outside_roi = 0
    for index, image_path in enumerate(image_paths, start=1):
        frame = cv2.imread(str(image_path))
        if frame is None:
            rows.append({"image": image_path.name, "status": "skipped_unreadable_image", "detections": []})
            continue

        height, width = frame.shape[:2]
        raw_players = predict_boxes(
            player_model,
            image_path,
            class_ids=player_class_ids,
            export_class_id=EXPORT_CLASSES["player"],
            export_class_name="player",
            conf=player_conf,
            iou=iou,
            imgsz=player_imgsz,
            device=device,
            max_detections=0,
        )
        raw_balls = predict_boxes(
            ball_model,
            image_path,
            class_ids=ball_class_config["class_ids"],
            export_class_id=EXPORT_CLASSES["ball"],
            export_class_name="ball",
            conf=ball_conf,
            iou=iou,
            imgsz=ball_imgsz,
            device=device,
            max_detections=max_ball_detections,
        )
        players, player_rejected_outside_roi = filter_detections_by_roi(
            raw_players,
            roi_polygon=roi_polygon,
            roi_margin_px=roi_margin_px,
        )
        balls, ball_rejected_outside_roi = filter_detections_by_roi(
            raw_balls,
            roi_polygon=roi_polygon,
            roi_margin_px=roi_margin_px,
        )
        detections = players + balls
        label_lines = [
            line
            for detection in detections
            if (line := bbox_xyxy_to_yolo_line(detection["bbox_xyxy"], frame_size=(width, height), class_id=detection["export_class_id"]))
            is not None
        ]

        shutil.copy2(image_path, images_dir / image_path.name)
        (labels_dir / f"{image_path.stem}.txt").write_text(
            "\n".join(label_lines) + ("\n" if label_lines else ""),
            encoding="utf-8",
        )
        cv2.imwrite(str(preview_dir / image_path.name), draw_detections(frame, detections))

        player_label_count = sum(1 for detection in players if valid_bbox(detection["bbox_xyxy"], width, height))
        ball_label_count = sum(1 for detection in balls if valid_bbox(detection["bbox_xyxy"], width, height))
        total_player_labels += player_label_count
        total_ball_labels += ball_label_count
        total_player_rejected_outside_roi += player_rejected_outside_roi
        total_ball_rejected_outside_roi += ball_rejected_outside_roi
        rows.append(
            {
                "image": image_path.name,
                "index": index,
                "width": width,
                "height": height,
                "status": "processed",
                "player_labels": player_label_count,
                "ball_labels": ball_label_count,
                "player_rejected_outside_roi": player_rejected_outside_roi,
                "ball_rejected_outside_roi": ball_rejected_outside_roi,
                "detections": detections,
            }
        )

    write_yolo_data_yaml(roboflow_dir / "data.yaml")
    write_class_names(roboflow_dir / "classes.txt")
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "output_dir": str(target_dir),
        "roboflow_yolo_dir": str(roboflow_dir),
        "annotated_preview_dir": str(preview_dir),
        "models": {
            "player": player_model_name,
            "ball": ball_model_name,
        },
        "parameters": {
            "player_conf": float(player_conf),
            "ball_conf": float(ball_conf),
            "iou": float(iou),
            "player_imgsz": int(player_imgsz),
            "ball_imgsz": int(ball_imgsz),
            "device": device or "auto",
            "max_ball_detections": int(max_ball_detections),
            "roi_polygon": roi_polygon,
            "roi_margin_px": float(roi_margin_px),
        },
        "classes": {
            "0": "player",
            "1": "ball",
        },
        "class_resolution": {
            "player_model_class_ids": player_class_ids,
            "ball": {
                "source": ball_class_config["source"],
                "class_ids": ball_class_config["class_ids"],
                "class_names": ball_class_config["class_names"],
                "resolution": ball_class_config["resolution"],
                "model_classes": ball_class_config["model_classes"],
            },
        },
        "summary": {
            "input_images": len(image_paths),
            "processed_images": sum(1 for row in rows if row["status"] == "processed"),
            "skipped_images": sum(1 for row in rows if row["status"] != "processed"),
            "images_with_any_label": sum(1 for row in rows if row.get("player_labels", 0) + row.get("ball_labels", 0) > 0),
            "images_with_ball_label": sum(1 for row in rows if row.get("ball_labels", 0) > 0),
            "player_label_count": total_player_labels,
            "ball_label_count": total_ball_labels,
            "total_label_count": total_player_labels + total_ball_labels,
            "player_rejected_outside_roi": total_player_rejected_outside_roi,
            "ball_rejected_outside_roi": total_ball_rejected_outside_roi,
        },
        "images": rows,
    }
    predictions_path = target_dir / "predictions.json"
    predictions_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if create_zip:
        zip_path = target_dir / "roboflow_yolo.zip"
        zip_directory(roboflow_dir, zip_path)
        summary["roboflow_yolo_zip"] = str(zip_path)
        predictions_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


def load_yolo_model(model_name: str):
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("Pre-labeling requires ultralytics. Install backend requirements first.") from exc
    return YOLO(resolve_yolo_model_name(model_name))


def resolve_yolo_model_name(model_name: str) -> str:
    raw = str(model_name or "").strip()
    if not raw:
        raise ValueError("YOLO model name/path cannot be empty.")

    direct = Path(raw)
    if direct.is_absolute() or direct.exists():
        return str(direct)

    normalized = raw.replace("\\", "/")
    candidates = [
        BACKEND_DIR / raw,
        BACKEND_DIR.parent / raw,
    ]
    if normalized.startswith("backend/"):
        candidates.append(BACKEND_DIR / normalized[len("backend/") :])
    if normalized.startswith("models/"):
        candidates.append(BACKEND_DIR / normalized)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return raw


def list_image_paths(frames_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in frames_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )


def default_output_dir(frames_dir: Path) -> Path:
    return frames_dir.parent / f"{frames_dir.name}_player_ball_prelabels"


def resolve_player_class_ids(model: Any) -> list[int]:
    names = getattr(model, "names", {}) or {}
    if isinstance(names, dict):
        person_ids = [int(class_id) for class_id, name in names.items() if str(name).lower() in {"person", "player"}]
        if person_ids:
            return person_ids
        if len(names) == 1:
            return [int(next(iter(names.keys())))]
    return [0]


def predict_boxes(
    model: Any,
    image_path: Path,
    *,
    class_ids: list[int],
    export_class_id: int,
    export_class_name: str,
    conf: float,
    iou: float,
    imgsz: int,
    device: str | None,
    max_detections: int,
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
            "model_class_name": model_class_name(model, int(model_class_id)),
            "export_class_id": int(export_class_id),
            "export_class_name": export_class_name,
        }
        for bbox, score, model_class_id in zip(xyxy, confs, classes)
    ]
    detections.sort(key=lambda item: float(item["confidence"]), reverse=True)
    if max_detections > 0:
        return detections[:max_detections]
    return detections


def model_class_name(model: Any, class_id: int) -> str:
    names = getattr(model, "names", {}) or {}
    if isinstance(names, dict):
        return str(names.get(class_id, class_id))
    return str(class_id)


def parse_roi_polygon(raw_polygon: str | None) -> list[tuple[float, float]] | None:
    if raw_polygon is None or not str(raw_polygon).strip():
        return None

    points: list[tuple[float, float]] = []
    for raw_point in str(raw_polygon).split(";"):
        raw_point = raw_point.strip()
        if not raw_point:
            continue
        values = [value.strip() for value in raw_point.split(",")]
        if len(values) != 2:
            raise ValueError(f"Invalid ROI point '{raw_point}'. Expected x,y.")
        points.append((float(values[0]), float(values[1])))

    if len(points) < 3:
        raise ValueError("ROI polygon must contain at least 3 points.")
    return points


def filter_detections_by_roi(
    detections: list[dict[str, Any]],
    *,
    roi_polygon: list[tuple[float, float]] | None,
    roi_margin_px: float,
) -> tuple[list[dict[str, Any]], int]:
    if not roi_polygon:
        return detections, 0

    import cv2
    import numpy as np

    polygon = np.array(roi_polygon, dtype=np.float32)
    accepted: list[dict[str, Any]] = []
    rejected = 0
    margin_px = float(roi_margin_px)
    for detection in detections:
        point = roi_reference_point(detection)
        distance = float(cv2.pointPolygonTest(polygon, point, measureDist=True))
        if distance >= -margin_px:
            accepted.append({**detection, "roi_distance_px": round(distance, 2)})
        else:
            rejected += 1
    return accepted, rejected


def roi_reference_point(detection: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = [float(value) for value in detection["bbox_xyxy"]]
    class_name = str(detection.get("export_class_name") or "")
    x_center = x1 + (x2 - x1) / 2.0
    if class_name == "player":
        return (x_center, y2)
    return (x_center, y1 + (y2 - y1) / 2.0)


def bbox_xyxy_to_yolo_line(bbox_xyxy: list[float], *, frame_size: tuple[int, int], class_id: int) -> str | None:
    width, height = frame_size
    if width <= 0 or height <= 0 or len(bbox_xyxy) != 4:
        return None

    x1, y1, x2, y2 = [float(value) for value in bbox_xyxy]
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
    values = [
        int(class_id),
        x_center / width,
        y_center / height,
        box_width / width,
        box_height / height,
    ]
    return f"{values[0]} " + " ".join(f"{value:.6f}" for value in values[1:])


def valid_bbox(bbox_xyxy: list[float], width: int, height: int) -> bool:
    return bbox_xyxy_to_yolo_line(bbox_xyxy, frame_size=(width, height), class_id=0) is not None


def write_yolo_data_yaml(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "path: .",
                "train: train/images",
                "val: train/images",
                "names:",
                "  0: player",
                "  1: ball",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_class_names(path: Path) -> None:
    path.write_text("player\nball\n", encoding="utf-8")


def draw_detections(frame: Any, detections: list[dict[str, Any]]) -> Any:
    import cv2

    annotated = frame.copy()
    for detection in detections:
        x1, y1, x2, y2 = [int(round(float(value))) for value in detection["bbox_xyxy"]]
        class_name = str(detection["export_class_name"])
        color = (0, 255, 0) if class_name == "player" else (0, 255, 255)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        label = f"{class_name} {float(detection.get('confidence') or 0.0):.2f}"
        draw_label(annotated, label, x1, y1, color)
    if not detections:
        draw_label(annotated, "no labels", 12, 24, (180, 180, 180))
    return annotated


def draw_label(frame: Any, label: str, x: int, y: int, color: tuple[int, int, int]) -> None:
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


def prepare_output_dir(source_dir: Path, target_dir: Path, *, overwrite: bool) -> None:
    if target_dir == source_dir or is_relative_to(target_dir, source_dir):
        raise ValueError("Output directory must not be the same as, or inside, the input frames directory.")
    if target_dir.exists():
        if not overwrite:
            raise ValueError(f"Output directory already exists. Use --overwrite to replace it: {target_dir}")
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)


def zip_directory(source_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir).as_posix())


def cli_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") or {}
    return {
        "processed_images": summary.get("processed_images"),
        "player_label_count": summary.get("player_label_count"),
        "ball_label_count": summary.get("ball_label_count"),
        "images_with_ball_label": summary.get("images_with_ball_label"),
        "roboflow_yolo_dir": result.get("roboflow_yolo_dir"),
        "roboflow_yolo_zip": result.get("roboflow_yolo_zip"),
        "annotated_preview_dir": result.get("annotated_preview_dir"),
        "predictions_json": str(Path(result.get("output_dir", "")) / "predictions.json"),
    }


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    main()
