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
    match_roster,
    reviewed_decisions_semantic_digest,
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
    render_mixed_review_evidence,
    temporal_evidence_for_observations,
)
from app.services.play_area import is_on_pitch_product_observation


FILENAME = "reviewed_identity_hot_state.json"
REVISION_FILENAME = "reviewed_identity_hot_state_revision.json"
SCHEMA_VERSION = "2.0.0"


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
    if prebuilt_progress is not None:
        # Authoritative caller already materialized progress in this request;
        # reuse it instead of a second full canonical pass.
        progress = dict(prebuilt_progress)
    else:
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
    # Canonical segments intentionally retain the legacy segment registry
    # semantics. Whole subjects and material continuity use the materialized
    # registry because their source can cross automatic subject boundaries.
    canonical_segment_registry = build_reviewed_slot_registry(
        match_path,
        load_reviewed_slot_assignments(match_path),
    )
    internal = list(progress.pop("_internal_review_units", []) or [])
    projection_inputs = dict(progress.pop("_projection_inputs", {}) or {})
    _attach_exact_whole_subject_digests(match_path, internal)
    _attach_legacy_context_fields(match_path, internal)
    _attach_correction_temporal_evidence(match_path, match_doc, internal)
    _attach_temporal_split_context(match_path, internal)
    state = {
        "schema_version": SCHEMA_VERSION,
        "state_version": _next_state_version(match_path),
        "match_id": str(match_doc.get("id") or match_path.name),
        "progress": progress,
        "internal_review_units": internal,
        "unit_lookup": _unit_lookup(internal),
        "source_index": _source_index(internal),
        "projection_inputs": projection_inputs,
        "roster_options": match_roster(match_doc),
        "slot_options": [registry[key] for key in sorted(registry)],
        "canonical_segment_slot_options": [
            canonical_segment_registry[key]
            for key in sorted(canonical_segment_registry)
        ],
        "freshness": _freshness(match_path, match_doc),
    }
    write_identity_json_atomic(match_path / FILENAME, _encode_for_write(state), compact=True)
    state["progress"] = _json_safe(progress)
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
    decision = dict(saved_decision or {})
    roster_teams = {
        str(row.get("player_id") or ""): str(row.get("team_label") or "U").upper()
        for row in state.get("roster_options") or []
        if isinstance(row, dict)
    }
    # Compact units stay compact through the whole save. Projection evaluates
    # ownership from validated runs directly, so an ordinary correction never
    # materializes match-wide (tracklet_id, frame) pairs.
    updated_unit = False
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
        updated_unit = True
        break
    if not updated_unit:
        raise ReviewedIdentityHotStateError("review_queue_stale")
    projected = project_reviewed_identity_progress(
        list(state.get("internal_review_units") or []),
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
) -> None:
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
        if crops:
            render_cases.append({"temporal_evidence": {"anchor_crops": crops}})
    if render_cases:
        try:
            render_mixed_review_evidence(match_path, match_doc, {"cases": render_cases})
        except FileNotFoundError:
            # Matches retained without a local source video can still serve
            # their cached artifact references and normal image failure UI.
            pass


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
