from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit import AUDIT_DIRECTORY, SELECTION_FILENAME
from app.services.identity_initial_audit_store import (
    SEEDS_FILENAME,
    find_identity_artifact,
    load_identity_json,
    production_identity_snapshot,
    write_identity_json_atomic,
)
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_operator_seed_digest import (
    DIGEST_CONTRACT,
    identity_operator_seed_decisions_digest,
)
from app.services.identity_second_half_reanchor import (
    REANCHOR_DIRECTORY,
    SELECTION_FILENAME as REANCHOR_SELECTION_FILENAME,
)
from app.services.identity_second_half_reanchor_store import (
    SEEDS_FILENAME as REANCHOR_SEEDS_FILENAME,
)


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_seeded_candidate_assignments"
ALGORITHM_VERSION = "0.2.0"
OUTPUT_FILENAME = "identity_seeded_candidate_assignments.json"
CANDIDATE_FILENAME = "identity_candidate_shadow.json"
TIMELINE_FILENAME = "identity_offline_shadow_timeline.json"

HARD_STRUCTURAL_BLOCKERS = frozenset(
    {
        "cross_production_transition",
        "merges_multiple_production_subjects",
        "merges_production_subjects",
        "production_anchor_team_mismatch",
        "uncertain_transition",
    }
)
NON_PROPAGATING_ACTION_REASONS = {
    "team_a_unknown": "team_only_seed",
    "team_b_unknown": "team_only_seed",
    "referee": "non_player_observation",
    "false_detection": "false_detection_observation",
    "skip": "operator_skipped_observation",
}


class SeededCandidateAssignmentsStaleError(ValueError):
    pass


