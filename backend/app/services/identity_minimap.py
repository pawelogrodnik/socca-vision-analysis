from __future__ import annotations

"""Small pitch radar drawn from frozen calibrated pitch-meter positions."""

from typing import Any


TEAM_COLORS = {"A": (40, 70, 235), "B": (235, 150, 35), "U": (180, 180, 180)}


def draw_reviewed_minimap(frame: Any, rows: list[dict[str, Any]], *, pitch_width: float, pitch_length: float, include_ball: bool = False, ball: dict[str, Any] | None = None) -> dict[str, Any]:
    import cv2

    height, width = frame.shape[:2]; map_width = min(300, max(180, width // 5)); map_height = int(map_width * pitch_length / max(pitch_width, 1)); x0, y0 = width - map_width - 16, height - map_height - 16
    cv2.rectangle(frame, (x0 - 5, y0 - 5), (x0 + map_width + 5, y0 + map_height + 5), (12, 18, 25), -1)
    cv2.rectangle(frame, (x0, y0), (x0 + map_width, y0 + map_height), (210, 210, 210), 1)
    cv2.line(frame, (x0, y0 + map_height // 2), (x0 + map_width, y0 + map_height // 2), (150, 150, 150), 1)
    cv2.circle(frame, (x0 + map_width // 2, y0 + map_height // 2), max(5, map_width // 10), (150, 150, 150), 1)
    rendered = 0
    for row in rows:
        point = row.get("pitch_m")
        if not isinstance(point, list) or len(point) < 2: continue
        x, y = map_pitch_point(point, x0, y0, map_width, map_height, pitch_width, pitch_length)
        color = TEAM_COLORS.get(str(row.get("team_label") or "U"), TEAM_COLORS["U"])
        cv2.circle(frame, (x, y), 5, color, -1)
        if row.get("identity_status") == "conflicted": cv2.circle(frame, (x, y), 8, (0, 200, 255), 1)
        rendered += 1
    ball_point = reviewed_ball_pitch_point(ball) if include_ball else None
    if ball_point:
        x, y = map_pitch_point(ball_point, x0, y0, map_width, map_height, pitch_width, pitch_length)
        cv2.circle(frame, (x, y), 4, (0, 230, 255), -1)
    return {"status": "available", "players_rendered": rendered, "ball_rendered": bool(ball_point), "ball_policy": "detected_only; unknown and interpolated rows are omitted", "orientation": "pitch_m_x_to_horizontal_pitch_m_y_to_vertical", "smoothing": "none_for_MVP; source tracklet positions are already smoothed"}


def reviewed_ball_pitch_point(ball: dict[str, Any] | None) -> list[float] | None:
    if not ball or str(ball.get("source") or ball.get("status") or "detected") != "detected":
        return None
    point = ball.get("position_m")
    if not isinstance(point, list) or len(point) < 2:
        return None
    return [float(point[0]), float(point[1])]


def map_pitch_point(point: list[float], x0: int, y0: int, width: int, height: int, pitch_width: float, pitch_length: float) -> tuple[int, int]:
    x = max(0.0, min(float(pitch_width), float(point[0]))); y = max(0.0, min(float(pitch_length), float(point[1])))
    return round(x0 + x / max(float(pitch_width), 1) * width), round(y0 + y / max(float(pitch_length), 1) * height)
