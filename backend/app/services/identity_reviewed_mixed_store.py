from __future__ import annotations

"""Durable mixed-player issue markers and operator-created review targets."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_canonical_io import load_json_cached_or
from app.services.identity_reviewed_effective_observation import is_real_detected_position
from app.services.play_area import is_on_pitch_product_observation
from app.services.identity_roster_anchor_crop_renderer import render_identity_roster_anchor_crops
from app.services.identity_reviewed_slot_registry import build_reviewed_slot_registry
from app.services.identity_reviewed_scope_eligibility import (
    mixed_review_relevant_for_scope,
)
from app.services.identity_reviewed_action_scope import (
    reviewed_identity_action_capabilities,
)
from app.services.identity_reviewed_mixed_topology import (
    analyze_temporal_split_topology,
    require_simple_temporal_split,
)
from app.services.identity_reviewed_concurrent_lanes import (
    derive_concurrent_lanes,
    expanded_concurrent_lane_segments,
    validate_concurrent_lane_resolutions,
)
from app.services.video import resolve_match_video_path


FILENAME = "reviewed_identity_mixed_players.json"
SCHEMA_VERSION = "2.0.0"
MIXED_HINTS = frozenset(
    {"cross_team", "same_team_a", "same_team_b", "player_referee", "unknown"}
)
UNRESOLVED_STATUSES = frozenset({"unresolved", "unresolved_complex_mix"})
MANDATORY_MIXED_CASE_STATUSES = frozenset(
    {"current_blocking", "stale_or_unclassifiable_blocking"}
)


def load_mixed_player_cases(match_path: Path) -> dict[str, Any]:
    path = match_path / FILENAME
    if not path.exists():
        return _document([])
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
        raise ValueError(f"{FILENAME} must contain a cases array")
    return value


def save_mixed_player_classification(
    match_path: Path,
    match_doc: dict[str, Any],
    subject_id: str,
    mixed_hint: str | None,
    comment: str | None,
    *,
    source: dict[str, Any] | None = None,
    case_id: str | None = None,
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hint = str(mixed_hint or "unknown").strip()
    if hint not in MIXED_HINTS:
        raise ValueError(f"Unsupported mixed_hint: {hint}")
    # A material-continuity source may not have a raw candidate subject at
    # all.  Its exact ownership is already resolved by the caller, so never
    # fall back to (and accidentally widen to) a subject lookup here.
    subject = _subject(match_path, subject_id) if source is None else None
    observations = (
        list(source.get("observations") or [])
        if isinstance(source, dict)
        else _subject_observations(match_path, subject or {})
    )
    if len(observations) < 2:
        raise ValueError("mixed_players requires at least two detected observations")
    document = load_mixed_player_cases(match_path)
    marker_id = str(case_id or subject_id)
    cases = [dict(row) for row in document.get("cases") or [] if isinstance(row, dict)]
    previous = next((row for row in cases if str(row.get("case_id") or row.get("candidate_subject_id") or "") == marker_id), {})
    now = datetime.now(timezone.utc).isoformat()
    case = {
        **previous,
        "candidate_subject_id": subject_id,
        **({"case_id": marker_id, "source": dict(source_payload or {})} if source_payload else {}),
        "original_issue": "mixed_players",
        "mixed_hint": hint,
        "resolution_status": "unresolved",
        "source_tracklet_ids": sorted({str(row["tracklet_id"]) for row in observations}),
        "source_subject_digest": str(source.get("source_ownership_digest") if isinstance(source, dict) else _subject_digest(subject or {}, observations)),
        "observation_count": len(observations),
        "frame_start": int(observations[0]["frame"]),
        "frame_end": int(observations[-1]["frame"]),
        "classified_at": previous.get("classified_at") or now,
        "updated_at": now,
        "comment": str(comment or "").strip() or None,
        "split_after_frames": [],
        "segment_target_ids": [],
    }
    cases = [row for row in cases if str(row.get("case_id") or row.get("candidate_subject_id") or "") != marker_id]
    cases.append(case)
    write_identity_json_atomic(match_path / FILENAME, _document(cases))
    return case


def save_mixed_case_document(match_path: Path, document: dict[str, Any]) -> None:
    write_identity_json_atomic(match_path / FILENAME, document)


def mixed_case_for_subject(match_path: Path, subject_id: str) -> dict[str, Any] | None:
    """Return only the legacy whole-subject marker for a correction card.

    Inline temporal splits also live in this file for durable provenance, but
    they must not turn the original whole-subject card into a legacy
    ``mixed_players`` decision. Their child targets are surfaced separately.
    """
    return next(
        (
            dict(row)
            for row in load_mixed_player_cases(match_path).get("cases") or []
            if str(row.get("candidate_subject_id") or "") == subject_id
            and str(row.get("original_issue") or "") == "mixed_players"
            and not isinstance(row.get("source"), dict)
        ),
        None,
    )


def inline_temporal_split_for_source(
    match_path: Path,
    source: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the durable inline split for one exact server-resolved source.

    The comparison deliberately includes the ownership digest.  A split is
    never resurrected after its underlying observation ownership changed.
    """
    wanted = {
        key: source.get(key)
        for key in (
            "scope_kind",
            "candidate_subject_id",
            "review_target_id",
            "continuity_group_id",
            "source_ownership_digest",
        )
    }
    for row in load_mixed_player_cases(match_path).get("cases") or []:
        if (
            str(row.get("original_issue") or "") != "inline_temporal_split"
            and str(row.get("resolution_model") or "") != "concurrent_lanes"
        ):
            continue
        stored = row.get("source")
        if not isinstance(stored, dict):
            continue
        if all(stored.get(key) == value for key, value in wanted.items()):
            return dict(row)
    return None


