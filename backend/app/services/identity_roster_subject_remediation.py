from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from app.services.identity_promotion_safety import canonical_document_digest


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_roster_subject_structural_remediation"
ALGORITHM_VERSION = "0.1.0"
ALLOWED_ACTIONS = {
    "split_subject_at_tracklet_boundary",
    "split_subject_at_transition_frame",
    "assign_fragment",
    "mark_fragment_unresolved",
    "exclude_structural_fragment",
    "clear_remediation_decision",
}


def stable_fragment_key(
    *,
    candidate_subject_id: str,
    start_frame: int,
    end_frame: int,
    tracklet_ids: list[str] | None = None,
) -> str:
    payload = {
        "candidate_subject_id": str(candidate_subject_id),
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "tracklet_ids": sorted(str(value) for value in (tracklet_ids or [])),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"identity-fragment:v1:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def build_identity_roster_subject_remediation_plan(
    promotion_plan: dict[str, Any],
    decisions_doc: dict[str, Any] | None = None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Resolve or conservatively exclude P1.20A conflicts without production writes."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    promotion_digest = canonical_document_digest(promotion_plan)
    decisions = decisions_doc or {}
    supplied_decisions = [row for row in decisions.get("decisions") or [] if isinstance(row, dict)]
    decisions_fresh = not supplied_decisions or (
        decisions.get("source_promotion_plan_digest") == promotion_digest
    )
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not decisions_fresh:
        errors.append({"code": "stale_remediation_decisions"})

    normalized_decisions = _normalize_decisions(supplied_decisions, errors)
    canonical_rows = _canonical_rows(promotion_plan)
    rows_by_subject = _rows_by_subject(promotion_plan)
    structural_subject_ids = {
        str(row.get("candidate_subject_id") or "")
        for row in promotion_plan.get("structural_subjects") or []
    }

    excluded_keys: set[tuple[str, int, str]] = set()
    excluded_fragments: list[dict[str, Any]] = []
    unresolved_fragments: list[dict[str, Any]] = []
    split_fragments: list[dict[str, Any]] = []
    assigned_structural_rows: list[dict[str, Any]] = []
    actions_applied: list[dict[str, Any]] = []

    decisions_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in normalized_decisions:
        decisions_by_subject[str(decision.get("candidate_subject_id") or "")].append(decision)

    for subject in promotion_plan.get("structural_subjects") or []:
        subject_id = str(subject.get("candidate_subject_id") or "")
        subject_rows = rows_by_subject.get(subject_id, [])
        subject_decisions = decisions_by_subject.get(subject_id, []) if decisions_fresh else []
        if not subject_decisions:
            fragment = _fragment_payload(
                subject_id,
                subject_rows,
                fallback_start=int(subject.get("start_frame") or 0),
                fallback_end=int(subject.get("end_frame") or 0),
                tracklet_ids=[str(value) for value in subject.get("tracklet_ids") or []],
                reason="structural_conflict_auto_excluded",
            )
            excluded_fragments.append(fragment)
            continue
        for decision in sorted(subject_decisions, key=_decision_sort_key):
            action = str(decision.get("action") or "")
            selected = _select_fragment_rows(subject_rows, decision)
            actions_applied.append(_action_result(decision, len(selected)))
            if action == "assign_fragment":
                player_id = str(decision.get("player_id") or "")
                if not player_id:
                    errors.append({"code": "assign_fragment_missing_player_id", "decision_key": decision["decision_key"]})
                    continue
                assigned_structural_rows.extend({**row, "player_id": player_id} for row in selected)
            elif action in {"exclude_structural_fragment", "clear_remediation_decision"}:
                excluded_fragments.append(
                    _fragment_payload_from_decision(decision, selected, "operator_excluded_structural_fragment")
                )
            elif action == "mark_fragment_unresolved":
                unresolved_fragments.append(
                    _fragment_payload_from_decision(decision, selected, "operator_marked_unresolved")
                )
            elif action.startswith("split_subject_at_"):
                fragments = _split_selected_rows(decision, selected, errors)
                split_fragments.extend(fragments)
                unresolved_fragments.extend(
                    {**fragment, "reason": "split_fragment_requires_assignment"}
                    for fragment in fragments
                )

    unsafe_duplicates = [
        row
        for row in promotion_plan.get("duplicate_observations") or []
        if isinstance(row, dict) and not bool(row.get("safe_to_deduplicate"))
    ]
    for duplicate in unsafe_duplicates:
        kept = duplicate.get("kept_observation") or {}
        key = _observation_key(kept)
        if key is not None:
            excluded_keys.add(key)
        excluded_fragments.append(
            {
                "fragment_key": stable_fragment_key(
                    candidate_subject_id=str(duplicate.get("kept_candidate_subject_id") or ""),
                    start_frame=int(duplicate.get("frame") or 0),
                    end_frame=int(duplicate.get("frame") or 0),
                    tracklet_ids=[str(duplicate.get("kept_tracklet_id") or "")],
                ),
                "candidate_subject_id": duplicate.get("kept_candidate_subject_id"),
                "start_frame": int(duplicate.get("frame") or 0),
                "end_frame": int(duplicate.get("frame") or 0),
                "tracklet_ids": [str(duplicate.get("kept_tracklet_id") or "")],
                "reason": "unsafe_parallel_duplicate_excluded",
                "classification": duplicate.get("classification"),
            }
        )

    eligible_rows = [row for row in canonical_rows if _observation_key(row) not in excluded_keys]
    eligible_rows.extend(assigned_structural_rows)
    eligible_rows = _deduplicate_exact_source(eligible_rows, errors)
    excluded_fragments = _deduplicate_fragments(excluded_fragments)
    unresolved_fragments = _deduplicate_fragments(unresolved_fragments)
    split_fragments = _deduplicate_fragments(split_fragments)
    structural_remaining = len(structural_subject_ids - set(decisions_by_subject))
    status = "blocked" if errors else "ready_for_partial_candidate"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_structural_remediation",
        "status": status,
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source": {
            "promotion_plan_digest": promotion_digest,
            "remediation_decisions_digest": canonical_document_digest(decisions) if decisions else None,
            "decisions_fresh": decisions_fresh,
        },
        "safety": {
            "writes_shadow_only": True,
            "mutates_production_identity": False,
            "allows_partial_candidate": True,
        },
        "summary": {
            "input_canonical_observations": len(canonical_rows),
            "eligible_observations": len(eligible_rows),
            "assigned_structural_observations": len(assigned_structural_rows),
            "excluded_fragments": len(excluded_fragments),
            "unresolved_fragments": len(unresolved_fragments),
            "split_fragments": len(split_fragments),
            "unsafe_duplicate_frames_excluded": len(excluded_keys),
            "structural_subjects_auto_excluded": structural_remaining,
            "actions_applied": len(actions_applied),
            "blocking_errors": len(errors),
        },
        "eligible_observations": eligible_rows,
        "excluded_fragments": excluded_fragments,
        "unresolved_fragments": unresolved_fragments,
        "split_fragments": split_fragments,
        "actions_applied": actions_applied,
        "errors": errors,
        "warnings": warnings,
    }


