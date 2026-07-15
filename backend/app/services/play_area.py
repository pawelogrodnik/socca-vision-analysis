from __future__ import annotations

import math
from typing import Any


DEFAULT_PLAY_AREA_INTERIOR_MARGIN_M = 0.35
DEFAULT_PLAY_AREA_BOUNDARY_MARGIN_M = 1.25


def classify_pitch_position(
    point: Any,
    *,
    pitch_width_m: float,
    pitch_length_m: float,
    interior_margin_m: float = DEFAULT_PLAY_AREA_INTERIOR_MARGIN_M,
    boundary_margin_m: float = DEFAULT_PLAY_AREA_BOUNDARY_MARGIN_M,
) -> dict[str, Any]:
    """Classify a mapped footpoint before clamping it to pitch dimensions.

    A narrow strip on both sides of the painted line is deliberately treated
    as transient. It preserves continuity for a known player without allowing
    bench players or restart actors to become trusted on-pitch observations.
    """
    x_m = float(point[0])
    y_m = float(point[1])
    width_m = max(0.0, float(pitch_width_m))
    length_m = max(0.0, float(pitch_length_m))
    clamped_x = min(width_m, max(0.0, x_m))
    clamped_y = min(length_m, max(0.0, y_m))
    outside_distance_m = math.hypot(x_m - clamped_x, y_m - clamped_y)
    inside = outside_distance_m <= 1e-9
    interior_distance_m = (
        min(x_m, width_m - x_m, y_m, length_m - y_m)
        if inside
        else 0.0
    )

    if inside and interior_distance_m >= max(0.0, float(interior_margin_m)):
        status = "inside_play"
    elif outside_distance_m <= max(0.0, float(boundary_margin_m)):
        status = "boundary_transient"
    else:
        status = "outside_play"

    return {
        "pitch_m_raw": [round(x_m, 3), round(y_m, 3)],
        "pitch_m": [round(clamped_x, 3), round(clamped_y, 3)],
        "pitch_m_clamped": outside_distance_m > 1e-9,
        "play_area_status": status,
        "pitch_boundary_distance_m": round(outside_distance_m, 3),
        "pitch_interior_distance_m": round(interior_distance_m, 3),
    }
