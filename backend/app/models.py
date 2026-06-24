from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class PlayerPayload(BaseModel):
    id: str | None = None
    name: str
    number: str | None = None
    role: str = "player"  # player | goalkeeper | guest | unknown
    is_guest: bool = False


class TeamPayload(BaseModel):
    id: str | None = None
    name: str
    color: str | None = None
    players: list[PlayerPayload] = Field(default_factory=list)


class MatchMetadataPayload(BaseModel):
    title: str
    match_date: str | None = None
    season: str | None = None
    venue: str | None = None
    format: str = "7v7"
    status: str = "draft"  # draft | uploaded | calibrated | analyzed | reviewed | published
    teams: list[TeamPayload] = Field(default_factory=list, min_length=0, max_length=8)


class PitchConfigPayload(BaseModel):
    # Order should match destination corners: top-left, top-right, bottom-right, bottom-left
    image_points: list[list[float]] = Field(min_length=4, max_length=4)
    width_m: float = 30.0
    length_m: float = 47.4
    source: str = "manual"

    @model_validator(mode="after")
    def normalize_legacy_default_pitch_size(self) -> "PitchConfigPayload":
        # Older UI builds sent 26 x 56 by default. Current pitch is 30 x 47.40 m.
        if abs(self.width_m - 26.0) < 0.001 and abs(self.length_m - 56.0) < 0.001:
            self.width_m = 30.0
            self.length_m = 47.4
        return self


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
