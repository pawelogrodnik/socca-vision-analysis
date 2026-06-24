from __future__ import annotations

from pydantic import BaseModel, Field


class PitchConfigPayload(BaseModel):
    # Order should match destination corners: top-left, top-right, bottom-right, bottom-left
    image_points: list[list[float]] = Field(min_length=4, max_length=4)
    width_m: float = 26.0
    length_m: float = 56.0
    source: str = "manual"


class AnalyzePayload(BaseModel):
    adapter: str = "yolo"  # yolo | motion
    max_seconds: float = 30.0
    frame_stride: int = 1

    # YOLO options
    yolo_model: str = "yolov8n.pt"
    yolo_conf: float = 0.25
    yolo_imgsz: int = 960
    yolo_tracker: str = "botsort.yaml"
    yolo_device: str | None = None  # None/empty = auto, "cpu", or "0"
