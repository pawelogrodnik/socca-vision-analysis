from __future__ import annotations

"""Reviewed-only targets and decisions for frame-owned mixed tracklets."""

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
import time
from typing import Any

from app.services.identity_canonical_io import load_json_cached
from app.services.identity_canonical_ownership import global_observation_ownership
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_mixed_store import (
    load_mixed_player_cases,
    operator_mixed_targets,
    resolved_inline_temporal_split_source_keys,
)
from app.services.identity_reviewed_slot_registry import (
    build_reviewed_slot_registry,
    next_free_reviewed_slot,
    normalize_reviewed_slot_id,
)
from app.services.identity_reviewed_slot_review import (
    FILENAME as SLOT_REVIEW_FILENAME,
    load_reviewed_slot_assignments,
)
from app.services.identity_roster_anchor_crop_renderer import (
    render_identity_roster_anchor_crops,
)
from app.services.identity_reviewed_segment_coalescing import (
    MAX_SEGMENT_REVIEW_GAP_SEC,
    coalesced_conflict_episodes,
    exact_frame_ranges,
    max_segment_review_gap_frames,
)
from app.services.play_area import is_on_pitch_product_observation
from app.services.video import read_match_video_metadata, resolve_match_video_path


REVIEW_FILENAME = "reviewed_identity_segment_review.json"
DECISIONS_FILENAME = "reviewed_identity_segment_decisions.json"
SCHEMA_VERSION = "1.0.0"
TARGET_ALGORITHM_VERSION = "gap-coalesced-v2"
ALLOWED_ACTIONS = frozenset(
    {
        "assign_roster_player",
        "assign_existing_slot",
        "assign_team",
        "create_new_stable_player",
        "referee",
        "false_detection",
        "team_unknown",
        "unresolved",
    }
)


