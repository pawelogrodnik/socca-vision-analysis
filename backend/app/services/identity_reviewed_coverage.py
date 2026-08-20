from __future__ import annotations

"""Canonical named-identity coverage and coverage-review policy.

The module deliberately separates observed identity coverage from operator
queue completion. A partial roster can acknowledge an anonymous subject
without turning it into named-player data.
"""

from collections import Counter, defaultdict
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
from typing import Any, Iterable

from app.services.identity_reviewed_effective_observation import (
    iter_effective_reviewed_observations,
)
from app.services.identity_review_scope import (
    COMPLETE_ROSTER,
    SUPPORTED_TEAM_SCOPES,
    TEAM_STATS_ONLY,
    identity_review_scope_read_model,
    team_review_scope,
)
from app.services.identity_reviewed_material_continuity import (
    is_material_continuity_case,
)
from app.services.play_area import is_on_pitch_product_observation


COVERAGE_SCHEMA_VERSION = "1.0.0"
COVERAGE_POLICY_VERSION = "coverage-driven-review:v6-material-continuity"
COVERAGE_UNIT = "unique_detected_tracklet_frame_observation"
REVIEWED_OBSERVATION_TARGET_RATIO = 0.90
COMPLETE_ROSTER_NAMED_TARGET_RATIO = 0.90
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
WORKLOAD_THRESHOLDS = {
    "elevated": 150,
    "excessive": 500,
    "critical": 1000,
}
ROSTER_SCOPES = SUPPORTED_TEAM_SCOPES
RELIABLE_STATUSES = frozenset(
    {"confirmed", "unresolved", "conflicted", "blocked", "team_unknown"}
)
IGNORED_STATUSES = frozenset(
    {"ignored", "referee", "false_detection"}
)


def load_effective_coverage_context(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    """Read current reviewed output once and return coverage plus pair state."""
    snapshot = _load(match_path / "reviewed_identity_snapshot.json")
    tracklets_document = _load(match_path / "tracklets.json")
    if not snapshot or not tracklets_document:
        return {
            "coverage": empty_coverage(match_doc),
            "pair_index": {},
            "source_snapshot_digest": None,
        }
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_document.get("tracklets") or []
        if row.get("tracklet_id")
    }
    rows = iter_effective_reviewed_observations(
        tracklets,
        list(snapshot.get("tracklet_assignments") or []),
        list(snapshot.get("observation_overrides") or []),
        list(snapshot.get("observation_demotions") or []),
        list(snapshot.get("canonical_observation_assignments") or []),
        list(snapshot.get("segment_observation_assignments") or []),
    )
    coverage, pair_index = summarize_effective_observations(rows, match_doc)
    return {
        "coverage": coverage,
        "pair_index": pair_index,
        "source_snapshot_digest": snapshot.get("semantic_digest"),
    }


def summarize_effective_observations(
    rows: Iterable[dict[str, Any]],
    match_doc: dict[str, Any],
) -> tuple[dict[str, Any], dict[tuple[str, int], dict[str, Any]]]:
    counts: Counter[str] = Counter()
    team_counts: dict[str, Counter[str]] = defaultdict(Counter)
    pair_index: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
        if key in pair_index or not is_on_pitch_product_observation(row):
            continue
        status = str(row.get("identity_status") or "unresolved")
        team = _team_label(row.get("team_label"))
        player_id = str(row.get("canonical_player_id") or "") or None
        pair_index[key] = {
            "identity_status": status,
            "team_label": team,
            "canonical_player_id": player_id if status == "confirmed" else None,
        }
        counts[status] += 1
        team_counts[team][status] += 1
        if status == "confirmed" and player_id:
            counts["confirmed_named"] += 1
            team_counts[team]["confirmed_named"] += 1

    per_team = {
        team: _coverage_row(
            team_counts.get(team, Counter()),
            roster_scope(match_doc, team),
            team_label=team,
        )
        for team in _coverage_teams(match_doc, team_counts)
    }
    global_row = _coverage_row(counts, "combined")
    global_row["team_known_observations"] = sum(
        int(row.get("team_known_observations") or 0)
        for team, row in per_team.items()
        if team in {"A", "B"}
    )
    global_row["team_known_observation_coverage"] = _ratio(
        global_row["team_known_observations"],
        int(global_row.get("reliable_observations") or 0),
    )
    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "policy_version": COVERAGE_POLICY_VERSION,
        "coverage_unit": COVERAGE_UNIT,
        **global_row,
        "per_team": per_team,
    }, pair_index


