from __future__ import annotations

"""Canonical reviewed identity snapshot built from operator-backed evidence."""

import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_canonical_io import (
    invalidate_cached_json,
    load_json_cached,
    scoped_memo_invalidate,
)
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_snapshot_observations import (
    build_observation_overrides,
    observation_coverage,
)
from app.services.identity_canonical_ownership import global_observation_ownership
from app.services.identity_reviewed_frame_uniqueness import build_frame_slot_demotions
from app.services.identity_reviewed_effective_observation import (
    effective_observations_by_frame,
    visible_reviewed_overlay,
)
from app.services.identity_reviewed_slot_review import load_reviewed_slot_assignments
from app.services.identity_reviewed_segments import (
    build_segment_review_document,
    load_segment_decisions,
    segment_observation_assignments,
)
from app.services.identity_reviewed_mixed_store import (
    FILENAME as MIXED_PLAYERS_FILENAME,
    unresolved_mixed_observation_assignments,
)
from app.services.identity_reviewed_material_continuity import (
    load_material_continuity_decisions,
    material_continuity_observation_assignments,
)
from app.services.identity_reviewed_scope_eligibility import (
    team_attribution_state as classify_team_attribution_state,
)
from app.services.identity_reviewed_team_attribution_policy import (
    SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
    derive_short_track_team_projection,
    persist_automatic_team_assignments,
)
from app.services.identity_seeded_candidate_assignments import load_combined_operator_seeds
from app.services.identity_seeded_review_reduction import load_fresh_seeded_assignments
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities
from app.services.review_source_fingerprints import (
    FINGERPRINTS_FIELD,
    build_canonical_source_fingerprints,
)
from app.services.play_area import is_on_pitch_product_observation


SNAPSHOT_FILENAME = "reviewed_identity_snapshot.json"
REPORT_FILENAME = "reviewed_identity_report.json"
ALGORITHM_VERSION = "reviewed_identity_snapshot:v15-authoritative-short-track-team-projection"

logger = logging.getLogger(__name__)

# Subphase timings (ms) of the most recent authoritative snapshot build.
# Diagnostics only; never part of the persisted snapshot contract.
_LAST_BUILD_PHASES: dict[str, float] = {}


def last_snapshot_build_phases() -> dict[str, float]:
    return dict(_LAST_BUILD_PHASES)


