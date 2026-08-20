from __future__ import annotations

"""Read-only, operator-oriented progress for reviewed player identity."""

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

from app.services.identity_reviewed_active_cap import build_reviewed_active_cap_context
from app.services.identity_reviewed_coverage import (
    COVERAGE_POLICY_VERSION,
    apply_coverage_policy,
    load_effective_coverage_context,
)
from app.services.identity_reviewed_effective_observation import is_real_detected_position
from app.services.identity_reviewed_slot_review import (
    load_reviewed_slot_assignments,
    whole_subject_reviewability,
)
from app.services.identity_reviewed_slot_registry import normalize_reviewed_slot_id
from app.services.identity_reviewed_segments import load_segment_review
from app.services.identity_reviewed_mixed_store import (
    build_mixed_review_queue,
    load_mixed_player_cases,
)
from app.services.identity_reviewed_material_continuity import (
    MATERIAL_CONTINUITY_POLICY_VERSION,
    coalesce_material_continuity_units,
)
from app.services.identity_reviewed_team_attribution_evidence import (
    evidence_status_for_unit,
    load_team_attribution_evidence,
    visual_evidence_for_unit,
)
from app.services.identity_review_scope import (
    identity_review_scope_digest,
    identity_review_scope_read_model,
)
from app.services.identity_seeded_review_reduction import load_fresh_seeded_assignments
from app.services.play_area import is_on_pitch_product_observation
from app.services.video import read_match_video_metadata


OPTIONAL_MIN_DETECTED_SEC = 0.5
OPTIONAL_MIN_OBSERVATIONS = 15
PROGRESS_SCHEMA_VERSION = "2.6.0"
REVIEWED_ACTIONS = frozenset(
    {
        "assign_roster_player",
        "assign_existing_slot",
        "assign_team",
        "create_new_stable_player",
        "referee",
        "false_detection",
        "team_unknown",
        "unresolved",
        "mixed_players",
    }
)
SEMANTIC_CONFLICT_REASON_MARKERS = (
    "conflict",
    "contradict",
    "incompatible",
    "parallel_",
    "multiple_manual_players",
    "duplicate_canonical",
    "cross_team",
    "team_mismatch",
)


def reviewed_snapshot_file_fingerprint(match_path: Path) -> dict[str, int] | None:
    try:
        stat = (match_path / "reviewed_identity_snapshot.json").stat()
    except OSError:
        return None
    return {
        "mtime_ns": int(stat.st_mtime_ns),
        "size_bytes": int(stat.st_size),
    }