def build_segment_review_document(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    performance: dict[str, float] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    tracklets_doc = _load(match_path / "tracklets.json")
    subjects_doc = _load(match_path / "identity_candidate_shadow.json")
    global_doc = _load(match_path / "global_identity.json")
    card_doc = _load(match_path / "identity_roster_subject_review_shadow.json")
    decisions_doc = load_segment_decisions(match_path)
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_doc.get("tracklets") or []
        if row.get("tracklet_id")
    }
    on_pitch_pairs = {
        (tracklet_id, int(position.get("frame") or 0))
        for tracklet_id, tracklet in tracklets.items()
        for position in tracklet.get("positions_m") or []
        if is_on_pitch_product_observation(position)
    }
    subjects = {
        str(row.get("candidate_subject_id")): {
            str(value) for value in row.get("tracklet_ids") or []
        }
        for row in subjects_doc.get("subjects") or []
        if row.get("candidate_subject_id")
    }
    subject_by_tracklet: dict[str, set[str]] = defaultdict(set)
    for subject_id, tracklet_ids in subjects.items():
        for tracklet_id in tracklet_ids:
            subject_by_tracklet[tracklet_id].add(subject_id)

    ownership = global_observation_ownership(global_doc)
    owner_slots: dict[str, set[str]] = defaultdict(set)
    for row in ownership:
        tracklet_id = str(row.get("tracklet_id") or "")
        if (tracklet_id, int(row.get("frame") or 0)) not in on_pitch_pairs:
            continue
        owner_slots[tracklet_id].add(
            str(row.get("stable_slot_id") or "")
        )
    mixed_tracklets = {
        tracklet_id for tracklet_id, slots in owner_slots.items() if len(slots) > 1
    }
    cards = {
        str(row.get("candidate_subject_id")): row
        for row in card_doc.get("cards") or []
        if row.get("candidate_subject_id")
    }
    legacy = _legacy_roster_decisions(match_path)
    terminal_whole_subjects = _terminal_whole_subject_decisions(match_path)
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for claim in ownership:
        tracklet_id = str(claim.get("tracklet_id") or "")
        if (tracklet_id, int(claim.get("frame") or 0)) not in on_pitch_pairs:
            continue
        if tracklet_id not in mixed_tracklets:
            continue
        for subject_id in subject_by_tracklet.get(tracklet_id) or set():
            if not _requires_segment_review(
                cards.get(subject_id),
                legacy.get(subject_id),
                subject_id in terminal_whole_subjects,
            ):
                continue
            slot_id = str(claim.get("stable_slot_id") or "")
            team_label = str(claim.get("team_label") or slot_id[:1] or "U")
            groups[(subject_id, tracklet_id, slot_id, team_label)].append(claim)

    stored_decisions = {
        str(row.get("review_target_id")): row
        for row in decisions_doc.get("decisions") or []
        if row.get("review_target_id")
    }
    roster = _roster(match_doc)
    targets: list[dict[str, Any]] = []
    matched_decision_ids: set[str] = set()
    fps = _review_fps(match_path, match_doc)
    on_pitch_ownership = [
        claim
        for claim in ownership
        if (
            str(claim.get("tracklet_id") or ""),
            int(claim.get("frame") or 0),
        )
        in on_pitch_pairs
    ]
    episodes = coalesced_conflict_episodes(groups, on_pitch_ownership, fps=fps)
    for (subject_id, tracklet_id, slot_id, team_label), grouped_frames in sorted(
        episodes.items()
    ):
        for frames in grouped_frames:
            ownership_payload = [
                {
                    "tracklet_id": tracklet_id,
                    "frame": frame,
                    "stable_slot_id": slot_id,
                    "team_label": team_label,
                }
                for frame in frames
            ]
            ownership_digest = canonical_digest(ownership_payload)
            target_id = _target_id(
                subject_id,
                tracklet_id,
                slot_id,
                team_label,
                frames,
            )
            evidence = _target_evidence(
                cards.get(subject_id),
                tracklets.get(tracklet_id) or {},
                frames,
                target_id,
            )
            decision = stored_decisions.get(target_id)
            decision_current = bool(
                decision
                and decision.get("source_ownership_digest") == ownership_digest
            )
            if decision is not None:
                matched_decision_ids.add(target_id)
            targets.append(
                {
                    "review_target_id": target_id,
                    "scope_kind": "canonical_segment",
                    "candidate_subject_id": subject_id,
                    "tracklet_ids": [tracklet_id],
                    "stable_slot_id": slot_id,
                    "source_team_label": team_label,
                    "effective_team_label": str(
                        (decision or {}).get("team_label") or team_label
                    ),
                    "frame_start": frames[0],
                    "frame_end": frames[-1],
                    "frame_ranges": exact_frame_ranges(frames),
                    # This exact list is authoritative. Presentation ranges must
                    # never be used to synthesize an unowned observation.
                    "owned_frames": frames,
                    "owned_observations": [
                        {"tracklet_id": tracklet_id, "frame": frame}
                        for frame in frames
                    ],
                    "detected_observation_count": len(frames),
                    "source_ownership_digest": ownership_digest,
                    "reason_codes": ["mixed_tracklet_canonical_owners"],
                    "visual_evidence": evidence,
                    "current_decision": decision if decision_current else None,
                    "decision_status": "reviewed" if decision_current else "pending",
                    "stale_decision": bool(decision and not decision_current),
                    "legacy_suggestion": None,
                }
            )

    dominant_by_subject: dict[str, dict[str, Any]] = {}
    for target in targets:
        subject_id = str(target["candidate_subject_id"])
        current = dominant_by_subject.get(subject_id)
        if current is None or int(target["detected_observation_count"]) > int(
            current["detected_observation_count"]
        ):
            dominant_by_subject[subject_id] = target
    for subject_id, target in dominant_by_subject.items():
        player_id = legacy.get(subject_id)
        player = roster.get(player_id or "")
        if player and str(player.get("team_label")) == str(target["source_team_label"]):
            target["legacy_suggestion"] = {
                "action": "assign_roster_player",
                "player_id": player_id,
                "player_name": player.get("player_name"),
                "roster_number": player.get("roster_number"),
                "team_label": player.get("team_label"),
                "requires_confirmation": True,
            }

    operator_targets_started = time.perf_counter()
    operator_targets = operator_mixed_targets(match_path)
    for target in operator_targets:
        target_id = str(target["review_target_id"])
        decision = stored_decisions.get(target_id)
        decision_current = bool(
            decision
            and decision.get("source_ownership_digest")
            == target.get("source_ownership_digest")
        )
        if decision is not None:
            matched_decision_ids.add(target_id)
        target["current_decision"] = decision if decision_current else None
        target["decision_status"] = "reviewed" if decision_current else "pending"
        target["stale_decision"] = bool(decision and not decision_current)
        targets.append(target)

    mixed_cases = [
        dict(row)
        for row in load_mixed_player_cases(match_path).get("cases") or []
        if isinstance(row, dict)
    ]
    retired_source_keys = resolved_inline_temporal_split_source_keys(
        mixed_cases,
        stored_decisions,
        operator_targets,
    )
    if retired_source_keys:
        targets = [
            target
            for target in targets
            if (
                str(target.get("review_target_id") or ""),
                str(target.get("source_ownership_digest") or ""),
            ) not in retired_source_keys
        ]

    if performance is not None:
        performance["segment_review_operator_targets_ms"] = round(
            (time.perf_counter() - operator_targets_started) * 1000,
            1,
        )

    _attach_boundary_evidence(targets)

    document = {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_segment_review",
        "match_id": str(match_doc.get("id") or match_path.name),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "global_identity_digest": canonical_digest(global_doc),
            "subjects_digest": canonical_digest(subjects_doc),
            "decisions_digest": canonical_digest(decisions_doc.get("decisions") or []),
        },
        "target_policy": {
            "algorithm_version": TARGET_ALGORITHM_VERSION,
            "product_play_area_policy": "inside_play_only",
            "max_unowned_gap_sec": MAX_SEGMENT_REVIEW_GAP_SEC,
            "max_unowned_gap_frames": max_segment_review_gap_frames(fps),
            "preserves_exact_owned_frames": True,
            "crosses_canonical_owner_transitions": False,
        },
        "summary": {
            "mixed_tracklets": len({key[1] for key in groups}),
            "operator_mixed_targets": sum(
                str(row.get("target_origin") or "").startswith("operator_")
                for row in targets
            ),
            "targets_total": len(targets),
            "targets_reviewed": sum(
                target["decision_status"] == "reviewed" for target in targets
            ),
            "targets_pending": sum(
                target["decision_status"] == "pending" for target in targets
            ),
            "stale_decisions": (
                sum(bool(target["stale_decision"]) for target in targets)
                + len(set(stored_decisions) - matched_decision_ids)
            ),
            "orphaned_decisions_requiring_review": len(
                set(stored_decisions) - matched_decision_ids
            ),
        },
        "targets": sorted(
            targets,
            key=lambda row: (
                str(row["candidate_subject_id"]),
                int(row["frame_start"]),
                str(row["stable_slot_id"]),
            ),
        ),
        "safety": {
            "mutates_raw_tracklets": False,
            "fills_unowned_observation_gaps": False,
            "reruns_yolo": False,
        },
    }
    write_identity_json_atomic(match_path / REVIEW_FILENAME, document)
    if performance is not None:
        performance["segment_review_build_ms"] = round(
            (time.perf_counter() - started) * 1000,
            1,
        )
    return document