class _Phases:
    __slots__ = ("_data", "_started", "_mark")

    def __init__(self) -> None:
        self._data: dict[str, float] = {}
        self._started = time.perf_counter()
        self._mark = self._started

    def phase(self, name: str) -> None:
        now = time.perf_counter()
        self._data[name] = round((now - self._mark) * 1000, 1)
        self._mark = now

    def finish(self) -> dict[str, float]:
        self._data["total_ms"] = round((time.perf_counter() - self._started) * 1000, 1)
        return self._data


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
    phases = _Phases()
    documents = _source_documents(match_path)
    phases.phase("source_document_load_ms")
    roster = _roster(match_doc)
    segment_review = build_segment_review_document(match_path, match_doc)
    phases.phase("segment_review_ms")
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in documents["tracklets"].get("tracklets") or []
        if row.get("tracklet_id")
    }
    subject_detected_team_labels = _subject_detected_team_labels(
        documents["subjects"], tracklets
    )
    stable, fragmentation = resolve_stable_anonymous_entities(
        match_path,
        tracklets,
        documents["subjects"],
        documents["slot_review"],
    )
    phases.phase("stable_identity_resolution_ms")
    reviews = _review_decisions(documents["review_decisions"], documents["slot_review"])
    # This is an exact-source *derived* team projection, not an operator
    # decision.  It is deliberately computed before the compact snapshot
    # drops candidate ownership evidence, then persisted on every affected
    # assignment so effective observations, coverage and stats share one
    # team truth.
    auto_projection_exclusions = {
        subject_id
        for subject_id, decisions in reviews.items()
        if decisions
    }
    auto_projection_exclusions.update(
        str(row.get("candidate_subject_id") or "")
        for row in stable.values()
        if row.get("manual_action")
        or set(row.get("hard_blockers") or []) - {"mixed_team_candidate_subject"}
    )
    automatic_team_projections = derive_short_track_team_projection(
        tracklets,
        documents["subjects"],
        excluded_subject_ids=auto_projection_exclusions,
    )
    slot_roster_bindings, conflicting_slot_roster_bindings = _slot_roster_bindings(
        stable,
        reviews,
        documents["slot_review"],
        roster,
    )
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
        slot_id = stable_row["stable_anonymous_slot_id"]
        slot_binding = slot_roster_bindings.get(str(slot_id or ""))
        if (
            slot_binding
            and not (decision and decision.get("decision") == "mark_unresolved")
        ):
            bound_player_id = str(slot_binding["player_id"])
            if player_id and player_id != bound_player_id:
                blockers.append("conflicting_subject_and_stable_slot_roster_binding")
            else:
                status = "confirmed"
                player_id = bound_player_id
                source = str(slot_binding["source"])
                evidence = []
        manual_action = stable_row.get("manual_action")
        if manual_action in {"referee", "false_detection", "team_unknown", "unresolved"}:
            status = str(manual_action)
            player_id = None
            source = "manual_review"
        elif manual_action == "assign_team" and player_id is None:
            source = "operator_team_assignment"
        if status == "assign_team":
            status = "unresolved"
        blockers.extend(stable_row["hard_blockers"])
        team_label = (
            "U"
            if manual_action == "team_unknown"
            else str(stable_row.get("effective_team_label") or tracklet.get("team_label") or "U")
        )
        team_id = (
            ""
            if manual_action == "team_unknown"
            else str(tracklet.get("team_id") or "")
        )
        player = roster.get(player_id or "")
        if player and team_label == "U":
            team_label = str(player["team_label"])
        assignment_conflicts: list[dict[str, Any]] = []
        propagation_conflicted_stable_slot_ids: list[str] = []
        if len(accepted_seeds) > 1:
            blockers.append("ambiguous_seeded_subject_membership")
        if _has_conflicting_review_decisions(decisions):
            assignment_conflicts.append({"code": "conflicting_explicit_operator_decisions"})
        if slot_id in conflicting_slot_roster_bindings:
            # The conflict belongs to this stable-slot hypothesis itself. Keep
            # the slot identifier on every assignment so a stronger
            # exact/segment layer only disables stable-slot uniqueness when it
            # still uses this exact slot.
            propagation_conflicted_stable_slot_ids.append(str(slot_id))
            if not _is_explicit_subject_player_decision(decision):
                blockers.append("conflicting_stable_slot_roster_bindings")
                assignment_conflicts.append(
                    {"code": "conflicting_stable_slot_roster_bindings"}
                )
        for blocker in stable_row["hard_blockers"]:
            # A raw A/B vote mixture is the input to the conservative
            # short-track gate below. It is not by itself a structural
            # ownership collision once that exact source passes the gate.
            if blocker != "mixed_team_candidate_subject":
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
        # Project team truth only after every current assignment check.  In
        # particular, a roster player from the opposite team turns this into a
        # live A/B conflict even when the original tracker label was certain.
        automatic_team_projection = automatic_team_projections.get(tracklet_id)
        if (
            automatic_team_projection
            and not assignment_conflicts
            and not manual_action
            and not decision
            and not accepted_seed
        ):
            team_label = str(automatic_team_projection["team_label"])
            team_id = _team_id_for_label(match_doc, team_label)
        reviewed_team_attribution_state = _reviewed_team_attribution_state(
            stable_row,
            subject_ids,
            subject_detected_team_labels,
            decision,
            manual_action,
            team_label,
            assignment_conflicts,
        )
        if automatic_team_projection and team_label == str(automatic_team_projection.get("team_label")):
            # The exact source has passed the versioned temporal-noise gate.
            # Its raw A/B votes are the evidence used by that gate, not a
            # surviving cross-team conflict after the derived projection.
            reviewed_team_attribution_state = f"certain_{team_label}"
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
            "automatic_team_assignment": (
                {
                    **automatic_team_projection,
                    "provenance": SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
                }
                if automatic_team_projection and team_label == str(automatic_team_projection.get("team_label"))
                else None
            ),
            # This compact state is deliberately projected while the canonical
            # candidate/tracklet evidence is still available.  Effective
            # reviewed observations later carry only their current identity
            # projection, where re-inferring A/B certainty would be lossy.
            "reviewed_team_attribution_state": reviewed_team_attribution_state,
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
            "propagation_conflicted_stable_slot_ids": sorted(
                set(propagation_conflicted_stable_slot_ids)
            ),
        }
        assignments.append(row)
        conflicts.extend({"tracklet_id": tracklet_id, **item} for item in assignment_conflicts)

    canonical_observation_assignments = _canonical_observation_assignments(
        global_observation_ownership(documents["global_identity"]),
        assignments,
        slot_roster_bindings,
        conflicting_slot_roster_bindings,
        roster,
    )
    phases.phase("canonical_observation_assignment_ms")
    segment_assignments = segment_observation_assignments(
        segment_review,
        documents["segment_decisions"],
        roster,
    )
    phases.phase("segment_observation_assignment_ms")
    segment_assignments.extend(
        material_continuity_observation_assignments(
            match_path,
            match_doc,
            documents["material_continuity_decisions"],
        )
    )
    phases.phase("material_continuity_observation_assignment_ms")
    segment_assignments.extend(
        unresolved_mixed_observation_assignments(match_path, match_doc)
    )
    phases.phase("unresolved_mixed_observation_assignment_ms")
    segment_assignments.sort(key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))
    observation_demotions, uniqueness = build_frame_slot_demotions(
        tracklets,
        assignments,
        observation_overrides,
        canonical_observation_assignments,
        segment_assignments,
    )
    phases.phase("frame_uniqueness_guard_ms")
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
        canonical_observation_assignments,
        segment_assignments,
    )
    summary = _summary(
        assignments,
        coverage,
        fragmentation,
        uniqueness,
        tracklets,
        canonical_observation_assignments,
        segment_assignments,
    )
    source = _source_descriptor(documents, match_doc, seeded_freshness)
    phases.phase("source_semantic_digest_ms")
    product_tracklets_total = int(summary["product_tracklets_total"])
    status = (
        "blocked"
        if summary["blocked"] == product_tracklets_total and product_tracklets_total
        else "complete_reviewed"
        if summary["unresolved"] == summary["conflicted"] == summary["blocked"] == 0
        else "partial_reviewed"
    )
    snapshot = {
        "schema_version": "3.1.0",
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
            "segment_correction": "override_only_canonical_target_observations",
        },
        "count_semantics": {
            "tracklets_total": "technical_all_tracklets",
            "entities": "technical_all_entities",
            "stable_anonymous_entities": "technical_all_stable_entities",
            "product_tracklets_total": "tracklets_with_inside_play_detections",
            "product_entities_total": "entities_with_inside_play_detections",
            "product_stable_anonymous_entities": "stable_entities_with_inside_play_detections",
            "status": "product_tracklets_only",
        },
        "entities": _entities(assignments),
        "tracklet_assignments": assignments,
        "automatic_team_assignments": sorted(
            {
                str(row["automatic_team_assignment"].get("source_ownership_digest")): row["automatic_team_assignment"]
                for row in assignments
                if isinstance(row.get("automatic_team_assignment"), dict)
            }.values(),
            key=lambda row: str(row.get("source_ownership_digest") or ""),
        ),
        "canonical_observation_assignments": canonical_observation_assignments,
        "observation_overrides": observation_overrides,
        "segment_observation_assignments": segment_assignments,
        "observation_demotions": observation_demotions,
        "summary": summary,
        "fragmentation_diagnostics": fragmentation,
        "frame_uniqueness_diagnostics": uniqueness,
        "conflicts": conflicts,
        "readiness": {
            "identity": "ready_with_review" if summary["confirmed_detected_observations"] else "partial_review_required",
            "reason": "Names are shown only for explicit review decisions or fresh, safe seeded lineage; exact seeds affect only their observation.",
        },
        "safety": {
            "production_identity_mutated": False,
            "production_applies": 0,
            "reran_yolo": False,
            "reran_tracking": False,
            "segment_observation_assignments": len(segment_assignments),
            "automatic_reid_names_rendered": 0,
        },
    }
    snapshot["semantic_digest"] = _semantic_digest(snapshot)
    phases.phase("snapshot_semantic_digest_ms")
    report = {
        "schema_version": "3.1.0",
        "status": snapshot["status"],
        "snapshot_digest": snapshot["semantic_digest"],
        "summary": summary,
        "fragmentation_diagnostics": fragmentation,
        "frame_uniqueness_diagnostics": uniqueness,
        "conflicts": conflicts,
        "source": source,
        # Captured AFTER the build: any canonical file rewritten while this
        # snapshot was being produced must be observed at its final state, so
        # the cheap preflight never sees an immediately-stale fingerprint.
        FINGERPRINTS_FIELD: build_canonical_source_fingerprints(match_path),
        "safety": snapshot["safety"],
    }
    write_identity_json_atomic(match_path / SNAPSHOT_FILENAME, snapshot)
    # Keep the inspection artifact in lockstep with the snapshot authority.
    # The policy never writes an operator decision and does not alter source
    # topology; it only records the exact-source projection already embedded
    # above.
    persist_automatic_team_assignments(match_path, assignments)
    write_identity_json_atomic(match_path / REPORT_FILENAME, report)
    phases.phase("snapshot_write_ms")
    # The snapshot was just replaced inside this authoritative scope; any
    # later same-scope read must observe the new document.
    invalidate_cached_json(match_path / SNAPSHOT_FILENAME)
    invalidate_cached_json(match_path / REPORT_FILENAME)
    # File-content invalidation alone does not drop derived semantic memos.
    # Any authoritative progress projected inside this scope before the write
    # belonged to the previous snapshot generation and must never be served
    # after it (the progress memo key also carries the snapshot fingerprint,
    # so this is defense in depth, not the only guard).
    scoped_memo_invalidate("__authoritative_progress__")
    _LAST_BUILD_PHASES.clear()
    _LAST_BUILD_PHASES.update(phases.finish())
    logger.info(
        "reviewed_snapshot_perf match=%s status=%s %s",
        match_doc.get("id") or match_path.name,
        status,
        " ".join(f"{key}={value}" for key, value in sorted(_LAST_BUILD_PHASES.items())),
    )
    return snapshot