def build_reviewed_identity_progress(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    """Build progress from frozen identity artifacts without writing anything."""
    tracklets = _tracklets(match_path)
    subjects = _subjects(match_path)
    stable_slots = _subject_stable_slots(match_path)
    cards = _cards(match_path)
    roster_teams = _roster_teams(match_doc)
    manual = _manual_decisions(match_path)
    seeded, freshness = load_fresh_seeded_assignments(match_path)
    safe_seeded = {
        str(row.get("candidate_subject_id") or ""): row
        for row in (seeded or {}).get("accepted_assignments") or []
        if row.get("candidate_subject_id")
    } if freshness.get("status") == "fresh" else {}
    memberships = _memberships(subjects)
    fps = _fps(match_path, match_doc)
    segment_review = load_segment_review(match_path)
    mixed_document = load_mixed_player_cases(match_path)
    mixed_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in mixed_document.get("cases") or []
        if row.get("candidate_subject_id")
    }
    segmented_subjects = {
        str(row.get("candidate_subject_id") or "")
        for row in segment_review.get("targets") or []
    }
    whole_subject_units = [
        _unit(
            subject_id,
            tracklet_ids,
            tracklets,
            memberships,
            cards.get(subject_id),
            manual.get(subject_id),
            safe_seeded.get(subject_id),
            roster_teams,
            fps,
            stable_slots.get(subject_id),
        )
        for subject_id, tracklet_ids in sorted(subjects.items())
    ]
    _attach_team_attribution_evidence(
        whole_subject_units,
        load_team_attribution_evidence(match_path),
    )
    units = [
        unit
        for unit in whole_subject_units
        if str(unit.get("candidate_subject_id") or "") not in segmented_subjects
        and str(unit.get("candidate_subject_id") or "") not in mixed_by_subject
    ]
    units.extend(_segment_units(segment_review, roster_teams, fps))
    units = coalesce_material_continuity_units(units, fps)
    coverage_context = load_effective_coverage_context(match_path, match_doc)
    coverage_policy = apply_coverage_policy(
        units,
        coverage_context["coverage"],
        coverage_context["pair_index"],
        match_doc,
    )
    queue_by_key = {
        _unit_key(unit): unit
        for unit in [
            *coverage_policy["next_cases"],
            *coverage_policy["optional_audit_cases"],
        ]
    }
    units = [queue_by_key.get(_unit_key(unit), unit) for unit in units]
    counts = Counter(str(unit["current_resolution_status"]) for unit in units)
    non_actionable_reason_counts = Counter(
        str(unit.get("non_actionable_reason") or "unknown")
        for unit in units
        if unit.get("operator_actionable") is False
    )
    observed_pairs = _all_detected_pairs(tracklets)
    operator_pairs = {
        pair
        for unit in units
        if unit["current_resolution_status"] == "reviewed_by_operator"
        for pair in unit["detected_pairs"]
    }
    team_known_pairs = {
        pair
        for unit in units
        if unit["effective_team_label"] in {"A", "B"}
        for pair in unit["detected_pairs"]
    }
    confirmed_pairs = {
        pair
        for unit in units
        if unit["canonical_player_id"]
        for pair in unit["detected_pairs"]
    }
    # Coverage policy is authoritative and deliberately uncapped. Pagination
    # is applied only at the API presentation boundary.
    next_cases = coverage_policy["next_cases"]
    completed = (
        counts["reviewed_by_operator"]
        + counts["resolved_automatically"]
        + counts["safe_anonymous"]
    )
    important_remaining = (
        int(coverage_policy["semantic_blockers"])
        + int(coverage_policy["coverage_blockers"])
        + int(coverage_policy["material_continuity_blockers"])
    )
    queue_total = completed + important_remaining
    mixed_queue = build_mixed_review_queue(match_path, match_doc)
    return {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "status": "ready",
        "match_id": str(match_doc.get("id") or match_path.name),
        "source_snapshot_digest": coverage_context["source_snapshot_digest"],
        "source_snapshot_file": reviewed_snapshot_file_fingerprint(match_path),
        "source_review_scope_digest": identity_review_scope_digest(match_doc),
        "identity_review_scope": identity_review_scope_read_model(match_doc),
        "summary": {
            "review_units_total": len(units),
            "review_units_completed": completed,
            "review_units_actionable_total": queue_total,
            "completed_by_operator": counts["reviewed_by_operator"],
            "completed_automatically": (
                counts["resolved_automatically"] + counts["safe_anonymous"]
            ),
            "important_decisions_remaining": important_remaining,
            "semantic_decisions_remaining": coverage_policy["semantic_blockers"],
            "coverage_decisions_remaining": coverage_policy["coverage_blockers"],
            "material_continuity_decisions_remaining": coverage_policy[
                "material_continuity_blockers"
            ],
            "optional_cases_remaining": counts["pending_optional"],
            "optional_audit_cases_remaining": len(
                coverage_policy["optional_audit_cases"]
            ),
            "safe_anonymous_units": counts["safe_anonymous"],
            "structural_blockers": counts["structurally_blocked"],
            "non_actionable_review_units": sum(non_actionable_reason_counts.values()),
            "non_actionable_reason_counts": dict(sorted(non_actionable_reason_counts.items())),
            "ignored_low_impact": counts["ignored_low_impact"],
            "operator_decisions_saved": counts["reviewed_by_operator"],
            "operator_queue_completion_ratio": _ratio(completed, queue_total),
        },
        "observations": {
            "total_detected_observations": len(observed_pairs),
            "operator_reviewed_observations": len(operator_pairs),
            "operator_reviewed_observation_ratio": _ratio(len(operator_pairs), len(observed_pairs)),
            "team_known_observations": len(team_known_pairs),
            "team_known_observation_ratio": _ratio(len(team_known_pairs), len(observed_pairs)),
            "confirmed_player_observations": len(confirmed_pairs),
            "confirmed_player_observation_ratio": _ratio(len(confirmed_pairs), len(observed_pairs)),
        },
        "identity_coverage": coverage_context["coverage"],
        "coverage_readiness": coverage_policy["readiness"],
        "coverage_residuals": coverage_policy["residual_by_team"],
        "workload": coverage_policy["workload"],
        "optional_audit": coverage_policy["optional_audit"],
        "next_cases": [_public_unit(unit) for unit in next_cases],
        "optional_audit_cases": [
            _public_unit(unit) for unit in coverage_policy["optional_audit_cases"]
        ],
        "mixed_players": mixed_queue,
        "technical_diagnostics": {
            "candidate_subjects": len(subjects),
            "tracklets": len(tracklets),
            "unresolved_tracklet_assignments": _technical_unresolved(match_path, len(tracklets)),
        },
        "policy": {
            "version": COVERAGE_POLICY_VERSION,
            "material_continuity_version": MATERIAL_CONTINUITY_POLICY_VERSION,
            "optional_min_detected_sec": OPTIONAL_MIN_DETECTED_SEC,
            "optional_min_observations": OPTIONAL_MIN_OBSERVATIONS,
            "long_unresolved_requires_operator": True,
            "generic_requires_operator_review_requires_operator": True,
            "queue_has_hard_case_cap": False,
        },
        "review_units": [_public_unit(unit, include_pairs=False) for unit in units],
        "deferred_correction_context": build_reviewed_active_cap_context(
            match_path,
            whole_subject_units,
        ),
    }