def load_segment_review(match_path: Path) -> dict[str, Any]:
    return _load(match_path / REVIEW_FILENAME)


def project_segment_decisions_onto_materialized_review(
    match_path: Path,
    materialized_review: dict[str, Any],
) -> dict[str, Any]:
    """Refresh decision-derived fields without rebuilding target topology.

    A Mixed split's first canonical build establishes the exact child target
    IDs, ownership, evidence and boundary relationships. Persisting decisions
    cannot change that topology. A second full build previously recalculated
    all large canonical ownership inputs only to update the fields below:
    current/stale decisions, decision counts, orphan counts and the decisions
    digest. In particular, operator-split ``effective_team_label`` remains a
    topology field from the first build; the canonical second build does not
    derive it from the newly saved decision. Keep that exact contract explicit
    and differential-tested against a fresh canonical build.
    """
    document = deepcopy(materialized_review)
    decisions_document = load_segment_decisions(match_path)
    stored = {
        str(row.get("review_target_id") or ""): row
        for row in decisions_document.get("decisions") or []
        if row.get("review_target_id")
    }
    matched: set[str] = set()
    targets = [row for row in document.get("targets") or [] if isinstance(row, dict)]
    for target in targets:
        target_id = str(target.get("review_target_id") or "")
        decision = stored.get(target_id)
        current = bool(
            decision
            and str(decision.get("source_ownership_digest") or "")
            == str(target.get("source_ownership_digest") or "")
        )
        if decision is not None:
            matched.add(target_id)
        target["current_decision"] = decision if current else None
        target["decision_status"] = "reviewed" if current else "pending"
        target["stale_decision"] = bool(decision and not current)

    mixed_cases = [
        dict(row)
        for row in load_mixed_player_cases(match_path).get("cases") or []
        if isinstance(row, dict)
    ]
    retired_source_keys = resolved_inline_temporal_split_source_keys(
        mixed_cases,
        stored,
        targets,
    )
    if retired_source_keys:
        targets = [
            target
            for target in targets
            if (
                str(target.get("review_target_id") or ""),
                str(target.get("source_ownership_digest") or ""),
            ) not in retired_source_keys
        ]

    orphaned = len(set(stored) - matched)
    summary = dict(document.get("summary") or {})
    summary.update(
        {
            "targets_reviewed": sum(target.get("decision_status") == "reviewed" for target in targets),
            "targets_pending": sum(target.get("decision_status") == "pending" for target in targets),
            "stale_decisions": sum(bool(target.get("stale_decision")) for target in targets) + orphaned,
            "orphaned_decisions_requiring_review": orphaned,
        }
    )
    document["targets"] = targets
    document["summary"] = summary
    source = dict(document.get("source") or {})
    source["decisions_digest"] = canonical_digest(decisions_document.get("decisions") or [])
    document["source"] = source
    document["generated_at"] = datetime.now(timezone.utc).isoformat()
    write_identity_json_atomic(match_path / REVIEW_FILENAME, document)
    return document