def reviewed_assignment_at(
    snapshot: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
    time_sec: float,
    fps: float,
) -> list[dict[str, Any]]:
    frame = max(0, round(time_sec * fps))
    output: list[dict[str, Any]] = []
    for row in effective_observations_by_frame(tracklets, snapshot).get(frame, []):
        if not visible_reviewed_overlay(row):
            continue
        entity = {
            "frame": frame,
            "time_sec": float(row.get("time_sec") or frame / fps),
            "tracklet_id": row.get("tracklet_id"),
            "candidate_subject_id": row.get("candidate_subject_id"),
            "candidate_subject_ids": list(row.get("candidate_subject_ids") or []),
            "team_label": row.get("team_label") or "U",
            "stable_anonymous_slot_id": row.get("stable_anonymous_slot_id"),
            "canonical_player_id": row.get("canonical_player_id"),
            "player_name": row.get("player_name"),
            "display_label": row.get("display_label"),
            "identity_status": row.get("identity_status"),
            "identity_source": row.get("identity_source"),
            "fallback_label": row.get("fallback_label"),
            "requires_review": bool(row.get("requires_review")),
            "hard_blockers": list(row.get("hard_blockers") or []),
            "conflicts": list(row.get("conflicts") or []),
            "detected_evidence_count": int(row.get("detected_evidence_count") or 0),
            "frame_start": (
                int(row["frame_start"])
                if row.get("frame_start") is not None
                else frame
            ),
            "frame_end": (
                int(row["frame_end"])
                if row.get("frame_end") is not None
                else frame
            ),
            "bbox_xyxy": row.get("bbox_xyxy"),
        }
        if row.get("observation_key"):
            entity["observation_key"] = row["observation_key"]
        output.append(entity)
    return sorted(
        output,
        key=lambda row: (str(row.get("candidate_subject_id") or ""), str(row["tracklet_id"])),
    )


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
        "segment_decisions": load_segment_decisions(path),
        "material_continuity_decisions": load_material_continuity_decisions(path),
        "mixed_players": _optional(path / MIXED_PLAYERS_FILENAME),
    }