def decision_impact(
    before: dict[str, Any],
    after: dict[str, Any],
    subject_id: str,
    review_target_id: str | None = None,
) -> dict[str, Any]:
    before_unit = _find_unit(before, subject_id, review_target_id)
    after_unit = _find_unit(after, subject_id, review_target_id)
    before_observations = int((before_unit or {}).get("detected_observation_count") or 0)
    after_observations = int((after_unit or {}).get("detected_observation_count") or 0)
    before_operator = int(before["observations"]["operator_reviewed_observations"])
    after_operator = int(after["observations"]["operator_reviewed_observations"])
    return {
        "affected_tracklets": len((after_unit or before_unit or {}).get("tracklet_ids") or []),
        "affected_detected_observations": after_observations or before_observations,
        "important_decisions_remaining_before": int(before["summary"]["important_decisions_remaining"]),
        "important_decisions_remaining_after": int(after["summary"]["important_decisions_remaining"]),
        "operator_reviewed_observations_delta": after_operator - before_operator,
        "operator_reviewed_ratio_before": before["observations"]["operator_reviewed_observation_ratio"],
        "operator_reviewed_ratio_after": after["observations"]["operator_reviewed_observation_ratio"],
    }


def _unit(
    subject_id: str,
    tracklet_ids: set[str],
    tracklets: dict[str, dict[str, Any]],
    memberships: dict[str, set[str]],
    card: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    seeded: dict[str, Any] | None,
    roster_teams: dict[str, str],
    fps: float,
    stable_slot_id: str | None,
) -> dict[str, Any]:
    pairs = {
        (tracklet_id, int(position.get("frame") or 0))
        for tracklet_id in tracklet_ids
        for position in tracklets.get(tracklet_id, {}).get("positions_m") or []
        if is_real_detected_position(position)
        and is_on_pitch_product_observation(position)
    }
    frames = sorted({frame for _, frame in pairs})
    on_pitch_tracklet_ids = {tracklet_id for tracklet_id, _ in pairs}
    teams = {
        str(tracklets.get(tracklet_id, {}).get("team_label") or "U")
        for tracklet_id in on_pitch_tracklet_ids
        if tracklet_id in tracklets
    }
    detected_team_labels = sorted(teams & {"A", "B"})
    reason_codes: list[str] = []
    structural = False
    if any(len(memberships.get(tracklet_id) or set()) > 1 for tracklet_id in tracklet_ids):
        structural = True
        reason_codes.append("ambiguous_candidate_subject_membership")
    reviewability = whole_subject_reviewability(
        ambiguous_membership=structural,
        detected_team_labels=set(detected_team_labels),
    )
    team_conflict = len(teams) > 1
    if team_conflict:
        reason_codes.append("conflicting_detected_team_labels")
    action = str((decision or {}).get("action") or "")
    player_id = str((decision or {}).get("player_id") or "") or None
    source_team = next(iter(teams), "U") if len(teams) == 1 else "U"
    if stable_slot_id and source_team in {"A", "B"} and not stable_slot_id.startswith(source_team):
        stable_slot_id = None
    effective_team = str((decision or {}).get("team_label") or "").upper()
    if action == "assign_roster_player" and player_id:
        effective_team = roster_teams.get(player_id, effective_team)
    if effective_team not in {"A", "B"}:
        effective_team = source_team
    card_requires_review = bool(card) and card.get("requires_operator_review") is not False
    card_conflict = _card_has_semantic_conflict(card)
    has_operator_visual_evidence = _card_has_operator_visual_evidence(card)
    if card_conflict:
        reason_codes.append("review_card_conflict")
    if action in REVIEWED_ACTIONS:
        status = "reviewed_by_operator"
    elif not pairs:
        status = "ignored_low_impact"
        reason_codes.append("no_inside_play_observations")
    elif seeded is not None or (card is not None and not card_requires_review):
        status = "resolved_automatically"
        reason_codes.append("safe_seeded_or_completed_review_card")
    elif (card_conflict or team_conflict) and has_operator_visual_evidence:
        status = "pending_high_priority"
        reason_codes.append("semantic_identity_conflict")
    elif card_conflict or team_conflict:
        status = "pending_optional"
        reason_codes.append("semantic_conflict_without_visual_evidence")
    elif structural:
        # This describes a data-quality condition. Some frame ownership paths
        # safely resolve it downstream, so it is never a mandatory human task
        # solely because the diagnostic exists.
        status = "structurally_blocked"
    elif len(pairs) >= OPTIONAL_MIN_OBSERVATIONS or len(frames) / fps >= OPTIONAL_MIN_DETECTED_SEC:
        status = "pending_optional"
        reason_codes.append("long_unresolved_safe_anonymous")
    else:
        # An unnamed stable Team A, Team B, or unknown slot is still valid
        # reviewed output. Naming is enrichment, not a prerequisite for stats.
        status = "safe_anonymous"
        reason_codes.append("safe_anonymous_stable_slot")
    canonical_player_id = player_id if action == "assign_roster_player" else None
    if seeded is not None:
        canonical_player_id = str((seeded.get("assigned_player") or {}).get("player_id") or "") or canonical_player_id
        if canonical_player_id:
            effective_team = roster_teams.get(canonical_player_id, effective_team)
    return {
        "candidate_subject_id": subject_id,
        "tracklet_ids": sorted(tracklet_ids),
        "tracklet_count": len(tracklet_ids),
        "source_team_label": source_team,
        "detected_team_labels": detected_team_labels,
        "effective_team_label": effective_team,
        "frame_start": frames[0] if frames else None,
        "frame_end": frames[-1] if frames else None,
        "detected_frame_count": len(frames),
        "detected_observation_count": len(pairs),
        "detected_time_sec": round(len(frames) / fps, 3),
        "current_decision": decision,
        "current_resolution_status": status,
        "canonical_player_id": canonical_player_id,
        "stable_slot_id": stable_slot_id,
        "priority": "high" if status == "pending_high_priority" else "optional" if status == "pending_optional" else None,
        "reason_codes": sorted(set(reason_codes)),
        "correction_scope": "whole_subject",
        "operator_actionable": bool(reviewability["actionable"]),
        "non_actionable_reason": reviewability["reason"],
        "has_operator_visual_evidence": has_operator_visual_evidence,
        "visual_evidence": dict((card or {}).get("visual_evidence") or {}),
        "detected_pairs": sorted(pairs),
    }