def staged_mixed_case_for_source(match_path: Path, source: dict[str, Any]) -> dict[str, Any] | None:
    """Find an unresolved staged marker for one exact source only."""
    wanted = {
        key: source.get(key)
        for key in (
            "scope_kind", "candidate_subject_id", "review_target_id",
            "continuity_group_id", "source_ownership_digest",
        )
    }
    return next(
        (
            dict(row)
            for row in load_mixed_player_cases(match_path).get("cases") or []
            if str(row.get("original_issue") or "") == "mixed_players"
            and isinstance(row.get("source"), dict)
            and all((row.get("source") or {}).get(key) == value for key, value in wanted.items())
        ),
        None,
    )


def resolved_material_continuity_observation_pairs(
    match_path: Path,
) -> set[tuple[str, int]]:
    """Return exact ownership retired by active resolved material splits.

    A temporal split has no one parent-level identity decision: its children
    are the authoritative resolution.  Material-continuity coalescing must
    therefore not recreate the same parent from those exact observations.
    Keep this deliberately narrow: unresolved complex mixes stay visible as
    blockers, and a stale/incomplete child set is never trusted to suppress a
    newly actionable continuity case.
    """
    from app.services.identity_reviewed_segments import load_segment_decisions

    decisions = {
        str(row.get("review_target_id") or ""): row
        for row in load_segment_decisions(match_path).get("decisions") or []
        if row.get("review_target_id")
    }
    active_pairs: set[tuple[str, int]] = set()
    for case in load_mixed_player_cases(match_path).get("cases") or []:
        source = case.get("source")
        if (
            str(case.get("original_issue") or "") != "inline_temporal_split"
            or str(case.get("resolution_status") or "") != "resolved"
            or not isinstance(source, dict)
            or str(source.get("scope_kind") or "") != "material_continuity"
        ):
            continue
        source_pairs = _owned_observation_pairs(source.get("owned_observations"))
        target_ids = {
            str(value)
            for value in case.get("segment_target_ids") or []
            if str(value)
        }
        if not source_pairs or not target_ids:
            continue
        targets = {
            str(row.get("review_target_id") or ""): row
            for row in operator_mixed_targets(match_path)
            if str(row.get("split_parent_case_id") or "")
            == str(case.get("case_id") or "")
        }
        if set(targets) != target_ids:
            continue
        child_pairs: set[tuple[str, int]] = set()
        valid = True
        for target_id in target_ids:
            target = targets[target_id]
            decision = decisions.get(target_id)
            if (
                not isinstance(decision, dict)
                or str(decision.get("source_ownership_digest") or "")
                != str(target.get("source_ownership_digest") or "")
            ):
                valid = False
                break
            child_pairs.update(_owned_observation_pairs(target.get("owned_observations")))
        if valid and child_pairs == source_pairs:
            active_pairs.update(source_pairs)
    return active_pairs


def mixed_case_summary(
    rows: list[dict[str, Any]],
    blocking_case_ids: set[str],
) -> dict[str, int]:
    unresolved_rows = [
        row for row in rows
        if str(row.get("resolution_status")) in UNRESOLVED_STATUSES
    ]
    unresolved_total = len(unresolved_rows)
    blocking = sum(
        str(row.get("case_id") or row.get("candidate_subject_id") or "")
        in blocking_case_ids
        for row in unresolved_rows
    )
    return {
        "total": len(rows),
        "unresolved": blocking,
        "unresolved_total": unresolved_total,
        "nonblocking_by_scope": unresolved_total - blocking,
        "resolved": sum(str(row.get("resolution_status")) == "resolved" for row in rows),
        "complex_unresolved": sum(
            str(row.get("resolution_status")) == "unresolved_complex_mix" for row in rows
        ),
    }


def build_mixed_review_queue(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    document = load_mixed_player_cases(match_path)
    subjects = {
        str(row.get("candidate_subject_id")): row
        for row in _load(match_path / "identity_candidate_shadow.json").get("subjects") or []
        if row.get("candidate_subject_id")
    }
    cards = {
        str(row.get("candidate_subject_id")): row
        for row in _load(match_path / "identity_roster_subject_review_shadow.json").get("cards") or []
        if row.get("candidate_subject_id")
    }
    cases = []
    blocking_case_ids: set[str] = set()
    for marker in document.get("cases") or []:
        marker_id = str(marker.get("case_id") or marker.get("candidate_subject_id") or "")
        subject_id = str(marker.get("candidate_subject_id") or "")
        materialized = _materialize_mixed_review_case(
            match_path,
            match_doc,
            marker,
            subject=subjects.get(subject_id),
            card=cards.get(subject_id),
        )
        if materialized["status"] in MANDATORY_MIXED_CASE_STATUSES:
            blocking_case_ids.add(marker_id)
            cases.append(materialized["case"])
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_mixed_queue",
        "match_id": str(match_doc.get("id") or match_path.name),
        "summary": mixed_case_summary(
            list(document.get("cases") or []),
            blocking_case_ids,
        ),
        "assignment_options": {
            "roster": _match_roster(match_doc),
            "slots": list(build_reviewed_slot_registry(match_path).values()),
        },
        "cases": sorted(cases, key=lambda row: (int(row.get("frame_start") or 0), str(row.get("case_id") or row.get("candidate_subject_id")))),
    }