def empty_coverage(match_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "policy_version": COVERAGE_POLICY_VERSION,
        "coverage_unit": COVERAGE_UNIT,
        **_coverage_row(Counter(), "combined"),
        "per_team": {
            team: _coverage_row(Counter(), roster_scope(match_doc, team))
            for team in _coverage_teams(match_doc, {})
        },
    }


def apply_coverage_policy(
    units: list[dict[str, Any]],
    coverage: dict[str, Any],
    pair_index: dict[tuple[str, int], dict[str, Any]],
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    """Rank every meaningful unreviewed identity unit without a case cap."""
    semantic = [
        unit
        for unit in units
        if unit.get("current_resolution_status") == "pending_high_priority"
        and unit.get("operator_actionable") is not False
    ]
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    material_continuity: list[dict[str, Any]] = []
    optional_audit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unreviewable: dict[str, list[dict[str, Any]]] = defaultdict(list)
    non_actionable_team_uncertainty: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        if unit in semantic or _has_explicit_disposition(unit, match_doc):
            continue
        # A raw subject may have a diagnostic card while every observation is
        # outside the product play area.  It cannot become a team-attribution
        # task because it contributes no player-facing observation at all.
        if not unit.get("detected_pairs"):
            continue
        enriched = _coverage_impact(unit, pair_index, coverage)
        team = _team_label(enriched.get("coverage_team_label"))
        operator_actionable = enriched.get("operator_actionable") is not False
        # A Team-U or cross-team fragment must be resolved before named-player
        # coverage is considered.  It is a team-attribution decision, not a
        # request to name a player for the wrong roster scope.
        if _has_team_uncertainty(enriched):
            enriched["reason_codes"] = sorted(
                set(enriched.get("reason_codes") or [])
                | {"team_attribution_uncertain"}
            )
            if operator_actionable and enriched.get("has_operator_visual_evidence"):
                enriched["current_resolution_status"] = "pending_high_priority"
                enriched["priority"] = "high"
                semantic.append(enriched)
            else:
                non_actionable_team_uncertainty[team].append(enriched)
            continue
        # Aggregate coverage is deliberately not allowed to hide a severe,
        # safe, Team-A continuity break.  This is independent of the 90%
        # coverage target and only applies to pre-grouped material cases.
        if is_material_continuity_case(enriched):
            enriched["current_resolution_status"] = "pending_material_continuity_review"
            enriched["priority"] = "continuity"
            enriched["reason_codes"] = sorted(
                set(enriched.get("reason_codes") or [])
                | {"material_identity_continuity_gap"}
            )
            material_continuity.append(enriched)
            continue
        if team_review_scope(match_doc, team) == TEAM_STATS_ONLY:
            if int(enriched["potential_named_observation_gain"]) <= 0:
                continue
            if operator_actionable and enriched.get("has_operator_visual_evidence"):
                enriched["current_resolution_status"] = "optional_team_audit"
                enriched["priority"] = "optional"
                enriched["reason_codes"] = sorted(
                    set(enriched.get("reason_codes") or [])
                    | {"team_stats_only_optional_identity_audit"}
                )
                optional_audit[team].append(enriched)
            continue
        if int(enriched["potential_named_observation_gain"]) <= 0:
            continue
        if operator_actionable and enriched.get("has_operator_visual_evidence"):
            candidates[team].append(enriched)
        else:
            enriched["coverage_non_actionable_reason"] = (
                enriched.get("non_actionable_reason")
                if not operator_actionable
                else "no_visual_evidence"
            )
            unreviewable[team].append(enriched)

    coverage_blockers: list[dict[str, Any]] = []
    residual_by_team: dict[str, dict[str, Any]] = {}
    independently_required: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in [*semantic, *material_continuity]:
        enriched = _coverage_impact(unit, pair_index, coverage)
        team = _team_label(enriched.get("coverage_team_label"))
        if (
            team in {"A", "B"}
            and team_review_scope(match_doc, team) == COMPLETE_ROSTER
            and enriched.get("operator_actionable") is not False
            and enriched.get("has_operator_visual_evidence")
            and not _has_team_uncertainty(enriched)
            and int(enriched.get("potential_named_observation_gain") or 0) > 0
        ):
            independently_required[team].append(enriched)
    for team in sorted(
        set((coverage.get("per_team") or {}).keys())
        | set(candidates)
        | set(unreviewable)
        | set(optional_audit)
        | set(non_actionable_team_uncertainty)
        | set(independently_required)
    ):
        team_coverage = (coverage.get("per_team") or {}).get(team) or {}
        reliable = int(team_coverage.get("reliable_observations") or 0)
        scope = team_review_scope(match_doc, team)
        rows = sorted(
            candidates.get(team, []),
            key=lambda row: (
                -int(row.get("potential_named_observation_gain") or 0),
                -float(row.get("detected_time_sec") or 0.0),
                -int(row.get("tracklet_count") or 0),
                str(row.get("candidate_subject_id") or ""),
            ),
        )
        reviewable_pairs = _unique_named_gain_pairs(rows)
        preselected_pairs = _unique_named_gain_pairs(independently_required.get(team, []))
        unreviewable_pairs = _unique_named_gain_pairs(unreviewable.get(team, []))
        reviewable_debt = len(reviewable_pairs | preselected_pairs)
        unreviewable_debt = len(unreviewable_pairs)
        unreviewable_reason_counts = Counter(
            str(row.get("coverage_non_actionable_reason") or "unknown")
            for row in unreviewable.get(team, [])
        )
        unreviewable_observations_by_reason: Counter[str] = Counter()
        for row in unreviewable.get(team, []):
            unreviewable_observations_by_reason[
                str(row.get("coverage_non_actionable_reason") or "unknown")
            ] += int(row.get("potential_named_observation_gain") or 0)
        current_named = int(team_coverage.get("confirmed_named_observations") or 0)
        target_named = (
            target_named_observations(
                reliable,
                COMPLETE_ROSTER_NAMED_TARGET_RATIO,
            )
            if scope == COMPLETE_ROSTER
            else None
        )
        residual_budget = (
            reliable - target_named
            if target_named is not None
            else round(reliable * (1.0 - REVIEWED_OBSERVATION_TARGET_RATIO))
        )
        required_gain = (
            max(0, target_named - current_named)
            if target_named is not None
            else 0
            if scope == TEAM_STATS_ONLY
            else max(0, reviewable_debt + unreviewable_debt - residual_budget)
        )
        remaining_required_gain = max(0, required_gain - len(preselected_pairs))
        selected_rows, selected_pairs = _select_required_coverage_cases(
            rows,
            remaining_required_gain,
            existing_pairs=preselected_pairs,
        )
        for row in selected_rows:
            coverage_blockers.append(row)
        selected_gain = len(selected_pairs)
        selected_total_gain = len(preselected_pairs | selected_pairs)
        selected_count = len(selected_rows)
        residual = max(
            0,
            len(reviewable_pairs | unreviewable_pairs | preselected_pairs) - selected_total_gain,
        )
        residual_by_team[team] = {
            "scope": scope,
            "named_player_review_required": scope != TEAM_STATS_ONLY,
            "team_stats_required": True,
            "reliable_observations": reliable,
            "current_named_observations": current_named,
            "target_named_observations": target_named,
            "required_named_gain": required_gain,
            "independently_required_named_gain": len(preselected_pairs),
            "remaining_required_named_gain": remaining_required_gain,
            "available_actionable_named_gain": reviewable_debt,
            "selected_required_named_gain": selected_total_gain,
            "remaining_uncovered_named_gain": max(0, required_gain - selected_total_gain),
            "nonactionable_or_unavailable_gap": max(0, required_gain - reviewable_debt),
            "unreviewed_unnamed_observations": len(reviewable_pairs | unreviewable_pairs | preselected_pairs),
            "selected_coverage_gain": selected_gain,
            "residual_unreviewed_observations": residual,
            "residual_unreviewed_ratio": _ratio(residual, reliable),
            "residual_budget_observations": residual_budget,
            "coverage_cases": selected_count,
            "low_impact_reviewable_units": max(0, len(rows) - selected_count),
            "low_impact_reviewable_observations": max(
                0,
                max(0, len(reviewable_pairs) - selected_gain),
            ),
            "unreviewable_observations": unreviewable_debt,
            "unreviewable_units": len(unreviewable.get(team, [])),
            "unreviewable_reason_counts": dict(sorted(unreviewable_reason_counts.items())),
            "unreviewable_observations_by_reason": dict(
                sorted(unreviewable_observations_by_reason.items())
            ),
            "optional_audit_cases": len(optional_audit.get(team, [])),
            "non_actionable_required_team_uncertainty_units": len(
                non_actionable_team_uncertainty.get(team, [])
            ),
            "non_actionable_required_team_uncertainty_observations": sum(
                int(row.get("detected_observation_count") or 0)
                for row in non_actionable_team_uncertainty.get(team, [])
            ),
            "non_actionable_required_team_uncertainty_cases": [
                {
                    "candidate_subject_id": row.get("candidate_subject_id"),
                    "detected_observation_count": int(
                        row.get("detected_observation_count") or 0
                    ),
                    "coverage_team_label": row.get("coverage_team_label"),
                    "effective_team_label": row.get("effective_team_label"),
                    "reason_codes": list(row.get("reason_codes") or []),
                }
                for row in sorted(
                    non_actionable_team_uncertainty.get(team, []),
                    key=lambda value: (
                        -int(value.get("detected_observation_count") or 0),
                        str(value.get("candidate_subject_id") or ""),
                    ),
                )
            ],
        }

    semantic_sorted = sorted(
        semantic,
        key=lambda unit: (
            0 if "conflict" in " ".join(unit.get("reason_codes") or []) else 1,
            -int(unit.get("detected_observation_count") or 0),
            -float(unit.get("detected_time_sec") or 0.0),
            str(unit.get("candidate_subject_id") or ""),
        ),
    )
    coverage_sorted = sorted(
        coverage_blockers,
        key=lambda unit: (
            -int(unit.get("potential_named_observation_gain") or 0),
            -float(unit.get("detected_time_sec") or 0.0),
            str(unit.get("candidate_subject_id") or ""),
        ),
    )
    material_sorted = sorted(
        material_continuity,
        key=lambda unit: (
            -float(unit.get("continuity_span_sec") or unit.get("detected_time_sec") or 0.0),
            -int(unit.get("potential_named_observation_gain") or 0),
            str(unit.get("candidate_subject_id") or ""),
        ),
    )
    optional_sorted = sorted(
        [row for rows in optional_audit.values() for row in rows],
        key=lambda unit: (
            -int(unit.get("detected_observation_count") or 0),
            -float(unit.get("detected_time_sec") or 0.0),
            str(unit.get("candidate_subject_id") or ""),
        ),
    )
    workload_count = len(semantic_sorted) + len(material_sorted) + len(coverage_sorted)
    readiness = _readiness(
        coverage,
        residual_by_team,
        match_doc,
        semantic_count=len(semantic_sorted),
        material_continuity_count=len(material_sorted),
        coverage_count=len(coverage_sorted),
        non_actionable_team_uncertainty=non_actionable_team_uncertainty,
    )
    return {
        "next_cases": [*semantic_sorted, *material_sorted, *coverage_sorted],
        "optional_audit_cases": optional_sorted,
        "semantic_blockers": len(semantic_sorted),
        "material_continuity_blockers": len(material_sorted),
        "coverage_blockers": len(coverage_sorted),
        "residual_by_team": residual_by_team,
        "readiness": readiness,
        "workload": {
            "remaining_cases": workload_count,
            "level": workload_level(workload_count),
            "diagnostic_only": True,
            "queue_truncated": False,
        },
        "optional_audit": {
            "remaining_cases": len(optional_sorted),
            "blocking": False,
            "per_team": {
                team: len(rows) for team, rows in sorted(optional_audit.items())
            },
        },
    }


def paginate_progress(
    progress: dict[str, Any],
    *,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    team_label: str | None = None,
    queue: str = "required",
) -> dict[str, Any]:
    if queue not in {"required", "optional_audit"}:
        raise ValueError("queue must be required or optional_audit")
    source_key = "next_cases" if queue == "required" else "optional_audit_cases"
    cases = list(progress.get(source_key) or [])
    active_team_label = _validated_filter_team_label(team_label)
    classified_cases = [
        (unit, review_case_team_label(unit))
        for unit in cases
    ]
    filter_counts = Counter(label for _, label in classified_cases)
    filtered_cases = [
        unit
        for unit, label in classified_cases
        if active_team_label is None or label == active_team_label
    ]
    public_progress = {
        key: value
        for key, value in progress.items()
        if key not in {
            "review_units",
            "deferred_correction_context",
            "next_cases",
            "optional_audit_cases",
        }
    }
    safe_offset = max(0, int(offset))
    safe_limit = min(MAX_PAGE_SIZE, max(1, int(limit)))
    page = filtered_cases[safe_offset : safe_offset + safe_limit]
    return {
        **public_progress,
        "queue": queue,
        "next_cases": [
            {**unit, "filter_team_label": review_case_team_label(unit)}
            for unit in page
        ],
        "filters": {
            "queue": queue,
            "active_team_label": active_team_label,
            "counts": {
                "all": len(cases),
                "A": filter_counts["A"],
                "B": filter_counts["B"],
                "U": filter_counts["U"],
            },
        },
        "pagination": {
            "offset": safe_offset,
            "limit": safe_limit,
            "returned": len(page),
            "total_remaining": len(filtered_cases),
            "global_total_remaining": len(cases),
            "has_more": safe_offset + len(page) < len(filtered_cases),
        },
    }


def review_case_team_label(unit: dict[str, Any]) -> str:
    """Return the authoritative navigation team without changing case meaning."""
    for field in (
        "coverage_team_label",
        "effective_team_label",
        "source_team_label",
    ):
        team = str(unit.get(field) or "").upper()
        if team in {"A", "B"}:
            return team
    return "U"


def _validated_filter_team_label(team_label: str | None) -> str | None:
    if team_label is None:
        return None
    normalized = str(team_label)
    if normalized not in {"A", "B"}:
        raise ValueError("team_label must be A or B")
    return normalized


def roster_scope(match_doc: dict[str, Any], team_label: str) -> str:
    return team_review_scope(match_doc, team_label)


def workload_level(case_count: int) -> str:
    if case_count >= WORKLOAD_THRESHOLDS["critical"]:
        return "critical"
    if case_count >= WORKLOAD_THRESHOLDS["excessive"]:
        return "excessive"
    if case_count >= WORKLOAD_THRESHOLDS["elevated"]:
        return "elevated"
    return "normal"


def _coverage_impact(
    unit: dict[str, Any],
    pair_index: dict[tuple[str, int], dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    pairs = {
        (str(pair[0]), int(pair[1]))
        for pair in unit.get("detected_pairs") or []
        if isinstance(pair, (list, tuple)) and len(pair) >= 2
    }
    rows = [pair_index[pair] for pair in pairs if pair in pair_index]
    team_counts = Counter(_team_label(row.get("team_label")) for row in rows)
    team = (
        team_counts.most_common(1)[0][0]
        if team_counts
        else _team_label(unit.get("effective_team_label"))
    )
    named_gain_pairs = {
        pair
        for pair in pairs
        if pair in pair_index
        and _team_label(pair_index[pair].get("team_label")) == team
        and str(pair_index[pair].get("identity_status") or "unresolved")
        in RELIABLE_STATUSES
        and not pair_index[pair].get("canonical_player_id")
    }
    unnamed = len(named_gain_pairs)
    reliable = int(
        ((coverage.get("per_team") or {}).get(team) or {}).get(
            "reliable_observations"
        )
        or 0
    )
    current_named = int(
        ((coverage.get("per_team") or {}).get(team) or {}).get(
            "confirmed_named_observations"
        )
        or 0
    )
    return {
        **unit,
        "coverage_team_label": team,
        "potential_named_observation_gain": unnamed,
        "_potential_named_observation_pairs": named_gain_pairs,
        "potential_team_unnamed_share": _ratio(unnamed, reliable),
        "potential_named_coverage_gain_pp": round(100.0 * unnamed / reliable, 2)
        if reliable
        else 0.0,
        "named_coverage_before": _ratio(current_named, reliable),
        "named_coverage_after_max": _ratio(current_named + unnamed, reliable),
    }


def target_named_observations(reliable_observations: int, target_ratio: float) -> int:
    """Smallest integer count that satisfies ``named / reliable >= target``."""
    reliable = max(0, int(reliable_observations))
    if reliable == 0:
        return 0
    ratio = Decimal(str(target_ratio))
    return int((Decimal(reliable) * ratio).to_integral_value(rounding=ROUND_CEILING))


def _unique_named_gain_pairs(rows: Iterable[dict[str, Any]]) -> set[tuple[str, int]]:
    return {
        (str(pair[0]), int(pair[1]))
        for row in rows
        for pair in row.get("_potential_named_observation_pairs") or set()
    }


def _select_required_coverage_cases(
    rows: list[dict[str, Any]],
    required_gain: int,
    *,
    existing_pairs: set[tuple[str, int]] | None = None,
) -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
    if required_gain <= 0:
        return [], set()
    selected_rows: list[dict[str, Any]] = []
    selected_pairs: set[tuple[str, int]] = set()
    accounted_pairs: set[tuple[str, int]] = set(existing_pairs or set())
    for row in rows:
        gain_pairs = set(row.get("_potential_named_observation_pairs") or set())
        marginal_pairs = gain_pairs - accounted_pairs
        if not marginal_pairs:
            continue
        selected_pairs.update(marginal_pairs)
        accounted_pairs.update(marginal_pairs)
        row["coverage_rank_within_team"] = len(selected_rows) + 1
        row["marginal_named_observation_gain"] = len(marginal_pairs)
        row["cumulative_selected_named_gain"] = len(accounted_pairs)
        row["current_resolution_status"] = "pending_coverage_review"
        row["priority"] = "coverage"
        row["reason_codes"] = sorted(
            set(row.get("reason_codes") or []) | {"significant_named_coverage_debt"}
        )
        selected_rows.append(row)
        if len(selected_pairs) >= required_gain:
            break
    return selected_rows, selected_pairs


def _has_explicit_disposition(unit: dict[str, Any], match_doc: dict[str, Any]) -> bool:
    decision = unit.get("current_decision") or {}
    action = str(decision.get("action") or "")
    if not action:
        return False
    if action == "assign_roster_player":
        return True
    team = _team_label(unit.get("effective_team_label"))
    return roster_scope(match_doc, team) != "complete_roster"


def _readiness(
    coverage: dict[str, Any],
    residual_by_team: dict[str, dict[str, Any]],
    match_doc: dict[str, Any],
    *,
    semantic_count: int,
    material_continuity_count: int,
    coverage_count: int,
    non_actionable_team_uncertainty: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    if semantic_count:
        blockers.append({"code": "semantic_identity_conflicts", "count": semantic_count})
    if material_continuity_count:
        blockers.append(
            {
                "code": "material_identity_continuity_gap",
                "count": material_continuity_count,
            }
        )
    if coverage_count:
        blockers.append({"code": "significant_named_coverage_debt", "count": coverage_count})
    for team, units in non_actionable_team_uncertainty.items():
        if units:
            evidence_status_counts = Counter(
                str(unit.get("team_attribution_evidence_status") or "no_safe_visual_evidence")
                for unit in units
            )
            blockers.append(
                {
                    "code": "team_attribution_evidence_unavailable",
                    "team_label": team,
                    "units": len(units),
                    "observations": sum(
                        int(unit.get("detected_observation_count") or 0)
                        for unit in units
                    ),
                    "evidence_status_counts": dict(sorted(evidence_status_counts.items())),
                }
            )
    for team, row in residual_by_team.items():
        scope = team_review_scope(match_doc, team)
        if scope == TEAM_STATS_ONLY:
            continue
        remaining_gap = int(row.get("remaining_uncovered_named_gain") or 0)
        available_gain = int(row.get("available_actionable_named_gain") or 0)
        if scope == COMPLETE_ROSTER and remaining_gap > 0:
            blockers.append(
                {
                    "code": "complete_roster_named_coverage_gap_unreachable",
                    "team_label": team,
                    "target_named_observations": row.get("target_named_observations"),
                    "current_named_observations": row.get("current_named_observations"),
                    "required_named_gain": row.get("required_named_gain"),
                    "available_actionable_named_gain": available_gain,
                    "selected_required_named_gain": row.get(
                        "selected_required_named_gain"
                    ),
                    "remaining_uncovered_named_gain": remaining_gap,
                    "unreviewable_units": row.get("unreviewable_units"),
                    "reason_counts": row.get("unreviewable_reason_counts"),
                    "observations_by_reason": row.get(
                        "unreviewable_observations_by_reason"
                    ),
                }
            )
        elif int(row.get("unreviewable_observations") or 0) > int(
            row.get("residual_budget_observations") or 0
        ):
            blockers.append(
                {
                    "code": "coverage_evidence_unavailable",
                    "team_label": team,
                    "observations": row.get("unreviewable_observations"),
                    "unreviewable_units": row.get("unreviewable_units"),
                    "reason_counts": row.get("unreviewable_reason_counts"),
                    "observations_by_reason": row.get(
                        "unreviewable_observations_by_reason"
                    ),
                }
            )
    # Coverage debt is represented by the selected review units and any
    # unavailable residual above. Do not separately declare a raw gap
    # unreachable while an independently-required material case can supply its
    # unique named observations after the operator makes one choice.
    reliable = int(coverage.get("reliable_observations") or 0)
    scopes = {
        team: roster_scope(match_doc, team)
        for team in (coverage.get("per_team") or {})
        if team in {"A", "B"}
    }
    status = (
        "not_assessable"
        if reliable <= 0
        else "incomplete"
        if blockers
        else "ready"
        if scopes and all(value == "complete_roster" for value in scopes.values())
        else "ready_with_review"
    )
    return {
        "status": status,
        "policy_version": COVERAGE_POLICY_VERSION,
        "coverage_unit": COVERAGE_UNIT,
        "reviewed_observation_target_ratio": REVIEWED_OBSERVATION_TARGET_RATIO,
        "complete_roster_named_target_ratio": COMPLETE_ROSTER_NAMED_TARGET_RATIO,
        "roster_scope": scopes,
        "identity_review_scope": identity_review_scope_read_model(match_doc),
        "blockers": blockers,
        "allows_finalize": status in {"ready", "ready_with_review"},
    }


def _coverage_row(
    counts: Counter[str],
    scope: str,
    *,
    team_label: str | None = None,
) -> dict[str, Any]:
    reliable = sum(counts[status] for status in RELIABLE_STATUSES)
    confirmed = counts["confirmed_named"]
    team_known = (
        reliable
        if team_label in {"A", "B"}
        else 0
        if team_label == "U"
        else sum(
            value
            for status, value in counts.items()
            if status not in IGNORED_STATUSES
        )
    )
    unresolved = counts["unresolved"] + counts["team_unknown"]
    conflicted = counts["conflicted"] + counts["blocked"]
    ignored = sum(counts[status] for status in IGNORED_STATUSES)
    return {
        "scope": scope,
        "roster_scope": scope,
        "named_player_review_required": scope != TEAM_STATS_ONLY,
        "team_stats_required": True,
        "named_coverage_status": (
            "not_required_by_scope" if scope == TEAM_STATS_ONLY else "required"
        ),
        "reliable_observations": reliable,
        "confirmed_named_observations": confirmed,
        "named_observation_coverage": _ratio(confirmed, reliable),
        "team_known_observations": team_known,
        "team_known_observation_coverage": _ratio(team_known, reliable),
        "unresolved_observations": unresolved,
        "conflicted_observations": conflicted,
        "ignored_observations": ignored,
        "unresolved_observation_share": _ratio(unresolved + conflicted, reliable),
    }


def _coverage_teams(
    match_doc: dict[str, Any], counts: dict[str, Counter[str]]
) -> list[str]:
    labels = {_team_label(value) for value in counts}
    labels.update(
        _team_label(row.get("team_label") or row.get("label"))
        for row in match_doc.get("teams") or []
    )
    labels.discard("")
    return sorted(labels or {"A", "B"})


def _team_label(value: Any) -> str:
    normalized = str(value or "U").upper()
    return normalized if normalized in {"A", "B"} else "U"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _has_team_uncertainty(unit: dict[str, Any]) -> bool:
    if _team_label(unit.get("effective_team_label")) == "U":
        return True
    markers = (
        "conflict",
        "contradict",
        "cross_team",
        "team_mismatch",
    )
    return any(
        marker in str(reason).lower()
        for reason in unit.get("reason_codes") or []
        for marker in markers
    )