def _subject_detected_team_labels(
    candidate_document: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Return canonical A/B evidence for each current candidate subject."""
    labels_by_subject: dict[str, set[str]] = defaultdict(set)
    for subject in candidate_document.get("subjects") or []:
        subject_id = str(subject.get("candidate_subject_id") or "")
        if not subject_id:
            continue
        for tracklet_id in subject.get("tracklet_ids") or []:
            label = str(
                tracklets.get(str(tracklet_id), {}).get("team_label") or "U"
            ).upper()
            if label in {"A", "B"}:
                labels_by_subject[subject_id].add(label)
    return {
        subject_id: sorted(labels)
        for subject_id, labels in labels_by_subject.items()
    }


def _reviewed_team_attribution_state(
    stable_row: dict[str, Any],
    subject_ids: list[str],
    subject_detected_team_labels: dict[str, list[str]],
    decision: dict[str, Any] | None,
    manual_action: str | None,
    effective_team_label: str,
    assignment_conflicts: list[dict[str, Any]] | None = None,
) -> str:
    """Freeze current team truth for a compact reviewed snapshot assignment.

    Diagnostic reason strings are intentionally excluded.  They can describe
    a prior failure even after the exact source has been safely resolved.
    """
    if any(
        str(conflict.get("code") or "") == "cross_team_confirmed_assignment"
        for conflict in assignment_conflicts or []
        if isinstance(conflict, dict)
    ):
        return "cross_team"
    current_decision = dict(decision or {})
    if "action" not in current_decision and current_decision.get("decision"):
        current_decision["action"] = current_decision["decision"]
    if manual_action:
        current_decision["action"] = manual_action
    detected_labels = {
        label
        for subject_id in subject_ids
        for label in subject_detected_team_labels.get(str(subject_id), [])
    }
    return classify_team_attribution_state(
        {
            "source_team_label": stable_row.get("source_team_label"),
            "effective_team_label": effective_team_label,
            "detected_team_labels": sorted(detected_labels),
            "current_decision": current_decision,
        }
    )


def _source_descriptor(
    documents: dict[str, dict[str, Any]],
    match_doc: dict[str, Any],
    seeded_freshness: dict[str, Any],
) -> dict[str, Any]:
    # Per-document semantic digests are computed exactly once and reused for
    # both the named descriptor fields and semantic_input_digest.  The
    # aggregate value is byte-identical to the former _source_digest() output.
    values = {key: canonical_digest(_semantic_input(value)) if value else None for key, value in documents.items()}
    match_value = canonical_digest(_semantic_input(match_doc))
    return {
        "match_digest": match_value,
        "roster_digest": canonical_digest(_semantic_input(match_doc.get("teams") or [])),
        "tracklets_digest": values["tracklets"],
        "subjects_digest": values["subjects"],
        "operator_seed_decisions_digest": _decisions_digest(documents["seeds"]),
        "whole_subject_review_decisions_digest": _decisions_digest(documents["review_decisions"]),
        "segment_review_decisions_digest": _decisions_digest(documents["segment_decisions"]),
        "material_continuity_decisions_digest": _decisions_digest(documents["material_continuity_decisions"]),
        "mixed_players_digest": values["mixed_players"],
        "stable_identity_digests": {key: values[key] for key in ("gallery", "stable_players", "global_identity")},
        "seeded_assignment_freshness": seeded_freshness,
        "algorithm_version": ALGORITHM_VERSION,
        "optional_inputs": {key: "available" if value else "not_available" for key, value in documents.items()},
        "semantic_input_digest": canonical_digest({**values, "match": match_value}),
    }


def _source_digest(documents: dict[str, dict[str, Any]], match_doc: dict[str, Any] | None = None) -> str:
    """Reference implementation kept for digest-equivalence regression."""
    value = {key: canonical_digest(_semantic_input(document)) if document else None for key, document in documents.items()}
    if match_doc is not None:
        value["match"] = canonical_digest(_semantic_input(match_doc))
    return canonical_digest(value)


def _review_decisions(
    doc: dict[str, Any],
    slot_review: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in doc.get("decisions") or []:
        subject = str(row.get("candidate_subject_id") or "")
        if subject and str(row.get("decision")) in {"assign_roster_player", "confirm_recommended_player", "mark_unresolved"}:
            values[subject].append(dict(row))
    for row in (slot_review or {}).get("decisions") or []:
        subject = str(row.get("candidate_subject_id") or "")
        if subject and str(row.get("action")) == "assign_roster_player":
            values[subject].append(
                {
                    "candidate_subject_id": subject,
                    "decision": "assign_roster_player",
                    "player_id": row.get("player_id"),
                    "source": "manual_reviewed_slot_assignment",
                }
            )
    return values


def _safe_seeded_assignments(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = {}
    for row in doc.get("accepted_assignments") or []:
        provenance = row.get("propagation_provenance") or {}
        if provenance.get("team_consistency") and provenance.get("structural_gates_passed") and provenance.get("local_tracklet_continuity"):
            values[str(row.get("candidate_subject_id") or "")] = row
    return values


def _slot_roster_bindings(
    stable: dict[str, dict[str, Any]],
    reviews: dict[str, list[dict[str, Any]]],
    slot_review: dict[str, Any],
    roster: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, str]], set[str]]:
    claims: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in slot_review.get("decisions") or []:
        if str(row.get("action") or "") != "assign_roster_player":
            continue
        slot_id = str(row.get("stable_slot_id") or "")
        player_id = str(row.get("player_id") or "")
        if _valid_slot_roster_pair(slot_id, player_id, roster):
            claims[slot_id].append(
                {
                    "player_id": player_id,
                    "source": "manual_stable_slot_binding",
                }
            )

    subject_slots: dict[str, set[str]] = defaultdict(set)
    blocked_subjects: set[str] = set()
    for row in stable.values():
        subject_id = str(row.get("candidate_subject_id") or "")
        slot_id = str(row.get("stable_anonymous_slot_id") or "")
        if not subject_id:
            continue
        if row.get("hard_blockers") or row.get("subject_propagation_blockers"):
            blocked_subjects.add(subject_id)
        elif slot_id:
            subject_slots[subject_id].add(slot_id)

    for subject_id, decisions in reviews.items():
        slots = subject_slots.get(subject_id, set())
        player_ids = {
            str(row.get("player_id") or "")
            for row in decisions
            if row.get("decision") in {"assign_roster_player", "confirm_recommended_player"}
            and row.get("player_id")
        }
        if subject_id in blocked_subjects or len(slots) != 1 or len(player_ids) != 1:
            continue
        slot_id = next(iter(slots))
        player_id = next(iter(player_ids))
        if _valid_slot_roster_pair(slot_id, player_id, roster):
            claims[slot_id].append(
                {
                    "player_id": player_id,
                    "source": "legacy_subject_to_stable_slot_binding",
                }
            )

    bindings: dict[str, dict[str, str]] = {}
    conflicts: set[str] = set()
    for slot_id, rows in claims.items():
        player_ids = {row["player_id"] for row in rows}
        if len(player_ids) != 1:
            conflicts.add(slot_id)
            continue
        manual_binding = next(
            (
                row
                for row in rows
                if row["source"] == "manual_stable_slot_binding"
            ),
            None,
        )
        bindings[slot_id] = manual_binding or rows[0]
    return bindings, conflicts


def _valid_slot_roster_pair(
    slot_id: str,
    player_id: str,
    roster: dict[str, dict[str, Any]],
) -> bool:
    player = roster.get(player_id)
    return bool(
        len(slot_id) >= 2
        and slot_id[0] in {"A", "B"}
        and player
        and str(player.get("team_label") or "") == slot_id[0]
    )


def _canonical_observation_assignments(
    ownership_claims: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    slot_roster_bindings: dict[str, dict[str, str]],
    conflicting_slot_roster_bindings: set[str],
    roster: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply global per-frame ownership before exact seeds and uniqueness safety."""
    assignment_by_tracklet = {
        str(row.get("tracklet_id") or ""): row for row in assignments
    }
    output = []
    for claim in ownership_claims:
        tracklet_id = str(claim["tracklet_id"])
        base = assignment_by_tracklet.get(tracklet_id)
        if not base or str(base.get("identity_status") or "") in {
            "false_detection",
            "referee",
            "team_unknown",
        }:
            continue
        slot_id = str(claim["stable_slot_id"])
        blockers = [
            value
            for value in base.get("hard_blockers") or []
            if value != "upstream_multi_slot_tracklet_membership"
        ]
        conflicts = list(base.get("conflicts") or [])
        explicit_unresolved = (
            str(base.get("identity_status") or "") == "unresolved"
            and str(base.get("identity_source") or "")
            in {"operator_review", "manual_review"}
        )
        explicit_subject_player = (
            str(base.get("identity_status") or "") == "confirmed"
            and str(base.get("identity_source") or "") == "operator_review"
            and bool(base.get("canonical_player_id"))
        )
        binding = slot_roster_bindings.get(slot_id)
        if explicit_subject_player:
            player_id = str(base.get("canonical_player_id") or "") or None
            player = roster.get(player_id or "")
            status = "confirmed" if player else "blocked"
            source = str(base.get("identity_source") or "operator_review")
        elif explicit_unresolved:
            player_id = None
            player = None
            status = "unresolved"
            source = str(base.get("identity_source") or "manual_review")
        else:
            player_id = str((binding or {}).get("player_id") or "") or None
            player = roster.get(player_id or "")
            status = "confirmed" if player else "unresolved"
            source = (
                str((binding or {}).get("source") or "")
                if player
                else "canonical_frame_global_identity"
            )
        if slot_id in conflicting_slot_roster_bindings and not explicit_subject_player:
            blockers.append("conflicting_stable_slot_roster_bindings")
            conflicts.append({"code": "conflicting_stable_slot_roster_bindings"})
            status, player_id, player, source = (
                "conflicted",
                None,
                None,
                "structural_safety",
            )
        local_team = str(base.get("team_label") or "U")
        if local_team in {"A", "B"} and local_team != str(claim["team_label"]):
            blockers.append("canonical_frame_local_team_conflict")
            conflicts.append(
                {
                    "code": "canonical_frame_local_team_conflict",
                    "global_team_label": str(claim["team_label"]),
                    "local_team_label": local_team,
                }
            )
            status, player_id, player, source = (
                "conflicted",
                None,
                None,
                "structural_safety",
            )
        base_team_state = str(
            base.get("reviewed_team_attribution_state") or "unknown"
        )
        claim_team = str(claim.get("team_label") or "U")
        reviewed_team_state = (
            "cross_team"
            if local_team in {"A", "B"} and local_team != claim_team
            else base_team_state
            if base_team_state in {"certain_A", "certain_B", "cross_team"}
            else f"certain_{claim_team}"
            if claim_team in {"A", "B"}
            else "unknown"
        )
        output.append(
            {
                "tracklet_id": tracklet_id,
                "frame": int(claim["frame"]),
                "stable_anonymous_slot_id": (
                    None if status == "conflicted" else slot_id
                ),
                "stable_anonymous_entity_id": (
                    None if status == "conflicted" else slot_id
                ),
                "stable_anchor_source": "canonical_frame_global_identity",
                "stable_anchor_status": "frame_level_canonical_ownership",
                "stable_anchor_claims": [
                    {
                        "source": "global_identity",
                        "stable_slot_id": slot_id,
                    }
                ],
                "team_label": (
                    local_team
                    if status == "conflicted" and local_team in {"A", "B"}
                    else str(claim["team_label"])
                ),
                "reviewed_team_attribution_state": reviewed_team_state,
                "fallback_label": (
                    f"{local_team}?"
                    if status == "conflicted" and local_team in {"A", "B"}
                    else slot_id
                ),
                "identity_status": status,
                "canonical_player_id": player_id,
                "player_name": player.get("name") if player else None,
                "roster_number": player.get("number") if player else None,
                "display_label": (
                    str(player.get("name"))
                    if player
                    else f"{slot_id} !"
                    if status == "conflicted"
                    else slot_id
                ),
                "identity_source": source,
                "eligible_for_player_stats": status == "confirmed",
                "hard_blockers": sorted(set(blockers)),
                "conflicts": conflicts,
                "propagation_conflicted_stable_slot_ids": sorted(
                    {
                        str(value)
                        for value in base.get(
                            "propagation_conflicted_stable_slot_ids"
                        )
                        or []
                    }
                    | (
                        {str(slot_id)}
                        if slot_id in conflicting_slot_roster_bindings
                        else set()
                    )
                ),
                "canonical_ownership_evidence": {
                    "source": claim["ownership_evidence_source"],
                    "field": claim["ownership_evidence_field"],
                    "stable_subject_id": claim.get("stable_subject_id"),
                },
            }
        )
    return sorted(
        output,
        key=lambda row: (int(row["frame"]), str(row["tracklet_id"])),
    )


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


def _is_explicit_subject_player_decision(
    decision: dict[str, Any] | None,
) -> bool:
    return bool(
        decision
        and decision.get("decision")
        in {"assign_roster_player", "confirm_recommended_player"}
        and decision.get("player_id")
    )


def _summary(
    assignments: list[dict[str, Any]],
    coverage: dict[str, Any],
    fragmentation: dict[str, Any],
    uniqueness: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
    canonical_observations: list[dict[str, Any]],
    segment_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    effective_segment_observations = segment_observations or []
    counts, technical = _effective_assignment_status_counts(
        assignments, tracklets, canonical_observations, effective_segment_observations
    )
    product_tracklet_ids = _product_tracklet_ids(tracklets)
    confirmed_player_ids = {
        str(row["canonical_player_id"])
        for row in assignments
        if row.get("canonical_player_id")
        and str(row.get("tracklet_id") or "") in product_tracklet_ids
    }
    confirmed_player_ids.update(
        str(row["canonical_player_id"])
        for row in effective_segment_observations
        if row.get("canonical_player_id")
    )
    product_assignments = [
        row
        for row in assignments
        if str(row.get("tracklet_id") or "") in product_tracklet_ids
    ]
    product_entity_keys = {
        str(row.get("stable_anonymous_slot_id") or row.get("fragment_id"))
        for row in product_assignments
    }
    product_stable_slot_ids = {
        str(row["stable_anonymous_slot_id"])
        for row in product_assignments
        if row.get("stable_anonymous_slot_id")
    }
    return {
        # Technical lineage counts intentionally retain all tracklets/entities.
        # Product readiness below is scoped only to tracklets with on-pitch
        # detected observations.
        "tracklets_total": len(assignments),
        "product_tracklets_total": sum(counts.values()),
        "product_entities_total": len(product_entity_keys),
        "product_stable_anonymous_entities": len(product_stable_slot_ids),
        "confirmed": counts["confirmed"],
        "probable": counts["probable"],
        "unresolved": counts["unresolved"],
        "conflicted": counts["conflicted"],
        "blocked": counts["blocked"],
        "confirmed_players": len(confirmed_player_ids),
        **coverage,
        "conflict_count": counts["conflicted"],
        "cross_team_violations": sum(any(value.get("code") == "cross_team_confirmed_assignment" for value in row["conflicts"]) for row in assignments),
        "invalid_roster_references": sum("invalid_roster_player" in row["hard_blockers"] for row in assignments),
        "stable_anonymous_entities": fragmentation["stable_anonymous_entities_total"],
        "unanchored_fragments": fragmentation["unanchored_fragments"],
        "automatic_permanent_allocations": fragmentation[
            "automatic_permanent_allocations"
        ],
        **technical,
        **uniqueness,
    }


def _effective_assignment_status_counts(
    assignments: list[dict[str, Any]],
    tracklets: dict[str, dict[str, Any]],
    canonical_observations: list[dict[str, Any]],
    segment_observations: list[dict[str, Any]],
) -> tuple[Counter[str], dict[str, int]]:
    """Avoid treating a fully resolved frame-owned tracklet as a product conflict."""
    ownership = {
        (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0)): row
        for row in canonical_observations
    }
    ownership.update(
        {
            (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0)): row
            for row in segment_observations
        }
    )
    counts: Counter[str] = Counter()
    fully_resolved = 0
    ownership_gaps = 0
    excluded_without_inside = 0
    off_pitch_only = 0
    product_tracklet_ids = _product_tracklet_ids(tracklets)
    for assignment in assignments:
        tracklet_id = str(assignment.get("tracklet_id") or "")
        technical_multi = "upstream_multi_slot_tracklet_membership" in (
            assignment.get("hard_blockers") or []
        )
        positions = tracklets.get(tracklet_id, {}).get("positions_m")
        all_detected = {
            (tracklet_id, int(position.get("frame") or 0))
            for position in positions or []
            if str(position.get("status") or "detected") == "detected"
            and str(position.get("source") or "detected") not in {"predicted", "interpolated", "unknown", "missing", "ambiguous"}
        }
        detected = {
            (tracklet_id, int(position.get("frame") or 0))
            for position in positions or []
            if str(position.get("status") or "detected") == "detected"
            and str(position.get("source") or "detected") not in {"predicted", "interpolated", "unknown", "missing", "ambiguous"}
            and is_on_pitch_product_observation(position)
        }
        if tracklet_id not in product_tracklet_ids:
            excluded_without_inside += 1
            if all_detected:
                off_pitch_only += 1
            continue
        owned = [ownership.get(key) for key in detected]
        if technical_multi and detected and all(owned):
            statuses = {str(row.get("identity_status") or "unresolved") for row in owned if row}
            if statuses == {"confirmed"}:
                counts["confirmed"] += 1
                fully_resolved += 1
                continue
            if statuses == {"unresolved"}:
                counts["unresolved"] += 1
                fully_resolved += 1
                continue
            counts["conflicted"] += 1
            continue
        if technical_multi and detected and not all(owned):
            ownership_gaps += 1
        counts[str(assignment.get("identity_status") or "unresolved")] += 1
    return counts, {
        "technical_multi_slot_tracklets": sum(
            "upstream_multi_slot_tracklet_membership" in (row.get("hard_blockers") or [])
            for row in assignments
        ),
        "fully_resolved_frame_owned_tracklets": fully_resolved,
        "frame_ownership_gap_tracklets": ownership_gaps,
        "tracklets_without_inside_play_detections": excluded_without_inside,
        "off_pitch_only_tracklets": off_pitch_only,
    }