def build_empty_remediation_decisions(promotion_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "mode": "shadow_structural_remediation_decisions",
        "source_promotion_plan_digest": canonical_document_digest(promotion_plan),
        "decisions": [],
    }


def _canonical_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for player in plan.get("canonical_coverage") or []
        for row in player.get("frame_records") or []
        if isinstance(row, dict)
    ]


def _rows_by_subject(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for subject in plan.get("structural_subjects") or []:
        subject_id = str(subject.get("candidate_subject_id") or "")
        result[subject_id].extend(row for row in subject.get("frame_records") or [] if isinstance(row, dict))
    return result


def _normalize_decisions(
    decisions: list[dict[str, Any]], errors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(decisions):
        action = str(raw.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            errors.append({"code": "unsupported_remediation_action", "index": index, "action": action})
            continue
        start = int(raw.get("start_frame") or 0)
        end = int(raw.get("end_frame") if raw.get("end_frame") is not None else start)
        subject_id = str(raw.get("candidate_subject_id") or "")
        tracklets = sorted(str(value) for value in raw.get("tracklet_ids") or [])
        normalized = {
                **raw,
                "candidate_subject_id": subject_id,
                "start_frame": min(start, end),
                "end_frame": max(start, end),
                "tracklet_ids": tracklets,
                "decision_key": str(raw.get("decision_key") or stable_fragment_key(
                    candidate_subject_id=subject_id,
                    start_frame=min(start, end),
                    end_frame=max(start, end),
                    tracklet_ids=tracklets,
                )),
            }
        key = str(normalized["decision_key"])
        if action == "clear_remediation_decision":
            target_key = str(raw.get("target_decision_key") or key)
            active.pop(target_key, None)
            continue
        active[key] = normalized
    return [active[key] for key in sorted(active)]


def _select_fragment_rows(rows: list[dict[str, Any]], decision: dict[str, Any]) -> list[dict[str, Any]]:
    start = int(decision["start_frame"])
    end = int(decision["end_frame"])
    tracklets = set(decision.get("tracklet_ids") or [])
    return [
        row for row in rows
        if start <= int(row.get("frame") or 0) <= end
        and (not tracklets or str(row.get("tracklet_id") or "") in tracklets)
    ]


def _fragment_payload(
    subject_id: str,
    rows: list[dict[str, Any]],
    *,
    fallback_start: int,
    fallback_end: int,
    tracklet_ids: list[str],
    reason: str,
) -> dict[str, Any]:
    start = min((int(row.get("frame") or 0) for row in rows), default=fallback_start)
    end = max((int(row.get("frame") or 0) for row in rows), default=fallback_end)
    row_tracklets = sorted({str(row.get("tracklet_id") or "") for row in rows}) or tracklet_ids
    return {
        "fragment_key": stable_fragment_key(
            candidate_subject_id=subject_id,
            start_frame=start,
            end_frame=end,
            tracklet_ids=row_tracklets,
        ),
        "candidate_subject_id": subject_id,
        "start_frame": start,
        "end_frame": end,
        "tracklet_ids": row_tracklets,
        "observation_count": len(rows),
        "reason": reason,
    }


def _fragment_payload_from_decision(
    decision: dict[str, Any], rows: list[dict[str, Any]], reason: str
) -> dict[str, Any]:
    return {
        "fragment_key": decision["decision_key"],
        "candidate_subject_id": decision["candidate_subject_id"],
        "start_frame": decision["start_frame"],
        "end_frame": decision["end_frame"],
        "tracklet_ids": decision.get("tracklet_ids") or [],
        "observation_count": len(rows),
        "reason": reason,
    }


def _split_selected_rows(
    decision: dict[str, Any],
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    action = str(decision.get("action") or "")
    if not rows:
        errors.append({"code": "split_fragment_has_no_observations", "decision_key": decision["decision_key"]})
        return []

    groups: list[list[dict[str, Any]]] = []
    if action == "split_subject_at_tracklet_boundary":
        by_tracklet: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_tracklet[str(row.get("tracklet_id") or "")].append(row)
        groups = [by_tracklet[key] for key in sorted(by_tracklet)]
        if len(groups) < 2:
            errors.append({"code": "split_requires_multiple_tracklets", "decision_key": decision["decision_key"]})
            return []
    else:
        split_frame = int(decision.get("transition_frame") or decision.get("split_frame") or 0)
        if split_frame <= int(decision["start_frame"]) or split_frame > int(decision["end_frame"]):
            errors.append({"code": "split_transition_frame_outside_fragment", "decision_key": decision["decision_key"]})
            return []
        groups = [
            [row for row in rows if int(row.get("frame") or 0) < split_frame],
            [row for row in rows if int(row.get("frame") or 0) >= split_frame],
        ]
        if any(not group for group in groups):
            errors.append({"code": "split_transition_creates_empty_fragment", "decision_key": decision["decision_key"]})
            return []

    fragments = []
    for index, group in enumerate(groups, start=1):
        fragment = _fragment_payload(
            str(decision["candidate_subject_id"]),
            group,
            fallback_start=int(decision["start_frame"]),
            fallback_end=int(decision["end_frame"]),
            tracklet_ids=sorted({str(row.get("tracklet_id") or "") for row in group}),
            reason="operator_split_fragment",
        )
        fragment["parent_decision_key"] = decision["decision_key"]
        fragment["fragment_index"] = index
        fragments.append(fragment)
    return fragments


def _action_result(decision: dict[str, Any], observation_count: int) -> dict[str, Any]:
    return {
        "decision_key": decision["decision_key"],
        "action": decision.get("action"),
        "candidate_subject_id": decision.get("candidate_subject_id"),
        "start_frame": decision.get("start_frame"),
        "end_frame": decision.get("end_frame"),
        "observation_count": observation_count,
    }


def _observation_key(row: dict[str, Any]) -> tuple[str, int, str] | None:
    if not row or row.get("frame") is None:
        return None
    return (
        str(row.get("player_id") or ""),
        int(row["frame"]),
        str(row.get("candidate_subject_id") or ""),
    )


def _deduplicate_exact_source(
    rows: list[dict[str, Any]], errors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_source: dict[tuple[int, str], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (
        int(item.get("frame") or 0),
        str(item.get("tracklet_id") or ""),
        str(item.get("candidate_subject_id") or ""),
    )):
        key = (int(row.get("frame") or 0), str(row.get("tracklet_id") or ""))
        previous = by_source.get(key)
        if previous and previous.get("player_id") != row.get("player_id"):
            errors.append(
                {
                    "code": "remediation_same_source_multiple_players",
                    "frame": key[0],
                    "tracklet_id": key[1],
                    "player_ids": sorted({str(previous.get("player_id")), str(row.get("player_id"))}),
                }
            )
            continue
        by_source.setdefault(key, row)
    return [by_source[key] for key in sorted(by_source)]


def _deduplicate_fragments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {str(row["fragment_key"]): row for row in rows}
    return sorted(
        unique.values(),
        key=lambda row: (
            str(row.get("candidate_subject_id") or ""),
            int(row.get("start_frame") or 0),
            int(row.get("end_frame") or 0),
            str(row.get("fragment_key") or ""),
        ),
    )


def _decision_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (int(row.get("start_frame") or 0), int(row.get("end_frame") or 0), str(row.get("decision_key") or ""))
