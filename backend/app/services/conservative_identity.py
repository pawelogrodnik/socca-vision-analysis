from __future__ import annotations

from typing import Any, Callable

from app.services.global_identity import (
    build_frame_detection_counts_from_global_identity,
    build_global_identity_report,
    build_stable_players_from_global_identity,
    resolve_global_identity,
)


def resolve_conservative_identity(
    tracklets: list[dict[str, Any]],
    *,
    raw_tracks_count: int,
    rejected_tracklets_count: int,
    pitch_width_m: float,
    pitch_length_m: float,
    fps: float,
    pitch_polygon: Any | None = None,
    match_phase_config: dict[str, Any] | None = None,
    progress: Callable[[str, float, str, dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    return resolve_global_identity(
        tracklets,
        raw_tracks_count=raw_tracks_count,
        rejected_tracklets_count=rejected_tracklets_count,
        pitch_width_m=pitch_width_m,
        pitch_length_m=pitch_length_m,
        fps=fps,
        pitch_polygon=pitch_polygon,
        match_phase_config=match_phase_config,
        progress=progress,
    )
