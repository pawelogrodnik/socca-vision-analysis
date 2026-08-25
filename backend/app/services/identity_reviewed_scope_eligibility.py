from __future__ import annotations

"""Scope-aware blocking eligibility for Reviewed Identity work."""

from collections.abc import Iterable
from typing import Any

from app.services.identity_review_scope import TEAM_STATS_ONLY, team_review_scope


TEAM_UNCERTAINTY_MARKERS = (
    "cross_team",
    "team_mismatch",
    "team_attribution",
    "team_conflict",
)


def has_team_attribution_uncertainty(value: dict[str, Any]) -> bool:
    """Whether resolving this source is needed for team, not player, truth."""
    if _team_label(value.get("effective_team_label")) == "U":
        return True
    return any(
        marker in str(reason).lower()
        for reason in value.get("reason_codes") or []
        for marker in TEAM_UNCERTAINTY_MARKERS
    )


def required_review_relevant_for_scope(
    unit: dict[str, Any],
    match_doc: dict[str, Any],
) -> bool:
    """Return whether a unit is mandatory under the selected team scope.

    A certain team-only opponent can still contribute team statistics, but its
    player identity is intentionally not a mandatory review task. Any genuine
    A/B attribution uncertainty remains mandatory regardless of its current
    best team label.
    """
    if has_team_attribution_uncertainty(unit):
        return True
    team = unit_team_label(unit)
    return team == "U" or team_review_scope(match_doc, team) != TEAM_STATS_ONLY


def mixed_review_relevant_for_scope(
    marker: dict[str, Any],
    observations: Iterable[dict[str, Any]],
    match_doc: dict[str, Any],
) -> bool:
    """Return whether an unresolved Mixed source blocks the mandatory stage."""
    source = marker.get("source") if isinstance(marker.get("source"), dict) else {}
    context = {**marker, **source}
    if str(marker.get("mixed_hint") or "") == "cross_team":
        return True
    if has_team_attribution_uncertainty(context):
        return True
    labels = {
        _team_label(row.get("team_label"))
        for row in observations
        if isinstance(row, dict)
    }
    if "U" in labels or len(labels) != 1:
        return True
    team = next(iter(labels))
    return team_review_scope(match_doc, team) != TEAM_STATS_ONLY


def unit_team_label(unit: dict[str, Any]) -> str:
    for field in ("coverage_team_label", "effective_team_label", "source_team_label"):
        team = _team_label(unit.get(field))
        if team in {"A", "B"}:
            return team
    return "U"


def _team_label(value: Any) -> str:
    normalized = str(value or "U").upper()
    return normalized if normalized in {"A", "B"} else "U"
