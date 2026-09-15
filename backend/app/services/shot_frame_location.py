from __future__ import annotations

"""Optional operator frame-to-pitch projection for Shot Review."""

import math
from typing import Any, Mapping

from app import config
from app.services.json_publish_store import get_published_match
from app.services.match_groups import get_match_group
from app.services.merged_public_match import group_id_for_merged_published_id
from app.services.pitch import PitchConfig, image_to_pitch_m
from app.services.published_source_context import source_context_for_published_time


class ShotFrameLocationError(ValueError):
    def __init__(self, code: str, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _read_object(path: Any) -> dict[str, Any]:
    try:
        import json
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _source_context(published_id: str, logical_frame_time_sec: float) -> tuple[str, float]:
    context = source_context_for_published_time(
        get_published_match(published_id),
        published_id,
        logical_frame_time_sec,
        group_id_for_merged_published_id=group_id_for_merged_published_id,
        get_match_group=get_match_group,
    )
    if context is None:
        raise ShotFrameLocationError("shot_review_frame_source_unavailable", "Nie można ustalić źródłowego fragmentu wideo dla wybranej klatki.")
    return context


def _pitch_config(source_match_id: str) -> PitchConfig:
    document = _read_object(config.MATCHES_DIR / source_match_id / "pitch_config.json")
    points = document.get("image_points")
    width, length = _number(document.get("width_m")), _number(document.get("length_m"))
    if not isinstance(points, list) or len(points) != 4 or width is None or length is None or width <= 0 or length <= 0:
        raise ShotFrameLocationError("shot_review_frame_calibration_unavailable", "Dla tej klatki nie ma bezpiecznej kalibracji boiska.")
    normalized_points: list[list[float]] = []
    for point in points:
        if not isinstance(point, list) or len(point) < 2:
            raise ShotFrameLocationError("shot_review_frame_calibration_unavailable", "Dla tej klatki nie ma bezpiecznej kalibracji boiska.")
        x, y = _number(point[0]), _number(point[1])
        if x is None or y is None:
            raise ShotFrameLocationError("shot_review_frame_calibration_unavailable", "Dla tej klatki nie ma bezpiecznej kalibracji boiska.")
        normalized_points.append([x, y])
    try:
        pitch = PitchConfig(
            image_points=normalized_points,
            width_m=width,
            length_m=length,
            calibration_frame_time_sec=_number(document.get("calibration_frame_time_sec")) or 0.0,
        )
        pitch.homography()
    except (ValueError, TypeError):
        raise ShotFrameLocationError("shot_review_frame_calibration_unavailable", "Dla tej klatki nie ma bezpiecznej kalibracji boiska.") from None
    return pitch


def frame_location_context(published_id: str, logical_frame_time_sec: Any) -> dict[str, Any]:
    logical_time = _number(logical_frame_time_sec)
    if logical_time is None or logical_time < 0:
        raise ShotFrameLocationError("shot_review_frame_time_invalid", "Czas klatki jest nieprawidłowy.")
    source_match_id, source_time_sec = _source_context(published_id, logical_time)
    projection_error = None
    try:
        _pitch_config(source_match_id)
    except ShotFrameLocationError as error:
        projection_error = {"code": error.code, "detail": error.detail}
    return {
        "logical_frame_time_sec": round(logical_time, 6),
        "source_match_id": source_match_id,
        "source_time_sec": round(source_time_sec, 6),
        "projection_available": projection_error is None,
        "projection_error": projection_error,
    }


def project_frame_location(published_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    logical_time = _number(payload.get("logical_frame_time_sec"))
    x_px, y_px = _number(payload.get("x_px")), _number(payload.get("y_px"))
    frame_width, frame_height = _number(payload.get("frame_width")), _number(payload.get("frame_height"))
    if logical_time is None or logical_time < 0:
        raise ShotFrameLocationError("shot_review_frame_time_invalid", "Czas klatki jest nieprawidłowy.")
    if x_px is None or y_px is None or frame_width is None or frame_height is None or frame_width <= 0 or frame_height <= 0 or not (0 <= x_px <= frame_width and 0 <= y_px <= frame_height):
        raise ShotFrameLocationError("shot_review_frame_point_invalid", "Wybierz punkt wewnątrz źródłowej klatki.")
    source_match_id, source_time_sec = _source_context(published_id, logical_time)
    pitch = _pitch_config(source_match_id)
    try:
        mapped = image_to_pitch_m([(x_px, y_px)], pitch.homography())
    except (ValueError, TypeError):
        raise ShotFrameLocationError("shot_review_frame_projection_invalid", "Nie można bezpiecznie przeliczyć punktu z klatki na boisko.") from None
    if len(mapped) != 1:
        raise ShotFrameLocationError("shot_review_frame_projection_invalid", "Nie można bezpiecznie przeliczyć punktu z klatki na boisko.")
    x_m, y_m = mapped[0]
    if not math.isfinite(x_m) or not math.isfinite(y_m) or not (0 <= x_m <= pitch.width_m and 0 <= y_m <= pitch.length_m):
        raise ShotFrameLocationError("shot_review_frame_projection_out_of_pitch", "Wybrany punkt nie znajduje się na skalibrowanym boisku.")
    location_m = {"x": round(x_m, 6), "y": round(y_m, 6)}
    provenance = {
        "source_match_id": source_match_id,
        "source_time_sec": round(source_time_sec, 6),
        "logical_frame_time_sec": round(logical_time, 6),
        "x_px": round(x_px, 3),
        "y_px": round(y_px, 3),
        "frame_width": round(frame_width, 3),
        "frame_height": round(frame_height, 3),
    }
    return {"location_m": location_m, "provenance": provenance}
