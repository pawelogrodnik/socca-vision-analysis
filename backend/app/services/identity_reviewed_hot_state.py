from __future__ import annotations

"""Durable, derived read model for the Reviewed Identity operator hot path.

The canonical reviewed artifacts remain the source of truth.  This document is
only a restart-safe materialization of the already-built review queue and its
server-only exact ownership.  It deliberately keeps ownership out of every
public response while allowing context and deferred saves to avoid reparsing
the match-wide tracker artifact on each click.
"""

import json
from copy import deepcopy
from pathlib import Path
import time
from typing import Any

from app.services.identity_canonical_io import (
    load_json_cached_or,
    review_build_context,
)
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_ownership_compact import (
    CompactOwnershipError,
    decode_observation_runs,
    decode_pair_runs,
    encode_index_rows,
    encode_observation_rows,
    encode_pair_runs,
    validate_v2_hot_state,
)
from app.services.identity_reviewed_correction_context import (
    concurrent_correction_context_fields,
    match_roster,
    reviewed_decisions_semantic_digest,
    temporal_split_context_for_source,
)
from app.services.identity_reviewed_progress import (
    build_reviewed_identity_progress,
    project_reviewed_identity_progress,
    reviewed_snapshot_file_fingerprint,
)
from app.services.identity_reviewed_slot_registry import (
    build_materialized_reviewed_slot_registry,
    build_reviewed_slot_registry,
)
from app.services.identity_reviewed_slot_review import load_reviewed_slot_assignments
from app.services.identity_reviewed_segments import load_segment_review
from app.services.identity_review_scope import identity_review_scope_digest
from app.services.identity_reviewed_effective_observation import is_real_detected_position
from app.services.identity_reviewed_mixed_store import (
    load_mixed_player_cases,
    render_mixed_review_evidence,
    temporal_evidence_for_observations,
)
from app.services.play_area import is_on_pitch_product_observation


FILENAME = "reviewed_identity_hot_state.json"
REVISION_FILENAME = "reviewed_identity_hot_state_revision.json"
# 2.4 invalidates materializations that predate correction-only historical
# split repairs. A warm GET must never mistake an older hot document for a
# complete repair index.
SCHEMA_VERSION = "2.4.0"

# Diagnostics only. The reproject response reads this immediately after the
# authoritative warm write; it is never persisted in the hot-state contract.
_LAST_REBUILD_PHASES: dict[str, float] = {}


def last_hot_state_build_phases() -> dict[str, float]:
    return dict(_LAST_REBUILD_PHASES)