def _public_unit(unit: dict[str, Any], *, include_pairs: bool = False) -> dict[str, Any]:
    keys = (
        "candidate_subject_id", "tracklet_ids", "tracklet_count", "source_team_label",
        "effective_team_label", "frame_start", "frame_end", "detected_frame_count",
        "detected_observation_count", "detected_time_sec", "current_decision",
        "current_resolution_status", "priority", "reason_codes", "review_target_id",
        "scope_kind", "source_ownership_digest", "stable_slot_id", "frame_ranges",
        "continuity_group_id", "continuity_subject_ids", "continuity_fragment_count",
        "continuity_span_sec", "material_continuity_required",
        "visual_evidence", "legacy_suggestion",
        "coverage_team_label", "potential_named_observation_gain",
        "potential_team_unnamed_share", "potential_named_coverage_gain_pp",
        "named_coverage_before", "named_coverage_after_max",
        "coverage_rank_within_team", "marginal_named_observation_gain",
        "cumulative_selected_named_gain",
        "correction_scope", "operator_actionable", "non_actionable_reason",
        "has_operator_visual_evidence", "team_attribution_evidence_status",
    )
    result = {key: unit.get(key) for key in keys}
    if include_pairs:
        result["detected_pairs"] = unit.get("detected_pairs")
    return result


