from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.model_defaults import DEFAULT_BALL_YOLO_MODEL, DEFAULT_PLAYER_YOLO_MODEL


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
    pitch_dimensions_m: dict[str, float] | None = None
    calibration_frame_time_sec: float | None = None
    created_at: str | None = None
    source: str = "manual"

    @model_validator(mode="after")
    def normalize_legacy_default_pitch_size(self) -> "PitchConfigPayload":
        if self.pitch_dimensions_m:
            self.width_m = float(self.pitch_dimensions_m.get("width_m", self.width_m))
            self.length_m = float(self.pitch_dimensions_m.get("length_m", self.length_m))
        # Older UI builds sent 26 x 56 by default. Current pitch is 30 x 47.40 m.
        if abs(self.width_m - 26.0) < 0.001 and abs(self.length_m - 56.0) < 0.001:
            self.width_m = 30.0
            self.length_m = 47.4
        self.pitch_dimensions_m = {"width_m": self.width_m, "length_m": self.length_m}
        return self


class AnalyzePayload(BaseModel):
    adapter: str = "yolo"  # yolo | motion
    max_seconds: float = 30.0
    frame_stride: int = 1
    chunked: bool = False
    chunk_duration_sec: float = 120.0
    chunk_overlap_sec: float = 2.0
    include_ball: bool = False
    render_stable_overlay: bool = True

    # YOLO options
    yolo_model: str = DEFAULT_PLAYER_YOLO_MODEL
    yolo_conf: float = 0.05
    yolo_imgsz: int = 1920
    yolo_tracker: str = "centroid_high_recall"
    yolo_device: str | None = None  # None/empty = auto, "cpu", or "0"

    # Ball YOLO options used by chunked analysis when include_ball=true.
    ball_yolo_model: str = DEFAULT_BALL_YOLO_MODEL
    ball_yolo_conf: float = 0.03
    ball_yolo_imgsz: int = 960
    ball_yolo_device: str | None = None

    # Camera motion compensation for drone sway within one stable camera segment.
    camera_motion_compensation: bool = True
    camera_motion_interval_sec: float = 0.5
    camera_motion_min_inlier_ratio: float = 0.6


class BallAnalyzePayload(BaseModel):
    max_seconds: float = 3.0
    frame_stride: int = 4
    yolo_model: str = DEFAULT_BALL_YOLO_MODEL
    yolo_conf: float = 0.05
    yolo_imgsz: int = 960
    yolo_device: str | None = None
