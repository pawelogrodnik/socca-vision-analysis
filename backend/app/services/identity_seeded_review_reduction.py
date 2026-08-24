from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import (
    SEEDS_FILENAME,
    load_identity_json,
    write_identity_json_atomic,
)
from app.services.identity_initial_audit import build_initial_identity_audit_document
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_operator_seed_digest import (
    DIGEST_CONTRACT,
    identity_operator_seed_decisions_digest,
)
from app.services.identity_seeded_candidate_assignments import (
    CANDIDATE_FILENAME,
    OUTPUT_FILENAME,
    SeededCandidateAssignmentsStaleError,
    TIMELINE_FILENAME,
    TRACKLETS_FILENAME,
    load_combined_operator_seeds,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_seeded_review_reduction"
ALGORITHM_VERSION = "0.2.0"
REPORT_FILENAME = "identity_seeded_review_reduction_report.json"

COMPLETED_STATUS = "completed_by_initial_audit"
CONFLICT_STATUS = "blocked_seed_conflict"
INITIAL_AUDIT_CASE_LIMIT = 12
EXPLICIT_INITIAL_AUDIT_ACTIONS = {
    "assign_roster_player",
    "team_a_unknown",
    "team_b_unknown",
    "referee",
    "false_detection",
    "skip",
}


def load_fresh_seeded_assignments(
    match_path: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    seeded_path = match_path / OUTPUT_FILENAME
    seeds_path = match_path / SEEDS_FILENAME
    reanchor_seeds_path = (
        match_path
        / "identity_second_half_reanchor"
        / "identity_second_half_reanchor_seeds.json"
    )
    if (
        not seeded_path.exists()
        or (
            not seeds_path.exists()
            and not reanchor_seeds_path.exists()
        )
    ):
        return None, {
            "status": "missing",
            "reason_codes": ["seeded_assignments_or_operator_seeds_missing"],
        }

    try:
        seeded = load_identity_json(seeded_path)
        seeds = _current_seed_document(match_path, seeds_path)
    except SeededCandidateAssignmentsStaleError:
        return None, {
            "status": "stale",
            "reason_codes": ["operator_seed_selection_digest_mismatch"],
        }
    except (OSError, ValueError):
        return None, {
            "status": "invalid",
            "reason_codes": ["seeded_assignments_or_operator_seeds_invalid"],
        }

    source = seeded.get("source") or {}
    expected_decisions_digest = str(
        source.get("operator_seed_decisions_digest") or ""
    )
    legacy_expected_digest = str(source.get("operator_seeds_digest") or "")
    current_decisions_digest = identity_operator_seed_decisions_digest(seeds)
    current_document_digest = canonical_digest(seeds)
    digest_matches = (
        expected_decisions_digest == current_decisions_digest
        if expected_decisions_digest
        else legacy_expected_digest
        in {current_decisions_digest, current_document_digest}
    )
    if not digest_matches:
        return None, {
            "status": "stale",
            "reason_codes": ["operator_seeds_digest_mismatch"],
            "expected_operator_seed_decisions_digest": (
                expected_decisions_digest or legacy_expected_digest or None
            ),
            "current_operator_seed_decisions_digest": current_decisions_digest,
            "current_operator_seeds_document_digest": current_document_digest,
        }
    artifact_mismatches: list[str] = []
    for source_key, filename in (
        ("candidate_identity_digest", CANDIDATE_FILENAME),
        ("timeline_digest", TIMELINE_FILENAME),
        ("tracklets_digest", TRACKLETS_FILENAME),
    ):
        expected = str(source.get(source_key) or "")
        if not expected:
            artifact_mismatches.append(f"{source_key}_contract_missing")
            continue
        artifact_path = match_path / filename
        current = canonical_digest(
            load_identity_json(artifact_path) if artifact_path.exists() else {}
        )
        if current != expected:
            artifact_mismatches.append(f"{source_key}_mismatch")
    if artifact_mismatches:
        return None, {
            "status": "stale",
            "reason_codes": artifact_mismatches,
        }
    if (seeded.get("safety") or {}).get("production_identity_untouched") is not True:
        return None, {
            "status": "unsafe",
            "reason_codes": ["seeded_assignments_safety_contract_missing"],
        }
    return seeded, {
        "status": "fresh",
        "reason_codes": [],
        "operator_seed_decisions_digest": current_decisions_digest,
        "operator_seed_decisions_digest_contract": DIGEST_CONTRACT,
        "operator_seeds_document_digest": current_document_digest,
        "seeded_assignments_digest": canonical_digest(seeded),
    }


def build_initial_audit_completion_evidence(
    selection: dict[str, Any] | None,
    decisions: list[dict[str, Any]] | None,
    *,
    reducer_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return bounded audit completion from reducer-selected observations.

    This is deliberately an adapter, not another identity scorer.  The seeded
    reducer chooses which candidate subjects still matter; this helper only
    checks whether its bounded representative observations have an explicit
    operator disposition.
    """
    selected_frames = len((selection or {}).get("selected_frames") or [])
    visible_observations = sum(
        len(row.get("visible_detections") or [])
        for row in (selection or {}).get("selected_frames") or []
        if isinstance(row, dict)
    )
    base = {
        "prepared": bool(selection),
        "selected_frames": selected_frames,
        "visible_observations": visible_observations,
        "completed": 0,
        "total": 0,
        "remaining": 0,
        "safe_to_stop": False,
        "complete": False,
        "completion_evidence_current": False,
    }
    if not selection or not isinstance(reducer_evidence, dict):
        return base
    if reducer_evidence.get("status") != "fresh":
        return {
            **base,
            "completion_evidence_reason": (
                reducer_evidence.get("reason")
                or "initial_audit_completion_evidence_missing"
            ),
        }

    required_keys = [
        str(value)
        for value in reducer_evidence.get("required_observation_keys") or []
        if value
    ]
    # A prepared audit without current reducer cases must remain open.  Zero
    # cases are only safe when the reducer itself explicitly says so.
    safe_to_stop = bool(reducer_evidence.get("safe_to_stop"))
    if not required_keys and not safe_to_stop:
        return {
            **base,
            "completion_evidence_current": True,
            "completion_evidence_reason": "initial_audit_cases_unavailable",
        }

    explicit_by_key = {
        str(row.get("observation_key"))
        for row in decisions or []
        if isinstance(row, dict)
        and str(row.get("action") or "") in EXPLICIT_INITIAL_AUDIT_ACTIONS
        and row.get("observation_key")
    }
    completed = sum(key in explicit_by_key for key in required_keys)
    total = len(required_keys)
    return {
        **base,
        "completed": completed,
        "total": total,
        "remaining": max(0, total - completed),
        "safe_to_stop": safe_to_stop,
        "complete": safe_to_stop or (total > 0 and completed == total),
        "completion_evidence_current": True,
        "required_case_observation_keys": required_keys,
        "reducer_source": reducer_evidence.get("source"),
    }


def load_initial_audit_completion_evidence(match_path: Path) -> dict[str, Any]:
    """Load the current seeded-reduction evidence without mutating artifacts.

    Inside an active review-build scope the derived completion document is
    memoized per match: a finalize transaction reads it through the cheap
    preflight, the refresh and the final workflow derivation, and its compact
    durable inputs cannot change inside that single authoritative request.
    """
    from app.services.identity_canonical_io import (
        has_active_scope,
        scoped_memo_get,
        scoped_memo_put,
    )

    memo_key = f"__initial_audit_evidence__::{match_path}"
    if has_active_scope():
        memoized = scoped_memo_get(memo_key)
        if memoized is not None:
            return memoized
        result = _load_initial_audit_completion_evidence_uncached(match_path)
        scoped_memo_put(memo_key, result)
        return result
    return _load_initial_audit_completion_evidence_uncached(match_path)


def _load_initial_audit_completion_evidence_uncached(match_path: Path) -> dict[str, Any]:
    selection_path = (
        match_path
        / "identity_initial_audit"
        / "identity_initial_audit_frame_selection.json"
    )
    if not selection_path.exists():
        return build_initial_audit_completion_evidence(None, None, reducer_evidence=None)
    try:
        selection = load_identity_json(selection_path)
        seed_path = match_path / SEEDS_FILENAME
        seeds = load_identity_json(seed_path) if seed_path.exists() else {}
    except (OSError, ValueError):
        return build_initial_audit_completion_evidence({}, [], reducer_evidence=None)

    seeded, freshness = load_fresh_seeded_assignments(match_path)
    reducer_evidence = _initial_audit_reducer_evidence(
        match_path,
        selection,
        seeded,
        freshness,
    )
    return build_initial_audit_completion_evidence(
        selection,
        list(seeds.get("decisions") or []),
        reducer_evidence=reducer_evidence,
    )


def _initial_audit_reducer_evidence(
    match_path: Path,
    selection: dict[str, Any],
    seeded: dict[str, Any] | None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    if seeded is None or freshness.get("status") != "fresh":
        return {"status": "stale", "reason": "seeded_reduction_evidence_not_current"}
    try:
        timeline = load_identity_json(match_path / TIMELINE_FILENAME)
    except (OSError, ValueError):
        return {"status": "stale", "reason": "candidate_timeline_missing_or_invalid"}

    source = seeded.get("source") or {}
    if canonical_digest(timeline) != str(source.get("timeline_digest") or ""):
        return {"status": "stale", "reason": "candidate_timeline_digest_mismatch"}

    candidate_subject_ids = {
        str(row.get("candidate_subject_id") or "")
        for section in ("accepted_assignments", "unresolved_subjects")
        for row in seeded.get(section) or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    for conflict in seeded.get("conflicts") or []:
        if isinstance(conflict, dict):
            candidate_subject_ids.update(
                str(value) for value in conflict.get("candidate_subject_ids") or [] if value
            )
    if not candidate_subject_ids:
        return {
            "status": "fresh",
            "required_observation_keys": [],
            "safe_to_stop": True,
            "source": "seeded_reduction_no_candidate_subjects",
        }

    subject_by_observation = {
        (
            str(observation.get("tracklet_id") or ""),
            int(observation.get("frame") or 0),
        ): str(subject.get("shadow_subject_id") or "")
        for subject in timeline.get("subjects") or []
        if isinstance(subject, dict)
        for observation in subject.get("observations") or []
        if isinstance(observation, dict)
        and str(subject.get("shadow_subject_id") or "") in candidate_subject_ids
    }
    audit_document = build_initial_identity_audit_document(selection, {})
    required_keys: list[str] = []
    seen_subjects: set[str] = set()
    for frame in audit_document.get("frames") or []:
        for observation in frame.get("observations") or []:
            provenance = observation.get("provenance") or {}
            subject_id = subject_by_observation.get(
                (
                    str(provenance.get("tracklet_id") or ""),
                    int(frame.get("frame_number") or 0),
                )
            )
            if not subject_id or subject_id in seen_subjects:
                continue
            required_keys.append(str(observation.get("observation_key") or ""))
            seen_subjects.add(subject_id)
            if len(required_keys) >= INITIAL_AUDIT_CASE_LIMIT:
                break
        if len(required_keys) >= INITIAL_AUDIT_CASE_LIMIT:
            break

    report_path = match_path / REPORT_FILENAME
    safe_to_stop = False
    if report_path.exists():
        try:
            report = load_identity_json(report_path)
            report_source = report.get("source") or {}
            safe_to_stop = (
                report.get("status") == "fresh"
                and report_source.get("seeded_assignments_digest")
                == freshness.get("seeded_assignments_digest")
                and int(
                    ((report.get("metrics") or {}).get("review_cards_after_seeding")
                    or 0)
                )
                == 0
            )
        except (OSError, ValueError):
            safe_to_stop = False
    return {
        "status": "fresh",
        "required_observation_keys": [key for key in required_keys if key],
        "safe_to_stop": safe_to_stop,
        "source": "seeded_candidate_reduction",
    }


def _current_seed_document(
    match_path: Path,
    fallback_seeds_path: Path,
) -> dict[str, Any]:
    """Load the canonical combined seeds, with a legacy test-safe fallback.

    Older artifacts and small unit-test fixtures may predate an audit selection
    document.  In that one case the raw initial-audit seeds remain the canonical
    input.  Real audit selections use the same combined document as the
    candidate-assignment rebuild, preventing false stale reports.
    """
    try:
        return load_combined_operator_seeds(match_path)
    except FileNotFoundError:
        return load_identity_json(fallback_seeds_path)


def apply_identity_seeded_review_reduction(
    cards: list[dict[str, Any]],
    seeded_assignments: dict[str, Any] | None,
    *,
    freshness: dict[str, Any] | None = None,
    operator_telemetry: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_cards = [dict(card) for card in cards]
    before_count = sum(_requires_review(card) for card in source_cards)
    manual_before = sum(_has_manual_decision(card) for card in source_cards)
    status = dict(freshness or {"status": "missing", "reason_codes": []})

    if seeded_assignments is None or status.get("status") != "fresh":
        report = _report(
            status=status,
            seeded_assignments=None,
            review_cards_before=before_count,
            review_cards_after=before_count,
            manual_before=manual_before,
            manual_after=manual_before,
            false_assignments=[],
            blocked_player_options=0,
            operator_telemetry=operator_telemetry,
        )
        return source_cards, report

    accepted_by_subject = {
        str(row.get("candidate_subject_id")): dict(row)
        for row in seeded_assignments.get("accepted_assignments") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    conflicts_by_subject = _conflicts_by_subject(seeded_assignments)
    accepted_intervals = _accepted_player_intervals(accepted_by_subject.values())
    false_assignments: list[dict[str, Any]] = []
    blocked_player_options = 0
    reduced: list[dict[str, Any]] = []

    for source_card in source_cards:
        card = dict(source_card)
        subject_id = str(card.get("candidate_subject_id") or "")
        accepted = accepted_by_subject.get(subject_id)
        conflicts = conflicts_by_subject.get(subject_id, [])
        existing_player_id = _manual_player_id(card)

        if accepted is not None:
            seeded_player = dict(accepted.get("assigned_player") or {})
            seeded_player_id = str(seeded_player.get("player_id") or "")
            if existing_player_id and existing_player_id != seeded_player_id:
                false_assignments.append(
                    _false_assignment(card, seeded_player_id, existing_player_id)
                )
                card = _as_seed_conflict(
                    card,
                    conflict_type="manual_assignment_disagrees_with_initial_audit",
                    seeded_player=seeded_player,
                )
            elif conflicts:
                card = _as_seed_conflict(
                    card,
                    conflict_type="seeded_assignment_conflict",
                    seeded_player=seeded_player,
                    conflict_rows=conflicts,
                )
            else:
                card = _as_seed_completed(card, accepted)
        elif conflicts:
            card = _as_seed_conflict(
                card,
                conflict_type="seeded_assignment_conflict",
                conflict_rows=conflicts,
            )
        else:
            card, removed = _block_overlapping_seeded_players(
                card,
                accepted_intervals,
            )
            blocked_player_options += removed
            if existing_player_id in set(card.get("overlapping_seeded_player_ids") or []):
                false_assignments.append(
                    _false_assignment(
                        card,
                        existing_player_id,
                        existing_player_id,
                        reason="parallel_manual_assignment_overlaps_seeded_subject",
                    )
                )
                card = _as_seed_conflict(
                    card,
                    conflict_type="parallel_manual_assignment_overlaps_seeded_subject",
                )

        card["decision_contract"] = _decision_contract(card)
        reduced.append(card)

    reduced.sort(key=_review_priority)
    after_count = sum(_requires_review(card) for card in reduced)
    manual_after = sum(
        _has_manual_decision(card) and _requires_review(card) for card in reduced
    )
    report = _report(
        status=status,
        seeded_assignments=seeded_assignments,
        review_cards_before=before_count,
        review_cards_after=after_count,
        manual_before=manual_before,
        manual_after=manual_after,
        false_assignments=false_assignments,
        blocked_player_options=blocked_player_options,
        operator_telemetry=operator_telemetry,
    )
    return reduced, report


def write_identity_seeded_review_reduction_report(
    match_path: Path,
    report: dict[str, Any],
) -> Path:
    path = match_path / REPORT_FILENAME
    write_identity_json_atomic(path, report)
    return path


def _as_seed_completed(
    card: dict[str, Any],
    accepted: dict[str, Any],
) -> dict[str, Any]:
    assigned_player = dict(accepted.get("assigned_player") or {})
    recommendation = {
        "player_id": assigned_player.get("player_id"),
        "player_name": assigned_player.get("name")
        or assigned_player.get("player_name"),
        "team_label": assigned_player.get("team_label"),
        "source": "initial_identity_audit",
        "confidence": 1.0,
    }
    return {
        **card,
        "review_status": COMPLETED_STATUS,
        "requires_operator_review": False,
        "recommended_player": recommendation,
        "seed_resolution": {
            "status": "accepted",
            "assigned_player": assigned_player,
            "seed_observations": accepted.get("seed_observations") or [],
            "tracklet_ids": accepted.get("tracklet_ids") or [],
            "start_frame": accepted.get("start_frame"),
            "end_frame": accepted.get("end_frame"),
            "reason_codes": accepted.get("reason_codes")
            or ["safe_seeded_subject_assignment"],
        },
        "allowed_actions": ["open_debug_context"],
    }


def _as_seed_conflict(
    card: dict[str, Any],
    *,
    conflict_type: str,
    seeded_player: dict[str, Any] | None = None,
    conflict_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    blockers = list(dict.fromkeys([
        *(str(value) for value in card.get("blockers") or []),
        conflict_type,
    ]))
    actions = ["assign_roster_player", "mark_unresolved", "open_debug_context"]
    return {
        **card,
        "review_status": CONFLICT_STATUS,
        "requires_operator_review": True,
        "blockers": blockers,
        "allowed_actions": actions,
        "seed_resolution": {
            "status": "conflict",
            "conflict_type": conflict_type,
            "assigned_player": seeded_player,
            "conflicts": conflict_rows or [],
        },
    }


def _block_overlapping_seeded_players(
    card: dict[str, Any],
    accepted_intervals: dict[str, list[tuple[int, int, str]]],
) -> tuple[dict[str, Any], int]:
    start_frame, end_frame = _frame_range(card)
    blocked: set[str] = set()
    for player_id, intervals in accepted_intervals.items():
        if any(
            subject_id != str(card.get("candidate_subject_id") or "")
            and _intervals_overlap(start_frame, end_frame, start, end)
            for start, end, subject_id in intervals
        ):
            blocked.add(player_id)
    if not blocked:
        return card, 0

    options = [
        row
        for row in card.get("operator_roster_options") or []
        if str(row.get("player_id") or "") not in blocked
    ]
    recommended = card.get("recommended_player")
    if str((recommended or {}).get("player_id") or "") in blocked:
        recommended = None
    return {
        **card,
        "operator_roster_options": options,
        "recommended_player": recommended,
        "overlapping_seeded_player_ids": sorted(blocked),
        "reason_codes": list(dict.fromkeys([
            *(str(value) for value in card.get("reason_codes") or []),
            "overlapping_seeded_player_option_blocked",
        ])),
    }, len(blocked)


def _accepted_player_intervals(
    accepted: Any,
) -> dict[str, list[tuple[int, int, str]]]:
    result: dict[str, list[tuple[int, int, str]]] = {}
    for row in accepted:
        player_id = str((row.get("assigned_player") or {}).get("player_id") or "")
        if not player_id:
            continue
        start = int(row.get("start_frame") or 0)
        end = int(row.get("end_frame") or start)
        result.setdefault(player_id, []).append(
            (start, end, str(row.get("candidate_subject_id") or ""))
        )
    return result


def _conflicts_by_subject(
    seeded_assignments: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in seeded_assignments.get("conflicts") or []:
        if not isinstance(row, dict):
            continue
        subject_ids = {
            str(value)
            for key in (
                "candidate_subject_ids",
                "subject_ids",
                "conflicting_subject_ids",
            )
            for value in row.get(key) or []
            if value
        }
        for key in ("candidate_subject_id", "source_subject_id", "target_subject_id"):
            if row.get(key):
                subject_ids.add(str(row[key]))
        for subject_id in subject_ids:
            result.setdefault(subject_id, []).append(dict(row))
    return result


def _report(
    *,
    status: dict[str, Any],
    seeded_assignments: dict[str, Any] | None,
    review_cards_before: int,
    review_cards_after: int,
    manual_before: int,
    manual_after: int,
    false_assignments: list[dict[str, Any]],
    blocked_player_options: int,
    operator_telemetry: dict[str, Any] | None,
) -> dict[str, Any]:
    seeded_summary = (seeded_assignments or {}).get("summary") or {}
    active_seconds = float(
        (operator_telemetry or {}).get("active_review_seconds") or 0.0
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "seed_aware_review_reduction_shadow",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
        },
        "status": status.get("status"),
        "reason_codes": status.get("reason_codes") or [],
        "source": {
            key: value
            for key, value in status.items()
            if key not in {"status", "reason_codes"}
        },
        "metrics": {
            "review_cards_before_seeding": review_cards_before,
            "review_cards_after_seeding": review_cards_after,
            "review_cards_reduced": max(
                0, review_cards_before - review_cards_after
            ),
            "subjects_resolved_after_seeding": int(
                seeded_summary.get("subjects_resolved_after_seeding") or 0
            ),
            "tracklets_resolved_after_seeding": int(
                seeded_summary.get("tracklets_resolved_after_seeding") or 0
            ),
            "frames_resolved_after_seeding": int(
                seeded_summary.get("frames_resolved_after_seeding") or 0
            ),
            "manual_decisions_before_seeding": manual_before,
            "manual_decisions_after_seeding": manual_after,
            "estimated_manual_decisions_remaining": review_cards_after,
            "active_review_seconds_before_seeding": active_seconds,
            "active_review_seconds_after_seeding": None,
            "active_time_after_measurable": False,
            "conflicts_created": int(
                seeded_summary.get("conflicts_created") or 0
            ),
            "false_assignments_found": len(false_assignments),
            "blocked_overlapping_player_options": blocked_player_options,
        },
        "false_assignments": false_assignments,
        "safety": {
            "mutates_production_identity": False,
            "writes_shadow_review_state_only": True,
            "seeded_completed_cards_are_not_manually_reassignable": True,
            "conflicts_remain_operator_visible": True,
        },
    }


def _false_assignment(
    card: dict[str, Any],
    seeded_player_id: str,
    manual_player_id: str,
    *,
    reason: str = "manual_assignment_disagrees_with_initial_audit",
) -> dict[str, Any]:
    return {
        "review_card_key": card.get("review_card_key"),
        "candidate_subject_id": card.get("candidate_subject_id"),
        "seeded_player_id": seeded_player_id,
        "manual_player_id": manual_player_id,
        "reason": reason,
    }


def _manual_player_id(card: dict[str, Any]) -> str:
    decision = card.get("operator_decision") or {}
    if decision.get("decision") not in {
        "assign_roster_player",
        "confirm_recommended_player",
    }:
        return ""
    return str(decision.get("player_id") or "")


def _has_manual_decision(card: dict[str, Any]) -> bool:
    return isinstance(card.get("operator_decision"), dict)


def _requires_review(card: dict[str, Any]) -> bool:
    return card.get("requires_operator_review") is not False


def _frame_range(card: dict[str, Any]) -> tuple[int, int]:
    start = int(card.get("start_frame") or 0)
    end = int(card.get("end_frame") or start)
    return min(start, end), max(start, end)


def _intervals_overlap(
    left_start: int,
    left_end: int,
    right_start: int,
    right_end: int,
) -> bool:
    return max(left_start, right_start) <= min(left_end, right_end)


def _decision_contract(card: dict[str, Any]) -> dict[str, Any]:
    contract = dict(card.get("decision_contract") or {})
    schema = dict(contract.get("decision_schema") or {})
    schema["player_id"] = [
        str(row["player_id"])
        for row in card.get("operator_roster_options") or []
        if row.get("player_id")
    ]
    contract["decision_schema"] = schema
    return contract


def _review_priority(card: dict[str, Any]) -> tuple[int, int, str]:
    status = str(card.get("review_status") or "")
    priority = 0 if status == CONFLICT_STATUS else 2 if status == COMPLETED_STATUS else 1
    return (
        priority,
        int(card.get("start_frame") or 0),
        str(card.get("review_card_key") or ""),
    )