class ReviewedIdentityHotStateError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def load_or_rebuild_review_hot_state(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    state = _load(match_path / FILENAME)
    if state is not None and _is_fresh(state, match_path, match_doc):
        return state
    return rebuild_review_hot_state(match_path, match_doc)


def load_or_rebuild_review_hot_state_with_source(
    match_path: Path,
    match_doc: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Single probe: exactly one load/validate pass per request.

    Returns ``(state, "warm_hit" | "cold_rebuild")`` so callers never pay a
    second parse/validation of a stale multi-MB hot document.
    """
    state = _load(match_path / FILENAME)
    if state is not None and _is_fresh(state, match_path, match_doc):
        return state, "warm_hit"
    return rebuild_review_hot_state(match_path, match_doc), "cold_rebuild"


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
    *,
    prebuilt_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cold/recovery path.  The only place that rebuilds full progress."""
    with review_build_context():
        return _rebuild_review_hot_state_scoped(
            match_path,
            match_doc,
            prebuilt_progress=prebuilt_progress,
        )


def _rebuild_review_hot_state_scoped(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    prebuilt_progress: dict[str, Any] | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    if prebuilt_progress is not None:
        # Authoritative caller already materialized progress in this request;
        # reuse it instead of a second full canonical pass.
        progress = dict(prebuilt_progress)
    else:
        phase_started = time.perf_counter()
        progress = build_reviewed_identity_progress(
            match_path,
            match_doc,
            include_internal_units=True,
        )
        timings["progress_build_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
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
    timings["materialized_slot_registry_ms"] = _elapsed_ms(phase_started)
    # Canonical segments intentionally retain the legacy segment registry
    # semantics. Whole subjects and material continuity use the materialized
    # registry because their source can cross automatic subject boundaries.
    phase_started = time.perf_counter()
    canonical_segment_registry = build_reviewed_slot_registry(
        match_path,
        load_reviewed_slot_assignments(match_path),
    )
    timings["canonical_segment_registry_ms"] = _elapsed_ms(phase_started)
    internal = list(progress.pop("_internal_review_units", []) or [])
    projection_inputs = dict(progress.pop("_projection_inputs", {}) or {})
    phase_started = time.perf_counter()
    _attach_exact_whole_subject_digests(match_path, internal)
    timings["exact_whole_subject_digest_attachment_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
    _attach_legacy_context_fields(match_path, internal)
    timings["legacy_context_attachment_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
    historical_split_repairs, historical_repair_ms = _attach_correction_temporal_evidence(
        match_path,
        match_doc,
        internal,
    )
    correction_total_ms = _elapsed_ms(phase_started)
    timings["historical_repair_materialization_ms"] = historical_repair_ms
    timings["correction_temporal_evidence_attachment_ms"] = round(
        max(0.0, correction_total_ms - historical_repair_ms),
        1,
    )
    phase_started = time.perf_counter()
    _attach_temporal_split_context(match_path, internal)
    timings["temporal_split_context_attachment_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
    unit_lookup = _unit_lookup(internal)
    source_index = _source_index(internal)
    timings["lookup_source_index_build_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
    state = {
        "schema_version": SCHEMA_VERSION,
        "state_version": _next_state_version(match_path),
        "match_id": str(match_doc.get("id") or match_path.name),
        "progress": progress,
        "internal_review_units": internal,
        "unit_lookup": unit_lookup,
        "source_index": source_index,
        "historical_split_repairs": historical_split_repairs,
        "projection_inputs": projection_inputs,
        "roster_options": match_roster(match_doc),
        "slot_options": [registry[key] for key in sorted(registry)],
        "canonical_segment_slot_options": [
            canonical_segment_registry[key]
            for key in sorted(canonical_segment_registry)
        ],
        "freshness": _freshness(match_path, match_doc),
    }
    timings["freshness_calculation_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
    encoded = _encode_for_write(state)
    timings["durable_encoding_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
    write_identity_json_atomic(match_path / FILENAME, encoded, compact=True)
    timings["hot_state_json_write_ms"] = _elapsed_ms(phase_started)
    state["progress"] = _json_safe(progress)
    timings["total_ms"] = _elapsed_ms(started)
    _LAST_REBUILD_PHASES.clear()
    _LAST_REBUILD_PHASES.update(timings)
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
    unit: dict[str, Any] | None = None
    if isinstance(index, int) and 0 <= index < len(units):
        found = units[index]
        if isinstance(found, dict):
            unit = found
    if unit is None:
        # Schema 1.0 materializations are never served because their schema is
        # rejected above. This tiny defensive fallback keeps a manually repaired
        # state document recoverable without exposing an incorrect context.
        for candidate in units:
            if (
                isinstance(candidate, dict)
                and _lookup_key(
                    str(candidate.get("candidate_subject_id") or ""),
                    candidate.get("review_target_id"),
                )
                == key
            ):
                unit = candidate
                break
    return _expand_unit(unit) if isinstance(unit, dict) else None


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
    """Apply a save then use the canonical queue projection, never patches.

    The compact coverage pair index and every exact internal unit were captured
    when the hot state was built.  Updating one unit therefore has the same
    coverage/MAX consequences as a cold progress build without rereading the
    match-wide tracklets file.
    """
    subject = str(review_unit.get("candidate_subject_id") or "")
    target = str(review_unit.get("review_target_id") or "") or None
    decision = _projection_decision(review_unit, saved_decision)
    source_digest = str(review_unit.get("source_ownership_digest") or "")
    roster_teams = {
        str(row.get("player_id") or ""): str(row.get("team_label") or "U").upper()
        for row in state.get("roster_options") or []
        if isinstance(row, dict)
    }
    # Compact units stay compact through the whole save. Projection evaluates
    # ownership from validated runs directly, so an ordinary correction never
    # materializes match-wide (tracklet_id, frame) pairs.
    updated_unit = False
    units = list(state.get("internal_review_units") or [])
    action = str(decision.get("action") or "")
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        if str(unit.get("candidate_subject_id") or "") != subject:
            continue
        if (str(unit.get("review_target_id") or "") or None) != target:
            continue
        if source_digest and str(unit.get("source_ownership_digest") or "") != source_digest:
            continue
        if action == "mixed_players":
            # Exact staging changes queue routing, not source topology.  The
            # durable mixed marker owns the same source tuple, so retire this
            # one materialized Required source while leaving all siblings
            # (including ones with the same raw candidate id) available.
            units.pop(index)
            _update_hot_mixed_projection(state, decision)
            updated_unit = True
            break
        unit["current_decision"] = decision or unit.get("current_decision")
        unit["current_resolution_status"] = "reviewed_by_operator"
        unit["priority"] = None
        player_id = str(decision.get("player_id") or "") or None
        unit["canonical_player_id"] = player_id if action == "assign_roster_player" else None
        if player_id:
            unit["effective_team_label"] = roster_teams.get(player_id, unit.get("effective_team_label"))
        elif decision.get("team_label"):
            unit["effective_team_label"] = str(decision["team_label"]).upper()
        updated_unit = True
        break
    if not updated_unit:
        raise ReviewedIdentityHotStateError("review_queue_stale")
    projected = project_reviewed_identity_progress(
        units,
        match_doc,
        dict(state.get("projection_inputs") or {}),
        include_internal_units=True,
    )
    state["progress"] = {
        key: value
        for key, value in projected.items()
        if key not in {"_internal_review_units", "_projection_inputs"}
    }
    state["internal_review_units"] = list(projected.get("_internal_review_units") or [])
    state["unit_lookup"] = _unit_lookup(state["internal_review_units"])
    state["source_index"] = _source_index(state["internal_review_units"])
    state["state_version"] = _next_state_version(match_path)
    state["freshness"] = _freshness(match_path, match_doc, semantic_digest=semantic_decision_digest)
    write_identity_json_atomic(match_path / FILENAME, _encode_for_write(state), compact=True)
    return state


def _projection_decision(
    review_unit: dict[str, Any],
    saved_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    """Normalize a transport-shaped save response for hot queue projection.

    Material-continuity saves compact their exact ownership for HTTP and keep
    the canonical correction below ``decision``. The hot queue needs that
    canonical action at the top level to retire the just-resolved unit.
    """
    saved = dict(saved_decision or {})
    nested = saved.get("decision")
    if (
        str(review_unit.get("scope_kind") or "") == "material_continuity"
        and isinstance(nested, dict)
        and nested.get("action")
    ):
        return dict(nested)
    return saved


def _update_hot_mixed_projection(
    state: dict[str, Any],
    saved_decision: dict[str, Any],
) -> None:
    """Update the compact Mixed Players read model without a cold rebuild.

    The dedicated Mixed Players endpoint will materialize crops on its later
    workflow phase.  The Required hot path only exposes the compact summary,
    so retaining the durable marker's exact source while replacing its one
    matching case is sufficient and avoids reopening tracker artifacts.
    """
    inputs = state.get("projection_inputs")
    if not isinstance(inputs, dict):
        raise ReviewedIdentityHotStateError("review_queue_stale")
    mixed = deepcopy(inputs.get("mixed_players") or {})
    cases = [
        dict(case)
        for case in mixed.get("cases") or []
        if isinstance(case, dict)
    ]
    marker_id = str(
        saved_decision.get("case_id")
        or saved_decision.get("candidate_subject_id")
        or ""
    )
    if not marker_id:
        raise ReviewedIdentityHotStateError("review_queue_stale")
    cases = [
        case for case in cases
        if str(case.get("case_id") or case.get("candidate_subject_id") or "") != marker_id
    ]
    cases.append(dict(saved_decision))
    cases.sort(key=lambda case: (
        int(case.get("frame_start") or 0),
        str(case.get("case_id") or case.get("candidate_subject_id") or ""),
    ))
    unresolved = sum(
        str(case.get("resolution_status") or "")
        in {"unresolved", "unresolved_complex_mix"}
        for case in cases
    )
    mixed["cases"] = cases
    mixed["summary"] = {
        "total": len(cases),
        "unresolved": unresolved,
        "resolved": sum(str(case.get("resolution_status") or "") == "resolved" for case in cases),
        "complex_unresolved": sum(
            str(case.get("resolution_status") or "") == "unresolved_complex_mix"
            for case in cases
        ),
    }
    inputs["mixed_players"] = mixed


def hot_context(
    state: dict[str, Any],
    candidate_subject_id: str,
    review_target_id: str | None = None,
) -> dict[str, Any]:
    unit = hot_review_unit(state, candidate_subject_id, review_target_id)
    if not isinstance(unit, dict):
        raise ValueError(f"Unknown reviewed correction target: {candidate_subject_id}")
    return _hot_context_from_unit(state, unit)


def hot_historical_split_repair_context(
    state: dict[str, Any],
    case_id: str,
) -> dict[str, Any]:
    """Project one correction-only historical parent from fresh hot state."""
    repair = (state.get("historical_split_repairs") or {}).get(case_id)
    if not isinstance(repair, dict):
        raise ValueError(f"Unknown historical split repair: {case_id}")
    return _hot_context_from_unit(state, repair)


def _hot_context_from_unit(state: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    """Cheap public projection for active units and repair-only parents."""
    candidate_subject_id = str(unit.get("candidate_subject_id") or "")
    review_target_id = str(unit.get("review_target_id") or "") or None
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
    # Segment targets preserve their baseline effective team in the legacy
    # context even after a saved cross-team correction; that decision remains
    # visible separately in current_decision. Keep the hot contract identical.
    effective_team = str(
        (unit.get("context_effective_team_label") if scope_kind == "canonical_segment" else (decision or {}).get("team_label"))
        or unit.get("effective_team_label")
        or source_team
    ).upper()
    visual = dict(
        unit.get("correction_temporal_evidence")
        or unit.get("visual_evidence")
        or {}
    )
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
            option for option in (
                state.get("canonical_segment_slot_options")
                if scope_kind == "canonical_segment"
                else state.get("slot_options")
            ) or []
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
        "source_evidence_kind": str(
            unit.get("source_evidence_kind")
            or visual.get("kind")
            or "identity_continuity"
        ),
        "legacy_suggestion": unit.get("legacy_suggestion"),
        "temporal_split": unit.get("temporal_split"),
        "temporal_topology": unit.get("temporal_topology"),
        "concurrent_resolution": unit.get("concurrent_resolution"),
        "historical_concurrent_repair": bool(
            unit.get("historical_concurrent_repair")
        ),
        "historical_parent_repair": unit.get("historical_parent_repair"),
        "action_capabilities": _capabilities(unit),
        "scope_copy": _scope_copy(scope_kind),
        "review_state_version": int(state.get("state_version") or 0),
    }


def _freshness(match_path: Path, match_doc: dict[str, Any], *, semantic_digest: str | None = None) -> dict[str, Any]:
    return {
        "source_snapshot_file": reviewed_snapshot_file_fingerprint(match_path),
        "source_review_scope_digest": identity_review_scope_digest(match_doc),
        "roster_semantic_digest": _roster_semantic_digest(match_doc),
        "semantic_decision_digest": semantic_digest or reviewed_decisions_semantic_digest(match_path),
        "dependencies": {name: _file_fingerprint(match_path / name) for name in (
            "tracklets.json", "identity_candidate_shadow.json", "reviewed_identity_segment_review.json",
            "reviewed_identity_segment_decisions.json", "reviewed_identity_material_continuity_decisions.json",
            "reviewed_identity_mixed_players.json", "reviewed_identity_snapshot.json",
            "reviewed_identity_team_attribution_evidence.json",
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
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") == SCHEMA_VERSION:
        try:
            validate_v2_hot_state(value, schema_version=SCHEMA_VERSION)
        except CompactOwnershipError:
            # A malformed exact-source cache must never be partially decoded.
            # Treat it as absent; the caller cold-rebuilds from canonical
            # artifacts, which remain the only source of truth.
            return None
        _rehydrate_projection_twins(value)
    return value


def _rehydrate_projection_twins(state: dict[str, Any]) -> None:
    """Restore projection twins from the single durable progress copy.

    ``mixed_players`` and ``deferred_correction_context`` are identical in
    progress and projection inputs.  The cache stores them once (inside
    progress) and reload restores the second view by reference, preserving
    the exact contract project_reviewed_identity_progress expects.
    """
    progress = state.get("progress")
    inputs = state.get("projection_inputs")
    if not isinstance(progress, dict) or not isinstance(inputs, dict):
        return
    for twin in ("mixed_players", "deferred_correction_context"):
        if twin not in inputs and twin in progress:
            inputs[twin] = progress[twin]


def _expand_unit(unit: dict[str, Any]) -> dict[str, Any]:
    """Restore exact expanded pair structures from the compact durable form.

    Compact runs are the storage representation only.  Review consumers always
    receive units shaped like the canonical expanded ownership lists, so no
    downstream module needs to know the cache encoding.
    """
    return _decode_node(unit)


def _encode_for_write(state: dict[str, Any]) -> dict[str, Any]:
    """Swap expanded pair structures for lossless runs at the disk boundary.

    Only the durable representation changes.  The in-memory state handed to
    callers keeps the exact expanded pair lists every review consumer relies
    on, and decoding reproduces those lists exactly.
    """
    encoded = dict(state)
    encoded["internal_review_units"] = [
        _encode_node(unit) if isinstance(unit, dict) else unit
        for unit in state.get("internal_review_units") or []
    ]
    projection_inputs = dict(state.get("projection_inputs") or {})
    if "observed_pairs" in projection_inputs:
        projection_inputs["observed_pair_runs"] = encode_pair_runs(
            projection_inputs.pop("observed_pairs")
        )
    if "pair_index" in projection_inputs:
        projection_inputs["pair_index_runs"] = encode_index_rows(
            projection_inputs.pop("pair_index")
        )
    # These two payloads are byte-identical twins of the progress copies and
    # are rehydrated by _rehydrate_projection_twins after a reload.
    projection_inputs.pop("mixed_players", None)
    projection_inputs.pop("deferred_correction_context", None)
    encoded["projection_inputs"] = _encode_node(projection_inputs) if isinstance(projection_inputs, dict) else projection_inputs
    return encoded


def _encode_pair_set(value: Any) -> dict[str, list[list[int]]] | None:
    return encode_pair_runs(sorted(tuple(pair) for pair in value or []))


_PAIR_RUN_KEYS = {
    "detected_pairs": ("detected_pair_runs", encode_pair_runs, decode_pair_runs),
    "owned_observations": (
        "owned_observation_runs",
        encode_observation_rows,
        decode_observation_runs,
    ),
    "_potential_named_observation_pairs": (
        "_potential_named_observation_runs",
        _encode_pair_set,
        decode_pair_runs,
    ),
}


def _encode_node(node: Any) -> Any:
    """Recursively replace known exact pair lists with compact run twins.

    Material continuity units embed ownership inside continuity members and
    owned-observation rows, so the transform must reach nested payloads.  A
    list is only replaced when its codec can represent it exactly.  Server-only
    sets and tuples are normalized in the same single pass so the durable
    document stays pure JSON without a second full-state walk.
    """
    if isinstance(node, dict):
        collected: dict[str, Any] = {}
        for key, value in node.items():
            collected[str(key)] = value
        # Policy enrichment attaches fresh named-gain runs under the legacy
        # pairs name while the base unit still carries its own runs twin.
        # The durable contract is exactly one representation, so canonize.
        if isinstance(collected.get("_potential_named_observation_pairs"), dict):
            collected["_potential_named_observation_runs"] = collected.pop(
                "_potential_named_observation_pairs"
            )
        encoded: dict[str, Any] = {}
        for key, value in collected.items():
            spec = _PAIR_RUN_KEYS.get(key)
            if spec is not None and isinstance(value, (list, set, frozenset)):
                runs_key, encoder, _decoder = spec
                runs = encoder(value)
                if runs is not None:
                    encoded[runs_key] = runs
                    continue
            encoded[key] = _encode_node(value)
        return encoded
    if isinstance(node, list):
        return [_encode_node(item) for item in node]
    if isinstance(node, (set, frozenset)):
        return [_encode_node(item) for item in sorted(node, key=repr)]
    if isinstance(node, tuple):
        return [_encode_node(item) for item in node]
    return node


def _decode_node(node: Any) -> Any:
    if isinstance(node, dict):
        decoded: dict[str, Any] = {}
        for key, value in node.items():
            restored_key = str(key)
            decoded_value = value
            for pair_key, (runs_key, _encoder, decoder) in _PAIR_RUN_KEYS.items():
                if restored_key == runs_key:
                    restored_key = pair_key
                    decoded_value = decoder(value)
                    break
            decoded[restored_key] = _decode_node(decoded_value)
        return decoded
    if isinstance(node, list):
        return [_decode_node(item) for item in node]
    return node


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


def _next_state_version(match_path: Path) -> int:
    """Allocate a durable monotonic revision even after cache invalidation."""
    revision_path = match_path / REVISION_FILENAME
    previous = _load(revision_path) or {}
    version = max(0, int(previous.get("state_version") or 0)) + 1
    write_identity_json_atomic(revision_path, {"state_version": version})
    return version


def _roster_semantic_digest(match_doc: dict[str, Any]) -> str:
    """Freshness must cover all operator-visible roster choices and labels."""
    return canonical_digest({
        "identity_review_scope": identity_review_scope_digest(match_doc),
        "roster": match_roster(match_doc),
    })


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
    tracklet_document = load_json_cached_or(match_path / "tracklets.json", {}) or {}
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


def _attach_legacy_context_fields(
    match_path: Path,
    units: list[dict[str, Any]],
) -> None:
    """Preserve segment-context fields whose legacy source is the target doc."""
    targets = {
        str(target.get("review_target_id") or ""): target
        for target in (load_segment_review(match_path).get("targets") or [])
        if isinstance(target, dict) and target.get("review_target_id")
    }
    for unit in units:
        if not isinstance(unit, dict) or unit.get("scope_kind") != "canonical_segment":
            continue
        target = targets.get(str(unit.get("review_target_id") or ""))
        if isinstance(target, dict):
            unit["context_effective_team_label"] = str(
                target.get("effective_team_label")
                or target.get("source_team_label")
                or "U"
            ).upper()


def _attach_correction_temporal_evidence(
    match_path: Path,
    match_doc: dict[str, Any],
    units: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], float]:
    """Cache legacy-equivalent correction crops during cold materialization.

    Hot context reads must not reconstruct raw match-wide sources.  The exact
    pairs are already server-owned by each materialized unit, so create the
    same representative crop payload once here and reuse it until ownership
    changes.  Rendering is best-effort just as it is for the legacy context.
    """
    tracklet_document = load_json_cached_or(match_path / "tracklets.json", {}) or {}
    observations_by_pair: dict[tuple[str, int], dict[str, Any]] = {}
    for tracklet in tracklet_document.get("tracklets") or []:
        if not isinstance(tracklet, dict):
            continue
        tracklet_id = str(tracklet.get("tracklet_id") or "")
        team_label = str(tracklet.get("team_label") or "U")
        for position in tracklet.get("positions_m") or []:
            if not isinstance(position, dict):
                continue
            if not is_real_detected_position(position) or not is_on_pitch_product_observation(position):
                continue
            observations_by_pair[(tracklet_id, int(position.get("frame") or 0))] = {
                **position,
                "tracklet_id": tracklet_id,
                "team_label": team_label,
            }
    review_document = _load(match_path / "identity_roster_subject_review_shadow.json") or {}
    evidence_kind_by_subject = {
        str(card.get("candidate_subject_id") or ""): str(
            (card.get("visual_evidence") or {}).get("kind") or "identity_continuity"
        )
        for card in review_document.get("cards") or []
        if isinstance(card, dict)
    }
    render_cases: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        pairs = {
            (str(pair[0]), int(pair[1]))
            for pair in unit.get("detected_pairs") or []
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        }
        observations = sorted(
            (observations_by_pair[pair] for pair in pairs if pair in observations_by_pair),
            key=lambda row: (int(row["frame"]), str(row["tracklet_id"])),
        )
        if not observations:
            continue
        subject_id = str(unit.get("candidate_subject_id") or "")
        crops = temporal_evidence_for_observations(subject_id, observations, limit=12)
        temporal_evidence = {
            "kind": "identity_continuity",
            "status": "ready" if crops else "missing",
            "selected_crop_count": len(crops),
            "anchor_crops": crops,
        }
        unit["correction_temporal_evidence"] = temporal_evidence
        unit["source_evidence_kind"] = str(
            (unit.get("visual_evidence") or {}).get("kind")
            or evidence_kind_by_subject.get(subject_id)
            or "identity_continuity"
        )
        source = {
            key: unit.get(key)
            for key in (
                "scope_kind",
                "candidate_subject_id",
                "review_target_id",
                "continuity_group_id",
                "source_ownership_digest",
            )
        }
        source["scope_kind"] = str(unit.get("scope_kind") or "whole_subject")
        source["observations"] = observations
        concurrent_fields = concurrent_correction_context_fields(source, match_path)
        unit.update(concurrent_fields)
        unit["temporal_split"] = temporal_split_context_for_source(match_path, source)
        if crops:
            render_cases.append({
                "temporal_evidence": {"anchor_crops": crops},
                "concurrent_resolution": concurrent_fields["concurrent_resolution"],
            })
    historical_started = time.perf_counter()
    repairs = _materialize_historical_split_repairs(
        match_path,
        units,
        observations_by_pair,
        render_cases,
    )
    historical_repair_ms = _elapsed_ms(historical_started)
    if render_cases:
        try:
            render_mixed_review_evidence(match_path, match_doc, {"cases": render_cases})
        except FileNotFoundError:
            # Matches retained without a local source video can still serve
            # their cached artifact references and normal image failure UI.
            pass
    return repairs, historical_repair_ms


def _materialize_historical_split_repairs(
    match_path: Path,
    units: list[dict[str, Any]],
    observations_by_pair: dict[tuple[str, int], dict[str, Any]],
    render_cases: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build correction-only contexts for unsafe resolved historical splits.

    These parents are deliberately absent from normal Review progress after
    their canonical children cover the exact source. The hot index gives those
    children one explicit, read-only route back to the original durable parent
    without requeueing it or storing its full observation ownership.
    """
    repairs: dict[str, dict[str, Any]] = {}
    for case in load_mixed_player_cases(match_path).get("cases") or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or "")
        source = case.get("source")
        if (
            not case_id
            or str(case.get("original_issue") or "") != "inline_temporal_split"
            or str(case.get("resolution_status") or "") != "resolved"
            or str(case.get("resolution_model") or "") == "concurrent_lanes"
            or not isinstance(source, dict)
        ):
            continue
        pairs = {
            (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
            for row in source.get("owned_observations") or []
            if isinstance(row, dict)
            and row.get("tracklet_id") is not None
            and row.get("frame") is not None
        }
        if not pairs or any(pair not in observations_by_pair for pair in pairs):
            continue
        observations = sorted(
            (observations_by_pair[pair] for pair in pairs),
            key=lambda row: (int(row["frame"]), str(row["tracklet_id"])),
        )
        exact_source = {
            key: source.get(key)
            for key in (
                "scope_kind",
                "candidate_subject_id",
                "review_target_id",
                "continuity_group_id",
                "source_ownership_digest",
            )
        }
        if (
            not str(exact_source.get("candidate_subject_id") or "")
            or not str(exact_source.get("source_ownership_digest") or "")
        ):
            continue
        exact_source["scope_kind"] = str(
            exact_source.get("scope_kind") or "whole_subject"
        )
        exact_source["observations"] = observations
        concurrent_fields = concurrent_correction_context_fields(
            exact_source,
            match_path,
        )
        if (
            concurrent_fields["temporal_topology"].get("kind") != "concurrent"
            or not concurrent_fields["historical_concurrent_repair"]
            or str((concurrent_fields["concurrent_resolution"] or {}).get("parent_case_id") or "") != case_id
        ):
            continue
        crops = temporal_evidence_for_observations(
            str(exact_source["candidate_subject_id"]),
            observations,
            limit=12,
        )
        source_team = str(source.get("source_team_label") or "U").upper()
        scope_kind = str(exact_source["scope_kind"])
        repair = {
            "candidate_subject_id": str(exact_source["candidate_subject_id"]),
            "review_target_id": None,
            "scope_kind": scope_kind,
            "team_label": source_team,
            "source_team_label": source_team,
            "effective_team_label": source_team,
            "available_team_labels": ["A", "B"] if source_team == "U" or scope_kind == "material_continuity" else [source_team],
            "tracklet_ids": sorted({str(row["tracklet_id"]) for row in observations}),
            "continuity_group_id": exact_source.get("continuity_group_id"),
            "review_card_key": None,
            "current_decision": None,
            "source_ownership_digest": str(exact_source["source_ownership_digest"]),
            "frame_ranges": [],
            "frame_start": min(int(row["frame"]) for row in observations),
            "frame_end": max(int(row["frame"]) for row in observations),
            "detected_observation_count": len(observations),
            "visual_evidence": {
                "kind": "identity_continuity",
                "status": "ready" if crops else "missing",
                "selected_crop_count": len(crops),
                "anchor_crops": crops,
            },
            "source_evidence_kind": "identity_continuity",
            "legacy_suggestion": None,
            "temporal_split": temporal_split_context_for_source(match_path, exact_source),
            **concurrent_fields,
        }
        repairs[case_id] = repair
        render_cases.append({
            "temporal_evidence": {"anchor_crops": crops},
            "concurrent_resolution": concurrent_fields["concurrent_resolution"],
        })

    for unit in units:
        if not isinstance(unit, dict):
            continue
        parent_case_id = str(unit.get("split_parent_case_id") or "")
        if parent_case_id in repairs:
            unit["historical_parent_repair"] = {
                "available": True,
                "case_id": parent_case_id,
            }
    return repairs


def _attach_temporal_split_context(match_path: Path, units: list[dict[str, Any]]) -> None:
    """Keep saved split state for compact legacy hot fixtures too.

    Normally this is attached alongside exact observations above.  This small
    no-scan pass retains the existing split read contract for units whose
    source video/tracklet fixture has no materialized observations.
    """
    for unit in units:
        if not isinstance(unit, dict):
            continue
        source = {
            key: unit.get(key)
            for key in (
                "scope_kind",
                "candidate_subject_id",
                "review_target_id",
                "continuity_group_id",
                "source_ownership_digest",
            )
        }
        source["scope_kind"] = str(unit.get("scope_kind") or "whole_subject")
        split = temporal_split_context_for_source(match_path, source)
        if split is not None:
            unit["temporal_split"] = split


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


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