def _attach_team_attribution_evidence(
    units: list[dict[str, Any]],
    document: dict[str, Any],
) -> None:
    """Add Team-U-only crops without replacing stricter naming evidence."""
    for unit in units:
        if (
            str(unit.get("source_team_label") or "").upper() != "U"
            or unit.get("has_operator_visual_evidence")
        ):
            continue
        subject_id = str(unit.get("candidate_subject_id") or "")
        evidence = visual_evidence_for_unit(
            document,
            candidate_subject_id=subject_id,
            detected_pairs=unit.get("detected_pairs") or [],
        )
        if evidence is None:
            unit["team_attribution_evidence_status"] = evidence_status_for_unit(
                document,
                candidate_subject_id=subject_id,
                detected_pairs=unit.get("detected_pairs") or [],
            )
            unit["reason_codes"] = sorted(
                set(unit.get("reason_codes") or [])
                | {"team_attribution_evidence_unavailable"}
            )
            continue
        unit["visual_evidence"] = evidence
        unit["has_operator_visual_evidence"] = True
        unit["reason_codes"] = sorted(
            set(unit.get("reason_codes") or [])
            | {"team_attribution_evidence_recovered"}
        )


def _segment_units(
    review: dict[str, Any],
    roster_teams: dict[str, str],
    fps: float,
) -> list[dict[str, Any]]:
    output = []
    for target in review.get("targets") or []:
        decision = target.get("current_decision") or None
        action = str((decision or {}).get("action") or "")
        player_id = str((decision or {}).get("player_id") or "") or None
        frames = {int(frame) for frame in target.get("owned_frames") or []}
        tracklet_ids = [str(value) for value in target.get("tracklet_ids") or []]
        pairs = {
            (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
            for row in target.get("owned_observations") or []
        } or {(tracklet_id, frame) for tracklet_id in tracklet_ids for frame in frames}
        reviewed = action in REVIEWED_ACTIONS
        has_operator_visual_evidence = bool(
            ((target.get("visual_evidence") or {}).get("anchor_crops") or [])
        )
        status = (
            "reviewed_by_operator"
            if reviewed
            else "pending_high_priority"
            if has_operator_visual_evidence
            else "pending_optional"
        )
        reason_codes = list(target.get("reason_codes") or [])
        if not reviewed and not has_operator_visual_evidence:
            reason_codes.append("mixed_tracklet_segment_without_visual_evidence")
        effective_team = str(
            (decision or {}).get("team_label")
            or target.get("effective_team_label")
            or target.get("source_team_label")
            or "U"
        )
        if player_id:
            effective_team = roster_teams.get(player_id, effective_team)
        output.append(
            {
                "candidate_subject_id": target.get("candidate_subject_id"),
                "review_target_id": target.get("review_target_id"),
                "scope_kind": "canonical_segment",
                "correction_scope": "canonical_segment",
                "operator_actionable": True,
                "non_actionable_reason": None,
                "tracklet_ids": tracklet_ids,
                "tracklet_count": len(tracklet_ids),
                "source_team_label": target.get("source_team_label") or "U",
                "effective_team_label": effective_team,
                "frame_start": target.get("frame_start"),
                "frame_end": target.get("frame_end"),
                "frame_ranges": list(target.get("frame_ranges") or []),
                "detected_frame_count": len(frames),
                "detected_observation_count": len(pairs),
                "detected_time_sec": round(len(frames) / fps, 3),
                "current_decision": decision,
                "current_resolution_status": status,
                "canonical_player_id": player_id if action == "assign_roster_player" else None,
                "priority": (
                    None
                    if reviewed
                    else "high"
                    if has_operator_visual_evidence
                    else "optional"
                ),
                "reason_codes": reason_codes,
                "source_ownership_digest": target.get("source_ownership_digest"),
                "stable_slot_id": target.get("stable_slot_id"),
                "visual_evidence": target.get("visual_evidence") or {},
                "legacy_suggestion": target.get("legacy_suggestion"),
                "has_operator_visual_evidence": has_operator_visual_evidence,
                "detected_pairs": sorted(pairs),
            }
        )
    return output


def _card_has_semantic_conflict(card: dict[str, Any] | None) -> bool:
    """Keep legacy card status useful without treating missing names as conflict."""
    if not card or "conflict" not in str(card.get("review_status") or "").lower():
        return False
    signals = {
        str(value).strip().lower()
        for field in ("reason_codes", "blockers", "quality_flags")
        for value in card.get(field) or []
        if str(value).strip()
    }
    # Older review cards sometimes call a missing roster name `blocked_conflict`.
    # No evidence is conservatively retained as a hard conflict, but explicit
    # non-semantic evidence remains safe anonymous output rather than a blocker.
    return not signals or any(
        marker in signal
        for signal in signals
        for marker in SEMANTIC_CONFLICT_REASON_MARKERS
    )


def _card_has_operator_visual_evidence(card: dict[str, Any] | None) -> bool:
    visual_evidence = (card or {}).get("visual_evidence")
    if not isinstance(visual_evidence, dict):
        return False
    anchor_crops = visual_evidence.get("anchor_crops")
    return isinstance(anchor_crops, list) and bool(anchor_crops)


def _find_unit(
    progress: dict[str, Any],
    subject_id: str,
    review_target_id: str | None = None,
) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in progress.get("review_units") or []
            if row.get("candidate_subject_id") == subject_id
            and (
                review_target_id is None
                or row.get("review_target_id") == review_target_id
            )
        ),
        None,
    )