def render_segment_review_evidence(
    match_path: Path,
    match_doc: dict[str, Any],
    review: dict[str, Any],
) -> set[str]:
    crops = [
        crop
        for target in review.get("targets") or []
        for crop in (target.get("visual_evidence") or {}).get("anchor_crops") or []
        if crop.get("generated_for_segment_review")
        and crop.get("artifact")
        and not (match_path / str(crop["artifact"])).exists()
    ]
    if not crops:
        return set()
    preferred = str(match_doc.get("video_filename") or "") or None
    video = resolve_match_video_path(match_path, preferred)
    return render_identity_roster_anchor_crops(
        video,
        match_path,
        {"cards": [{"anchor_crops": crops}]},
    )


def load_segment_decisions(match_path: Path) -> dict[str, Any]:
    document = _load(match_path / DECISIONS_FILENAME)
    if document:
        return document
    return _decision_document([])


def _save_segment_decision_legacy(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
    *,
    materialized_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review = materialized_review or build_segment_review_document(match_path, match_doc)
    target_id = str(payload.get("review_target_id") or "")
    target = next(
        (
            row
            for row in review.get("targets") or []
            if str(row.get("review_target_id") or "") == target_id
        ),
        None,
    )
    if target is None:
        raise SegmentTargetError("review_target_unknown")
    supplied_digest = str(payload.get("source_ownership_digest") or "")
    if supplied_digest != str(target.get("source_ownership_digest") or ""):
        raise SegmentTargetError("review_target_stale")
    action = str(payload.get("action") or "")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported segment correction action: {action}")
    roster = _roster(match_doc)
    player_id = str(payload.get("player_id") or "") or None
    team_label = str(payload.get("team_label") or "").upper() or None
    if action == "assign_roster_player":
        player = roster.get(player_id or "")
        if player is None:
            raise ValueError(f"Invalid player_id: {player_id or '<missing>'}")
        team_label = str(player["team_label"])
    elif action == "assign_existing_slot":
        registry = build_reviewed_slot_registry(match_path)
        stable_slot_id = normalize_reviewed_slot_id(payload.get("stable_slot_id"))
        if not stable_slot_id or stable_slot_id not in registry:
            raise ValueError("assign_existing_slot requires an existing stable slot")
        team_label = str(registry[stable_slot_id]["team_label"])
        player_id = None
    elif action == "create_new_stable_player":
        if team_label not in {"A", "B"}:
            raise ValueError("create_new_stable_player requires team_label A or B")
        slot_document = load_reviewed_slot_assignments(match_path)
        registry = build_reviewed_slot_registry(match_path, slot_document)
        previous = next(
            (
                row
                for row in load_segment_decisions(match_path).get("decisions") or []
                if str(row.get("review_target_id") or "") == target_id
                and row.get("action") == "create_new_stable_player"
            ),
            None,
        )
        stable_slot_id = normalize_reviewed_slot_id((previous or {}).get("stable_slot_id"))
        if stable_slot_id is None:
            stable_slot_id = next_free_reviewed_slot(team_label, registry)
        if stable_slot_id is None:
            raise ValueError(f"bounded pool exhausted for team {team_label}")
        reviewed_slots = list(slot_document.get("reviewed_slots") or [])
        if not any(
            normalize_reviewed_slot_id(row.get("stable_slot_id")) == stable_slot_id
            for row in reviewed_slots
        ):
            reviewed_slots.append(
                {
                    "stable_slot_id": stable_slot_id,
                    "team_label": team_label,
                    "source": "manual_new_player_confirmation",
                    "created_for_candidate_subject_id": target["candidate_subject_id"],
                    "status": "active",
                }
            )
            write_identity_json_atomic(
                match_path / SLOT_REVIEW_FILENAME,
                {**slot_document, "reviewed_slots": reviewed_slots},
            )
        player_id = None
    elif action == "assign_team":
        if team_label not in {"A", "B"}:
            raise ValueError("assign_team requires team_label A or B")
        player_id = None
    else:
        player_id = None
        team_label = (
            "U" if action == "team_unknown" else str(target["source_team_label"])
        )

    existing = load_segment_decisions(match_path)
    decisions = {
        str(row.get("review_target_id")): dict(row)
        for row in existing.get("decisions") or []
        if row.get("review_target_id")
    }
    decision = {
        "review_target_id": target_id,
        "candidate_subject_id": target["candidate_subject_id"],
        "tracklet_ids": list(target["tracklet_ids"]),
        "stable_slot_id": target["stable_slot_id"],
        "source_ownership_digest": target["source_ownership_digest"],
        "action": action,
        "player_id": player_id,
        "team_label": team_label,
        "source_team_label": str(target["source_team_label"]),
        "team_correction": bool(
            action == "assign_roster_player"
            and str(target["source_team_label"]) in {"A", "B"}
            and team_label != str(target["source_team_label"])
        ),
        "source": "manual_segment_review",
        "supersedes_legacy_whole_subject_decision": bool(
            target.get("legacy_suggestion")
        ),
        "comment": str(payload.get("comment") or "").strip() or None,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    if action in {"assign_existing_slot", "create_new_stable_player"}:
        decision["stable_slot_id"] = stable_slot_id
    decisions[target_id] = decision
    document = _decision_document(
        sorted(decisions.values(), key=lambda row: str(row["review_target_id"]))
    )
    write_identity_json_atomic(match_path / DECISIONS_FILENAME, document)
    return decision


def save_segment_decisions_batch(
    match_path: Path,
    match_doc: dict[str, Any],
    payloads: list[dict[str, Any]],
    *,
    materialized_review: dict[str, Any] | None = None,
    performance: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Validate and persist an exact segment-decision batch atomically.

    Inline Mixed splits produce all child targets together. Reading and writing
    the decisions/slot documents per child was both slow and made atomicity
    harder to reason about. This keeps the public single-save contract while
    committing the multi-child result once after every child has validated.
    """
    if not payloads:
        return []
    review = materialized_review or build_segment_review_document(match_path, match_doc)
    targets = {
        str(row.get("review_target_id") or ""): row
        for row in review.get("targets") or []
        if row.get("review_target_id")
    }
    existing = load_segment_decisions(match_path)
    decisions = {
        str(row.get("review_target_id") or ""): dict(row)
        for row in existing.get("decisions") or []
        if row.get("review_target_id")
    }
    slot_document = load_reviewed_slot_assignments(match_path)
    registry = build_reviewed_slot_registry(match_path, slot_document)
    reviewed_slots = [dict(row) for row in slot_document.get("reviewed_slots") or []]
    slots_changed = False
    roster = _roster(match_doc)
    prepared: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    validation_started = time.perf_counter()
    for payload in payloads:
        target_id = str(payload.get("review_target_id") or "")
        target = targets.get(target_id)
        if target is None:
            raise SegmentTargetError("review_target_unknown")
        if str(payload.get("source_ownership_digest") or "") != str(target.get("source_ownership_digest") or ""):
            raise SegmentTargetError("review_target_stale")
        action = str(payload.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported segment correction action: {action}")
        player_id = str(payload.get("player_id") or "") or None
        team_label = str(payload.get("team_label") or "").upper() or None
        stable_slot_id: str | None = None
        if action == "assign_roster_player":
            player = roster.get(player_id or "")
            if player is None:
                raise ValueError(f"Invalid player_id: {player_id or '<missing>'}")
            team_label = str(player["team_label"])
        elif action == "assign_existing_slot":
            stable_slot_id = normalize_reviewed_slot_id(payload.get("stable_slot_id"))
            if not stable_slot_id or stable_slot_id not in registry:
                raise ValueError("assign_existing_slot requires an existing stable slot")
            team_label = str(registry[stable_slot_id]["team_label"])
            player_id = None
        elif action == "create_new_stable_player":
            if team_label not in {"A", "B"}:
                raise ValueError("create_new_stable_player requires team_label A or B")
            previous = decisions.get(target_id)
            stable_slot_id = normalize_reviewed_slot_id((previous or {}).get("stable_slot_id"))
            if stable_slot_id is None:
                stable_slot_id = next_free_reviewed_slot(team_label, registry)
            if stable_slot_id is None:
                raise ValueError(f"bounded pool exhausted for team {team_label}")
            if stable_slot_id not in registry:
                slot = {
                    "stable_slot_id": stable_slot_id,
                    "team_label": team_label,
                    "source": "manual_new_player_confirmation",
                    "created_for_candidate_subject_id": target["candidate_subject_id"],
                    "status": "active",
                }
                reviewed_slots.append(slot)
                registry[stable_slot_id] = slot
                slots_changed = True
            player_id = None
        elif action == "assign_team":
            if team_label not in {"A", "B"}:
                raise ValueError("assign_team requires team_label A or B")
            player_id = None
        else:
            player_id = None
            team_label = "U" if action == "team_unknown" else str(target["source_team_label"])

        decision = {
            "review_target_id": target_id,
            "candidate_subject_id": target["candidate_subject_id"],
            "tracklet_ids": list(target["tracklet_ids"]),
            "stable_slot_id": target["stable_slot_id"],
            "source_ownership_digest": target["source_ownership_digest"],
            "action": action,
            "player_id": player_id,
            "team_label": team_label,
            "source_team_label": str(target["source_team_label"]),
            "team_correction": bool(
                action == "assign_roster_player"
                and str(target["source_team_label"]) in {"A", "B"}
                and team_label != str(target["source_team_label"])
            ),
            "source": "manual_segment_review",
            "supersedes_legacy_whole_subject_decision": bool(target.get("legacy_suggestion")),
            "comment": str(payload.get("comment") or "").strip() or None,
            "reviewed_at": now,
        }
        if stable_slot_id is not None:
            decision["stable_slot_id"] = stable_slot_id
        decisions[target_id] = decision
        prepared.append(decision)
    if performance is not None:
        performance["segment_assignment_validation_ms"] = round(
            (time.perf_counter() - validation_started) * 1000,
            1,
        )

    if slots_changed:
        slot_persistence_started = time.perf_counter()
        write_identity_json_atomic(
            match_path / SLOT_REVIEW_FILENAME,
            {**slot_document, "reviewed_slots": reviewed_slots},
        )
        if performance is not None:
            performance["reviewed_slot_persistence_ms"] = round(
                (time.perf_counter() - slot_persistence_started) * 1000,
                1,
            )
    decision_persistence_started = time.perf_counter()
    write_identity_json_atomic(
        match_path / DECISIONS_FILENAME,
        _decision_document(sorted(decisions.values(), key=lambda row: str(row["review_target_id"]))),
    )
    if performance is not None:
        performance["segment_decision_persistence_ms"] = round(
            (time.perf_counter() - decision_persistence_started) * 1000,
            1,
        )
    return prepared


def save_segment_decision(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
    *,
    materialized_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for the atomic multi-target writer."""
    return save_segment_decisions_batch(
        match_path,
        match_doc,
        [payload],
        materialized_review=materialized_review,
    )[0]


def segment_observation_assignments(
    review: dict[str, Any],
    decisions: dict[str, Any],
    roster: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions_by_target = {
        str(row.get("review_target_id")): row
        for row in decisions.get("decisions") or []
        if row.get("review_target_id")
    }
    output: list[dict[str, Any]] = []
    for target in review.get("targets") or []:
        decision = decisions_by_target.get(str(target.get("review_target_id") or ""))
        if not decision or decision.get("source_ownership_digest") != target.get(
            "source_ownership_digest"
        ):
            continue
        action = str(decision.get("action") or "")
        player = roster.get(str(decision.get("player_id") or ""))
        observations = target.get("owned_observations") or [
            {
                "tracklet_id": str((target.get("tracklet_ids") or [""])[0]),
                "frame": frame,
            }
            for frame in target.get("owned_frames") or []
        ]
        for observation in observations:
            row = _segment_assignment_row(
                target,
                decision,
                action,
                player,
                int(observation["frame"]),
                str(observation["tracklet_id"]),
            )
            if row is not None:
                output.append(row)
    return sorted(output, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))


def target_for_id(review: dict[str, Any], target_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in review.get("targets") or []
            if str(row.get("review_target_id") or "") == target_id
        ),
        None,
    )


class SegmentTargetError(ValueError):
    pass


def _segment_assignment_row(
    target: dict[str, Any],
    decision: dict[str, Any],
    action: str,
    player: dict[str, Any] | None,
    frame: int,
    tracklet_id: str,
) -> dict[str, Any] | None:
    slot_id = str(
        decision.get("stable_slot_id") or target.get("stable_slot_id") or ""
    ) or None
    team = str(decision.get("team_label") or target.get("source_team_label") or "U")
    common = {
        "review_target_id": target.get("review_target_id"),
        "tracklet_id": tracklet_id,
        "frame": frame,
        "identity_source": "manual_segment_review",
        "hard_blockers": [],
        "conflicts": [],
        "eligible_for_player_stats": False,
    }
    if action == "assign_roster_player" and player:
        player_name = str(player.get("player_name") or player.get("name") or decision.get("player_id"))
        return {
            **common,
            "stable_anonymous_slot_id": slot_id,
            "stable_anonymous_entity_id": slot_id,
            "team_label": str(player["team_label"]),
            "fallback_label": slot_id or f"{player['team_label']}?",
            "identity_status": "confirmed",
            "canonical_player_id": str(decision.get("player_id")),
            "player_name": player_name,
            "roster_number": player.get("roster_number", player.get("number")),
            "display_label": player_name,
            "eligible_for_player_stats": True,
        }
    if action == "assign_team":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": team,
            "fallback_label": f"{team}?",
            "display_label": f"{team}?",
            "identity_status": "unresolved",
            "canonical_player_id": None,
            "player_name": None,
        }
    if action in {"assign_existing_slot", "create_new_stable_player"} and slot_id:
        return {
            **common,
            "stable_anonymous_slot_id": slot_id,
            "stable_anonymous_entity_id": slot_id,
            "team_label": slot_id[0],
            "fallback_label": slot_id,
            "display_label": slot_id,
            "identity_status": "stable_anonymous",
            "canonical_player_id": None,
            "player_name": None,
        }
    if action == "referee":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": "U",
            "fallback_label": "Sędzia",
            "display_label": "Sędzia",
            "identity_status": "referee",
            "canonical_player_id": None,
            "player_name": None,
        }
    if action == "false_detection":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": "U",
            "fallback_label": "Fałszywa detekcja",
            "display_label": "Fałszywa detekcja",
            "identity_status": "false_detection",
            "canonical_player_id": None,
            "player_name": None,
        }
    if action in {"team_unknown", "unresolved"}:
        unknown_team = "U" if action == "team_unknown" else team
        fallback = slot_id if action == "unresolved" and slot_id else f"{unknown_team}?"
        return {
            **common,
            "stable_anonymous_slot_id": slot_id if action == "unresolved" else None,
            "stable_anonymous_entity_id": slot_id if action == "unresolved" else None,
            "team_label": unknown_team,
            "fallback_label": fallback,
            "display_label": fallback,
            "identity_status": "team_unknown" if action == "team_unknown" else "unresolved",
            "canonical_player_id": None,
            "player_name": None,
        }
    return None


