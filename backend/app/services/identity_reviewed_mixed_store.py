from __future__ import annotations

"""Durable mixed-player issue markers and operator-created review targets."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_effective_observation import is_real_detected_position
from app.services.play_area import is_on_pitch_product_observation
from app.services.identity_roster_anchor_crop_renderer import render_identity_roster_anchor_crops
from app.services.identity_reviewed_slot_registry import build_reviewed_slot_registry
from app.services.video import resolve_match_video_path


FILENAME = "reviewed_identity_mixed_players.json"
SCHEMA_VERSION = "2.0.0"
MIXED_HINTS = frozenset(
    {"cross_team", "same_team_a", "same_team_b", "player_referee", "unknown"}
)
UNRESOLVED_STATUSES = frozenset({"unresolved", "unresolved_complex_mix"})


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
        if str(row.get("original_issue") or "") != "inline_temporal_split":
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


def mixed_case_summary(match_path: Path) -> dict[str, int]:
    rows = load_mixed_player_cases(match_path).get("cases") or []
    unresolved = sum(str(row.get("resolution_status")) in UNRESOLVED_STATUSES for row in rows)
    return {
        "total": len(rows),
        "unresolved": unresolved,
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
    for marker in document.get("cases") or []:
        if str(marker.get("resolution_status")) not in UNRESOLVED_STATUSES:
            continue
        subject_id = str(marker.get("candidate_subject_id") or "")
        subject = subjects.get(subject_id)
        if not subject and not isinstance(marker.get("source"), dict):
            continue
        observations = _observations_for_marker(match_path, marker, subject)
        if not observations:
            continue
        crops = _temporal_evidence(subject_id, observations, cards.get(subject_id), limit=12)
        cases.append(
            {
                **marker,
                "reviewed_complex": str(marker.get("resolution_status")) == "unresolved_complex_mix",
                "reviewed_complex_at": marker.get("updated_at")
                if str(marker.get("resolution_status")) == "unresolved_complex_mix"
                else None,
                "temporal_evidence": {
                    "status": "ready" if crops else "missing",
                    "anchor_crops": crops,
                },
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_mixed_queue",
        "match_id": str(match_doc.get("id") or match_path.name),
        "summary": mixed_case_summary(match_path),
        "assignment_options": {
            "roster": _match_roster(match_doc),
            "slots": list(build_reviewed_slot_registry(match_path).values()),
        },
        "cases": sorted(cases, key=lambda row: (int(row.get("frame_start") or 0), str(row.get("case_id") or row.get("candidate_subject_id")))),
    }


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
    card = next(
        (
            row
            for row in _load(match_path / "identity_roster_subject_review_shadow.json").get("cards") or []
            if str(row.get("candidate_subject_id") or "") == subject_id
        ),
        None,
    )
    overview_frames = [
        int(crop["frame"])
        for crop in _temporal_evidence(subject_id, observations, card, limit=12)
    ]
    if (after_frame, before_frame) not in set(zip(overview_frames, overview_frames[1:])):
        raise ValueError("Refinement interval must use neighboring overview samples")
    interval = [row for row in observations if after_frame < int(row["frame"]) <= before_frame]
    if not interval:
        raise ValueError("No detected observations in the selected refinement interval")
    crops = _temporal_evidence(subject_id, interval, card, limit=max(3, min(limit, 16)))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_mixed_boundary_refinement",
        "match_id": str(match_doc.get("id") or match_path.name),
        "candidate_subject_id": subject_id,
        "case_id": str(case.get("case_id") or subject_id),
        "source_subject_digest": case.get("source_subject_digest"),
        "after_frame": after_frame,
        "before_frame": before_frame,
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
        for crop in (case.get("temporal_evidence") or {}).get("anchor_crops") or []
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


def unresolved_mixed_observation_assignments(match_path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for case in load_mixed_player_cases(match_path).get("cases") or []:
        if str(case.get("resolution_status")) not in UNRESOLVED_STATUSES:
            continue
        for observation in observations_for_case(match_path, case):
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
    selected = _representative_values(observations, limit)
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
    if not path.exists():
        return {}
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}
