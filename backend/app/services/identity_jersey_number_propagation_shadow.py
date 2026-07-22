from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest, stable_key


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_propagation_shadow"
ALGORITHM_VERSION = "1.0.0"

BLOCKING_SUBJECT_FLAGS = {
    "cross_production_transition",
    "merges_production_subjects",
    "parallel_distant_observation",
    "parallel_roster_candidate_conflict",
    "roster_identity_conflict",
    "structural_identity_conflict",
    "team_switch",
    "temporal_overlap_conflict",
    "uncertain_transition",
}
UNSAFE_EDGE_STATUSES = {"ambiguous", "uncertain", "uncertain_transition"}
WEAK_EDGE_SOURCES = {"reid", "reid_only", "same_match_reid", "weak_reid"}
CONFIRMED_OPERATOR_DECISIONS = {
    "assign_roster_player",
    "confirm_recommended_player",
}


def build_identity_jersey_number_propagation_shadow(
    assignment_doc: dict[str, Any],
    evidence_doc: dict[str, Any],
    candidate_doc: dict[str, Any],
    timeline_doc: dict[str, Any],
    *,
    subject_review_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Propagate trusted jersey anchors only through explicit safe identity lineage."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    candidate_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in candidate_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    timeline_by_subject = {
        str(row.get("shadow_subject_id")): row
        for row in timeline_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("shadow_subject_id")
    }
    events_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in timeline_doc.get("transition_events") or []:
        if isinstance(event, dict) and event.get("shadow_subject_id"):
            events_by_subject[str(event["shadow_subject_id"])].append(event)

    evidence_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    confirmed_numbers_by_tracklet: dict[str, set[str]] = defaultdict(set)
    for row in evidence_doc.get("evidence") or []:
        if not isinstance(row, dict) or not row.get("candidate_subject_id"):
            continue
        subject_id = str(row["candidate_subject_id"])
        evidence_by_subject[subject_id].append(row)
        if row.get("state") == "number_confirmed" and row.get("tracklet_id") and row.get("number"):
            confirmed_numbers_by_tracklet[str(row["tracklet_id"])].add(str(row["number"]))

    review_by_subject = _review_cards(subject_review_doc or {})
    subjects: list[dict[str, Any]] = []
    edge_audit: list[dict[str, Any]] = []
    for assignment in assignment_doc.get("candidates") or []:
        if not isinstance(assignment, dict) or not assignment.get("strictly_eligible"):
            continue
        subject_id = str(assignment.get("candidate_subject_id") or "")
        result, audited_edges = _propagate_subject(
            assignment,
            evidence_by_subject.get(subject_id, []),
            candidate_by_subject.get(subject_id),
            timeline_by_subject.get(subject_id),
            events_by_subject.get(subject_id, []),
            confirmed_numbers_by_tracklet,
            review_by_subject.get(subject_id),
        )
        subjects.append(result)
        edge_audit.extend(audited_edges)

    edge_statuses = Counter(str(row.get("status")) for row in edge_audit)
    blocked_reasons = Counter(
        reason
        for row in edge_audit
        for reason in row.get("blockers") or []
    )
    source = {
        "assignment_digest": canonical_digest(assignment_doc),
        "evidence_digest": canonical_digest(evidence_doc),
        "candidate_digest": canonical_digest(candidate_doc),
        "timeline_digest": canonical_digest(timeline_doc),
        "subject_review_digest": canonical_digest(subject_review_doc or {}),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source": source,
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "writes_player_identity_assignments": False,
            "merges_tracklets": False,
            "number_similarity_creates_edges": False,
            "automatic_assignments": 0,
        },
        "summary": {
            "seed_subjects": len(subjects),
            "seed_tracklets": sum(len(row["seed_tracklet_ids"]) for row in subjects),
            "propagated_tracklets": sum(len(row["propagated_tracklet_ids"]) for row in subjects),
            "subjects_with_propagation": sum(bool(row["propagated_tracklet_ids"]) for row in subjects),
            "safe_edges": edge_statuses.get("accepted", 0),
            "blocked_edges": edge_statuses.get("blocked", 0),
            "blocked_reason_counts": dict(sorted(blocked_reasons.items())),
            "cross_subject_propagations": 0,
            "automatic_assignments": 0,
        },
        "subjects": sorted(subjects, key=lambda row: row["candidate_subject_id"]),
        "edge_audit": sorted(edge_audit, key=lambda row: row["propagation_edge_key"]),
    }


