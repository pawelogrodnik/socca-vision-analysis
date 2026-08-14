from __future__ import annotations

"""Canonical named-identity coverage and coverage-review policy.

The module deliberately separates observed identity coverage from operator
queue completion. A partial roster can acknowledge an anonymous subject
without turning it into named-player data.
"""

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Iterable

from app.services.identity_reviewed_effective_observation import (
    iter_effective_reviewed_observations,
)
from app.services.play_area import is_on_pitch_product_observation


COVERAGE_SCHEMA_VERSION = "1.0.0"
COVERAGE_POLICY_VERSION = "coverage-driven-review:v1-experimental"
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
ROSTER_SCOPES = {
    "complete_roster",
    "partial_roster",
    "players_of_interest",
    "unspecified",
}
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
    ]
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unreviewable: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        if unit in semantic or _has_explicit_disposition(unit, match_doc):
            continue
        enriched = _coverage_impact(unit, pair_index, coverage)
        if int(enriched["potential_named_observation_gain"]) <= 0:
            continue
        team = _team_label(enriched.get("coverage_team_label"))
        if enriched.get("has_operator_visual_evidence"):
            candidates[team].append(enriched)
        else:
            unreviewable[team].append(enriched)

    coverage_blockers: list[dict[str, Any]] = []
    residual_by_team: dict[str, dict[str, Any]] = {}
    for team in sorted(set((coverage.get("per_team") or {}).keys()) | set(candidates) | set(unreviewable)):
        team_coverage = (coverage.get("per_team") or {}).get(team) or {}
        reliable = int(team_coverage.get("reliable_observations") or 0)
        rows = sorted(
            candidates.get(team, []),
            key=lambda row: (
                -int(row.get("potential_named_observation_gain") or 0),
                -float(row.get("detected_time_sec") or 0.0),
                -int(row.get("tracklet_count") or 0),
                str(row.get("candidate_subject_id") or ""),
            ),
        )
        reviewable_debt = sum(
            int(row.get("potential_named_observation_gain") or 0) for row in rows
        )
        unreviewable_debt = sum(
            int(row.get("potential_named_observation_gain") or 0)
            for row in unreviewable.get(team, [])
        )
        residual_budget = round(
            reliable * (1.0 - REVIEWED_OBSERVATION_TARGET_RATIO)
        )
        required_gain = max(0, reviewable_debt + unreviewable_debt - residual_budget)
        selected_gain = 0
        selected_count = 0
        for row in rows:
            if selected_gain >= required_gain:
                break
            row["coverage_rank_within_team"] = selected_count + 1
            row["current_resolution_status"] = "pending_coverage_review"
            row["priority"] = "coverage"
            row["reason_codes"] = sorted(
                set(row.get("reason_codes") or []) | {"significant_named_coverage_debt"}
            )
            coverage_blockers.append(row)
            selected_gain += int(row.get("potential_named_observation_gain") or 0)
            selected_count += 1
        residual = max(0, reviewable_debt + unreviewable_debt - selected_gain)
        residual_by_team[team] = {
            "reliable_observations": reliable,
            "unreviewed_unnamed_observations": reviewable_debt + unreviewable_debt,
            "selected_coverage_gain": selected_gain,
            "residual_unreviewed_observations": residual,
            "residual_unreviewed_ratio": _ratio(residual, reliable),
            "residual_budget_observations": residual_budget,
            "coverage_cases": selected_count,
            "unreviewable_observations": unreviewable_debt,
            "unreviewable_units": len(unreviewable.get(team, [])),
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
    workload_count = len(semantic_sorted) + len(coverage_sorted)
    readiness = _readiness(
        coverage,
        residual_by_team,
        match_doc,
        semantic_count=len(semantic_sorted),
        coverage_count=len(coverage_sorted),
    )
    return {
        "next_cases": [*semantic_sorted, *coverage_sorted],
        "semantic_blockers": len(semantic_sorted),
        "coverage_blockers": len(coverage_sorted),
        "residual_by_team": residual_by_team,
        "readiness": readiness,
        "workload": {
            "remaining_cases": workload_count,
            "level": workload_level(workload_count),
            "diagnostic_only": True,
            "queue_truncated": False,
        },
    }


def paginate_progress(
    progress: dict[str, Any],
    *,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    team_label: str | None = None,
) -> dict[str, Any]:
    cases = list(progress.get("next_cases") or [])
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
        if key not in {"review_units", "deferred_correction_context"}
    }
    safe_offset = max(0, int(offset))
    safe_limit = min(MAX_PAGE_SIZE, max(1, int(limit)))
    page = filtered_cases[safe_offset : safe_offset + safe_limit]
    return {
        **public_progress,
        "next_cases": [
            {**unit, "filter_team_label": review_case_team_label(unit)}
            for unit in page
        ],
        "filters": {
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
    team = next(
        (
            row
            for row in match_doc.get("teams") or []
            if _team_label(row.get("team_label") or row.get("label")) == team_label
        ),
        {},
    )
    configured = str(team.get("identity_coverage_scope") or "").strip()
    if not configured:
        scopes = match_doc.get("identity_review_scope") or {}
        configured = str((scopes.get("teams") or {}).get(team_label) or "").strip()
    return configured if configured in ROSTER_SCOPES else "unspecified"


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
    unnamed = sum(
        str(row.get("identity_status") or "unresolved") in RELIABLE_STATUSES
        and not row.get("canonical_player_id")
        for row in rows
    )
    if not rows:
        unnamed = int(unit.get("detected_observation_count") or 0)
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
        "potential_team_unnamed_share": _ratio(unnamed, reliable),
        "potential_named_coverage_gain_pp": round(100.0 * unnamed / reliable, 2)
        if reliable
        else 0.0,
        "named_coverage_before": _ratio(current_named, reliable),
        "named_coverage_after_max": _ratio(current_named + unnamed, reliable),
    }


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
    coverage_count: int,
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    if semantic_count:
        blockers.append({"code": "semantic_identity_conflicts", "count": semantic_count})
    if coverage_count:
        blockers.append({"code": "significant_named_coverage_debt", "count": coverage_count})
    for team, row in residual_by_team.items():
        if int(row.get("unreviewable_observations") or 0) > int(
            row.get("residual_budget_observations") or 0
        ):
            blockers.append(
                {
                    "code": "coverage_evidence_unavailable",
                    "team_label": team,
                    "observations": row.get("unreviewable_observations"),
                }
            )
    complete_roster_failures = []
    for team, row in (coverage.get("per_team") or {}).items():
        if roster_scope(match_doc, team) != "complete_roster":
            continue
        ratio = row.get("named_observation_coverage")
        if ratio is None or float(ratio) < COMPLETE_ROSTER_NAMED_TARGET_RATIO:
            complete_roster_failures.append(
                {
                    "code": "complete_roster_named_coverage_below_target",
                    "team_label": team,
                    "actual": ratio,
                    "target": COMPLETE_ROSTER_NAMED_TARGET_RATIO,
                }
            )
    blockers.extend(complete_roster_failures)
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
        "roster_scope": scope,
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