def _target_evidence(
    card: dict[str, Any] | None,
    tracklet: dict[str, Any],
    frames: list[int],
    target_id: str,
) -> dict[str, Any]:
    frame_set = set(frames)
    existing = [
        dict(row)
        for row in ((card or {}).get("visual_evidence") or {}).get("anchor_crops") or []
        if int(row.get("frame") or -1) in frame_set
    ]
    positions = {
        int(row.get("frame") or 0): row
        for row in tracklet.get("positions_m") or []
        if int(row.get("frame") or 0) in frame_set
    }
    selected_frames = _representative_frames(frames, 5)
    generated = []
    safe_target = target_id.rsplit(":", 1)[-1]
    existing_frames = {int(row.get("frame") or 0) for row in existing}
    for index, frame in enumerate(selected_frames, start=1):
        if frame in existing_frames:
            continue
        position = positions.get(frame) or {}
        bbox = position.get("bbox_xyxy")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        generated.append(
            {
                "anchor_crop_id": f"segment-crop:{safe_target}:{frame}",
                "artifact": f"reviewed_identity_segments/{safe_target}/{index:02d}_f{frame:06d}.jpg",
                "frame": frame,
                "time_sec": position.get("time_sec"),
                "tracklet_id": tracklet.get("tracklet_id"),
                "bbox_xyxy": bbox,
                "generated_for_segment_review": True,
            }
        )
    by_frame: dict[int, dict[str, Any]] = {}
    for crop in [*existing, *generated]:
        by_frame.setdefault(int(crop.get("frame") or 0), crop)
    ordered = [by_frame[frame] for frame in sorted(by_frame)]
    crops = _representative_values(ordered, 5)
    return {
        "status": "ready" if crops else "missing",
        "selected_crop_count": len(crops),
        "anchor_crops": crops,
    }