def _propagate_subject(
    assignment: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    candidate: dict[str, Any] | None,
    timeline_subject: dict[str, Any] | None,
    events: list[dict[str, Any]],
    confirmed_numbers_by_tracklet: dict[str, set[str]],
    review_card: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    subject_id = str(assignment.get("candidate_subject_id") or "")
    team_label = str(assignment.get("team_label") or "U")
    jersey_number = str(assignment.get("jersey_number") or "")
    candidate_tracklets = set(str(value) for value in (candidate or {}).get("tracklet_ids") or [])
    timeline_tracklets = set(str(value) for value in (timeline_subject or {}).get("tracklet_ids") or [])
    tracklet_ids = candidate_tracklets & timeline_tracklets if timeline_tracklets else candidate_tracklets
    seeds = {
        str(row["tracklet_id"])
        for row in evidence_rows
        if row.get("state") == "number_confirmed"
        and str(row.get("number") or "") == jersey_number
        and row.get("tracklet_id")
        and str(row.get("team_label") or "U") == team_label
        and str(row["tracklet_id"]) in tracklet_ids
    }
    operator_membership = _operator_confirms_membership(review_card, assignment)
    subject_blockers = _subject_blockers(candidate, timeline_subject, team_label)
    if operator_membership and not subject_blockers:
        seeds.update(tracklet_ids)

    graph: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    edge_audit: list[dict[str, Any]] = []
    for event in events:
        source_tracklet = str(event.get("source_tracklet_id") or "")
        target_tracklet = str(event.get("target_tracklet_id") or "")
        blockers = _edge_blockers(
            event,
            subject_id=subject_id,
            team_label=team_label,
            tracklet_ids=tracklet_ids,
            jersey_number=jersey_number,
            confirmed_numbers_by_tracklet=confirmed_numbers_by_tracklet,
            subject_blockers=subject_blockers,
        )
        audit = {
            "propagation_edge_key": stable_key(
                "jersey-propagation-edge",
                {"subject": subject_id, "source": source_tracklet, "target": target_tracklet},
            ),
            "candidate_subject_id": subject_id,
            "source_tracklet_id": source_tracklet,
            "target_tracklet_id": target_tracklet,
            "identity_edge_key": event.get("edge_key"),
            "recommendation_source": event.get("recommendation_source"),
            "status": "blocked" if blockers else "accepted",
            "blockers": blockers,
        }
        edge_audit.append(audit)
        if not blockers:
            graph[source_tracklet].append((target_tracklet, audit))
            graph[target_tracklet].append((source_tracklet, audit))

    paths: dict[str, list[str]] = {tracklet: [] for tracklet in sorted(seeds)}
    queue: deque[str] = deque(sorted(seeds))
    while queue:
        current = queue.popleft()
        for neighbor, audit in sorted(graph.get(current, []), key=lambda item: item[0]):
            if neighbor in paths:
                continue
            paths[neighbor] = [*paths[current], str(audit["propagation_edge_key"])]
            queue.append(neighbor)

    propagated = sorted(set(paths) - seeds)
    blocked_tracklets = sorted(tracklet_ids - set(paths))
    tracklets = []
    for tracklet_id in sorted(tracklet_ids):
        if tracklet_id in seeds:
            state = "seed"
        elif tracklet_id in paths:
            state = "propagated"
        else:
            state = "not_propagated"
        tracklets.append(
            {
                "tracklet_id": tracklet_id,
                "state": state,
                "path_edge_keys": paths.get(tracklet_id, []),
                "hop_count": len(paths.get(tracklet_id, [])),
                "contradictory_numbers": sorted(
                    value for value in confirmed_numbers_by_tracklet.get(tracklet_id, set()) if value != jersey_number
                ),
            }
        )
    return (
        {
            "propagation_key": stable_key(
                "jersey-propagation",
                {"candidate_subject_id": subject_id, "player_id": assignment.get("player_id")},
            ),
            "candidate_subject_id": subject_id,
            "team_label": team_label,
            "jersey_number": jersey_number,
            "player_id": assignment.get("player_id"),
            "player_name": assignment.get("player_name"),
            "operator_confirmed_subject_membership": operator_membership,
            "seed_tracklet_ids": sorted(seeds),
            "propagated_tracklet_ids": propagated,
            "blocked_tracklet_ids": blocked_tracklets,
            "subject_blockers": subject_blockers,
            "tracklets": tracklets,
        },
        edge_audit,
    )


def _subject_blockers(
    candidate: dict[str, Any] | None,
    timeline_subject: dict[str, Any] | None,
    team_label: str,
) -> list[str]:
    blockers: list[str] = []
    if not candidate:
        blockers.append("missing_candidate_subject")
    if not timeline_subject:
        blockers.append("missing_timeline_subject")
    candidate_team = str((candidate or {}).get("team_label") or "U")
    timeline_team = str((timeline_subject or {}).get("team_label") or "U")
    if candidate_team != team_label or timeline_team != team_label:
        blockers.append("subject_team_mismatch")
    candidate_tracklets = {
        str(value) for value in (candidate or {}).get("tracklet_ids") or []
    }
    timeline_tracklets = {
        str(value) for value in (timeline_subject or {}).get("tracklet_ids") or []
    }
    if candidate and timeline_subject and candidate_tracklets != timeline_tracklets:
        blockers.append("candidate_timeline_tracklet_mismatch")
    flags = set(str(value) for value in (candidate or {}).get("quality_flags") or [])
    blockers.extend(sorted(flags & BLOCKING_SUBJECT_FLAGS))
    return sorted(set(blockers))


def _edge_blockers(
    event: dict[str, Any],
    *,
    subject_id: str,
    team_label: str,
    tracklet_ids: set[str],
    jersey_number: str,
    confirmed_numbers_by_tracklet: dict[str, set[str]],
    subject_blockers: list[str],
) -> list[str]:
    blockers = list(subject_blockers)
    source_tracklet = str(event.get("source_tracklet_id") or "")
    target_tracklet = str(event.get("target_tracklet_id") or "")
    if str(event.get("shadow_subject_id") or "") != subject_id:
        blockers.append("cross_subject_edge")
    if str(event.get("team_label") or "U") != team_label:
        blockers.append("edge_team_mismatch")
    if source_tracklet not in tracklet_ids or target_tracklet not in tracklet_ids:
        blockers.append("edge_tracklet_outside_subject")
    if bool(event.get("requires_review")):
        blockers.append("edge_requires_review")
    if str(event.get("status") or "") in UNSAFE_EDGE_STATUSES:
        blockers.append("uncertain_transition")
    if str(event.get("current_identity_relation") or "") == "different_subjects":
        blockers.append("cross_production_transition")
    if int(event.get("overlap_frames") or 0) > 0:
        blockers.append("temporal_overlap_conflict")
    source = str(event.get("recommendation_source") or "").lower()
    if source in WEAK_EDGE_SOURCES or ("reid" in source and "operator" not in source):
        blockers.append("weak_reid_only_edge")
    for tracklet_id in (source_tracklet, target_tracklet):
        contradictory = {
            value for value in confirmed_numbers_by_tracklet.get(tracklet_id, set()) if value != jersey_number
        }
        if contradictory:
            blockers.append("contradictory_number_evidence")
    return sorted(set(blockers))


def _review_cards(subject_review_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("candidate_subject_id")): row
        for row in subject_review_doc.get("cards") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }


def _operator_confirms_membership(
    review_card: dict[str, Any] | None,
    assignment: dict[str, Any],
) -> bool:
    decision = (review_card or {}).get("operator_decision") or {}
    if str(decision.get("decision") or "") not in CONFIRMED_OPERATOR_DECISIONS:
        return False
    selected_player = decision.get("player_id") or (review_card or {}).get("recommended_player", {}).get("player_id")
    return bool(selected_player and str(selected_player) == str(assignment.get("player_id") or ""))
