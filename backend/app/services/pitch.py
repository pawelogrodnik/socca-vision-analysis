from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class PitchConfig:
    image_points: list[list[float]]
    width_m: float = 26.0
    length_m: float = 56.0
    calibration_frame_time_sec: float = 0.0

    @property
    def polygon_np(self) -> np.ndarray:
        return np.array(self.image_points, dtype=np.float32)

    def destination_points_m(self) -> np.ndarray:
        # Convention: x = pitch width, y = pitch length.
        return np.array(
            [
                [0.0, 0.0],
                [self.width_m, 0.0],
                [self.width_m, self.length_m],
                [0.0, self.length_m],
            ],
            dtype=np.float32,
        )

    def homography(self) -> np.ndarray:
        H, _ = cv2.findHomography(self.polygon_np, self.destination_points_m())
        if H is None:
            raise ValueError("Could not compute homography from pitch points")
        return H


def create_pitch_mask(frame_shape_hw: tuple[int, int], polygon: np.ndarray) -> np.ndarray:
    height, width = frame_shape_hw
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon.astype(np.int32)], 255)
    return mask


def point_in_polygon(point: tuple[float, float], polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon.astype(np.float32), point, False) >= 0


def image_to_pitch_m(points: Iterable[tuple[float, float]], H: np.ndarray) -> list[tuple[float, float]]:
    pts = np.array(list(points), dtype=np.float32)
    if len(pts) == 0:
        return []
    pts = pts.reshape(-1, 1, 2)
    transformed = cv2.perspectiveTransform(pts, H).reshape(-1, 2)
    return [(float(x), float(y)) for x, y in transformed]