def _representative_frames(frames: list[int], limit: int) -> list[int]:
    return _representative_values(frames, limit)


def _representative_values(values: list[Any], limit: int) -> list[Any]:
    if limit <= 0 or not values:
        return []
    if limit == 1:
        return [values[0]]
    if len(values) <= limit:
        return values
    indexes = {
        round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)
    }
    return [values[index] for index in sorted(indexes)]


def _attach_boundary_evidence(targets: list[dict[str, Any]]) -> None:
    for target in targets:
        subject_id = str(target.get("candidate_subject_id") or "")
        tracklet_ids = set(target.get("tracklet_ids") or [])
        candidates = [
            {**crop, "outside_target": True}
            for other in targets
            if other is not target
            and str(other.get("candidate_subject_id") or "") == subject_id
            and tracklet_ids.intersection(other.get("tracklet_ids") or [])
            for crop in (other.get("visual_evidence") or {}).get("anchor_crops") or []
        ]
        start = int(target.get("frame_start") or 0)
        end = int(target.get("frame_end") or start)
        ranked = sorted(
            candidates,
            key=lambda crop: min(
                abs(int(crop.get("frame") or 0) - start),
                abs(int(crop.get("frame") or 0) - end),
            ),
        )
        (target.get("visual_evidence") or {})["boundary_crops"] = ranked[:2]


