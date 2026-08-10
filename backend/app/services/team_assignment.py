from __future__ import annotations

from typing import Any


TEAM_COLOR_UNKNOWN_CONFIDENCE = 0.42
TEAM_COLOR_MAX_ASSIGNMENT_DISTANCE = 95.0
UNTRUSTED_TEAM_ASSIGNMENT_REASONS = {
    "goalkeeper_outlier_requires_review",
    "team_color_outlier",
}


def is_trusted_tracklet_team_assignment(tracklet: dict[str, Any]) -> bool:
    team_label = str(tracklet.get("team_label") or "U").upper()
    if team_label not in {"A", "B"} or not tracklet.get("team_cluster_id"):
        return False
    if str(tracklet.get("team_assignment_reason") or "") in UNTRUSTED_TEAM_ASSIGNMENT_REASONS:
        return False
    try:
        team_confidence = float(tracklet.get("team_confidence") or 0.0)
    except (TypeError, ValueError):
        return False
    return team_confidence >= TEAM_COLOR_UNKNOWN_CONFIDENCE