def _unit_key(unit: dict[str, Any]) -> tuple[str, str | None]:
    return (
        str(unit.get("candidate_subject_id") or ""),
        str(unit.get("review_target_id") or "") or None,
    )


def _tracklets(path: Path) -> dict[str, dict[str, Any]]:
    return {str(row.get("tracklet_id")): row for row in _load(path / "tracklets.json").get("tracklets") or [] if row.get("tracklet_id")}


def _subjects(path: Path) -> dict[str, set[str]]:
    return {
        str(row.get("candidate_subject_id")): {str(value) for value in row.get("tracklet_ids") or []}
        for row in _load(path / "identity_candidate_shadow.json").get("subjects") or []
        if row.get("candidate_subject_id")
    }


def _subject_stable_slots(path: Path) -> dict[str, str | None]:
    """Read a single safe canonical slot hypothesis for each raw subject."""
    output: dict[str, str | None] = {}
    for row in _load(path / "identity_candidate_shadow.json").get("subjects") or []:
        subject_id = str(row.get("candidate_subject_id") or "")
        if not subject_id:
            continue
        slots = {
            slot_id
            for value in (
                list(row.get("production_player_ids") or [])
                + list(row.get("production_subject_ids") or [])
            )
            if (slot_id := normalize_reviewed_slot_id(value)) is not None
        }
        output[subject_id] = next(iter(slots)) if len(slots) == 1 else None
    return output