def _product_tracklet_ids(
    tracklets: dict[str, dict[str, Any]],
) -> set[str]:
    product_tracklet_ids: set[str] = set()
    for tracklet_id, tracklet in tracklets.items():
        positions = tracklet.get("positions_m")
        if not isinstance(positions, list):
            # Legacy fixtures/artifacts without materialized positions retain
            # their pre-play-area product semantics until rebuilt.
            product_tracklet_ids.add(tracklet_id)
            continue
        if any(
            str(position.get("status") or "detected") == "detected"
            and str(position.get("source") or "detected")
            not in {"predicted", "interpolated", "unknown", "missing", "ambiguous"}
            and is_on_pitch_product_observation(position)
            for position in positions
        ):
            product_tracklet_ids.add(tracklet_id)
    return product_tracklet_ids


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


def _team_id_for_label(match_doc: dict[str, Any], team_label: str) -> str:
    for index, team in enumerate(match_doc.get("teams") or []):
        label = str(team.get("team_label") or chr(ord("A") + index))
        if label == team_label:
            return str(team.get("id") or "")
    return ""


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
        return {
            key: _semantic_input(item)
            for key, item in value.items()
            if key
            not in {
                "generated_at",
                "updated_at",
                "reviewed_at",
                "operator_telemetry",
                "telemetry_state",
                "created_at",
                "comment",
            }
        }
    return value


def _optional(path: Path) -> dict[str, Any]:
    return _load(path) if path.exists() else {}


def _load(path: Path) -> dict[str, Any]:
    # Request-scoped reuse; identical strict parse semantics (raises on
    # malformed canonical JSON exactly as the raw loader did).
    return load_json_cached(path)