def build_focused_mixed_review_case(
    match_path: Path,
    match_doc: dict[str, Any],
    case_id: str,
) -> dict[str, Any]:
    """Load and materialize one durable Mixed marker without building its peers."""
    wanted_case_id = str(case_id or "").strip()
    marker = next(
        (
            dict(row)
            for row in load_mixed_player_cases(match_path).get("cases") or []
            if str(row.get("case_id") or row.get("candidate_subject_id") or "")
            == wanted_case_id
        ),
        None,
    )
    if marker is None:
        status = "missing"
        case = None
    elif str(marker.get("resolution_status")) not in UNRESOLVED_STATUSES:
        status = "no_longer_unresolved"
        case = None
    else:
        subject_id = str(marker.get("candidate_subject_id") or "")
        subject = next(
            (
                row
                for row in _load(match_path / "identity_candidate_shadow.json").get("subjects") or []
                if str(row.get("candidate_subject_id") or "") == subject_id
            ),
            None,
        )
        card = next(
            (
                row
                for row in _load(
                    match_path / "identity_roster_subject_review_shadow.json"
                ).get("cards") or []
                if str(row.get("candidate_subject_id") or "") == subject_id
            ),
            None,
        )
        materialized = _materialize_mixed_review_case(
            match_path,
            match_doc,
            marker,
            subject=subject,
            card=card,
        )
        status = str(materialized["status"])
        case = materialized["case"]
    assignment_options = (
        {
            "roster": _match_roster(match_doc),
            "slots": list(build_reviewed_slot_registry(match_path).values()),
        }
        if status == "current_blocking"
        else {"roster": [], "slots": []}
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_mixed_focused_case",
        "match_id": str(match_doc.get("id") or match_path.name),
        "requested_case_id": wanted_case_id,
        "status": status,
        "case": case,
        "assignment_options": assignment_options,
    }