def _target_id(
    subject_id: str,
    tracklet_id: str,
    slot_id: str,
    team_label: str,
    frames: list[int],
) -> str:
    digest = canonical_digest(
        {
            "candidate_subject_id": subject_id,
            "tracklet_id": tracklet_id,
            "stable_slot_id": slot_id,
            "team_label": team_label,
            "target_algorithm_version": TARGET_ALGORITHM_VERSION,
            "owned_frames_digest": canonical_digest(frames),
        }
    )
    return f"review-segment:v2:{digest}"


def _review_fps(match_path: Path, match_doc: dict[str, Any]) -> float:
    try:
        container_fps = _positive_fps(
            read_match_video_metadata(match_path, match_doc).get("fps")
        )
        if container_fps is not None:
            return container_fps
    except (FileNotFoundError, ValueError):
        pass

    for candidate in (
        (match_doc.get("video") or {}).get("fps"),
        match_doc.get("fps"),
    ):
        fallback_fps = _positive_fps(candidate)
        if fallback_fps is not None:
            return fallback_fps
    return 30.0


def _positive_fps(value: Any) -> float | None:
    try:
        fps = float(value)
    except (TypeError, ValueError):
        return None
    return fps if fps > 0 and isfinite(fps) else None


def _legacy_roster_decisions(match_path: Path) -> dict[str, str]:
    roster_doc = _load(
        match_path / "identity_roster_subject_review_decisions_shadow.json"
    )
    slot_doc = _load(match_path / "reviewed_identity_slot_assignments.json")
    output = {
        str(row.get("candidate_subject_id")): str(row.get("player_id"))
        for row in roster_doc.get("decisions") or []
        if row.get("candidate_subject_id")
        and row.get("player_id")
        and row.get("decision") in {"assign_roster_player", "confirm_recommended_player"}
    }
    output.update(
        {
            str(row.get("candidate_subject_id")): str(row.get("player_id"))
            for row in slot_doc.get("decisions") or []
            if row.get("candidate_subject_id")
            and row.get("player_id")
            and row.get("action") == "assign_roster_player"
            and not row.get("stable_slot_id")
        }
    )
    return output


