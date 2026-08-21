from __future__ import annotations

"""Durable, derived read model for the Reviewed Identity operator hot path.

The canonical reviewed artifacts remain the source of truth.  This document is
only a restart-safe materialization of the already-built review queue and its
server-only exact ownership.  It deliberately keeps ownership out of every
public response while allowing context and deferred saves to avoid reparsing
the match-wide tracker artifact on each click.
"""

import json
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_correction_context import (
    match_roster,
    reviewed_decisions_semantic_digest,
)
from app.services.identity_reviewed_progress import (
    build_reviewed_identity_progress,
    reviewed_snapshot_file_fingerprint,
)
from app.services.identity_reviewed_slot_registry import (
    build_materialized_reviewed_slot_registry,
    build_reviewed_slot_registry,
)
from app.services.identity_reviewed_slot_review import load_reviewed_slot_assignments
from app.services.identity_review_scope import identity_review_scope_digest
from app.services.identity_reviewed_effective_observation import is_real_detected_position
from app.services.play_area import is_on_pitch_product_observation


FILENAME = "reviewed_identity_hot_state.json"
SCHEMA_VERSION = "1.2.0"


class ReviewedIdentityHotStateError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def load_or_rebuild_review_hot_state(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    state = _load(match_path / FILENAME)
    if _is_fresh(state, match_path, match_doc):
        return state
    return rebuild_review_hot_state(match_path, match_doc)


def load_existing_fresh_hot_state(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a usable materialization without triggering a recovery rebuild."""
    state = _load(match_path / FILENAME)
    return state if _is_fresh(state, match_path, match_doc) else None


def rebuild_review_hot_state(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    """Cold/recovery path.  The only place that rebuilds full progress."""
    progress = build_reviewed_identity_progress(
        match_path,
        match_doc,
        include_internal_units=True,
    )
    candidate = _load(match_path / "identity_candidate_shadow.json") or {}
    registry = build_materialized_reviewed_slot_registry(
        candidate,
        load_reviewed_slot_assignments(match_path),
    )
    if not registry:
        registry = build_reviewed_slot_registry(
            match_path,
            load_reviewed_slot_assignments(match_path),
        )
    internal = list(progress.pop("_internal_review_units", []) or [])
    _attach_exact_whole_subject_digests(match_path, internal)
    _attach_temporal_split_context(match_path, internal)
    state = _json_safe({
        "schema_version": SCHEMA_VERSION,
        "state_version": 1,
        "match_id": str(match_doc.get("id") or match_path.name),
        "progress": progress,
        "internal_review_units": internal,
        "unit_lookup": _unit_lookup(internal),
        "source_index": _source_index(internal),
        "roster_options": match_roster(match_doc),
        "slot_options": [registry[key] for key in sorted(registry)],
        "freshness": _freshness(match_path, match_doc),
    })
    write_identity_json_atomic(match_path / FILENAME, state)
    return state


def hot_progress(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state.get("progress") or {})


def hot_review_unit(
    state: dict[str, Any],
    candidate_subject_id: str,
    review_target_id: str | None = None,
) -> dict[str, Any] | None:
    key = _lookup_key(candidate_subject_id, review_target_id)
    index = (state.get("unit_lookup") or {}).get(key)
    units = state.get("internal_review_units") or []
    if isinstance(index, int) and 0 <= index < len(units):
        unit = units[index]
        if isinstance(unit, dict):
            return unit
    # Schema 1.0 materializations are never served because their schema is
    # rejected above. This tiny defensive fallback keeps a manually repaired
    # state document recoverable without exposing an incorrect context.
    for unit in units:
        if isinstance(unit, dict) and _lookup_key(
            str(unit.get("candidate_subject_id") or ""), unit.get("review_target_id")
        ) == key:
            return unit
    return None


def assert_hot_state_version(
    state: dict[str, Any],
    supplied: Any,
) -> None:
    if supplied is None:
        # Compatibility for API consumers deployed before the version field.
        return
    try:
        value = int(supplied)
    except (TypeError, ValueError) as exc:
        raise ReviewedIdentityHotStateError("review_state_stale") from exc
    if value != int(state.get("state_version") or 0):
        raise ReviewedIdentityHotStateError("review_state_stale")


def update_hot_state_after_deferred_save(
    match_path: Path,
    match_doc: dict[str, Any],
    state: dict[str, Any],
    review_unit: dict[str, Any],
    saved_decision: dict[str, Any] | None,
    semantic_decision_digest: str,
) -> dict[str, Any]:
    """Apply one saved operator disposition to the derived queue.

    Finalization will reconstruct all coverage from canonical artifacts.  Until
    then this is intentionally conservative: the exact reviewed card is
    removed, its internal state is marked resolved, and no new work is guessed
    into the queue.
    """
    subject = str(review_unit.get("candidate_subject_id") or "")
    target = str(review_unit.get("review_target_id") or "") or None
    decision = dict(saved_decision or {})
    roster_teams = {
        str(row.get("player_id") or ""): str(row.get("team_label") or "U").upper()
        for row in state.get("roster_options") or []
        if isinstance(row, dict)
    }
    for unit in state.get("internal_review_units") or []:
        if not isinstance(unit, dict):
            continue
        if str(unit.get("candidate_subject_id") or "") != subject:
            continue
        if (str(unit.get("review_target_id") or "") or None) != target:
            continue
        unit["current_decision"] = decision or unit.get("current_decision")
        unit["current_resolution_status"] = "reviewed_by_operator"
        unit["priority"] = None
        action = str(decision.get("action") or "")
        player_id = str(decision.get("player_id") or "") or None
        unit["canonical_player_id"] = player_id if action == "assign_roster_player" else None
        if player_id:
            unit["effective_team_label"] = roster_teams.get(player_id, unit.get("effective_team_label"))
        elif decision.get("team_label"):
            unit["effective_team_label"] = str(decision["team_label"]).upper()

    progress = state.get("progress") or {}
    for key in ("next_cases", "optional_audit_cases"):
        progress[key] = [
            row for row in progress.get(key) or []
            if not (
                str((row or {}).get("candidate_subject_id") or "") == subject
                and (str((row or {}).get("review_target_id") or "") or None) == target
            )
        ]
    summary = progress.get("summary")
    if isinstance(summary, dict):
        remaining = len(progress.get("next_cases") or [])
        summary["important_decisions_remaining"] = remaining
        summary["semantic_decisions_remaining"] = min(int(summary.get("semantic_decisions_remaining") or 0), remaining)
        summary["coverage_decisions_remaining"] = min(int(summary.get("coverage_decisions_remaining") or 0), remaining)
        summary["material_continuity_decisions_remaining"] = min(int(summary.get("material_continuity_decisions_remaining") or 0), remaining)
        summary["optional_audit_cases_remaining"] = len(progress.get("optional_audit_cases") or [])
    state["state_version"] = int(state.get("state_version") or 0) + 1
    state["freshness"] = _freshness(match_path, match_doc, semantic_digest=semantic_decision_digest)
    write_identity_json_atomic(match_path / FILENAME, state)
    return state


def hot_context(
    state: dict[str, Any],
    candidate_subject_id: str,
    review_target_id: str | None = None,
) -> dict[str, Any]:
    unit = hot_review_unit(state, candidate_subject_id, review_target_id)
    if not isinstance(unit, dict):
        raise ValueError(f"Unknown reviewed correction target: {candidate_subject_id}")
    scope_kind = str(unit.get("scope_kind") or "whole_subject")
    source_team = str(unit.get("source_team_label") or "U").upper()
    # A material continuity unit joins several safe fragments. A human may
    # correct an earlier automatic team attribution, so it deliberately has
    # the same cross-team roster affordance as the legacy material card.
    available = (
        ["A", "B"]
        if source_team == "U" or scope_kind == "material_continuity"
        else [source_team]
    )
    decision = unit.get("current_decision")
    effective_team = str((decision or {}).get("team_label") or unit.get("effective_team_label") or source_team).upper()
    visual = dict(unit.get("visual_evidence") or {})
    return {
        "candidate_subject_id": candidate_subject_id,
        "review_target_id": review_target_id,
        "scope_kind": scope_kind,
        "team_label": source_team,
        "source_team_label": source_team,
        "effective_team_label": effective_team,
        "available_team_labels": available,
        "tracklet_ids": list(unit.get("tracklet_ids") or []),
        "continuity_subject_ids": list(unit.get("continuity_subject_ids") or []),
        "continuity_group_id": unit.get("continuity_group_id"),
        "review_card_key": unit.get("review_card_key"),
        "roster_options": list(state.get("roster_options") or []),
        "slot_options": [
            option for option in state.get("slot_options") or []
            if isinstance(option, dict) and option.get("team_label") in available
        ],
        "current_decision": decision,
        "semantic_decision_digest": str((state.get("freshness") or {}).get("semantic_decision_digest") or ""),
        "source_ownership_digest": unit.get("source_ownership_digest"),
        "frame_ranges": list(unit.get("frame_ranges") or []),
        "frame_start": unit.get("frame_start"),
        "frame_end": unit.get("frame_end"),
        "detected_observation_count": unit.get("detected_observation_count"),
        "visual_evidence": visual,
        "source_evidence_kind": str(visual.get("kind") or "identity_continuity"),
        "legacy_suggestion": unit.get("legacy_suggestion"),
        "temporal_split": unit.get("temporal_split"),
        "action_capabilities": _capabilities(unit),
        "scope_copy": _scope_copy(scope_kind),
        "review_state_version": int(state.get("state_version") or 0),
    }


def _freshness(match_path: Path, match_doc: dict[str, Any], *, semantic_digest: str | None = None) -> dict[str, Any]:
    return {
        "source_snapshot_file": reviewed_snapshot_file_fingerprint(match_path),
        "source_review_scope_digest": identity_review_scope_digest(match_doc),
        "semantic_decision_digest": semantic_digest or reviewed_decisions_semantic_digest(match_path),
        "dependencies": {name: _file_fingerprint(match_path / name) for name in (
            "tracklets.json", "identity_candidate_shadow.json", "reviewed_identity_segment_review.json",
            "reviewed_identity_segment_decisions.json", "reviewed_identity_material_continuity_decisions.json",
            "reviewed_identity_mixed_players.json", "reviewed_identity_snapshot.json",
        )},
    }


def _is_fresh(state: dict[str, Any] | None, match_path: Path, match_doc: dict[str, Any]) -> bool:
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        return False
    freshness = state.get("freshness")
    if not isinstance(freshness, dict):
        return False
    return freshness == _freshness(match_path, match_doc)


def _file_fingerprint(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"mtime_ns": int(stat.st_mtime_ns), "size_bytes": int(stat.st_size)}


def _load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _json_safe(value: Any) -> Any:
    """Normalize progress internals before writing the durable read model.

    A few server-only review units use sets for efficient same-frame checks.
    The hot-state file must remain a regular JSON artifact across restarts, so
    normalize those internals at the materialization boundary only.
    """
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=repr)]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def invalidate_review_hot_state(match_path: Path) -> None:
    """Make the next read rebuild from canonical artifacts after a cache failure."""
    try:
        (match_path / FILENAME).unlink(missing_ok=True)
    except OSError:
        # This is only a cache invalidation best effort. A stale state still
        # fails freshness validation because canonical decision fingerprints
        # changed, so it can never continue serving as current state.
        pass


def _attach_exact_whole_subject_digests(
    match_path: Path,
    units: list[dict[str, Any]],
) -> None:
    """Add the existing exact whole-subject digest during controlled rebuild.

    The source is calculated once while materializing, never during a normal
    context request or deferred save. Canonical segment/material units already
    carry this digest from their authoritative artifacts.
    """
    candidate_document = _load(match_path / "identity_candidate_shadow.json") or {}
    tracklet_document = _load(match_path / "tracklets.json") or {}
    subjects = {
        str(row.get("candidate_subject_id") or ""): row
        for row in candidate_document.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    tracklets = {
        str(row.get("tracklet_id") or ""): row
        for row in tracklet_document.get("tracklets") or []
        if isinstance(row, dict) and row.get("tracklet_id")
    }
    for unit in units:
        if str(unit.get("scope_kind") or "whole_subject") != "whole_subject":
            continue
        subject_id = str(unit.get("candidate_subject_id") or "")
        subject = subjects.get(subject_id)
        if not isinstance(subject, dict):
            # Tiny legacy/unit-test fixtures can materialize a progress unit
            # without a complete candidate artifact. Retain compatibility but
            # never fabricate an ownership claim.
            continue
        tracklet_ids = sorted(str(value) for value in subject.get("tracklet_ids") or [])
        observations: list[dict[str, Any]] = []
        for tracklet_id in tracklet_ids:
            tracklet = tracklets.get(tracklet_id) or {}
            for position in tracklet.get("positions_m") or []:
                if not isinstance(position, dict):
                    continue
                if not is_real_detected_position(position) or not is_on_pitch_product_observation(position):
                    continue
                observations.append({
                    "tracklet_id": tracklet_id,
                    "frame": int(position.get("frame") or 0),
                })
        observations.sort(key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))
        unit["source_ownership_digest"] = canonical_digest({
            "candidate_subject_id": subject.get("candidate_subject_id"),
            "tracklet_ids": tracklet_ids,
            "observations": observations,
        })


def _attach_temporal_split_context(match_path: Path, units: list[dict[str, Any]]) -> None:
    """Keep durable split state available without re-resolving raw ownership."""
    document = _load(match_path / "reviewed_identity_mixed_players.json") or {}
    cases = document.get("cases") or []
    split_by_source = {
        _source_key(source): case
        for case in cases
        if isinstance(case, dict)
        and str(case.get("original_issue") or "") == "inline_temporal_split"
        and isinstance((source := case.get("source")), dict)
    }
    for unit in units:
        case = split_by_source.get(_source_key(unit))
        if not isinstance(case, dict):
            continue
        unit["temporal_split"] = {
            "resolution_status": case.get("resolution_status"),
            "split_after_frames": list(case.get("split_after_frames") or []),
            "segment_assignments": list(case.get("segment_assignments") or []),
            "split_semantic_digest": case.get("split_semantic_digest"),
        }


def _lookup_key(candidate_subject_id: str, review_target_id: Any) -> str:
    target = str(review_target_id or "").strip()
    return f"{candidate_subject_id}\u001f{target}"


def _unit_lookup(units: list[dict[str, Any]]) -> dict[str, int]:
    return {
        _lookup_key(str(unit.get("candidate_subject_id") or ""), unit.get("review_target_id")): index
        for index, unit in enumerate(units)
        if str(unit.get("candidate_subject_id") or "")
    }


def _source_key(source: dict[str, Any]) -> str:
    return json.dumps(
        {
            key: source.get(key)
            for key in (
                "scope_kind",
                "candidate_subject_id",
                "review_target_id",
                "continuity_group_id",
                "source_ownership_digest",
            )
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _source_index(units: list[dict[str, Any]]) -> dict[str, int]:
    return {
        _source_key(unit): index
        for index, unit in enumerate(units)
        if str(unit.get("candidate_subject_id") or "")
    }


def _capabilities(unit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from app.services.identity_reviewed_action_scope import reviewed_identity_action_capabilities
    return reviewed_identity_action_capabilities(unit)


def _scope_copy(scope_kind: str) -> str:
    from app.services.identity_reviewed_action_scope import scope_copy
    return scope_copy(scope_kind)