def build_identity_seeded_candidate_assignments(
    seeds_document: dict[str, Any],
    candidate_document: dict[str, Any],
    timeline_document: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Resolve operator seeds into safe shadow candidate assignments."""
    generated = (
        generated_at
        or str(seeds_document.get("updated_at") or "")
        or datetime.now(timezone.utc).isoformat()
    )
    candidates = _candidate_index(candidate_document)
    timeline = _timeline_index(timeline_document)
    exact_observation_seeds: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    decisions = sorted(
        (
            row
            for row in seeds_document.get("decisions") or []
            if isinstance(row, dict)
        ),
        key=_decision_sort_key,
    )
    for decision in decisions:
        exact_seed = _exact_observation_seed(decision)
        exact_observation_seeds.append(exact_seed)
        if decision.get("action") != "assign_roster_player":
            rejected.append(
                _rejected_seed(
                    decision,
                    [NON_PROPAGATING_ACTION_REASONS.get(
                        str(decision.get("action") or ""),
                        "unsupported_operator_action",
                    )],
                )
            )
            continue

        proposal, reasons, candidate_subject_ids = _proposal_for_named_seed(
            decision,
            candidates=candidates,
            timeline=timeline,
        )
        if proposal is None:
            rejected.append(
                _rejected_seed(
                    decision,
                    reasons,
                    candidate_subject_ids=candidate_subject_ids,
                )
            )
            continue
        proposals.append(proposal)

    accepted, proposal_rejections, conflicts = _resolve_proposal_conflicts(
        proposals,
        timeline=timeline,
    )
    rejected.extend(proposal_rejections)
    rejected.sort(key=_rejection_sort_key)
    conflicts.sort(key=lambda row: str(row.get("conflict_key") or ""))
    accepted.sort(key=lambda row: str(row.get("candidate_subject_id") or ""))

    accepted_subject_ids = {
        str(row.get("candidate_subject_id") or "") for row in accepted
    }
    rejected_by_subject: dict[str, set[str]] = defaultdict(set)
    for row in rejected:
        for subject_id in row.get("candidate_subject_ids") or []:
            rejected_by_subject[str(subject_id)].update(
                str(reason) for reason in row.get("reasons") or []
            )
    unresolved = []
    for subject_id, candidate in sorted(candidates.items()):
        if subject_id in accepted_subject_ids:
            continue
        reasons = sorted(rejected_by_subject.get(subject_id) or {"no_operator_seed"})
        unresolved.append(
            {
                "candidate_subject_id": subject_id,
                "candidate_player_id": candidate.get("candidate_player_id"),
                "team_label": candidate.get("team_label"),
                "tracklet_ids": sorted(
                    str(value) for value in candidate.get("tracklet_ids") or []
                ),
                "reasons": reasons,
            }
        )

    accepted_tracklets = {
        str(tracklet_id)
        for row in accepted
        for tracklet_id in row.get("tracklet_ids") or []
    }
    accepted_frames = {
        int(observation.get("frame") or 0)
        for row in accepted
        for observation in (
            timeline.get(str(row.get("candidate_subject_id") or ""), {}).get(
                "observations"
            )
            or []
        )
    }
    cross_team_links = sum(
        str(row.get("team_label") or "")
        != str((row.get("assigned_player") or {}).get("team_label") or "")
        for row in accepted
    )
    parallel_conflicts = sum(
        row.get("conflict_type") == "parallel_same_player"
        for row in conflicts
    )
    accepted_parallel_conflicts = _accepted_parallel_assignment_count(
        accepted,
        timeline=timeline,
    )
    operator_decisions_digest = identity_operator_seed_decisions_digest(
        seeds_document
    )
    source = {
        "operator_seeds_digest": operator_decisions_digest,
        "operator_seed_decisions_digest": operator_decisions_digest,
        "operator_seed_decisions_digest_contract": DIGEST_CONTRACT,
        "operator_seeds_document_digest": canonical_digest(seeds_document),
        "candidate_identity_digest": canonical_digest(candidate_document),
        "timeline_digest": canonical_digest(timeline_document),
        "selection_digest": (seeds_document.get("source") or {}).get(
            "selection_digest"
        ),
        "selection_artifact_digest": (seeds_document.get("source") or {}).get(
            "selection_artifact_digest"
        ),
        "selection_sources": list(
            (seeds_document.get("source") or {}).get("selection_sources") or []
        ),
        "frozen_tracks_reused": True,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "seed_aware_candidate_shadow",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
        },
        "source": source,
        "exact_observation_seeds": exact_observation_seeds,
        "accepted_assignments": accepted,
        "rejected_propagations": rejected,
        "conflicts": conflicts,
        "unresolved_subjects": unresolved,
        "summary": {
            "operator_decisions": len(decisions),
            "named_player_seeds": sum(
                row.get("action") == "assign_roster_player"
                for row in decisions
            ),
            "exact_observation_seeds": len(exact_observation_seeds),
            "candidate_subjects": len(candidates),
            "subjects_resolved_after_seeding": len(accepted),
            "tracklets_resolved_after_seeding": len(accepted_tracklets),
            "frames_resolved_after_seeding": len(accepted_frames),
            "rejected_propagations": len(rejected),
            "conflicts_created": len(conflicts),
            "unresolved_subjects": len(unresolved),
        },
        "safety": {
            "mutates_production_identity": False,
            "production_identity_untouched": True,
            "eligible_for_player_stats": False,
            "eligible_for_roster_assignment": False,
            "promotion_status": "shadow_only",
            "cross_team_links": cross_team_links,
            "parallel_assignment_conflicts_detected": parallel_conflicts,
            "parallel_assignment_conflicts_blocked": parallel_conflicts,
            "impossible_parallel_assignments": accepted_parallel_conflicts,
            "unresolved_remains_explicit": True,
            "yolo_not_required": True,
        },
    }


def rebuild_identity_seeded_candidate_assignments(
    match_path: Path,
    match_document: dict[str, Any],
) -> dict[str, Any]:
    seeds_document = load_combined_operator_seeds(match_path)

    candidate_path = find_identity_artifact(
        match_path,
        match_document,
        CANDIDATE_FILENAME,
    )
    timeline_path = find_identity_artifact(
        match_path,
        match_document,
        TIMELINE_FILENAME,
    )
    if candidate_path is None:
        raise FileNotFoundError(f"{CANDIDATE_FILENAME} is missing")
    if timeline_path is None:
        raise FileNotFoundError(f"{TIMELINE_FILENAME} is missing")

    production_before = production_identity_snapshot(
        match_path,
        match_document,
    )
    document = build_identity_seeded_candidate_assignments(
        seeds_document,
        load_identity_json(candidate_path),
        load_identity_json(timeline_path),
    )
    if (
        int(document["safety"]["cross_team_links"]) > 0
        or int(document["safety"]["impossible_parallel_assignments"]) > 0
    ):
        raise RuntimeError("Seeded candidate assignments failed safety gates")
    write_identity_json_atomic(match_path / OUTPUT_FILENAME, document)

    production_after = production_identity_snapshot(
        match_path,
        match_document,
    )
    if production_after != production_before:
        raise RuntimeError(
            "Production identity artifacts changed during seeded shadow rebuild"
        )
    return document


def load_identity_seeded_candidate_assignments(
    match_path: Path,
) -> dict[str, Any]:
    path = match_path / OUTPUT_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            "Seed-aware candidate assignments have not been generated"
        )
    return load_identity_json(path)


def load_combined_operator_seeds(match_path: Path) -> dict[str, Any]:
    """Return the canonical set of decisions used by seeded shadow rebuilds.

    Both the seed-aware candidate builder and the downstream review reducer must
    hash this exact combined document.  A raw initial-audit document is not
    equivalent: the combined document adds audit-stage lineage and can also
    contain second-half re-anchor decisions.
    """
    seed_sources = [
        {
            "audit_stage": "initial_identity_audit",
            "seed_path": match_path / SEEDS_FILENAME,
            "selection_path": (
                match_path / AUDIT_DIRECTORY / SELECTION_FILENAME
            ),
            "required": True,
        },
        {
            "audit_stage": "second_half_identity_reanchor",
            "seed_path": (
                match_path / REANCHOR_DIRECTORY / REANCHOR_SEEDS_FILENAME
            ),
            "selection_path": (
                match_path
                / REANCHOR_DIRECTORY
                / REANCHOR_SELECTION_FILENAME
            ),
            "required": False,
        },
    ]
    documents: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    for source in seed_sources:
        seed_path = Path(source["seed_path"])
        selection_path = Path(source["selection_path"])
        if not seed_path.exists():
            if source["required"]:
                raise FileNotFoundError(
                    "Initial Identity Audit seeds are missing"
                )
            continue
        if not selection_path.exists():
            raise FileNotFoundError(
                f"{source['audit_stage']} selection is missing"
            )
        seed_document = load_identity_json(seed_path)
        selection_document = load_identity_json(selection_path)
        stored_digest = (seed_document.get("source") or {}).get(
            "selection_artifact_digest"
        )
        current_digest = canonical_digest(selection_document)
        if stored_digest != current_digest:
            raise SeededCandidateAssignmentsStaleError(
                f"{source['audit_stage']} seeds are stale for the current "
                "selection"
            )
        documents.append(seed_document)
        source_rows.append(
            {
                "audit_stage": source["audit_stage"],
                "selection_digest": (
                    seed_document.get("source") or {}
                ).get("selection_digest"),
                "selection_artifact_digest": current_digest,
                "seed_decisions_digest": (
                    identity_operator_seed_decisions_digest(seed_document)
                ),
            }
        )

    decisions_by_key: dict[str, dict[str, Any]] = {}
    for seed_document, source in zip(documents, source_rows):
        for decision in seed_document.get("decisions") or []:
            if not isinstance(decision, dict):
                continue
            observation_key = str(decision.get("observation_key") or "")
            if not observation_key:
                continue
            decisions_by_key[observation_key] = {
                **decision,
                "audit_stage": (
                    decision.get("audit_stage")
                    or source["audit_stage"]
                ),
            }

    initial_source = (
        documents[0].get("source") or {} if documents else {}
    )
    updated_at = max(
        (
            str(document.get("updated_at") or "")
            for document in documents
        ),
        default="",
    )
    return {
        "schema_version": "0.1.0",
        "mode": "combined_operator_identity_seeds",
        "source": {
            "selection_digest": initial_source.get("selection_digest"),
            "selection_artifact_digest": initial_source.get(
                "selection_artifact_digest"
            ),
            "selection_sources": source_rows,
        },
        "decisions": sorted(
            decisions_by_key.values(),
            key=_decision_sort_key,
        ),
        "updated_at": updated_at or None,
    }


def _candidate_index(
    document: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("candidate_subject_id")): dict(row)
        for row in document.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }


def _timeline_index(
    document: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("shadow_subject_id")): dict(row)
        for row in document.get("subjects") or []
        if isinstance(row, dict) and row.get("shadow_subject_id")
    }


def _decision_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(row.get("frame_number") or 0),
        int(row.get("display_order") or 0),
        str(row.get("observation_key") or ""),
    )


def _exact_observation_seed(
    decision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "observation_key": decision.get("observation_key"),
        "frame_number": decision.get("frame_number"),
        "action": decision.get("action"),
        "assigned_team": decision.get("assigned_team"),
        "assigned_player": decision.get("assigned_player"),
        "provenance": decision.get("provenance"),
        "audit_stage": decision.get("audit_stage")
        or "initial_identity_audit",
        "operator_certainty": "certain_or_explicit_skip",
        "propagation_scope": "observation_only",
    }


def _proposal_for_named_seed(
    decision: dict[str, Any],
    *,
    candidates: dict[str, dict[str, Any]],
    timeline: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    provenance = decision.get("provenance") or {}
    tracklet_id = str(provenance.get("tracklet_id") or "")
    frame_number = int(decision.get("frame_number") or 0)
    assigned_team = str(
        (decision.get("assigned_team") or {}).get("team_label") or ""
    )
    candidate_subject_ids = _subjects_for_observation(
        timeline,
        tracklet_id=tracklet_id,
        frame_number=frame_number,
    )
    if not tracklet_id:
        return None, ["missing_tracklet_lineage"], []
    if not candidate_subject_ids:
        return None, ["no_candidate_subject_for_exact_observation"], []
    if len(candidate_subject_ids) > 1:
        return (
            None,
            ["ambiguous_candidate_subject_for_observation"],
            candidate_subject_ids,
        )

    subject_id = candidate_subject_ids[0]
    candidate = candidates.get(subject_id)
    timeline_subject = timeline.get(subject_id)
    if candidate is None or timeline_subject is None:
        return None, ["candidate_timeline_lineage_mismatch"], [subject_id]
    candidate_team = str(candidate.get("team_label") or "")
    if assigned_team not in {"A", "B"}:
        return None, ["operator_player_team_missing"], [subject_id]
    if candidate_team != assigned_team:
        return (
            None,
            ["candidate_team_conflicts_operator_team"],
            [subject_id],
        )
    blockers = sorted(
        set(str(value) for value in candidate.get("quality_flags") or [])
        & HARD_STRUCTURAL_BLOCKERS
    )
    if blockers:
        return (
            None,
            [f"structural_blocker:{value}" for value in blockers],
            [subject_id],
        )

    player = dict(decision.get("assigned_player") or {})
    player["team_label"] = assigned_team
    return (
        {
            "candidate_subject_id": subject_id,
            "candidate_player_id": candidate.get("candidate_player_id"),
            "team_label": candidate_team,
            "role": candidate.get("role"),
            "tracklet_ids": sorted(
                str(value) for value in candidate.get("tracklet_ids") or []
            ),
            "start_frame": candidate.get("start_frame"),
            "end_frame": candidate.get("end_frame"),
            "assigned_player": player,
            "seed_observations": [
                {
                    "observation_key": decision.get("observation_key"),
                    "frame_number": frame_number,
                    "tracklet_id": tracklet_id,
                    "audit_stage": decision.get("audit_stage")
                    or "initial_identity_audit",
                }
            ],
            "accepted_reasons": [
                "operator_certain_observation",
                "exact_tracklet_lineage",
                "candidate_team_matches_operator_team",
                "no_hard_structural_blocker",
            ],
            "propagation_provenance": {
                "source": decision.get("audit_stage")
                or "initial_identity_audit",
                "scope": "candidate_subject",
                "local_tracklet_continuity": True,
                "team_consistency": True,
                "structural_gates_passed": True,
                "production_identity_mutated": False,
            },
        },
        [],
        [subject_id],
    )


def _subjects_for_observation(
    timeline: dict[str, dict[str, Any]],
    *,
    tracklet_id: str,
    frame_number: int,
) -> list[str]:
    matches = []
    for subject_id, subject in timeline.items():
        for observation in subject.get("observations") or []:
            if (
                str(observation.get("tracklet_id") or "") == tracklet_id
                and int(observation.get("frame") or 0) == frame_number
            ):
                matches.append(subject_id)
                break
    return sorted(set(matches))


def _resolve_proposal_conflicts(
    proposals: list[dict[str, Any]],
    *,
    timeline: dict[str, dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    rejected: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    grouped_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proposal in proposals:
        grouped_by_subject[str(proposal["candidate_subject_id"])].append(
            proposal
        )

    merged: list[dict[str, Any]] = []
    for subject_id, rows in sorted(grouped_by_subject.items()):
        player_ids = {
            str((row.get("assigned_player") or {}).get("player_id") or "")
            for row in rows
        }
        if len(player_ids) > 1:
            conflict = {
                "conflict_key": f"subject:{subject_id}",
                "conflict_type": "multiple_players_for_candidate_subject",
                "candidate_subject_ids": [subject_id],
                "player_ids": sorted(player_ids),
                "seed_observation_keys": sorted(
                    str(seed.get("observation_key") or "")
                    for row in rows
                    for seed in row.get("seed_observations") or []
                ),
            }
            conflicts.append(conflict)
            rejected.extend(
                _rejected_proposal(
                    row,
                    ["conflicting_operator_seeds_for_subject"],
                )
                for row in rows
            )
            continue
        merged.append(_merge_same_subject_proposals(rows))

    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proposal in merged:
        player_id = str(
            (proposal.get("assigned_player") or {}).get("player_id") or ""
        )
        by_player[player_id].append(proposal)

    blocked_subjects: set[str] = set()
    for player_id, rows in sorted(by_player.items()):
        if len(rows) < 2:
            continue
        for left_index, left in enumerate(rows):
            for right in rows[left_index + 1 :]:
                common_frames = sorted(
                    _detected_frames(
                        timeline.get(str(left["candidate_subject_id"]), {})
                    )
                    & _detected_frames(
                        timeline.get(str(right["candidate_subject_id"]), {})
                    )
                )
                if not common_frames:
                    continue
                subject_ids = sorted(
                    [
                        str(left["candidate_subject_id"]),
                        str(right["candidate_subject_id"]),
                    ]
                )
                conflicts.append(
                    {
                        "conflict_key": (
                            f"parallel:{player_id}:{subject_ids[0]}:"
                            f"{subject_ids[1]}"
                        ),
                        "conflict_type": "parallel_same_player",
                        "candidate_subject_ids": subject_ids,
                        "player_ids": [player_id],
                        "overlap_frames": common_frames,
                    }
                )
                blocked_subjects.update(subject_ids)

    accepted = []
    for proposal in merged:
        subject_id = str(proposal["candidate_subject_id"])
        if subject_id in blocked_subjects:
            rejected.append(
                _rejected_proposal(
                    proposal,
                    ["parallel_same_player_observation"],
                )
            )
            continue
        accepted.append(proposal)
    return accepted, rejected, conflicts


def _merge_same_subject_proposals(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(
        rows,
        key=lambda row: _decision_sort_key(
            (row.get("seed_observations") or [{}])[0]
        ),
    )
    merged = dict(ordered[0])
    merged["seed_observations"] = sorted(
        (
            dict(seed)
            for row in ordered
            for seed in row.get("seed_observations") or []
        ),
        key=lambda seed: (
            int(seed.get("frame_number") or 0),
            str(seed.get("observation_key") or ""),
        ),
    )
    merged["accepted_reasons"] = sorted(
        {
            str(reason)
            for row in ordered
            for reason in row.get("accepted_reasons") or []
        }
    )
    return merged


def _detected_frames(subject: dict[str, Any]) -> set[int]:
    return {
        int(row.get("frame") or 0)
        for row in subject.get("observations") or []
        if str(row.get("status") or "detected") == "detected"
    }


def _accepted_parallel_assignment_count(
    accepted: list[dict[str, Any]],
    *,
    timeline: dict[str, dict[str, Any]],
) -> int:
    """Count unsafe overlaps that survived conflict resolution."""
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in accepted:
        player_id = str(
            (assignment.get("assigned_player") or {}).get("player_id") or ""
        )
        if player_id:
            by_player[player_id].append(assignment)

    conflict_count = 0
    for assignments in by_player.values():
        for left_index, left in enumerate(assignments):
            left_frames = _detected_frames(
                timeline.get(str(left.get("candidate_subject_id") or ""), {})
            )
            for right in assignments[left_index + 1 :]:
                right_frames = _detected_frames(
                    timeline.get(
                        str(right.get("candidate_subject_id") or ""),
                        {},
                    )
                )
                if left_frames & right_frames:
                    conflict_count += 1
    return conflict_count


def _rejected_seed(
    decision: dict[str, Any],
    reasons: list[str],
    *,
    candidate_subject_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "observation_key": decision.get("observation_key"),
        "frame_number": decision.get("frame_number"),
        "action": decision.get("action"),
        "assigned_player": decision.get("assigned_player"),
        "candidate_subject_ids": sorted(set(candidate_subject_ids or [])),
        "reasons": sorted(set(reasons)),
    }


def _rejected_proposal(
    proposal: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "observation_key": (
            (proposal.get("seed_observations") or [{}])[0].get(
                "observation_key"
            )
        ),
        "frame_number": (
            (proposal.get("seed_observations") or [{}])[0].get("frame_number")
        ),
        "action": "assign_roster_player",
        "assigned_player": proposal.get("assigned_player"),
        "candidate_subject_ids": [proposal.get("candidate_subject_id")],
        "reasons": sorted(set(reasons)),
    }


def _rejection_sort_key(
    row: dict[str, Any],
) -> tuple[int, str, str]:
    return (
        int(row.get("frame_number") or 0),
        str(row.get("observation_key") or ""),
        ",".join(str(value) for value in row.get("reasons") or []),
    )