def _requires_segment_review(
    card: dict[str, Any] | None,
    legacy_player_id: str | None,
    terminal_whole_subject_decision: bool,
) -> bool:
    if terminal_whole_subject_decision:
        return False
    if legacy_player_id:
        return True
    if not card or card.get("requires_operator_review") is False:
        return False
    signals = {
        str(value).lower()
        for key in ("quality_flags", "reason_codes", "blockers")
        for value in card.get(key) or []
    }
    return any(
        marker in signal
        for signal in signals
        for marker in ("merge", "multiple", "conflict", "switch")
    )


def _terminal_whole_subject_decisions(match_path: Path) -> set[str]:
    # Only conservative identity removal can safely terminate review for a
    # mixed raw tracklet. assign_team, referee and false_detection affect the
    # whole raw fragment and may therefore misclassify another physical person.
    slot_doc = _load(match_path / "reviewed_identity_slot_assignments.json")
    return {
        str(row.get("candidate_subject_id"))
        for row in slot_doc.get("decisions") or []
        if row.get("candidate_subject_id")
        and row.get("action")
        in {"team_unknown", "unresolved"}
    }


def _roster(match_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for index, team in enumerate(match_doc.get("teams") or []):
        team_label = str(team.get("team_label") or chr(ord("A") + index))
        for player in team.get("players") or []:
            player_id = str(player.get("id") or "")
            if player_id:
                output[player_id] = {
                    "player_id": player_id,
                    "player_name": str(player.get("name") or player_id),
                    "roster_number": player.get("number"),
                    "team_label": team_label,
                }
    return output


def _decision_document(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_segment_decisions",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "decisions": decisions,
        "safety": {
            "mutates_raw_tracklets": False,
            "fills_unowned_observation_gaps": False,
        },
    }


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    # Strict malformed-JSON semantics preserved (raises); non-dict tolerated.
    value = load_json_cached(path)
    return value if isinstance(value, dict) else {}
