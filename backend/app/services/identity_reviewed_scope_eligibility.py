from __future__ import annotations

"""Scope-aware blocking eligibility for Reviewed Identity work."""

from collections.abc import Iterable
from typing import Any

from app.services.identity_review_scope import TEAM_STATS_ONLY, team_review_scope


TeamAttributionState = str

UNCERTAIN_TEAM_STATES = frozenset({"uncertain", "cross_team", "unknown"})


def team_attribution_state(value: dict[str, Any]) -> TeamAttributionState:
    """Classify current canonical team evidence for one exact review source.

    Reason codes are diagnostics about how the source reached its current
    state.  They are deliberately not evidence: a historical ``team_mismatch``
    must not turn a currently all-B source back into a Required team decision.
    """
    source = _team_label(value.get("source_team_label"))
    effective = _team_label(value.get("effective_team_label"))
    coverage = _team_label(value.get("coverage_team_label"))
    source_is_explicitly_unknown = _explicitly_unknown(value, "source_team_label")
    effective_is_explicitly_unknown = _explicitly_unknown(value, "effective_team_label")
    detected = _detected_team_labels(value.get("detected_team_labels"))

    if str(value.get("mixed_hint") or "") == "cross_team" or detected == {"A", "B"}:
        return "cross_team"

    established = {team for team in (source, effective, coverage) if team in {"A", "B"}}
    if len(established) > 1:
        return "cross_team"

    if detected:
        detected_team = next(iter(detected))
        if established and detected_team not in established:
            return "cross_team"
        established.add(detected_team)

    if len(established) == 1:
        team = next(iter(established))
        # A current unknown source is not made safe merely because a display
        # or coverage projection has a best-known team.  ``certain`` requires
        # its source and effective canonical labels to agree with the exact
        # detected evidence when that evidence is available.
        if (
            (source_is_explicitly_unknown or effective_is_explicitly_unknown)
            and not _has_explicit_team_decision(value, team)
        ):
            return "uncertain"
        return f"certain_{team}"

    return "unknown"


def has_team_attribution_uncertainty(value: dict[str, Any]) -> bool:
    """Whether resolving this source is needed for team, not player, truth."""
    return team_attribution_state(value) in UNCERTAIN_TEAM_STATES


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
    labels = {
        _team_label(row.get("team_label"))
        for row in observations
        if isinstance(row, dict)
    }
    context["detected_team_labels"] = sorted(labels & {"A", "B"})
    # Legacy markers may predate an explicit source-team field. Their exact
    # owned observations are still canonical evidence, so an all-B source
    # with a matching effective B label remains safely non-mandatory.
    if (
        "source_team_label" not in context
        and _team_label(context.get("effective_team_label")) in {"A", "B"}
    ):
        context["source_team_label"] = context["effective_team_label"]
    if has_team_attribution_uncertainty(context):
        return True
    if "U" in labels or len(labels) != 1:
        return True
    team = next(iter(labels))
    return team_review_scope(match_doc, team) != TEAM_STATS_ONLY


def unit_team_label(unit: dict[str, Any]) -> str:
    state = team_attribution_state(unit)
    if state == "certain_A":
        return "A"
    if state == "certain_B":
        return "B"
    for field in ("coverage_team_label", "effective_team_label", "source_team_label"):
        team = _team_label(unit.get(field))
        if team in {"A", "B"}:
            return team
    return "U"


def _detected_team_labels(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {_team_label(team) for team in value} & {"A", "B"}


def _explicitly_unknown(value: dict[str, Any], field: str) -> bool:
    return field in value and _team_label(value.get(field)) == "U"


def _has_explicit_team_decision(value: dict[str, Any], team: str) -> bool:
    decision = value.get("current_decision")
    return (
        isinstance(decision, dict)
        and str(decision.get("action") or "") in {"assign_team", "assign_roster_player"}
        and _team_label(decision.get("team_label")) == team
    )


def _team_label(value: Any) -> str:
    normalized = str(value or "U").upper()
    return normalized if normalized in {"A", "B"} else "U"