def _materialize_mixed_review_case(
    match_path: Path,
    match_doc: dict[str, Any],
    marker: dict[str, Any],
    *,
    subject: dict[str, Any] | None,
    card: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply one shared ownership/scope contract to queue and focused reads."""
    if str(marker.get("resolution_status")) not in UNRESOLVED_STATUSES:
        return {"status": "no_longer_unresolved", "case": None}
    observations = (
        _observations_for_marker(match_path, marker, subject)
        if subject is not None or isinstance(marker.get("source"), dict)
        else []
    )
    if not observations or not _mixed_marker_ownership_is_current(
        marker,
        subject,
        observations,
    ):
        # Exact ownership deliberately fails closed. Never reinterpret an
        # incomplete V2 marker as a whole-subject case or suppress the blocker.
        return {
            "status": "stale_or_unclassifiable_blocking",
            "case": _stale_blocking_case(marker),
        }
    if not mixed_review_relevant_for_scope(marker, observations, match_doc):
        return {"status": "not_in_mandatory_queue", "case": None}
    subject_id = str(marker.get("candidate_subject_id") or "")
    temporal_topology = analyze_temporal_split_topology(observations)
    crops = _temporal_evidence(subject_id, observations, card, limit=12)
    action_capabilities = _mixed_action_capabilities(marker, observations, card)
    concurrent_resolution = (
        materialize_concurrent_resolution(
            subject_id,
            str(marker.get("case_id") or subject_id),
            str(marker.get("source_subject_digest") or ""),
            observations,
            marker,
        )
        if temporal_topology["kind"] == "concurrent"
        else None
    )
    complex_unresolved = (
        str(marker.get("resolution_status")) == "unresolved_complex_mix"
    )
    return {
        "status": "current_blocking",
        "case": {
            **marker,
            "blocking": True,
            "scope_status": "blocking",
            "reviewed_complex": complex_unresolved,
            "reviewed_complex_at": marker.get("updated_at")
            if complex_unresolved
            else None,
            "temporal_topology": temporal_topology,
            "concurrent_resolution": concurrent_resolution,
            "action_capabilities": action_capabilities,
            "temporal_evidence": {
                "status": "ready" if crops else "missing",
                "anchor_crops": crops,
            },
        },
    }


def _mixed_marker_ownership_is_current(
    marker: dict[str, Any],
    subject: dict[str, Any] | None,
    observations: list[dict[str, Any]],
) -> bool:
    source = marker.get("source")
    if isinstance(source, dict):
        digest = str(source.get("source_ownership_digest") or "")
        scope_kind = str(source.get("scope_kind") or "")
        if (
            not digest
            or str(marker.get("candidate_subject_id") or "")
            != str(source.get("candidate_subject_id") or "")
        ):
            return False
        if scope_kind and scope_kind not in {
            "whole_subject",
            "canonical_segment",
            "material_continuity",
        }:
            return False
        if scope_kind and str(marker.get("source_subject_digest") or "") != digest:
            return False
        if scope_kind == "canonical_segment" and not source.get("review_target_id"):
            return False
        if scope_kind == "material_continuity" and not source.get("continuity_group_id"):
            return False
        wanted = _owned_observation_pairs(source.get("owned_observations"))
        found = {
            (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
            for row in observations
        }
        if not wanted or found != wanted:
            return False
        # Older exact V2 markers predate scope/bounds aliases but still own a
        # complete server-generated pair set. Never widen them to a subject;
        # exact pair equality above is their authoritative compatibility gate.
        return True
    if subject is None or str(marker.get("source_subject_digest") or "") != _subject_digest(
        subject,
        observations,
    ):
        return False
    observed_tracklets = sorted({str(row["tracklet_id"]) for row in observations})
    observed_frames = [int(row["frame"]) for row in observations]
    return (
        sorted(str(value) for value in marker.get("source_tracklet_ids") or [])
        == observed_tracklets
        and int(marker.get("observation_count") or 0) == len(observations)
        and marker.get("frame_start") is not None
        and int(marker["frame_start"]) == min(observed_frames)
        and marker.get("frame_end") is not None
        and int(marker["frame_end"]) == max(observed_frames)
    )


def _stale_blocking_case(marker: dict[str, Any]) -> dict[str, Any]:
    complex_unresolved = (
        str(marker.get("resolution_status")) == "unresolved_complex_mix"
    )
    return {
        **marker,
        "blocking": True,
        "scope_status": "stale_or_unclassifiable_blocking",
        "reviewed_complex": complex_unresolved,
        "reviewed_complex_at": marker.get("updated_at")
        if complex_unresolved
        else None,
        "temporal_topology": None,
        "concurrent_resolution": None,
        "temporal_evidence": {"status": "missing", "anchor_crops": []},
    }


def _mixed_action_capabilities(
    marker: dict[str, Any],
    observations: list[dict[str, Any]],
    card: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Expose the same server-owned action gate used by concurrent saves."""
    source = marker.get("source")
    scope_unit = {
        "scope_kind": (
            str(source.get("scope_kind") or "")
            if isinstance(source, dict)
            else "whole_subject"
        ) or "whole_subject",
        "detected_observation_count": len(observations),
    }
    if isinstance(card, dict) and card.get("priority") is not None:
        scope_unit["priority"] = card["priority"]
    return reviewed_identity_action_capabilities(scope_unit)


def build_mixed_boundary_refinement(
    match_path: Path,
    match_doc: dict[str, Any],
    subject_id: str,
    after_frame: int,
    before_frame: int,
    *,
    case_id: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    marker_id = str(case_id or subject_id)
    case = next(
        (
            dict(row)
            for row in load_mixed_player_cases(match_path).get("cases") or []
            if str(row.get("case_id") or row.get("candidate_subject_id") or "") == marker_id
        ),
        None,
    )
    if case is None or str(case.get("resolution_status")) not in UNRESOLVED_STATUSES:
        raise ValueError(f"Unknown unresolved mixed-player case: {subject_id or '<missing>'}")
    if after_frame >= before_frame:
        raise ValueError("Refinement interval must have increasing frame boundaries")
    source = case.get("source") if isinstance(case.get("source"), dict) else None
    if source and str(case.get("source_subject_digest") or "") != str(source.get("source_ownership_digest") or ""):
        raise ValueError("mixed_player_case_stale")
    if not source and str(case.get("source_subject_digest") or "") != current_mixed_subject_digest(match_path, subject_id):
        raise ValueError("mixed_player_case_stale")

    subject = _subject(match_path, subject_id) if source is None else {}
    if source:
        # Stored sources intentionally persist pairs/digest, not mutable raw
        # detections. Re-resolve those exact pairs for refinement evidence.
        from app.services.identity_reviewed_review_source import resolve_review_source

        resolved_source = resolve_review_source(
            match_path,
            match_doc,
            candidate_subject_id=str(source.get("candidate_subject_id") or subject_id),
            review_target_id=str(source.get("review_target_id") or "") or None,
            continuity_group_id=str(source.get("continuity_group_id") or "") or None,
            source_ownership_digest=str(source.get("source_ownership_digest") or ""),
        )
        observations = list(resolved_source.get("observations") or [])
    else:
        observations = _subject_observations(match_path, subject)
    require_simple_temporal_split(observations)
    card = next(
        (
            row
            for row in _load(match_path / "identity_roster_subject_review_shadow.json").get("cards") or []
            if str(row.get("candidate_subject_id") or "") == subject_id
        ),
        None,
    )
    overview = _temporal_evidence(subject_id, observations, card, limit=12)
    overview_frames = [int(crop["frame"]) for crop in overview]
    if (after_frame, before_frame) not in set(zip(overview_frames, overview_frames[1:])):
        raise ValueError("Refinement interval must use neighboring overview samples")
    interval = [row for row in observations if after_frame < int(row["frame"]) <= before_frame]
    if not interval:
        raise ValueError("No detected observations in the selected refinement interval")
    crops = _temporal_evidence(subject_id, interval, card, limit=max(3, min(limit, 16)))
    boundary_crops = refinement_boundary_crops(overview, after_frame, before_frame)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_mixed_boundary_refinement",
        "match_id": str(match_doc.get("id") or match_path.name),
        "candidate_subject_id": subject_id,
        "case_id": str(case.get("case_id") or subject_id),
        "source_subject_digest": case.get("source_subject_digest"),
        "after_frame": after_frame,
        "before_frame": before_frame,
        "boundary_crops": boundary_crops,
        "anchor_crops": crops,
    }
    if source:
        payload.update({
            "review_target_id": source.get("review_target_id"),
            "continuity_group_id": source.get("continuity_group_id"),
        })
    render_mixed_review_evidence(
        match_path,
        match_doc,
        {"cases": [{"temporal_evidence": {"anchor_crops": crops}}]},
    )
    return payload


def render_mixed_review_evidence(
    match_path: Path,
    match_doc: dict[str, Any],
    queue: dict[str, Any],
) -> set[str]:
    crops = [
        crop
        for case in queue.get("cases") or []
        for crop in [
            *((case.get("temporal_evidence") or {}).get("anchor_crops") or []),
            *(
                crop
                for lane in (case.get("concurrent_resolution") or {}).get("lanes") or []
                for crop in (lane.get("evidence") or {}).get("anchor_crops") or []
            ),
        ]
        if crop.get("generated_for_segment_review")
        and crop.get("artifact")
        and not (match_path / str(crop["artifact"])).exists()
    ]
    if not crops:
        return set()
    video = resolve_match_video_path(
        match_path,
        str(match_doc.get("video_filename") or "") or None,
    )
    return render_identity_roster_anchor_crops(
        video,
        match_path,
        {"cards": [{"anchor_crops": crops}]},
    )


def temporal_evidence_for_observations(
    subject_id: str,
    observations: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Shared evidence selection for legacy and inline temporal split UI."""
    return _temporal_evidence(subject_id, observations, None, limit=limit)


def refinement_boundary_crops(
    overview: list[dict[str, Any]],
    after_frame: int,
    before_frame: int,
) -> dict[str, dict[str, Any]]:
    """Return the exact two overview observations that define a refinement.

    The dense samples inside a refinement interval are useful for choosing a
    boundary, but their rounded timestamps must never be mistaken for the two
    authoritative endpoint observations selected in the overview.
    """
    by_frame = {int(crop["frame"]): crop for crop in overview}
    after_crop = by_frame.get(after_frame)
    before_crop = by_frame.get(before_frame)
    if after_crop is None or before_crop is None:
        raise ValueError("Refinement interval must use neighboring overview samples")
    return {"after": dict(after_crop), "before": dict(before_crop)}


def materialize_concurrent_resolution(
    subject_id: str,
    parent_case_id: str,
    parent_source_digest: str,
    observations: list[dict[str, Any]],
    marker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the bounded operator read model from exact server-owned lanes."""
    _topology, lanes = derive_concurrent_lanes(
        parent_case_id,
        parent_source_digest,
        observations,
    )
    stored = {
        str(row.get("lane_id") or ""): dict(row)
        for row in (marker or {}).get("lane_resolutions") or []
        if isinstance(row, dict)
    }
    public_lanes = []
    for lane in lanes:
        current = stored.get(str(lane["lane_id"]))
        if current and str(current.get("lane_source_digest") or "") != str(
            lane["source_ownership_digest"]
        ):
            current = None
        crops = _temporal_evidence(
            subject_id,
            list(lane["observations"]),
            None,
            limit=5,
        )
        public_lanes.append(
            {
                key: lane[key]
                for key in (
                    "lane_id",
                    "tracklet_id",
                    "source_ownership_digest",
                    "frame_start",
                    "frame_end",
                    "observation_count",
                    "split_allowed",
                    "overlap_lane_ids",
                )
            }
            | {
                "evidence": {
                    "status": "ready" if crops else "missing",
                    "anchor_crops": crops,
                },
                "current_resolution": _public_lane_resolution(current),
            }
        )
    return {
        "status": str((marker or {}).get("resolution_status") or "unresolved"),
        "parent_case_id": parent_case_id,
        "parent_source_digest": parent_source_digest,
        "resolution_semantic_digest": (marker or {}).get(
            "resolution_semantic_digest"
        ),
        "lanes": public_lanes,
    }


def operator_mixed_targets(
    match_path: Path,
    cases_document: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    document = cases_document or load_mixed_player_cases(match_path)
    subjects = {
        str(row.get("candidate_subject_id")): row
        for row in _load(match_path / "identity_candidate_shadow.json").get("subjects") or []
        if row.get("candidate_subject_id")
    }
    cards = {
        str(row.get("candidate_subject_id")): row
        for row in _load(match_path / "identity_roster_subject_review_shadow.json").get("cards") or []
        if row.get("candidate_subject_id")
    }
    targets: list[dict[str, Any]] = []
    for marker in document.get("cases") or []:
        if str(marker.get("resolution_model") or "") == "concurrent_lanes":
            targets.extend(_operator_concurrent_lane_targets(match_path, marker, cards))
            continue
        split_frames = sorted({int(value) for value in marker.get("split_after_frames") or []})
        if not split_frames:
            continue
        subject_id = str(marker.get("candidate_subject_id") or "")
        subject = subjects.get(subject_id)
        if not subject and not isinstance(marker.get("source"), dict):
            continue
        observations = _observations_for_marker(match_path, marker, subject)
        if not observations:
            continue
        groups = _split_observations(observations, split_frames)
        for index, group in enumerate(groups):
            teams = {str(row.get("team_label") or "U") for row in group}
            source_team = next(iter(teams)) if len(teams) == 1 else "U"
            ownership_payload = [
                {"tracklet_id": row["tracklet_id"], "frame": row["frame"]}
                for row in group
            ]
            digest = canonical_digest(
                {
                    "source_case_id": marker.get("case_id") or subject_id,
                    "source_subject_digest": marker.get("source_subject_digest"),
                    "segment_index": index,
                    "owned_observations": ownership_payload,
                }
            )
            target_id = f"review-mixed-segment:v1:{digest}"
            frames = sorted({int(row["frame"]) for row in group})
            crops = _temporal_evidence(subject_id, group, cards.get(subject_id), limit=5)
            targets.append(
                {
                    "review_target_id": target_id,
                    "scope_kind": "canonical_segment",
                    "target_origin": (
                        "operator_temporal_split"
                        if isinstance(marker.get("source"), dict)
                        else "operator_mixed_players"
                    ),
                    "candidate_subject_id": subject_id,
                    "tracklet_ids": sorted({str(row["tracklet_id"]) for row in group}),
                    "stable_slot_id": None,
                    "source_team_label": source_team,
                    "effective_team_label": source_team,
                    "frame_start": frames[0],
                    "frame_end": frames[-1],
                    "frame_ranges": _exact_ranges(frames),
                    "owned_frames": frames,
                    "owned_observations": ownership_payload,
                    "detected_observation_count": len(group),
                    "source_ownership_digest": digest,
                    "reason_codes": ["operator_temporal_split"],
                    "visual_evidence": {
                        "status": "ready" if crops else "missing",
                        "selected_crop_count": len(crops),
                        "anchor_crops": crops,
                    },
                    "current_decision": None,
                    "decision_status": "pending",
                    "stale_decision": False,
                    "legacy_suggestion": None,
                    "mixed_segment_index": index,
                    "split_parent_case_id": marker.get("case_id") or subject_id,
                }
            )
    return targets


def _operator_concurrent_lane_targets(
    match_path: Path,
    marker: dict[str, Any],
    cards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    subject_id = str(marker.get("candidate_subject_id") or "")
    observations = observations_for_case(match_path, marker)
    if not observations:
        return []
    try:
        _topology, lanes = derive_concurrent_lanes(
            str(marker.get("case_id") or subject_id),
            str(marker.get("source_subject_digest") or ""),
            observations,
        )
        resolutions = validate_concurrent_lane_resolutions(
            lanes,
            list(marker.get("lane_resolutions") or []),
        )
    except (ValueError, TypeError):
        return []

    targets: list[dict[str, Any]] = []
    for expanded in expanded_concurrent_lane_segments(lanes, resolutions):
        lane = expanded["lane"]
        group = expanded["observations"]
        ownership = [
            {"tracklet_id": str(row["tracklet_id"]), "frame": int(row["frame"])}
            for row in group
        ]
        digest = canonical_digest(
            {
                "source_case_id": marker.get("case_id") or subject_id,
                "lane_id": lane["lane_id"],
                "lane_source_digest": lane["source_ownership_digest"],
                "segment_index": expanded["segment_index"],
                "owned_observations": ownership,
            }
        )
        frames = [int(row["frame"]) for row in group]
        teams = {str(row.get("team_label") or "U") for row in group}
        source_team = next(iter(teams)) if len(teams) == 1 else "U"
        crops = _temporal_evidence(subject_id, group, cards.get(subject_id), limit=5)
        targets.append(
            {
                "review_target_id": f"review-mixed-lane-segment:v1:{digest}",
                "scope_kind": "canonical_segment",
                "target_origin": (
                    "operator_concurrent_lane"
                    if len(frames) == int(lane["observation_count"])
                    else "operator_concurrent_lane_split"
                ),
                "candidate_subject_id": subject_id,
                "tracklet_ids": [str(lane["tracklet_id"])],
                "stable_slot_id": None,
                "source_team_label": source_team,
                "effective_team_label": source_team,
                "frame_start": min(frames),
                "frame_end": max(frames),
                "frame_ranges": _exact_ranges(sorted(set(frames))),
                "owned_frames": sorted(set(frames)),
                "owned_observations": ownership,
                "detected_observation_count": len(group),
                "source_ownership_digest": digest,
                "reason_codes": ["operator_concurrent_lane_resolution"],
                "visual_evidence": {
                    "status": "ready" if crops else "missing",
                    "selected_crop_count": len(crops),
                    "anchor_crops": crops,
                },
                "current_decision": None,
                "decision_status": "pending",
                "stale_decision": False,
                "legacy_suggestion": None,
                "mixed_lane_id": lane["lane_id"],
                "mixed_lane_index": expanded["lane_index"],
                "mixed_lane_segment_index": expanded["segment_index"],
                "split_parent_case_id": marker.get("case_id") or subject_id,
            }
        )
    return targets


def _public_lane_resolution(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in (
            "lane_id",
            "lane_source_digest",
            "resolution",
            "assignment",
            "split_after_frames",
            "segment_assignments",
        )
        if value.get(key) is not None
    }


def validate_split_frames(observations: list[dict[str, Any]], split_frames: list[int]) -> None:
    frames = sorted({int(row["frame"]) for row in observations})
    if not frames:
        raise ValueError("Mixed subject has no detected observations")
    normalized = sorted({int(value) for value in split_frames})
    if not normalized:
        raise ValueError("Add at least one split point")
    if normalized[0] < frames[0] or normalized[-1] >= frames[-1]:
        raise ValueError("Split point must be inside the subject observation range")
    groups = _split_observations(observations, normalized)
    if len(groups) != len(normalized) + 1 or any(not group for group in groups):
        raise ValueError("Every split segment must contain detected observations")


def observations_for_case(match_path: Path, case: dict[str, Any]) -> list[dict[str, Any]]:
    marker_source = case.get("source")
    if isinstance(marker_source, dict) and _owned_observation_pairs(
        marker_source.get("owned_observations")
    ):
        # V2 exact-source cases carry their own observation ownership. The
        # candidate id may be a material-continuity group that never exists in
        # identity_candidate_shadow.json, so only legacy markers require it.
        subject = None
    else:
        subject = _subject(match_path, str(case.get("candidate_subject_id") or ""))
    return _observations_for_marker(match_path, case, subject)


def _observations_for_marker(
    match_path: Path,
    marker: dict[str, Any],
    subject: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Read legacy whole-subject ownership or v2 exact parent ownership."""
    source = marker.get("source")
    if not isinstance(source, dict):
        if subject is None:
            return []
        return _subject_observations(match_path, subject)
    wanted = {
        (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
        for row in source.get("owned_observations") or []
        if isinstance(row, dict) and row.get("tracklet_id") is not None and row.get("frame") is not None
    }
    if not wanted:
        return []
    all_rows = _subject_observations_from_pairs(match_path, wanted)
    found = {(str(row["tracklet_id"]), int(row["frame"])) for row in all_rows}
    return all_rows if found == wanted else []


def _owned_observation_pairs(rows: Any) -> set[tuple[str, int]]:
    return {
        (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
        for row in rows or []
        if isinstance(row, dict)
        and row.get("tracklet_id") is not None
        and row.get("frame") is not None
    }


def _subject_observations_from_pairs(
    match_path: Path,
    wanted: set[tuple[str, int]],
) -> list[dict[str, Any]]:
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in _load(match_path / "tracklets.json").get("tracklets") or []
    }
    rows: list[dict[str, Any]] = []
    for tracklet_id, tracklet in tracklets.items():
        for position in tracklet.get("positions_m") or []:
            if not is_real_detected_position(position) or not is_on_pitch_product_observation(position):
                continue
            pair = (tracklet_id, int(position.get("frame") or 0))
            if pair in wanted:
                rows.append({
                    **position,
                    "frame": pair[1],
                    "tracklet_id": tracklet_id,
                    "team_label": str(tracklet.get("team_label") or "U"),
                })
    return sorted(rows, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))


def current_mixed_subject_digest(match_path: Path, subject_id: str) -> str:
    subject = _subject(match_path, subject_id)
    return _subject_digest(subject, _subject_observations(match_path, subject))


def unresolved_mixed_observation_assignments(
    match_path: Path,
    match_doc: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for case in load_mixed_player_cases(match_path).get("cases") or []:
        if str(case.get("resolution_status")) not in UNRESOLVED_STATUSES:
            continue
        observations = observations_for_case(match_path, case)
        if match_doc is not None and not mixed_review_relevant_for_scope(
            case, observations, match_doc,
        ):
            continue
        for observation in observations:
            team = str(observation.get("team_label") or "U")
            output.append(
                {
                    "review_target_id": None,
                    "candidate_subject_id": case.get("candidate_subject_id"),
                    "tracklet_id": observation["tracklet_id"],
                    "frame": observation["frame"],
                    "identity_source": "manual_mixed_player_review",
                    "stable_anonymous_slot_id": None,
                    "stable_anonymous_entity_id": None,
                    "team_label": team,
                    "fallback_label": f"{team}?",
                    "display_label": f"{team}?",
                    "identity_status": "unresolved",
                    "canonical_player_id": None,
                    "player_name": None,
                    "hard_blockers": ["mixed_players_unresolved"],
                    "conflicts": ["mixed_players_unresolved"],
                    "eligible_for_player_stats": False,
                }
            )
    return sorted(output, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))


def _subject(match_path: Path, subject_id: str) -> dict[str, Any]:
    subject = next(
        (
            row
            for row in _load(match_path / "identity_candidate_shadow.json").get("subjects") or []
            if str(row.get("candidate_subject_id") or "") == subject_id
        ),
        None,
    )
    if subject is None:
        raise ValueError(f"Unknown candidate_subject_id: {subject_id or '<missing>'}")
    return subject


def _subject_observations(match_path: Path, subject: dict[str, Any]) -> list[dict[str, Any]]:
    tracklet_ids = {str(value) for value in subject.get("tracklet_ids") or []}
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in _load(match_path / "tracklets.json").get("tracklets") or []
        if str(row.get("tracklet_id") or "") in tracklet_ids
    }
    rows: list[dict[str, Any]] = []
    for tracklet_id, tracklet in tracklets.items():
        for position in tracklet.get("positions_m") or []:
            if not is_real_detected_position(position) or not is_on_pitch_product_observation(position):
                continue
            rows.append(
                {
                    **position,
                    "frame": int(position.get("frame") or 0),
                    "tracklet_id": tracklet_id,
                    "team_label": str(tracklet.get("team_label") or "U"),
                }
            )
    return sorted(rows, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))


def _subject_digest(subject: dict[str, Any], observations: list[dict[str, Any]]) -> str:
    return canonical_digest(
        {
            "candidate_subject_id": subject.get("candidate_subject_id"),
            "tracklet_ids": sorted(str(value) for value in subject.get("tracklet_ids") or []),
            "observations": [
                {"tracklet_id": row["tracklet_id"], "frame": row["frame"]}
                for row in observations
            ],
        }
    )


def _split_observations(
    observations: list[dict[str, Any]], split_frames: list[int]
) -> list[list[dict[str, Any]]]:
    groups = [[] for _ in range(len(split_frames) + 1)]
    for observation in observations:
        frame = int(observation["frame"])
        index = sum(frame > boundary for boundary in split_frames)
        groups[index].append(observation)
    return groups


def _temporal_evidence(
    subject_id: str,
    observations: list[dict[str, Any]],
    card: dict[str, Any] | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    existing = {
        (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0)): dict(row)
        for row in ((card or {}).get("visual_evidence") or {}).get("anchor_crops") or []
    }
    topology = analyze_temporal_split_topology(observations)
    selected = (
        _representative_values(observations, limit)
        if topology["simple_split_allowed"]
        else _representative_temporal_lane_values(observations, topology, limit)
    )
    crops = []
    safe_subject = canonical_digest(subject_id)[:16]
    for index, row in enumerate(selected, start=1):
        key = (str(row["tracklet_id"]), int(row["frame"]))
        crop = existing.get(key)
        if crop is None:
            bbox = row.get("bbox_xyxy")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            crop = {
                "anchor_crop_id": f"mixed-crop:{safe_subject}:{row['tracklet_id']}:{row['frame']}",
                "artifact": f"reviewed_identity_mixed/{safe_subject}/{index:02d}_f{int(row['frame']):06d}.jpg",
                "frame": int(row["frame"]),
                "time_sec": row.get("time_sec"),
                "tracklet_id": row["tracklet_id"],
                "bbox_xyxy": bbox,
                "generated_for_segment_review": True,
            }
        crops.append({**crop, "team_label": row.get("team_label") or "U"})
    return sorted(crops, key=lambda row: (int(row.get("frame") or 0), str(row.get("tracklet_id") or "")))


def _representative_values(values: list[Any], limit: int) -> list[Any]:
    if len(values) <= limit:
        return values
    indexes = {round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)}
    return [values[index] for index in sorted(indexes)]


def _representative_temporal_lane_values(
    observations: list[dict[str, Any]],
    topology: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep concurrent evidence tracklet-aware within the existing crop budget."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        groups.setdefault(str(observation["tracklet_id"]), []).append(observation)
    for values in groups.values():
        values.sort(key=lambda row: int(row["frame"]))

    overlapping_ids = {
        str(tracklet_id)
        for overlap in topology.get("overlap_ranges") or []
        for tracklet_id in overlap.get("tracklet_ids") or []
    }
    ranked_tracklets = sorted(
        topology.get("tracklets") or [],
        key=lambda row: (
            0 if str(row["tracklet_id"]) in overlapping_ids else 1,
            -int(row["observation_count"]),
            int(row["frame_start"]),
            str(row["tracklet_id"]),
        ),
    )[:limit]
    lane_ids = [str(row["tracklet_id"]) for row in ranked_tracklets]

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, int]] = set()

    def add(row: dict[str, Any]) -> None:
        key = (str(row["tracklet_id"]), int(row["frame"]))
        if key not in selected_keys and len(selected) < limit:
            selected_keys.add(key)
            selected.append(row)

    for tracklet_id in lane_ids:
        values = groups[tracklet_id]
        add(values[len(values) // 2])

    for sample_count in (2, 3, 5):
        for tracklet_id in lane_ids:
            for row in _representative_values(
                groups[tracklet_id],
                min(sample_count, len(groups[tracklet_id])),
            ):
                add(row)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for row in _representative_values(observations, limit):
            add(row)
    return sorted(
        selected,
        key=lambda row: (int(row["frame"]), str(row["tracklet_id"])),
    )


def _exact_ranges(frames: list[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for frame in frames:
        if not ranges or frame > ranges[-1][1] + 1:
            ranges.append([frame, frame])
        else:
            ranges[-1][1] = frame
    return ranges


def _document(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_mixed_players",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cases": sorted(cases, key=lambda row: str(row.get("candidate_subject_id") or "")),
        "safety": {"mutates_raw_tracklets": False, "reruns_yolo": False},
    }


def _match_roster(match_doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "player_id": str(player["id"]),
            "player_name": str(player.get("name") or player["id"]),
            "roster_number": player.get("number"),
            "team_label": str(team.get("team_label") or chr(ord("A") + index)),
        }
        for index, team in enumerate(match_doc.get("teams") or [])
        for player in team.get("players") or []
        if player.get("id")
    ]


def _load(path: Path) -> dict[str, Any]:
    """Parse once per review-build scope; tracklets.json dominates cold builds."""
    value = load_json_cached_or(path, {})
    return value if isinstance(value, dict) else {}