def _memberships(subjects: dict[str, set[str]]) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for subject_id, tracklet_ids in subjects.items():
        for tracklet_id in tracklet_ids:
            output[tracklet_id].add(subject_id)
    return output


def _cards(path: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("candidate_subject_id")): row
        for row in _load(path / "identity_roster_subject_review_shadow.json").get("cards") or []
        if row.get("candidate_subject_id")
    }


def _manual_decisions(path: Path) -> dict[str, dict[str, Any]]:
    roster = {
        str(row.get("candidate_subject_id")): {
            "action": "unresolved" if row.get("decision") == "mark_unresolved" else "assign_roster_player",
            "player_id": row.get("player_id"),
        }
        for row in _load(path / "identity_roster_subject_review_decisions_shadow.json").get("decisions") or []
        if row.get("candidate_subject_id") and row.get("decision") in {"mark_unresolved", "assign_roster_player", "confirm_recommended_player"}
    }
    slots = {
        str(row.get("candidate_subject_id")): dict(row)
        for row in load_reviewed_slot_assignments(path).get("decisions") or []
        if row.get("candidate_subject_id") and row.get("action") in REVIEWED_ACTIONS
    }
    mixed = {
        str(row.get("candidate_subject_id")): {
            "action": "mixed_players",
            "mixed_hint": row.get("mixed_hint"),
            "resolution_status": row.get("resolution_status"),
        }
        for row in load_mixed_player_cases(path).get("cases") or []
        if row.get("candidate_subject_id")
    }
    return {**roster, **slots, **mixed}


def _roster_teams(match_doc: dict[str, Any]) -> dict[str, str]:
    return {
        str(player.get("id")): str(team.get("team_label") or chr(ord("A") + index))
        for index, team in enumerate(match_doc.get("teams") or [])
        for player in team.get("players") or []
        if player.get("id")
    }


def _all_detected_pairs(tracklets: dict[str, dict[str, Any]]) -> set[tuple[str, int]]:
    return {
        (tracklet_id, int(position.get("frame") or 0))
        for tracklet_id, tracklet in tracklets.items()
        for position in tracklet.get("positions_m") or []
        if is_real_detected_position(position)
        and is_on_pitch_product_observation(position)
    }


def _technical_unresolved(path: Path, fallback: int) -> int:
    rows = _load(path / "reviewed_identity_snapshot.json").get("tracklet_assignments") or []
    return sum(str(row.get("identity_status") or "") == "unresolved" for row in rows) if rows else fallback


def _fps(path: Path, match_doc: dict[str, Any]) -> float:
    try:
        value = float(read_match_video_metadata(path, match_doc).get("fps") or 25)
        return value if value > 0 else 25.0
    except (FileNotFoundError, ValueError):
        return 25.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
