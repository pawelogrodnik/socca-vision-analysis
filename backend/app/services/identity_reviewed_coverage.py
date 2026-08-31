from __future__ import annotations

"""Canonical named-identity coverage and coverage-review policy.

The module deliberately separates observed identity coverage from operator
queue completion. A partial roster can acknowledge an anonymous subject
without turning it into named-player data.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
from typing import Any, Iterable

from app.services.identity_ownership_compact import (
    CompactPairIndexView,
    count_pair_runs,
    encode_pair_runs,
    runs_difference,
    runs_union,
)
from app.services.identity_canonical_io import load_json_cached
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
from app.services.identity_reviewed_scope_eligibility import (
    has_team_attribution_uncertainty,
    required_review_relevant_for_scope,
    unit_team_label,
)
from app.services.identity_reviewed_team_attribution_evidence import (
    classify_team_attribution_evidence_status,
    normalized_team_attribution_evidence_status,
)
from app.services.play_area import is_on_pitch_product_observation


COVERAGE_SCHEMA_VERSION = "1.0.0"
COVERAGE_POLICY_VERSION = "coverage-driven-review:v11-fail-closed-evidence-status"
OPTIONAL_MAX_POLICY_VERSION = "optional-reviewed-identity-max:v3-authoritative-roster-projection"
COVERAGE_DEBT_POLICY_VERSION = "reviewed-identity-coverage-debt:v2-queue-observability"
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
MIXED_UNRESOLVED_STATUSES = frozenset({"unresolved", "unresolved_complex_mix"})


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
    pair_index: Mapping[tuple[str, int], dict[str, Any]],
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    """Rank every meaningful unreviewed identity unit without a case cap."""
    semantic_candidates = [
        unit
        for unit in units
        if unit.get("current_resolution_status") == "pending_high_priority"
        and unit.get("operator_actionable") is not False
    ]
    semantic = [
        enriched
        for unit in semantic_candidates
        if required_review_relevant_for_scope(
            enriched := _coverage_impact(unit, pair_index, coverage),
            match_doc,
        )
    ]
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    material_continuity: list[dict[str, Any]] = []
    # This queue is intentionally not an opponent-name review.  It is the
    # optional, Team-A-only full audit available after the required gate is
    # already safe to finalize.
    optional_max_candidates: list[dict[str, Any]] = []
    optional_max_unavailable: list[dict[str, Any]] = []
    unreviewable: dict[str, list[dict[str, Any]]] = defaultdict(list)
    non_actionable_team_uncertainty: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        if unit in semantic_candidates or _has_explicit_disposition(unit, match_doc):
            continue
        # A raw subject may have a diagnostic card while every observation is
        # outside the product play area.  It cannot become a team-attribution
        # task because it contributes no player-facing observation at all.
        if not unit.get("detected_pairs") and not unit.get("detected_pair_runs"):
            continue
        enriched = _coverage_impact(unit, pair_index, coverage)
        team = _team_label(enriched.get("coverage_team_label"))
        operator_actionable = enriched.get("operator_actionable") is not False
        # A Team-U or cross-team fragment must be resolved before named-player
        # coverage is considered.  It is a team-attribution decision, not a
        # request to name a player for the wrong roster scope.
        if has_team_attribution_uncertainty(enriched):
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
            if not required_review_relevant_for_scope(enriched, match_doc):
                continue
            enriched["current_resolution_status"] = "pending_material_continuity_review"
            enriched["priority"] = "continuity"
            enriched["reason_codes"] = sorted(
                set(enriched.get("reason_codes") or [])
                | {"material_identity_continuity_gap"}
            )
            material_continuity.append(enriched)
            continue
        if not required_review_relevant_for_scope(enriched, match_doc):
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
            and not has_team_attribution_uncertainty(enriched)
            and int(enriched.get("potential_named_observation_gain") or 0) > 0
        ):
            independently_required[team].append(enriched)
    for team in sorted(
        set((coverage.get("per_team") or {}).keys())
        | set(candidates)
        | set(unreviewable)
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
            "optional_audit_cases": 0,
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
                    "scope_kind": row.get("scope_kind"),
                    "review_target_id": row.get("review_target_id"),
                    "continuity_group_id": row.get("continuity_group_id"),
                    "source_ownership_digest": row.get("source_ownership_digest"),
                    "team_attribution_evidence_source_digest": row.get(
                        "team_attribution_evidence_source_digest"
                    ),
                    "detected_observation_count": int(
                        row.get("detected_observation_count") or 0
                    ),
                    "coverage_team_label": row.get("coverage_team_label"),
                    "effective_team_label": row.get("effective_team_label"),
                    "reason_codes": list(row.get("reason_codes") or []),
                    "team_attribution_evidence_status": row.get(
                        "team_attribution_evidence_status"
                    ),
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
    required_keys = {_unit_key(unit) for unit in [*semantic_sorted, *material_sorted, *coverage_sorted]}
    deferred_named_pairs = _deferred_named_gain_pairs(units, pair_index, match_doc)
    for unit in units:
        enriched = _coverage_impact(unit, pair_index, coverage)
        if _optional_max_ineligible(enriched, match_doc, required_keys):
            action = str((enriched.get("current_decision") or {}).get("action") or "")
            if action and action != "assign_roster_player":
                optional_max_unavailable.append(enriched)
            continue
        if int(enriched.get("potential_named_observation_gain") or 0) <= 0:
            continue
        if enriched.get("operator_actionable") is not False and enriched.get("has_operator_visual_evidence"):
            optional_max_candidates.append(enriched)
        else:
            optional_max_unavailable.append(enriched)
    optional_sorted = _rank_optional_max_cases(
        optional_max_candidates,
        accounted_pairs=deferred_named_pairs,
        reliable_observations=int(
            ((coverage.get("per_team") or {}).get("A") or {}).get(
                "reliable_observations"
            )
            or 0
        ),
    )
    optional_audit = _optional_max_summary(
        coverage,
        optional_sorted,
        optional_max_unavailable,
        readiness,
        match_doc,
        deferred_named_pairs,
        pair_index,
    )
    for team, residual in residual_by_team.items():
        residual["optional_audit_cases"] = (
            len(optional_sorted) if team == "A" else 0
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
        "optional_audit": optional_audit,
    }


def build_coverage_debt(
    units: list[dict[str, Any]],
    coverage: dict[str, Any],
    pair_index: Mapping[tuple[str, int], dict[str, Any]],
    match_doc: dict[str, Any],
    coverage_policy: dict[str, Any],
    mixed_players: dict[str, Any],
) -> dict[str, Any]:
    """Explain current unnamed coverage using one non-overlapping partition.

    This is deliberately a read-model projection: Required and Optional/MAX
    keep their existing policy authority. Compact pair-index inputs stay as
    interval runs on the hot path; legacy mapping fixtures use the compatible
    explicit-pair fallback.
    """
    unnamed_by_team = _unnamed_reliable_runs_by_team(pair_index)
    debt_teams = set(coverage.get("per_team") or {}) | set(unnamed_by_team)
    remaining_by_team: dict[str, dict[str, list[list[int]]]] = {
        team: runs for team, runs in unnamed_by_team.items() if team in {"A", "B"}
    }
    assigned_by_team: dict[str, dict[str, list[list[int]]]] = {
        team: {} for team in debt_teams if team in {"A", "B"}
    }
    bucket_cases: dict[str, dict[str, set[str]]] = {
        team: defaultdict(set) for team in assigned_by_team
    }
    bucket_reasons: dict[str, Counter[str]] = {
        team: Counter() for team in assigned_by_team
    }
    actual_required_queue = _actual_required_queue(
        coverage_policy.get("next_cases") or [],
        match_doc,
    )

    def claim(
        team: str,
        bucket: str,
        runs: dict[str, list[list[int]]],
        case_id: str | None = None,
    ) -> dict[str, list[list[int]]]:
        if team not in assigned_by_team:
            return {}
        claimed = _runs_intersection(remaining_by_team.get(team, {}), runs)
        if not claimed:
            return {}
        remaining_by_team[team] = runs_difference(
            remaining_by_team.get(team, {}), claimed,
        )
        assigned_by_team[team] = runs_union(assigned_by_team[team], claimed)
        bucket_runs[team][bucket] = runs_union(bucket_runs[team][bucket], claimed)
        if case_id:
            bucket_cases[team][bucket].add(case_id)
        return claimed

    bucket_runs: dict[str, dict[str, dict[str, list[list[int]]]]] = {
        team: {
            name: {} for name in (
                "committed_pending", "required", "mixed", "optional_max", "unavailable"
            )
        }
        for team in assigned_by_team
    }
    required_breakdown_runs: dict[str, dict[str, dict[str, list[list[int]]]]] = {
        team: {"semantic": {}, "continuity": {}, "coverage": {}}
        for team in assigned_by_team
    }
    required_breakdown_cases: dict[str, dict[str, set[str]]] = {
        team: {"semantic": set(), "continuity": set(), "coverage": set()}
        for team in assigned_by_team
    }
    roster_teams = _authoritative_roster_teams(match_doc)
    for unit in units:
        decision = unit.get("current_decision") or {}
        if str(decision.get("action") or "") != "assign_roster_player":
            continue
        team = roster_teams.get(str(decision.get("player_id") or ""))
        if team not in {"A", "B"}:
            continue
        if team_review_scope(match_doc, team) == TEAM_STATS_ONLY:
            continue
        claim(
            team,
            "committed_pending",
            _unnamed_runs_for_unit(unit, unnamed_by_team.get(team, {})),
            _unit_key(unit),
        )

    for unit in coverage_policy.get("next_cases") or []:
        team = _required_debt_team(unit)
        if team not in assigned_by_team:
            continue
        scope = team_review_scope(match_doc, team)
        if scope == TEAM_STATS_ONLY and not _is_team_stats_required_safety_case(unit):
            continue
        kind = _required_debt_kind(unit)
        case_key = _required_queue_key(unit)
        # Required queue workload is a source-count fact, not a marginal
        # observation-gain fact. A safety card with zero new pairs remains a
        # real decision and must stay visible.
        bucket_cases[team]["required"].add(case_key)
        required_breakdown_cases[team][kind].add(case_key)
        claimed = claim(team, "required", _unit_gain_runs(unit))
        if claimed:
            required_breakdown_runs[team][kind] = runs_union(
                required_breakdown_runs[team][kind], claimed,
            )

    ambiguous_cases: set[str] = set()
    ambiguous_raw_marker_observations = 0
    ambiguous_runs_by_team: dict[str, dict[str, list[list[int]]]] = {"A": {}, "B": {}}

    def reserve_ambiguous(team: str, runs: dict[str, list[list[int]]]) -> None:
        reserved = _runs_intersection(remaining_by_team.get(team, {}), runs)
        if not reserved:
            return
        remaining_by_team[team] = runs_difference(remaining_by_team[team], reserved)
        ambiguous_runs_by_team[team] = runs_union(ambiguous_runs_by_team[team], reserved)

    for marker in (mixed_players or {}).get("cases") or []:
        if str(marker.get("resolution_status") or "") not in MIXED_UNRESOLVED_STATUSES:
            continue
        source = marker.get("source")
        case_id = str(marker.get("case_id") or marker.get("candidate_subject_id") or "")
        if not isinstance(source, dict):
            ambiguous_cases.add(case_id)
            ambiguous_raw_marker_observations += int(marker.get("observation_count") or 0)
            continue
        source_runs = encode_pair_runs(
            (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
            for row in source.get("owned_observations") or []
            if isinstance(row, dict) and row.get("tracklet_id") is not None and row.get("frame") is not None
        )
        if not source_runs:
            ambiguous_cases.add(case_id)
            ambiguous_raw_marker_observations += int(marker.get("observation_count") or 0)
            continue
        by_team = {
            team: _runs_intersection(source_runs, unnamed_by_team.get(team, {}))
            for team in ("A", "B")
        }
        located_teams = [team for team, runs in by_team.items() if runs]
        if str(marker.get("mixed_hint") or "") == "cross_team" or len(located_teams) != 1:
            ambiguous_cases.add(case_id)
            ambiguous_raw_marker_observations += int(marker.get("observation_count") or 0)
            for team, exact in by_team.items():
                reserve_ambiguous(team, exact)
            continue
        located = False
        for team in ("A", "B"):
            exact = by_team[team]
            if exact:
                if team_review_scope(match_doc, team) == TEAM_STATS_ONLY:
                    located = True
                    continue
                claim(team, "mixed", exact, case_id)
                located = True
        if not located:
            ambiguous_cases.add(case_id)
            ambiguous_raw_marker_observations += int(marker.get("observation_count") or 0)

    # This is intentionally the same ranked list and marginal-gain semantics
    # used by optional_audit; the partition merely removes earlier buckets.
    for unit in coverage_policy.get("optional_audit_cases") or []:
        if team_review_scope(match_doc, "A") != TEAM_STATS_ONLY:
            claim("A", "optional_max", _unit_gain_runs(unit), _unit_key(unit))

    optional = coverage_policy.get("optional_audit") or {}
    for team, unnamed in unnamed_by_team.items():
        if team not in bucket_runs:
            # Team U has no safe per-team denominator. It stays outside the
            # A/B debt partition rather than being silently attributed.
            continue
        remaining = remaining_by_team.get(team, {})
        if remaining:
            if team_review_scope(match_doc, team) == TEAM_STATS_ONLY:
                continue
            bucket_runs[team]["unavailable"] = remaining
            assigned_by_team[team] = runs_union(assigned_by_team[team], remaining)
            if team == "A":
                optional_reasons = {
                    str(reason): int(count)
                    for reason, count in (optional.get("unavailable_reason_counts") or {}).items()
                }
                if sum(optional_reasons.values()) == count_pair_runs(remaining):
                    bucket_reasons[team].update(optional_reasons)
            if not bucket_reasons[team]:
                bucket_reasons[team]["other_no_safe_path"] = count_pair_runs(remaining)

    per_team: dict[str, Any] = {}
    for team in sorted(assigned_by_team):
        row = (coverage.get("per_team") or {}).get(team) or {}
        reliable = int(row.get("reliable_observations") or 0)
        named = int(row.get("confirmed_named_observations") or 0)
        unnamed = count_pair_runs(unnamed_by_team.get(team, {}))
        buckets = {
            name: {
                "case_count": len(bucket_cases[team][name]),
                "unique_observations": count_pair_runs(runs),
                "share_of_reliable": _ratio(count_pair_runs(runs), reliable),
                "coverage_pp": 100.0 * count_pair_runs(runs) / reliable if reliable else 0.0,
                **(
                    {"reason_counts": dict(sorted(bucket_reasons[team].items()))}
                    if name == "unavailable" else {}
                ),
            }
            for name, runs in bucket_runs[team].items()
        }
        scope = team_review_scope(match_doc, team)
        not_required_runs = remaining_by_team.get(team, {}) if scope == TEAM_STATS_ONLY else {}
        ambiguous_current = count_pair_runs(ambiguous_runs_by_team.get(team, {}))
        not_required = {
            "unique_observations": count_pair_runs(not_required_runs),
            "share_of_reliable": _ratio(count_pair_runs(not_required_runs), reliable),
            "coverage_pp": 100.0 * count_pair_runs(not_required_runs) / reliable if reliable else 0.0,
        }
        required_breakdown = {
            kind: {
                "case_count": len(required_breakdown_cases[team][kind]),
                "unique_observations": count_pair_runs(runs),
                "coverage_pp": 100.0 * count_pair_runs(runs) / reliable if reliable else 0.0,
            }
            for kind, runs in required_breakdown_runs[team].items()
        }
        buckets["required"]["breakdown"] = required_breakdown
        accounted = sum(int(value["unique_observations"]) for value in buckets.values())
        accounted_with_scope = (
            accounted
            + int(not_required["unique_observations"])
            + ambiguous_current
        )
        target = (
            COMPLETE_ROSTER_NAMED_TARGET_RATIO if scope == COMPLETE_ROSTER else None
        )
        target_count = target_named_observations(reliable, target) if target is not None else None
        committed = buckets["committed_pending"]["unique_observations"]
        per_team[team] = {
            "scope": scope,
            "reliable_observations": reliable,
            "current_named_observations": named,
            "current_named_coverage": _ratio(named, reliable),
            "target_named_coverage": target,
            "target_named_observations": target_count,
            "target_gap_observations": max(0, target_count - named) if target_count is not None else None,
            "target_gap_pp": (100.0 * max(0, target_count - named) / reliable) if target_count is not None and reliable else None,
            "projected_named_coverage_after_committed": _ratio(named + committed, reliable),
            "unnamed_observations": unnamed,
            "operator_identity_debt_observations": accounted,
            "not_required_by_scope": not_required,
            "ambiguous_mixed_currently_labeled_observations": ambiguous_current,
            "accounted_unnamed_observations": accounted_with_scope,
            "unaccounted_unnamed_observations": unnamed - accounted_with_scope,
            "buckets": buckets,
        }
    return {
        "policy_version": COVERAGE_DEBT_POLICY_VERSION,
        "coverage_unit": COVERAGE_UNIT,
        "accounting_precedence": [
            "committed_pending", "required", "mixed", "optional_max", "unavailable"
        ],
        "per_team": per_team,
        "actual_required_queue": actual_required_queue,
        "ambiguous": {
            "mixed_case_count": len(ambiguous_cases),
            "unique_current_reliable_observations": sum(
                count_pair_runs(runs) for runs in ambiguous_runs_by_team.values()
            ),
            "currently_labeled": {
                team: count_pair_runs(runs)
                for team, runs in ambiguous_runs_by_team.items()
            },
            "raw_marker_observations": ambiguous_raw_marker_observations,
            "note": "current labels are diagnostic only; unresolved Mixed ownership is not assigned to A or B",
        },
    }


def _unit_gain_runs(unit: dict[str, Any]) -> dict[str, list[list[int]]]:
    value = unit.get("_potential_named_observation_pairs")
    if isinstance(value, dict):
        return value
    return encode_pair_runs(value or [])


def _required_debt_kind(unit: dict[str, Any]) -> str:
    """Use queue policy fields, never client-side reason-string inference."""
    priority = str(unit.get("priority") or "")
    if priority == "continuity" or is_material_continuity_case(unit):
        return "continuity"
    if priority == "coverage":
        return "coverage"
    return "semantic"


def _required_queue_key(unit: dict[str, Any]) -> str:
    """Stable Required source identity, including exact ownership when present."""
    return "|".join(
        (
            str(unit.get("review_target_id") or ""),
            str(unit.get("candidate_subject_id") or ""),
            str(unit.get("scope_kind") or ""),
            str(unit.get("continuity_group_id") or ""),
            str(unit.get("source_ownership_digest") or ""),
        )
    )


def _actual_required_queue(
    units: list[dict[str, Any]],
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    """Actual queue workload, intentionally independent of debt pair claims."""
    per_team: dict[str, dict[str, Any]] = {
        team: {
            "total_cases": 0,
            "expected_by_scope": 0,
            "unexpected_by_scope": 0,
            "breakdown": {
                kind: {"case_count": 0}
                for kind in ("semantic", "continuity", "coverage")
            },
        }
        for team in ("A", "B", "U")
    }
    seen: set[str] = set()
    for unit in units:
        key = _required_queue_key(unit)
        if key in seen:
            continue
        seen.add(key)
        team = _required_debt_team(unit)
        row = per_team[team]
        kind = _required_debt_kind(unit)
        row["total_cases"] += 1
        row["breakdown"][kind]["case_count"] += 1
        expected = (
            team in {"A", "B"}
            and (
                team_review_scope(match_doc, team) != TEAM_STATS_ONLY
                or _is_team_stats_required_safety_case(unit)
            )
        )
        if expected:
            row["expected_by_scope"] += 1
        else:
            row["unexpected_by_scope"] += 1
    return {
        "total_cases": len(seen),
        # The same deduplicated source set is the normal blocking count for
        # this projection generation. Keeping it here makes reconciliation
        # explicit without inventing a second queue authority.
        "normal_blocking_case_count": len(seen),
        "per_team": per_team,
        "source": "coverage_policy.next_cases",
    }


def _required_debt_team(unit: dict[str, Any]) -> str:
    return unit_team_label(unit)


def _is_team_stats_required_safety_case(unit: dict[str, Any]) -> bool:
    """Only attribution/semantic safety work remains debt in team-only scope."""
    return _required_debt_kind(unit) == "semantic" and _has_team_uncertainty(unit)


def _unnamed_reliable_runs_by_team(
    pair_index: Mapping[tuple[str, int], dict[str, Any]],
) -> dict[str, dict[str, list[list[int]]]]:
    rows: dict[str, list[tuple[str, int]]] = defaultdict(list)
    if isinstance(pair_index, CompactPairIndexView):
        result: dict[str, dict[str, list[list[int]]]] = {"A": {}, "B": {}, "U": {}}
        for tracklet_id, entries in pair_index.tracklets().items():
            for start, end, value in entries:
                team = _team_label(value.get("team_label"))
                if str(value.get("identity_status") or "") in RELIABLE_STATUSES and not value.get("canonical_player_id"):
                    result.setdefault(team, {}).setdefault(tracklet_id, []).append([start, end])
        return result
    for (tracklet_id, frame), value in pair_index.items():
        if str(value.get("identity_status") or "") in RELIABLE_STATUSES and not value.get("canonical_player_id"):
            rows[_team_label(value.get("team_label"))].append((tracklet_id, frame))
    return {team: encode_pair_runs(pairs) for team, pairs in rows.items()}


def _unnamed_runs_for_unit(
    unit: dict[str, Any],
    unnamed_team_runs: dict[str, list[list[int]]],
) -> dict[str, list[list[int]]]:
    source = unit.get("detected_pair_runs") if isinstance(unit.get("detected_pair_runs"), dict) else encode_pair_runs(unit.get("detected_pairs") or [])
    return _runs_intersection(source, unnamed_team_runs)


def _runs_intersection(
    left: dict[str, list[list[int]]],
    right: dict[str, list[list[int]]],
) -> dict[str, list[list[int]]]:
    return runs_difference(left, runs_difference(left, right))


PUBLIC_REVIEW_CASE_FIELDS = (
    "candidate_subject_id",
    "review_target_id",
    "scope_kind",
    "continuity_group_id",
    "continuity_fragment_count",
    "review_card_key",
    "source_team_label",
    "effective_team_label",
    "coverage_team_label",
    "frame_start",
    "frame_end",
    "frame_ranges",
    "detected_frame_count",
    "detected_observation_count",
    "tracklet_ids",
    "tracklet_count",
    "detected_time_sec",
    "priority",
    "reason_codes",
    "current_resolution_status",
    "operator_actionable",
    "non_actionable_reason",
    "potential_named_observation_gain",
    "marginal_named_observation_gain",
    "potential_named_coverage_gain_pp",
    "coverage_rank_within_team",
    "optional_max_rank",
    "optional_max_marginal_coverage_gain_pp",
    "stable_slot_id",
    "source_ownership_digest",
    "visual_evidence",
    "legacy_suggestion",
)


def public_review_case(unit: dict[str, Any]) -> dict[str, Any]:
    """HTTP presentation of one queue card.

    Exact per-frame ownership and server-only gain sets stay behind the
    correction context endpoint; navigation needs none of them.
    """
    public = {key: unit.get(key) for key in PUBLIC_REVIEW_CASE_FIELDS}
    public["filter_team_label"] = _projected_filter_team_label(unit)
    return public


def compact_mixed_players_summary(mixed_players: Any) -> Any:
    """Strip exact ownership/evidence from the embedded mixed queue summary.

    The operator panel fetches full mixed cases from the dedicated endpoint;
    review-progress only needs counts and identity metadata for badges.
    """
    if not isinstance(mixed_players, dict):
        return mixed_players
    compact = {
        key: value
        for key, value in mixed_players.items()
        if key != "cases"
    }
    cases: list[dict[str, Any]] = []
    for case in mixed_players.get("cases") or []:
        if not isinstance(case, dict):
            continue
        source = case.get("source")
        cases.append({
            "case_id": case.get("case_id"),
            "candidate_subject_id": case.get("candidate_subject_id"),
            "original_issue": case.get("original_issue"),
            "mixed_hint": case.get("mixed_hint"),
            "resolution_status": case.get("resolution_status"),
            "observation_count": case.get("observation_count"),
            "updated_at": case.get("updated_at"),
            "has_exact_source": isinstance(source, dict),
        })
    compact["cases"] = cases
    return compact


# Backwards-compatible internal alias.
_compact_mixed_players_summary = compact_mixed_players_summary


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
        (unit, _projected_filter_team_label(unit))
        for unit in cases
    ]
    filter_counts = Counter(label for _, label in classified_cases)
    filtered_cases = [
        unit
        for unit, label in classified_cases
        if active_team_label is None or label == active_team_label
    ]
    public_progress = {
        key: _compact_mixed_players_summary(value) if key == "mixed_players" else value
        for key, value in progress.items()
        if key not in {
            "review_units",
            "_internal_review_units",
            "_projection_inputs",
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
        "next_cases": [public_review_case(unit) for unit in page],
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
    """Return the authoritative navigation team without changing case meaning.

    A current best-label must not make an A/B conflict look like ordinary Team
    B work.  Those cases are deliberately exposed through the explicit ``U``
    filter, where the operator can see that team attribution is the blocker.
    """
    if has_team_attribution_uncertainty(unit):
        return "U"
    for field in (
        "coverage_team_label",
        "effective_team_label",
        "source_team_label",
    ):
        team = str(unit.get(field) or "").upper()
        if team in {"A", "B"}:
            return team
    return "U"


def _projected_filter_team_label(unit: dict[str, Any]) -> str:
    """Use the full-unit classification retained by the hot queue projection."""
    projected = str(unit.get("filter_team_label") or "").upper()
    if projected in {"A", "B", "U"}:
        return projected
    return review_case_team_label(unit)


def _validated_filter_team_label(team_label: str | None) -> str | None:
    if team_label is None:
        return None
    normalized = str(team_label)
    if normalized not in {"A", "B", "U"}:
        raise ValueError("team_label must be A, B or U")
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
    pair_index: Mapping[tuple[str, int], dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    compact_runs = unit.get("detected_pair_runs")
    if isinstance(compact_runs, dict):
        team, named_gain_pairs, unnamed = _impact_from_compact_runs(
            compact_runs, pair_index
        )
    else:
        pairs = {
            (str(pair[0]), int(pair[1]))
            for pair in unit.get("detected_pairs") or []
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        }
        present_rows = [(pair, pair_index[pair]) for pair in pairs if pair in pair_index]
        team_counts = Counter(_team_label(row.get("team_label")) for _pair, row in present_rows)
        team = (
            team_counts.most_common(1)[0][0]
            if team_counts
            else _team_label(unit.get("effective_team_label"))
        )
        named_gain_pairs = {
            pair
            for pair, observation in present_rows
            if _team_label(observation.get("team_label")) == team
            and str(observation.get("identity_status") or "unresolved")
            in RELIABLE_STATUSES
            and not observation.get("canonical_player_id")
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


def _impact_from_compact_runs(
    detected_runs: dict[str, list[list[int]]],
    pair_index: "CompactPairIndexView | Mapping[tuple[str, int], dict[str, Any]]",
) -> tuple[str, dict[str, list[list[int]]], int]:
    """Evaluate unit impact via interval sweeps over validated index segments.

    Never materializes per-frame pairs: team attribution and the exact
    named-gain runs are computed from run intersections only.
    """
    segments: Mapping[str, list[list[Any]]] | None = (
        pair_index.tracklets()
        if isinstance(pair_index, CompactPairIndexView)
        else None
    )
    team_counts: Counter[str] = Counter()
    if segments is None:
        # Legacy mapping input: fall back to per-pair joins for this unit.
        pairs = {
            (tracklet_id, frame)
            for tracklet_id, tracklet_runs in detected_runs.items()
            for start, end in tracklet_runs
            for frame in range(start, end + 1)
        }
        present = [(pair, pair_index[pair]) for pair in pairs if pair in pair_index]
        for _pair, row in present:
            team_counts[_team_label(row.get("team_label"))] += 1
        majority = _majority_or(team_counts)
        gain_pairs = {
            pair
            for pair, row in present
            if _team_label(row.get("team_label")) == majority
            and str(row.get("identity_status") or "unresolved") in RELIABLE_STATUSES
            and not row.get("canonical_player_id")
        }
        return majority, encode_pair_runs(gain_pairs), len(gain_pairs)
    gain_intervals: dict[str, list[list[int]]] = {}
    unnamed_total = 0
    for tracklet_id, tracklet_runs in detected_runs.items():
        entries = segments.get(tracklet_id) or []
        entry_index = 0
        for start, end in tracklet_runs:
            while entry_index < len(entries) and entries[entry_index][1] < start:
                entry_index += 1
            walk = entry_index
            while walk < len(entries) and entries[walk][0] <= end:
                seg_start, seg_end, value = entries[walk]
                overlap_start = max(start, seg_start)
                overlap_end = min(end, seg_end)
                if overlap_start <= overlap_end:
                    team_counts[_team_label(value.get("team_label"))] += (
                        overlap_end - overlap_start + 1
                    )
                walk += 1
    majority = _majority_or(team_counts)
    # Mirror the legacy semantics exactly: gains are bound to the attributed
    # (majority) team, so collect them in a second filtered sweep.
    for tracklet_id, tracklet_runs in detected_runs.items():
        for start, end in tracklet_runs:
            for seg_start, seg_end, value in segments.get(tracklet_id) or []:
                if seg_end < start or seg_start > end:
                    continue
                if (
                    _team_label(value.get("team_label")) != majority
                    or str(value.get("identity_status") or "unresolved") not in RELIABLE_STATUSES
                    or value.get("canonical_player_id")
                ):
                    continue
                overlap_start = max(start, seg_start)
                overlap_end = min(end, seg_end)
                bucket = gain_intervals.setdefault(tracklet_id, [])
                if bucket and bucket[-1][1] + 1 == overlap_start:
                    bucket[-1][1] = overlap_end
                else:
                    bucket.append([overlap_start, overlap_end])
                unnamed_total += overlap_end - overlap_start + 1
    return majority, {tid: runs for tid, runs in sorted(gain_intervals.items())}, unnamed_total


def _majority_or(counts: Counter[str], fallback: str | None = None) -> str:
    if counts:
        return counts.most_common(1)[0][0]
    return fallback if fallback is not None else "U"


def target_named_observations(reliable_observations: int, target_ratio: float) -> int:
    """Smallest integer count that satisfies ``named / reliable >= target``."""
    reliable = max(0, int(reliable_observations))
    if reliable == 0:
        return 0
    ratio = Decimal(str(target_ratio))
    return int((Decimal(reliable) * ratio).to_integral_value(rounding=ROUND_CEILING))


def _gain_pairs_set(value: Any) -> set[tuple[str, int]]:
    """Materialize a gain payload as an explicit pair set.

    Gain payloads are compact run dicts for compact-sourced units and pair
    sets/lists for legacy ones.  Only accounting subsets touch this helper,
    so full expansion stays bounded to selected rows instead of the match.
    """
    if isinstance(value, dict):
        return {
            (tracklet_id, frame)
            for tracklet_id, tracklet_runs in value.items()
            for start, end in tracklet_runs
            for frame in range(start, end + 1)
        }
    return {(str(pair[0]), int(pair[1])) for pair in value or []}


def _unique_named_gain_pairs(rows: Iterable[dict[str, Any]]) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for row in rows:
        pairs |= _gain_pairs_set(row.get("_potential_named_observation_pairs"))
    return pairs


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
        gain_pairs = _gain_pairs_set(row.get("_potential_named_observation_pairs"))
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


def _unit_key(unit: dict[str, Any]) -> str:
    return str(unit.get("review_target_id") or unit.get("candidate_subject_id") or "")


def _optional_max_explicitly_unresolved(unit: dict[str, Any]) -> bool:
    return str((unit.get("current_decision") or {}).get("action") or "") == "unresolved"


def _optional_max_ineligible(
    unit: dict[str, Any],
    match_doc: dict[str, Any],
    required_keys: set[str],
) -> bool:
    """Keep MAX strictly separate from safety and required-review semantics."""
    team = _team_label(unit.get("coverage_team_label"))
    if (
        team != "A"
        or _team_label(unit.get("effective_team_label")) != "A"
        or team_review_scope(match_doc, team) != COMPLETE_ROSTER
        or unit.get("canonical_player_id")
    ):
        return True
    if _unit_key(unit) in required_keys:
        return True
    if _has_team_uncertainty(unit) or is_material_continuity_case(unit):
        return True
    if str(unit.get("scope_kind") or "") == "material_continuity":
        return True
    reasons = " ".join(str(value).lower() for value in unit.get("reason_codes") or [])
    if any(marker in reasons for marker in ("mixed", "conflict", "contradict", "cross_team", "team_mismatch")):
        return True
    decision = unit.get("current_decision") or {}
    if decision.get("action"):
        # An explicit "Nie wiem" is a valid outcome of MAX: it removes the
        # fragment from the safe maximum instead of immediately presenting it
        # again. Other explicit dispositions are likewise final for this queue.
        return True
    return str(unit.get("current_resolution_status") or "") in {
        "pending_high_priority",
        "pending_material_continuity_review",
        "structurally_blocked",
    }


def _deferred_named_gain_pairs(
    units: list[dict[str, Any]],
    pair_index: Mapping[tuple[str, int], dict[str, Any]],
    match_doc: dict[str, Any],
) -> set[tuple[str, int]]:
    """Project only authoritative Team-A roster decisions before final rebuild.

    The denominator deliberately remains the current reliable Team-A set until
    final recomputation.  A cross-team correction can change that denominator
    later, but it must never be presented as a positive Team-A naming gain.
    """
    roster_teams = _authoritative_roster_teams(match_doc)
    pairs: set[tuple[str, int]] = set()
    for unit in units:
        decision = unit.get("current_decision") or {}
        if str(decision.get("action") or "") != "assign_roster_player":
            continue
        player_id = str(decision.get("player_id") or "")
        if roster_teams.get(player_id) != "A":
            continue
        compact_runs = unit.get("detected_pair_runs")
        if isinstance(compact_runs, dict):
            candidates: Iterable[tuple[str, int]] = (
                (tracklet_id, frame)
                for tracklet_id, tracklet_runs in compact_runs.items()
                for start, end in tracklet_runs
                for frame in range(start, end + 1)
            )
        else:
            candidates = (
                (str(pair[0]), int(pair[1]))
                for pair in unit.get("detected_pairs") or []
                if isinstance(pair, (tuple, list)) and len(pair) >= 2
            )
        for normalized in candidates:
            row = pair_index.get(normalized) or {}
            if (
                _team_label(row.get("team_label")) == "A"
                and row.get("identity_status") in RELIABLE_STATUSES
                and not row.get("canonical_player_id")
            ):
                pairs.add(normalized)
    return pairs


def _authoritative_roster_teams(match_doc: dict[str, Any]) -> dict[str, str]:
    return {
        str(player.get("id")): str(team.get("team_label") or chr(ord("A") + index)).upper()
        for index, team in enumerate(match_doc.get("teams") or [])
        for player in team.get("players") or []
        if player.get("id")
    }


def _rank_optional_max_cases(
    rows: list[dict[str, Any]],
    *,
    accounted_pairs: set[tuple[str, int]] | None = None,
    reliable_observations: int = 0,
) -> list[dict[str, Any]]:
    """Greedily rank by *marginal* unique observation gain, without a cap."""
    remaining = list(rows)
    selected: list[dict[str, Any]] = []
    accounted: set[tuple[str, int]] = set(accounted_pairs or set())
    while remaining:
        # Keep the ordering deterministic. Bigger unique gain wins; equal
        # gains prefer longer evidence, then the earlier fragment boundary.
        chosen = min(
            remaining,
            key=lambda row: (
                -len(_gain_pairs_set(row.get("_potential_named_observation_pairs")) - accounted),
                -float(row.get("detected_time_sec") or 0.0),
                int(row.get("frame_start") or 0),
                str(row.get("candidate_subject_id") or ""),
            ),
        )
        gain = len(_gain_pairs_set(chosen.get("_potential_named_observation_pairs")) - accounted)
        remaining.remove(chosen)
        if gain <= 0:
            continue
        pairs = _gain_pairs_set(chosen.get("_potential_named_observation_pairs")) - accounted
        accounted.update(pairs)
        chosen["current_resolution_status"] = "pending_optional_max_audit"
        chosen["priority"] = "optional"
        chosen["optional_max_rank"] = len(selected) + 1
        chosen["marginal_named_observation_gain"] = gain
        chosen["optional_max_marginal_coverage_gain_pp"] = (
            round(100.0 * gain / reliable_observations, 4)
            if reliable_observations > 0
            else 0.0
        )
        chosen["cumulative_selected_named_gain"] = len(accounted)
        chosen["reason_codes"] = sorted(
            set(chosen.get("reason_codes") or []) | {"optional_max_named_coverage"}
        )
        selected.append(chosen)
    return selected


def _optional_max_summary(
    coverage: dict[str, Any],
    rows: list[dict[str, Any]],
    unavailable: list[dict[str, Any]],
    readiness: dict[str, Any],
    match_doc: dict[str, Any],
    deferred_named_pairs: set[tuple[str, int]],
    pair_index: Mapping[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    team = "A"
    row = (coverage.get("per_team") or {}).get(team) or {}
    reliable = int(row.get("reliable_observations") or 0)
    current_named = int(row.get("confirmed_named_observations") or 0)
    team_unnamed_pairs = {
        pair
        for pair, observation in pair_index.items()
        if _team_label(observation.get("team_label")) == team
        and str(observation.get("identity_status") or "unresolved") in RELIABLE_STATUSES
        and not observation.get("canonical_player_id")
    }
    deferred_pairs = team_unnamed_pairs & deferred_named_pairs
    safely_actionable_pairs = (
        _unique_named_gain_pairs(rows) & team_unnamed_pairs
    ) - deferred_pairs
    unavailable_pairs = team_unnamed_pairs - deferred_pairs - safely_actionable_pairs
    scope_ready = team_review_scope(match_doc, team) == COMPLETE_ROSTER
    normal_ready = bool(readiness.get("allows_finalize"))
    status = (
        "not_ready"
        if not scope_ready or not normal_ready
        else "available"
        if rows
        else "safe_max_reached"
    )
    unavailable_reason_by_pair = {
        pair: "other_no_safe_path"
        for pair in unavailable_pairs
    }
    # Classify only the residual pairs. A unit can overlap another unit, so a
    # pair receives one deterministic reason; the public counts then reconcile
    # exactly with the residual total rather than with the number of units.
    for value in unavailable:
        if _optional_max_explicitly_unresolved(value):
            reason = "explicit_unresolved"
        elif (value.get("current_decision") or {}).get("action"):
            reason = "explicit_non_naming_disposition"
        elif not value.get("has_operator_visual_evidence"):
            reason = "insufficient_safe_evidence"
        else:
            reason = "safety_constraint"
        for pair in _gain_pairs_set(value.get("_potential_named_observation_pairs")):
            if pair in unavailable_pairs:
                unavailable_reason_by_pair[pair] = reason
    reason_counts = Counter(unavailable_reason_by_pair.values())
    projected_named = min(reliable, current_named + len(deferred_pairs))
    safe_max = min(projected_named + len(safely_actionable_pairs), reliable)
    current_minimum_target_met = (
        reliable > 0
        and current_named / reliable >= COMPLETE_ROSTER_NAMED_TARGET_RATIO
    )
    projected_minimum_target_met = (
        reliable > 0
        and projected_named / reliable >= COMPLETE_ROSTER_NAMED_TARGET_RATIO
    )
    return {
        "policy_version": OPTIONAL_MAX_POLICY_VERSION,
        "queue": "optional_audit",
        "team_label": team,
        "scope": team_review_scope(match_doc, team),
        "blocking": False,
        "status": status,
        "eligible_to_start": status in {"available", "safe_max_reached"},
        "minimum_target_ratio": COMPLETE_ROSTER_NAMED_TARGET_RATIO,
        # Retained for compatibility, but it is intentionally numeric rather
        # than a proxy for workflow readiness. Consumers should use the three
        # explicit facts below.
        "minimum_target_met": current_minimum_target_met,
        "current_minimum_target_met": current_minimum_target_met,
        "projected_minimum_target_met": projected_minimum_target_met,
        "required_readiness_met": normal_ready,
        "remaining_cases": len(rows),
        "actionable_cases_remaining": len(rows),
        "current_named_observations": current_named,
        "reliable_observations": reliable,
        "current_named_coverage": _ratio(current_named, reliable),
        "pending_named_gain": len(deferred_pairs),
        "projected_named_observations": projected_named,
        "projected_named_coverage": _ratio(projected_named, reliable),
        "safe_max_named_observations": safe_max,
        "safe_max_named_coverage": _ratio(safe_max, reliable),
        "remaining_actionable_named_gain": len(safely_actionable_pairs),
        "actionable_unique_observations_remaining": len(safely_actionable_pairs),
        "unavailable_residual_observations": len(unavailable_pairs),
        "unavailable_residual_ratio": _ratio(len(unavailable_pairs), reliable),
        "unavailable_actionable_observations": len(unavailable_pairs),
        "unavailable_reason_counts": dict(sorted(reason_counts.items())),
        "per_team": {team: len(rows)},
    }


def _has_explicit_disposition(unit: dict[str, Any], match_doc: dict[str, Any]) -> bool:
    decision = unit.get("current_decision") or {}
    action = str(decision.get("action") or "")
    if not action:
        return False
    if action == "assign_roster_player":
        return True
    # "Nie wiem" is a completed operator decision for this exact source, not
    # a temporary absence of a decision. In particular, complete-roster
    # coverage must never keep presenting the same ambiguous/corrupt crop in
    # an attempt to force a name. The residual coverage gap remains explicit
    # in readiness as unavailable, and a changed ownership digest creates a
    # new unit which can be reviewed again.
    if action == "unresolved":
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
    team_attribution_units = [
        unit
        for units in non_actionable_team_uncertainty.values()
        for unit in units
    ]
    team_attribution_statuses = [
        normalized_team_attribution_evidence_status(
            unit.get("team_attribution_evidence_status")
        )
        for unit in team_attribution_units
    ]
    team_attribution_status_counts = Counter(team_attribution_statuses)
    team_attribution_requires_materialization = any(
        classify_team_attribution_evidence_status(
            unit.get("team_attribution_evidence_status")
        )
        == "remediable_not_established"
        for unit in team_attribution_units
    )
    team_attribution_has_technical_failure = any(
        classify_team_attribution_evidence_status(
            unit.get("team_attribution_evidence_status")
        )
        == "technical_failure"
        for unit in team_attribution_units
    )
    reliable_observations = int(coverage.get("reliable_observations") or 0)
    # These units are exact final Review ownership scopes. Count them rather
    # than inferring uncertainty from a previous snapshot's tentative team
    # labels: the whole point of the residual is that no durable attribution
    # is safe for these observations yet.
    team_attribution_runs = runs_union(*[
        unit.get("detected_pair_runs")
        if isinstance(unit.get("detected_pair_runs"), dict)
        else encode_pair_runs(unit.get("detected_pairs") or [])
        for unit in team_attribution_units
    ])
    unknown_team_observations = count_pair_runs(team_attribution_runs)
    # Reuse the existing 90% reviewed-observation tolerance rather than
    # turning team attribution into an implicit 100% completion requirement.
    # The budget applies globally because Team-U observations intentionally do
    # not belong to either roster scope until a safe operator decision exists.
    unknown_team_budget = max(
        0,
        reliable_observations
        - target_named_observations(
            reliable_observations,
            REVIEWED_OBSERVATION_TARGET_RATIO,
        ),
    )
    team_attribution_residual = {
        "units": len(team_attribution_units),
        "observations": unknown_team_observations,
        "residual_budget_observations": unknown_team_budget,
        "within_tolerance": unknown_team_observations <= unknown_team_budget,
        "evidence_status_counts": dict(sorted(team_attribution_status_counts.items())),
    }
    if team_attribution_units:
        if team_attribution_has_technical_failure:
            # A video/render failure is not evidence that the operator cannot
            # decide. Keep it visible and fail closed rather than spending the
            # ordinary Team-U residual tolerance.
            team_attribution_residual["status"] = "technical_evidence_failure"
            blockers.append(
                {
                    "code": "team_attribution_evidence_technical_failure",
                    **team_attribution_residual,
                }
            )
        elif team_attribution_requires_materialization:
            # This is a recoverable materialization gap, not a claim that
            # evidence is impossible. The workflow must offer its bounded
            # recompute/evidence pass before displaying a terminal residual.
            team_attribution_residual["status"] = "materialization_required"
            blockers.append(
                {
                    "code": "team_attribution_evidence_not_materialized",
                    **team_attribution_residual,
                }
            )
        elif unknown_team_observations > unknown_team_budget:
            team_attribution_residual["status"] = "exceeds_tolerance"
            blockers.append(
                {
                    "code": "team_attribution_residual_exceeds_tolerance",
                    **team_attribution_residual,
                }
            )
        else:
            # Genuine no-safe-evidence residuals remain explicitly Team-U and
            # stay out of unsafe stats; they are accepted only inside policy.
            team_attribution_residual["status"] = "accepted_within_tolerance"
    else:
        team_attribution_residual["status"] = "none"
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
        "team_attribution_residual": team_attribution_residual,
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
    if not path.exists():
        return {}
    value = load_json_cached(path)
    return value if isinstance(value, dict) else {}


def _has_team_uncertainty(unit: dict[str, Any]) -> bool:
    """Compatibility alias for callers/tests that predate the shared policy."""
    return has_team_attribution_uncertainty(unit)
