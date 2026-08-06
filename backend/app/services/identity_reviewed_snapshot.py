from __future__ import annotations

"""Canonical reviewed identity snapshot built from operator-backed evidence."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_snapshot_observations import (
    build_observation_overrides,
    observation_coverage,
)
from app.services.identity_reviewed_frame_uniqueness import build_frame_slot_demotions
from app.services.identity_reviewed_effective_observation import (
    effective_observations_by_frame,
    visible_reviewed_player,
)
from app.services.identity_reviewed_slot_review import load_reviewed_slot_assignments
from app.services.identity_seeded_candidate_assignments import load_combined_operator_seeds
from app.services.identity_seeded_review_reduction import load_fresh_seeded_assignments
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities


SNAPSHOT_FILENAME = "reviewed_identity_snapshot.json"
REPORT_FILENAME = "reviewed_identity_report.json"
ALGORITHM_VERSION = "reviewed_identity_snapshot:v4"


def get_reviewed_identity_status(match_path: Path) -> dict[str, Any]:
    snapshot_path = match_path / SNAPSHOT_FILENAME
    if not snapshot_path.exists():
        return {"status": "missing", "summary": None, "source": None}
    snapshot = _load(snapshot_path)
    current = _source_documents(match_path)
    match_doc = _optional(match_path / "match.json")
    stale = (
        snapshot.get("source", {}).get("semantic_input_digest")
        != _source_digest(current, match_doc)
        or snapshot.get("source", {}).get("algorithm_version") != ALGORITHM_VERSION
    )
    return {
        **snapshot,
        "status": "stale" if stale else str(snapshot.get("status") or "partial_reviewed"),
        "stale": stale,
    }


def finalize_reviewed_identity(match_path: Path, match_doc: dict[str, Any]) -> dict[str, Any]:
    documents = _source_documents(match_path)
    roster = _roster(match_doc)
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in documents["tracklets"].get("tracklets") or []
        if row.get("tracklet_id")
    }
    stable, fragmentation = resolve_stable_anonymous_entities(
        match_path,
        tracklets,
        documents["subjects"],
        documents["slot_review"],
    )
    reviews = _review_decisions(documents["review_decisions"])
    seeded_document, seeded_freshness = load_fresh_seeded_assignments(match_path)
    seeded = _safe_seeded_assignments(seeded_document or {}) if seeded_freshness.get("status") == "fresh" else {}
    observation_overrides = build_observation_overrides(
        documents["seeds"], tracklets, roster
    )
    assignments: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for tracklet_id, tracklet in sorted(tracklets.items()):
        stable_row = stable[tracklet_id]
        subject_ids = stable_row["candidate_subject_ids"]
        subject_id = stable_row["candidate_subject_id"]
        decisions = [item for value in subject_ids for item in reviews.get(value, [])]
        decision = decisions[-1] if decisions else None
        accepted_seeds = [seeded[value] for value in subject_ids if value in seeded]
        accepted_seed = accepted_seeds[0] if len(accepted_seeds) == 1 else None
        status, player_id, source, evidence, blockers = _resolve_assignment(
            decision, accepted_seed
        )
        manual_action = stable_row.get("manual_action")
        if manual_action in {"referee", "false_detection", "team_unknown", "unresolved"}:
            status = str(manual_action)
            player_id = None
            source = "manual_review"
        blockers.extend(stable_row["hard_blockers"])
        team_label = str(tracklet.get("team_label") or "U")
        team_id = str(tracklet.get("team_id") or "")
        player = roster.get(player_id or "")
        assignment_conflicts: list[dict[str, Any]] = []
        if len(accepted_seeds) > 1:
            blockers.append("ambiguous_seeded_subject_membership")
        if _has_conflicting_review_decisions(decisions):
            assignment_conflicts.append({"code": "conflicting_explicit_operator_decisions"})
        for blocker in stable_row["hard_blockers"]:
            assignment_conflicts.append({"code": blocker})
        if assignment_conflicts or len(accepted_seeds) > 1:
            status, player_id, source = "conflicted", None, source or "structural_safety"
        if player_id and player is None:
            blockers.append("invalid_roster_player")
            status, player_id = "blocked", None
        elif player_id and player["team_label"] != team_label:
            assignment_conflicts.append(
                {"code": "cross_team_confirmed_assignment", "player_id": player_id}
            )
            status, player_id = "conflicted", None
        slot_id = stable_row["stable_anonymous_slot_id"]
        fallback = str(stable_row["fallback_label"])
        display = (
            player["name"]
            if player and status == "confirmed"
            else "Sędzia"
            if status == "referee"
            else fallback
            if status not in {"conflicted", "blocked"}
            else f"{fallback} !"
        )
        row = {
            "tracklet_id": tracklet_id,
            "candidate_subject_id": subject_id,
            "candidate_subject_ids": subject_ids,
            "fragment_id": stable_row["fragment_id"],
            "stable_anonymous_slot_id": slot_id,
            "stable_anonymous_entity_id": slot_id,
            "stable_anchor_source": stable_row["stable_anchor_source"],
            "stable_anchor_claims": stable_row["stable_anchor_claims"],
            "stable_anchor_status": stable_row["stable_anchor_status"],
            "unanchored": stable_row["unanchored"],
            "requires_review": stable_row["requires_review"],
            "detected_evidence_count": stable_row["detected_evidence_count"],
            "insufficient_evidence": stable_row["insufficient_evidence"],
            "ephemeral_anonymous_entity": stable_row["ephemeral"],
            "team_id": team_id,
            "team_label": team_label,
            "canonical_player_id": player_id if status == "confirmed" else None,
            "player_name": player["name"] if player and status == "confirmed" else None,
            "roster_number": player.get("number") if player and status == "confirmed" else None,
            "fallback_label": fallback,
            "display_label": display,
            "identity_status": status,
            "identity_source": source,
            "eligible_for_player_stats": status == "confirmed" and player_id is not None,
            "frame_start": _frame_start(tracklet),
            "frame_end": _frame_end(tracklet),
            "source_review_keys": [str(item.get("review_card_key") or "") for item in decisions],
            "source_seed_keys": evidence,
            "accepted_evidence": [source] if source else [],
            "rejected_evidence": [],
            "hard_blockers": sorted(set(blockers)),
            "conflicts": assignment_conflicts,
        }
        assignments.append(row)
        conflicts.extend({"tracklet_id": tracklet_id, **item} for item in assignment_conflicts)

    observation_demotions, uniqueness = build_frame_slot_demotions(
        tracklets,
        assignments,
        observation_overrides,
    )
    conflicts.extend(
        {
            "tracklet_id": row["tracklet_id"],
            "frame": row["frame"],
            **conflict,
        }
        for row in observation_demotions
        for conflict in row.get("conflicts") or []
    )
    coverage = observation_coverage(
        tracklets,
        assignments,
        observation_overrides,
        observation_demotions,
    )
    summary = _summary(assignments, coverage, fragmentation, uniqueness)
    source = _source_descriptor(documents, match_doc, seeded_freshness)
    status = (
        "blocked"
        if summary["blocked"] == len(assignments) and assignments
        else "complete_reviewed"
        if summary["unresolved"] == summary["conflicted"] == summary["blocked"] == 0
        else "partial_reviewed"
    )
    snapshot = {
        "schema_version": "3.0.0",
        "mode": "reviewed_identity_snapshot",
        "match_id": str(match_doc.get("id") or match_path.name),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "source": source,
        "display_policy": {
            "confirmed": "roster_name",
            "unresolved": "stable_anonymous_entity",
            "conflicted": "stable_anonymous_entity_with_marker",
            "exact_observation_seed": "override_only_that_observation",
        },
        "entities": _entities(assignments),
        "tracklet_assignments": assignments,
        "observation_overrides": observation_overrides,
        "observation_demotions": observation_demotions,
        "summary": summary,
        "fragmentation_diagnostics": fragmentation,
        "frame_uniqueness_diagnostics": uniqueness,
        "conflicts": conflicts,
        "readiness": {
            "identity": "ready_with_review" if summary["confirmed"] or summary["exact_named_observations"] else "partial_review_required",
            "reason": "Names are shown only for explicit review decisions or fresh, safe seeded lineage; exact seeds affect only their observation.",
        },
        "safety": {
            "production_identity_mutated": False,
            "production_applies": 0,
            "reran_yolo": False,
            "reran_tracking": False,
            "automatic_reid_names_rendered": 0,
        },
    }
    snapshot["semantic_digest"] = _semantic_digest(snapshot)
    report = {
        "schema_version": "3.0.0",
        "status": snapshot["status"],
        "snapshot_digest": snapshot["semantic_digest"],
        "summary": summary,
        "fragmentation_diagnostics": fragmentation,
        "frame_uniqueness_diagnostics": uniqueness,
        "conflicts": conflicts,
        "source": source,
        "safety": snapshot["safety"],
    }
    write_identity_json_atomic(match_path / SNAPSHOT_FILENAME, snapshot)
    write_identity_json_atomic(match_path / REPORT_FILENAME, report)
    return snapshot


def reviewed_assignment_at(
    snapshot: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
    time_sec: float,
    fps: float,
) -> list[dict[str, Any]]:
    frame = max(0, round(time_sec * fps))
    return [
        row
        for row in effective_observations_by_frame(tracklets, snapshot).get(frame, [])
        if visible_reviewed_player(row)
    ]


def _source_documents(path: Path) -> dict[str, dict[str, Any]]:
    try:
        seeds = load_combined_operator_seeds(path)
    except (FileNotFoundError, ValueError):
        seeds = _optional(path / "identity_operator_seeds.json")
    return {
        "tracklets": _optional(path / "tracklets.json"),
        "subjects": _optional(path / "identity_candidate_shadow.json"),
        "timeline": _optional(path / "identity_offline_shadow_timeline.json"),
        "seeds": seeds,
        "seeded": _optional(path / "identity_seeded_candidate_assignments.json"),
        "review_decisions": _optional(path / "identity_roster_subject_review_decisions_shadow.json"),
        "slot_review": load_reviewed_slot_assignments(path),
        "remediation": _optional(path / "identity_structural_remediation_shadow.json"),
        "gallery": _optional(path / "identity_review_gallery.json"),
        "stable_players": _optional(path / "stable_players.json"),
        "global_identity": _optional(path / "global_identity.json"),
    }


def _source_descriptor(
    documents: dict[str, dict[str, Any]],
    match_doc: dict[str, Any],
    seeded_freshness: dict[str, Any],
) -> dict[str, Any]:
    values = {key: canonical_digest(_semantic_input(value)) if value else None for key, value in documents.items()}
    return {
        "match_digest": canonical_digest(_semantic_input(match_doc)),
        "roster_digest": canonical_digest(_semantic_input(match_doc.get("teams") or [])),
        "tracklets_digest": values["tracklets"],
        "subjects_digest": values["subjects"],
        "operator_seed_decisions_digest": _decisions_digest(documents["seeds"]),
        "whole_subject_review_decisions_digest": _decisions_digest(documents["review_decisions"]),
        "stable_identity_digests": {key: values[key] for key in ("gallery", "stable_players", "global_identity")},
        "seeded_assignment_freshness": seeded_freshness,
        "algorithm_version": ALGORITHM_VERSION,
        "optional_inputs": {key: "available" if value else "not_available" for key, value in documents.items()},
        "semantic_input_digest": _source_digest(documents, match_doc),
    }


def _source_digest(documents: dict[str, dict[str, Any]], match_doc: dict[str, Any] | None = None) -> str:
    value = {key: canonical_digest(_semantic_input(document)) if document else None for key, document in documents.items()}
    if match_doc is not None:
        value["match"] = canonical_digest(_semantic_input(match_doc))
    return canonical_digest(value)


def _review_decisions(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in doc.get("decisions") or []:
        subject = str(row.get("candidate_subject_id") or "")
        if subject and str(row.get("decision")) in {"assign_roster_player", "confirm_recommended_player", "mark_unresolved"}:
            values[subject].append(dict(row))
    return values


def _safe_seeded_assignments(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = {}
    for row in doc.get("accepted_assignments") or []:
        provenance = row.get("propagation_provenance") or {}
        if provenance.get("team_consistency") and provenance.get("structural_gates_passed") and provenance.get("local_tracklet_continuity"):
            values[str(row.get("candidate_subject_id") or "")] = row
    return values


def _resolve_assignment(
    decision: dict[str, Any] | None,
    seed: dict[str, Any] | None,
) -> tuple[str, str | None, str | None, list[str], list[str]]:
    if decision:
        if decision.get("decision") == "mark_unresolved":
            return "unresolved", None, "operator_review", [], []
        return "confirmed", str(decision.get("player_id") or "") or None, "operator_review", [], []
    if seed:
        player = (seed.get("assigned_player") or {}).get("player_id")
        return (
            "confirmed",
            str(player) if player else None,
            "operator_seed_safe_lineage",
            [str(item.get("observation_key") or "") for item in seed.get("seed_observations") or []],
            [],
        )
    return "unresolved", None, None, [], []


def _summary(
    assignments: list[dict[str, Any]],
    coverage: dict[str, Any],
    fragmentation: dict[str, Any],
    uniqueness: dict[str, Any],
) -> dict[str, Any]:
    counts = Counter(str(row["identity_status"]) for row in assignments)
    return {
        "tracklets_total": len(assignments),
        "confirmed": counts["confirmed"],
        "probable": counts["probable"],
        "unresolved": counts["unresolved"],
        "conflicted": counts["conflicted"],
        "blocked": counts["blocked"],
        "confirmed_players": len({row["canonical_player_id"] for row in assignments if row.get("canonical_player_id")}),
        **coverage,
        "conflict_count": sum(bool(row["conflicts"]) for row in assignments),
        "cross_team_violations": sum(any(value.get("code") == "cross_team_confirmed_assignment" for value in row["conflicts"]) for row in assignments),
        "invalid_roster_references": sum("invalid_roster_player" in row["hard_blockers"] for row in assignments),
        "stable_anonymous_entities": fragmentation["stable_anonymous_entities_total"],
        "unanchored_fragments": fragmentation["unanchored_fragments"],
        "automatic_permanent_allocations": fragmentation[
            "automatic_permanent_allocations"
        ],
        **uniqueness,
    }


def _entities(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in assignments:
        entity_key = row.get("stable_anonymous_slot_id") or row.get("fragment_id")
        values[str(entity_key)].append(row)
    return [
        {
            "entity_key": key,
            "stable_anonymous_slot_id": rows[0].get("stable_anonymous_slot_id"),
            "fallback_label": rows[0]["fallback_label"],
            "team_label": rows[0]["team_label"],
            "tracklet_ids": sorted(row["tracklet_id"] for row in rows),
            "candidate_subject_ids": sorted({subject for row in rows for subject in row["candidate_subject_ids"]}),
            "identity_status": "confirmed" if all(row["identity_status"] == "confirmed" for row in rows) else "unresolved",
        }
        for key, rows in sorted(values.items())
    ]


def _roster(match_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output = {}
    for index, team in enumerate(match_doc.get("teams") or []):
        label = str(team.get("team_label") or chr(ord("A") + index))
        for player in team.get("players") or []:
            output[str(player.get("id"))] = {**player, "team_id": str(team.get("id") or ""), "team_label": label}
    return output


def _has_conflicting_review_decisions(decisions: list[dict[str, Any]]) -> bool:
    values = {(str(row.get("decision") or ""), str(row.get("player_id") or "")) for row in decisions}
    return len(values) > 1


def _frame_start(row: dict[str, Any]) -> int:
    return int(row.get("start_frame") or ((row.get("positions_m") or [{}])[0].get("frame") or 0))


def _frame_end(row: dict[str, Any]) -> int:
    return int(row.get("end_frame") or ((row.get("positions_m") or [{}])[-1].get("frame") or _frame_start(row)))


def _decisions_digest(doc: dict[str, Any]) -> str | None:
    return canonical_digest(_semantic_input(doc.get("decisions") or [])) if doc else None


def _semantic_digest(snapshot: dict[str, Any]) -> str:
    return canonical_digest({key: value for key, value in snapshot.items() if key not in {"generated_at", "semantic_digest"}})


def _semantic_input(value: Any) -> Any:
    if isinstance(value, list):
        return [_semantic_input(item) for item in value]
    if isinstance(value, dict):
        return {key: _semantic_input(item) for key, item in value.items() if key not in {"generated_at", "updated_at", "operator_telemetry", "telemetry_state", "created_at"}}
    return value


def _optional(path: Path) -> dict[str, Any]:
    return _load(path) if path.exists() else {}


def _load(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
