from __future__ import annotations

"""Sparse, exact-observation visual evidence for unknown-team review.

Roster identity crops deliberately reject weak detections because they are used
to name a person.  That is stricter than the evidence needed for an operator to
say "this is Team A", "this is Team B", "referee", or "false detection".
This module creates that second, narrower evidence channel without changing
candidate identity, tracklets, or reviewed output.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.identity_canonical_io import load_json_cached_or
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_effective_observation import is_real_detected_position
from app.services.identity_roster_anchor_crop_renderer import (
    render_identity_roster_anchor_crops,
)
from app.services.play_area import is_on_pitch_product_observation


FILENAME = "reviewed_identity_team_attribution_evidence.json"
SCHEMA_VERSION = "1.0.0"
# Cached crops are operator-facing evidence.  Bump this when their selection
# policy changes, so unchanged source observations cannot preserve stale crops.
EVIDENCE_SELECTION_VERSION = "1.2.0"
MAX_CROPS_PER_CASE = 5
MIN_CROPS_PER_CASE = 3
MIN_BBOX_WIDTH_PX = 8
MIN_BBOX_HEIGHT_PX = 18
PREFERRED_MAX_OVERLAP = 0.12
UNUSABLE_MAX_OVERLAP = 0.85
TEAM_ATTRIBUTION_EVIDENCE_NOT_MATERIALIZED = (
    "team_attribution_evidence_not_materialized"
)
# Only these statuses are emitted by the current evidence builder after it has
# actually evaluated the exact source and found no safe operator evidence.
# Everything else, including a missing or future status, is intentionally
# remediable: it must never silently consume the terminal residual budget.
TERMINAL_UNAVAILABLE_TEAM_ATTRIBUTION_EVIDENCE_STATUSES = frozenset({
    "insufficient_team_attribution_evidence",
    "no_team_attribution_evidence",
})
# These statuses mean the exact source was evaluated but the application could
# not provide its evidence.  They are deliberately not ordinary "no safe
# evidence" outcomes: accepting them would hide a broken video/artifact path
# behind the Team-U residual tolerance.
TECHNICAL_TEAM_ATTRIBUTION_EVIDENCE_STATUSES = frozenset({
    "source_video_unavailable",
    "team_attribution_crops_unavailable",
    "team_attribution_evidence_recovery_incomplete",
    "team_attribution_evidence_materialization_failed",
    "focused_source_subject_missing",
    "focused_source_pairs_missing",
    "focused_source_pairs_stale",
    "focused_source_digest_mismatch",
    "focused_source_not_reviewable",
    "team_attribution_evidence_source_digest_mismatch",
})

TEAM_ATTRIBUTION_EVIDENCE_ACTIONABLE = "actionable"
TEAM_ATTRIBUTION_EVIDENCE_TERMINAL_UNAVAILABLE = "terminal_unavailable"
TEAM_ATTRIBUTION_EVIDENCE_TECHNICAL_FAILURE = "technical_failure"
TEAM_ATTRIBUTION_EVIDENCE_LIFECYCLE_NOT_MATERIALIZED = "not_materialized"
FOCUSED_SOURCE_ALREADY_ACTIONABLE = "focused_source_already_actionable"


def classify_team_attribution_evidence_status(value: object) -> str:
    """Classify a persisted status without treating absent metadata as proof.

    The returned values are deliberately policy-level rather than mirroring
    every evidence-builder implementation detail.  A new evidence status must
    be explicitly added to the terminal allowlist before it can be accepted as
    a genuine Team-U residual.
    """
    status = str(value or "").strip()
    if status in TERMINAL_UNAVAILABLE_TEAM_ATTRIBUTION_EVIDENCE_STATUSES:
        return "terminal_unavailable"
    if status in TECHNICAL_TEAM_ATTRIBUTION_EVIDENCE_STATUSES:
        return "technical_failure"
    return "remediable_not_established"


def normalized_team_attribution_evidence_status(value: object) -> str:
    """Return a safe public status for readiness diagnostics."""
    status = str(value or "").strip()
    if classify_team_attribution_evidence_status(status) in {
        "terminal_unavailable",
        "technical_failure",
    }:
        return status
    return TEAM_ATTRIBUTION_EVIDENCE_NOT_MATERIALIZED


def team_attribution_evidence_lifecycle(unit: Mapping[str, Any]) -> str:
    """Classify whether the current exact Team-attribution source is usable.

    The progress pipeline may establish evidence either through the dedicated
    Team-attribution renderer or through an existing, server-authorized normal
    operator crop.  Both are valid only when their crops still belong to the
    exact current ownership scope.  Readiness must use this same predicate so
    an attached proof cannot regress into an absent-status cache miss.
    """
    visual_evidence = unit.get("visual_evidence")
    if isinstance(visual_evidence, Mapping):
        evidence_kind = str(visual_evidence.get("kind") or "")
        expected_digest = str(unit.get("team_attribution_evidence_source_digest") or "")
        evidence_digest = str(visual_evidence.get("source_ownership_digest") or "")
        if evidence_kind == "team_attribution" and expected_digest and evidence_digest:
            if evidence_digest != expected_digest:
                return TEAM_ATTRIBUTION_EVIDENCE_TECHNICAL_FAILURE
        if _has_exact_safe_operator_visual_evidence(unit, visual_evidence):
            return TEAM_ATTRIBUTION_EVIDENCE_ACTIONABLE

    classification = classify_team_attribution_evidence_status(
        unit.get("team_attribution_evidence_status")
    )
    if classification == "terminal_unavailable":
        return TEAM_ATTRIBUTION_EVIDENCE_TERMINAL_UNAVAILABLE
    if classification == "technical_failure":
        return TEAM_ATTRIBUTION_EVIDENCE_TECHNICAL_FAILURE
    return TEAM_ATTRIBUTION_EVIDENCE_LIFECYCLE_NOT_MATERIALIZED


def _has_exact_safe_operator_visual_evidence(
    unit: Mapping[str, Any],
    visual_evidence: Mapping[str, Any],
) -> bool:
    """Accept only server-owned crops that are still inside this exact unit."""
    if not unit.get("has_operator_visual_evidence"):
        return False
    pairs = {
        (str(pair[0]), int(pair[1]))
        for pair in unit.get("detected_pairs") or []
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    }
    if not pairs:
        return False
    valid_crops = [
        crop
        for crop in visual_evidence.get("anchor_crops") or []
        if isinstance(crop, Mapping)
        and crop.get("selection_eligible") is not False
        and str(crop.get("artifact") or "")
        and _crop_belongs_to_pairs(crop, pairs)
    ]
    return len(valid_crops) >= MIN_CROPS_PER_CASE


def _crop_belongs_to_pairs(
    crop: Mapping[str, Any],
    pairs: set[tuple[str, int]],
) -> bool:
    frame = crop.get("frame")
    return isinstance(frame, int) and (
        str(crop.get("tracklet_id") or ""), frame
    ) in pairs


def mark_team_attribution_evidence_technical_failure(
    match_path: Path,
    focused_sources: list[dict[str, Any]],
    *,
    status: str,
) -> dict[str, Any]:
    """Durably mark only exact failed focused sources as technical failures.

    A retry must never return success while an exact remediation source is
    still merely "not materialized".  This helper preserves every unrelated
    cached case and writes a diagnostic status only for the owned source keys.
    """
    if status not in TECHNICAL_TEAM_ATTRIBUTION_EVIDENCE_STATUSES:
        raise ValueError(f"unsupported team-attribution technical status: {status}")
    document = load_team_attribution_evidence(match_path)
    requested_keys = {_source_key(row) for row in focused_sources if isinstance(row, dict)}
    cases = [dict(row) for row in document.get("cases") or [] if isinstance(row, dict)]
    existing_keys = {_case_key(row) for row in cases}
    for row in cases:
        if _case_key(row) in requested_keys:
            row["status"] = status
            row["rendered_anchor_crops"] = []
    for source in focused_sources:
        if not isinstance(source, dict) or _source_key(source) in existing_keys:
            continue
        cases.append({
            "candidate_subject_id": source.get("candidate_subject_id"),
            "source_ownership_digest": source.get("source_ownership_digest"),
            "scope_kind": source.get("scope_kind"),
            "review_target_id": source.get("review_target_id"),
            "continuity_group_id": source.get("continuity_group_id"),
            "source_team_label": source.get("source_team_label"),
            "detected_observation_count": len(source.get("detected_pairs") or []),
            "status": status,
            "anchor_crops": [],
            "rendered_anchor_crops": [],
        })
    cases.sort(key=_case_sort_key)
    updated = {
        **document,
        "schema_version": document.get("schema_version") or SCHEMA_VERSION,
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "source_observations": sum(int(row.get("detected_observation_count") or 0) for row in cases),
            "reviewable_cases": sum(row.get("status") == "ready_for_team_attribution" for row in cases),
            "unavailable_cases": sum(
                row.get("status") not in {
                    "ready_for_team_attribution",
                    FOCUSED_SOURCE_ALREADY_ACTIONABLE,
                }
                for row in cases
            ),
            "already_actionable_cases": sum(
                row.get("status") == FOCUSED_SOURCE_ALREADY_ACTIONABLE
                for row in cases
            ),
            "rendered_reviewable_cases": sum(len(row.get("rendered_anchor_crops") or []) >= MIN_CROPS_PER_CASE for row in cases),
        },
    }
    write_identity_json_atomic(match_path / FILENAME, updated)
    return updated


def load_team_attribution_evidence(match_path: Path) -> dict[str, Any]:
    try:
        document = json.loads((match_path / FILENAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def build_team_attribution_evidence(
    candidate_document: dict[str, Any],
    tracklets_document: dict[str, Any],
    roster_review_document: dict[str, Any],
    *,
    candidate_subject_ids: set[str] | None = None,
    focused_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build Team-U evidence references from exact raw observations only.

    A selected crop always comes from the subject's own detected, inside-play
    `(tracklet_id, frame)` observation.  Weak ReID/footpoint confidence is not
    a rejection reason here; it is precisely why the item needs a team-only
    human decision.  Invalid geometry and observations outside the product
    play area remain non-actionable.
    """
    tracklets = {
        str(row.get("tracklet_id") or ""): row
        for row in tracklets_document.get("tracklets") or []
        if row.get("tracklet_id")
    }
    cards = {
        str(row.get("candidate_subject_id") or ""): row
        for row in roster_review_document.get("cards") or []
        if row.get("candidate_subject_id")
    }
    frame_positions = _frame_positions(tracklets)
    subjects = {
        str(row.get("candidate_subject_id") or ""): row
        for row in candidate_document.get("subjects") or []
        if row.get("candidate_subject_id")
    }
    cases: list[dict[str, Any]] = []
    for source in _evidence_sources(
        subjects,
        tracklets,
        candidate_subject_ids=candidate_subject_ids,
        focused_sources=focused_sources,
    ):
        subject = source["subject"]
        subject_id = str(subject.get("candidate_subject_id") or "")
        card = cards.get(subject_id) or {}
        if (
            not subject_id
            or str(card.get("review_status") or "") != "no_visual_evidence"
            or card.get("requires_operator_review") is False
        ):
            continue
        pairs = source["detected_pairs"]
        observations, rejected = _subject_observations(
            subject,
            tracklets,
            frame_positions,
            allowed_pairs=set(pairs),
        )
        selected = _select_representative_crops(observations)
        if observations and not selected:
            rejected["overlaps_nearby_person"] += len(observations)
        source_digest = source["source_ownership_digest"]
        crops = [
            {
                "anchor_crop_id": _crop_id(
                    subject_id,
                    source_digest,
                    row["tracklet_id"],
                    row["frame"],
                ),
                "artifact": _artifact_path(
                    subject_id,
                    source_digest,
                    index,
                    row["frame"],
                ),
                "frame": row["frame"],
                "time_sec": row.get("time_sec"),
                "tracklet_id": row["tracklet_id"],
                "bbox_xyxy": row["bbox_xyxy"],
                "detection_confidence": row.get("detection_confidence"),
                "quality_class": "team_attribution",
                "selection_eligible": True,
                "selection_reasons": row["selection_reasons"],
            }
            for index, row in enumerate(selected, start=1)
        ]
        cases.append(
            {
                "candidate_subject_id": subject_id,
                "scope_kind": source.get("scope_kind"),
                "review_target_id": source.get("review_target_id"),
                "continuity_group_id": source.get("continuity_group_id"),
                "tracklet_ids": sorted(
                    {str(value) for value in subject.get("tracklet_ids") or []}
                ),
                "source_team_label": str(
                    source.get("source_team_label") or subject.get("team_label") or "U"
                ).upper(),
                "source_ownership_digest": source_digest,
                "detected_observation_count": len(pairs),
                "source_observation_pairs": [list(pair) for pair in pairs],
                "status": (
                    "ready_for_team_attribution"
                    if len(crops) >= MIN_CROPS_PER_CASE
                    else "insufficient_team_attribution_evidence"
                    if crops
                    else "no_team_attribution_evidence"
                ),
                "selected_crop_count": len(crops),
                "minimum_required": MIN_CROPS_PER_CASE,
                "anchor_crops": crops,
                "rendered_anchor_crops": [],
                "rejected_observations": dict(sorted(rejected.items())),
                "safety": {
                    "exact_subject_observations_only": True,
                    "does_not_assign_team_automatically": True,
                    "does_not_assign_roster_player_automatically": True,
                    "does_not_mutate_canonical_identity": True,
                },
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "team_attribution_evidence_only",
        "source_inputs_digest": _source_inputs_digest(
            candidate_document,
            tracklets_document,
            roster_review_document,
        ),
        "parameters": {
            "evidence_selection_version": EVIDENCE_SELECTION_VERSION,
            "max_crops_per_case": MAX_CROPS_PER_CASE,
            "minimum_crops_per_case": MIN_CROPS_PER_CASE,
            "min_bbox_width_px": MIN_BBOX_WIDTH_PX,
            "min_bbox_height_px": MIN_BBOX_HEIGHT_PX,
        },
        "summary": {
            "cases": len(cases),
            "source_observations": sum(
                int(row.get("detected_observation_count") or 0) for row in cases
            ),
            "reviewable_cases": sum(
                row.get("status") == "ready_for_team_attribution" for row in cases
            ),
            "unavailable_cases": sum(
                row.get("status") not in {
                    "ready_for_team_attribution",
                    FOCUSED_SOURCE_ALREADY_ACTIONABLE,
                }
                for row in cases
            ),
            "already_actionable_cases": sum(
                row.get("status") == FOCUSED_SOURCE_ALREADY_ACTIONABLE
                for row in cases
            ),
        },
        "cases": cases,
    }


def materialize_team_attribution_evidence(
    match_path: Path,
    *,
    candidate_subject_ids: set[str] | None = None,
    focused_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Render selected team-attribution crops and persist their read-only artifact.

    A focused call is reserved for the terminal coverage recovery path. It
    renders only exact sources that were previously classified as not yet
    materialized, rather than restoring a global crop-render pass after every
    structural Mixed decision.
    """
    candidate_document = _load(match_path / "identity_candidate_shadow.json")
    tracklets_document = _load(match_path / "tracklets.json")
    roster_review_document = _load(
        match_path / "identity_roster_subject_review_shadow.json"
    )
    document = build_team_attribution_evidence(
        candidate_document,
        tracklets_document,
        roster_review_document,
        candidate_subject_ids=candidate_subject_ids,
        focused_sources=focused_sources,
    )
    if focused_sources is not None:
        _append_focused_source_diagnostics(
            document,
            focused_sources,
            candidate_document,
            tracklets_document,
            roster_review_document,
        )
    video_path = match_path / "video.mp4"
    existing = load_team_attribution_evidence(match_path)
    if candidate_subject_ids is None and focused_sources is None and _cached_evidence_is_current(
        existing,
        document,
        match_path,
        video_path,
    ):
        return existing
    if video_path.exists():
        requested = {
            "cards": [
                {"anchor_crops": row.get("anchor_crops") or []}
                for row in document.get("cases") or []
            ]
        }
        rendered = render_identity_roster_anchor_crops(
            video_path,
            match_path,
            requested,
        )
        for row in document.get("cases") or []:
            row["rendered_anchor_crops"] = [
                crop
                for crop in row.get("anchor_crops") or []
                if str(crop.get("artifact") or "") in rendered
            ]
            if (
                row.get("status") == "ready_for_team_attribution"
                and len(row["rendered_anchor_crops"]) < MIN_CROPS_PER_CASE
            ):
                row["status"] = "team_attribution_crops_unavailable"
    else:
        for row in document.get("cases") or []:
            if row.get("status") == "ready_for_team_attribution":
                row["status"] = "source_video_unavailable"
    document["summary"]["rendered_reviewable_cases"] = sum(
        len(row.get("rendered_anchor_crops") or []) >= MIN_CROPS_PER_CASE
        for row in document.get("cases") or []
    )
    if candidate_subject_ids is not None or focused_sources is not None:
        document = _merge_focused_evidence(
            existing,
            document,
            focused_sources=focused_sources,
        )
    write_identity_json_atomic(match_path / FILENAME, document)
    return document


def _append_focused_source_diagnostics(
    document: dict[str, Any],
    focused_sources: list[dict[str, Any]],
    candidate_document: dict[str, Any],
    tracklets_document: dict[str, Any],
    roster_review_document: dict[str, Any],
) -> None:
    """Make every exact focused-source rejection durable and observable.

    Focused recovery is a terminal safety path. A source emitted by progress
    cannot disappear because its current raw ownership no longer normalizes;
    it must receive a specific technical outcome before the one reproject.
    """
    subjects = {
        str(row.get("candidate_subject_id") or ""): row
        for row in candidate_document.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    tracklets = {
        str(row.get("tracklet_id") or ""): row
        for row in tracklets_document.get("tracklets") or []
        if isinstance(row, dict) and row.get("tracklet_id")
    }
    cards = {
        str(row.get("candidate_subject_id") or ""): row
        for row in roster_review_document.get("cards") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    present = {_case_key(row) for row in document.get("cases") or [] if isinstance(row, dict)}
    for source in focused_sources:
        if not isinstance(source, dict) or _source_key(source) in present:
            continue
        status = _focused_source_failure_status(source, subjects, tracklets, cards)
        document.setdefault("cases", []).append({
            "candidate_subject_id": source.get("candidate_subject_id"),
            "scope_kind": source.get("scope_kind"),
            "review_target_id": source.get("review_target_id"),
            "continuity_group_id": source.get("continuity_group_id"),
            "source_team_label": source.get("source_team_label") or "U",
            "source_ownership_digest": source.get("source_ownership_digest"),
            "detected_observation_count": len(source.get("detected_pairs") or []),
            "source_observation_pairs": [list(pair) for pair in source.get("detected_pairs") or []],
            "status": status,
            "materialization_reason": status,
            "selected_crop_count": 0,
            "minimum_required": MIN_CROPS_PER_CASE,
            "anchor_crops": [],
            "rendered_anchor_crops": [],
            "rejected_observations": {status: 1},
            "safety": {
                "exact_subject_observations_only": True,
                "does_not_assign_team_automatically": True,
                "does_not_assign_roster_player_automatically": True,
                "does_not_mutate_canonical_identity": True,
            },
        })
    document["cases"] = sorted(
        [row for row in document.get("cases") or [] if isinstance(row, dict)],
        key=_case_sort_key,
    )


def _focused_source_failure_status(
    source: dict[str, Any],
    subjects: dict[str, dict[str, Any]],
    tracklets: dict[str, dict[str, Any]],
    cards: dict[str, dict[str, Any]],
) -> str:
    subject_id = str(source.get("candidate_subject_id") or "")
    subject = subjects.get(subject_id)
    if subject is None:
        return "focused_source_subject_missing"
    try:
        pairs = sorted({
            (str(pair[0]), int(pair[1]))
            for pair in source.get("detected_pairs") or []
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        })
    except (TypeError, ValueError):
        return "focused_source_pairs_missing"
    if not pairs:
        return "focused_source_pairs_missing"
    if not set(pairs).issubset(set(_source_pairs(subject, tracklets))):
        return "focused_source_pairs_stale"
    if source_ownership_digest(subject_id, pairs) != str(source.get("source_ownership_digest") or ""):
        return "focused_source_digest_mismatch"
    card = cards.get(subject_id) or {}
    if _card_has_exact_safe_operator_visual_evidence(card, pairs):
        return FOCUSED_SOURCE_ALREADY_ACTIONABLE
    if (
        str(card.get("review_status") or "") != "no_visual_evidence"
        or card.get("requires_operator_review") is False
    ):
        return "focused_source_not_reviewable"
    # The normal builder should have emitted this source. Keep the generic
    # safety fallback only for impossible future implementation drift.
    return "team_attribution_evidence_recovery_incomplete"


def _card_has_exact_safe_operator_visual_evidence(
    card: Mapping[str, Any],
    pairs: list[tuple[str, int]],
) -> bool:
    if card.get("requires_operator_review") is False:
        return False
    visual_evidence = card.get("visual_evidence")
    return isinstance(visual_evidence, Mapping) and _has_exact_safe_operator_visual_evidence(
        {
            "has_operator_visual_evidence": True,
            "detected_pairs": pairs,
        },
        visual_evidence,
    )


def _merge_focused_evidence(
    existing: dict[str, Any],
    focused: dict[str, Any],
    *,
    focused_sources: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Replace only freshly requested exact cases in a current evidence file."""
    focused_keys = {
        _case_key(row)
        for row in focused.get("cases") or []
        if isinstance(row, dict)
    }
    requested_keys = {
        _source_key(row)
        for row in focused_sources or []
        if isinstance(row, dict)
    }
    can_retain_existing = (
        existing.get("schema_version") == SCHEMA_VERSION
        and existing.get("source_inputs_digest") == focused.get("source_inputs_digest")
        and (existing.get("parameters") or {}).get("evidence_selection_version")
        == EVIDENCE_SELECTION_VERSION
    )
    retained = (
        [
            row
            for row in existing.get("cases") or []
            if isinstance(row, dict)
            and _case_key(row) not in focused_keys | requested_keys
        ]
        if can_retain_existing
        else []
    )
    cases = sorted(
        [*retained, *(row for row in focused.get("cases") or [] if isinstance(row, dict))],
        key=_case_sort_key,
    )
    merged = {**focused, "cases": cases}
    merged["summary"] = {
        "cases": len(cases),
        "source_observations": sum(
            int(row.get("detected_observation_count") or 0) for row in cases
        ),
        "reviewable_cases": sum(
            row.get("status") == "ready_for_team_attribution" for row in cases
        ),
        "unavailable_cases": sum(
            row.get("status") not in {
                "ready_for_team_attribution",
                FOCUSED_SOURCE_ALREADY_ACTIONABLE,
            }
            for row in cases
        ),
        "already_actionable_cases": sum(
            row.get("status") == FOCUSED_SOURCE_ALREADY_ACTIONABLE
            for row in cases
        ),
        "rendered_reviewable_cases": sum(
            len(row.get("rendered_anchor_crops") or []) >= MIN_CROPS_PER_CASE
            for row in cases
        ),
    }
    return merged


def visual_evidence_for_unit(
    document: dict[str, Any],
    *,
    candidate_subject_id: str,
    detected_pairs: list[tuple[str, int]] | set[tuple[str, int]],
) -> dict[str, Any] | None:
    """Return rendered evidence only when it still belongs to the same inputs."""
    expected_digest = source_ownership_digest(candidate_subject_id, detected_pairs)
    case = next(
        (
            row
            for row in document.get("cases") or []
            if str(row.get("candidate_subject_id") or "") == candidate_subject_id
            and str(row.get("source_ownership_digest") or "") == expected_digest
        ),
        None,
    )
    if not isinstance(case, dict):
        return None
    crops = list(case.get("rendered_anchor_crops") or [])
    if len(crops) < MIN_CROPS_PER_CASE:
        return None
    return {
        "kind": "team_attribution",
        "status": "ready_for_team_attribution",
        "selected_crop_count": len(crops),
        "minimum_required": MIN_CROPS_PER_CASE,
        "anchor_crops": crops,
        "source_ownership_digest": case.get("source_ownership_digest"),
        "safety": case.get("safety"),
    }


def evidence_status_for_unit(
    document: dict[str, Any],
    *,
    candidate_subject_id: str,
    detected_pairs: list[tuple[str, int]] | set[tuple[str, int]],
) -> str:
    """Expose a stable operator-facing reason when exact evidence is absent."""
    expected_digest = source_ownership_digest(candidate_subject_id, detected_pairs)
    case = next(
        (
            row
            for row in document.get("cases") or []
            if str(row.get("candidate_subject_id") or "") == candidate_subject_id
            and str(row.get("source_ownership_digest") or "") == expected_digest
        ),
        None,
    )
    if not isinstance(case, dict):
        return "team_attribution_evidence_not_materialized"
    return str(case.get("status") or "team_attribution_evidence_unavailable")


def _evidence_sources(
    subjects: dict[str, dict[str, Any]],
    tracklets: dict[str, dict[str, Any]],
    *,
    candidate_subject_ids: set[str] | None,
    focused_sources: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return only verified, exact server-owned evidence sources."""
    if focused_sources is not None:
        output = []
        for source in focused_sources:
            normalized = _normalize_focused_source(source, subjects, tracklets)
            if normalized is not None:
                output.append(normalized)
        return sorted(output, key=_case_sort_key)

    output = []
    for subject_id, subject in sorted(subjects.items()):
        if candidate_subject_ids is None and str(subject.get("team_label") or "U").upper() != "U":
            continue
        if candidate_subject_ids is not None and subject_id not in candidate_subject_ids:
            continue
        pairs = _source_pairs(subject, tracklets)
        output.append(
            {
                "subject": subject,
                "detected_pairs": pairs,
                "source_ownership_digest": source_ownership_digest(subject_id, pairs),
                "source_team_label": subject.get("team_label"),
            }
        )
    return output


def _normalize_focused_source(
    source: dict[str, Any],
    subjects: dict[str, dict[str, Any]],
    tracklets: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Reject stale or broadened focused ownership before rendering crops."""
    subject_id = str(source.get("candidate_subject_id") or "")
    subject = subjects.get(subject_id)
    if subject is None:
        return None
    try:
        pairs = sorted(
            {
                (str(pair[0]), int(pair[1]))
                for pair in source.get("detected_pairs") or []
                if isinstance(pair, (list, tuple)) and len(pair) >= 2
            }
        )
    except (TypeError, ValueError):
        return None
    if not pairs or not set(pairs).issubset(set(_source_pairs(subject, tracklets))):
        return None
    digest = source_ownership_digest(subject_id, pairs)
    if digest != str(source.get("source_ownership_digest") or ""):
        return None
    return {
        "subject": subject,
        "detected_pairs": pairs,
        "source_ownership_digest": digest,
        "source_team_label": source.get("source_team_label"),
        "scope_kind": source.get("scope_kind"),
        "review_target_id": source.get("review_target_id"),
        "continuity_group_id": source.get("continuity_group_id"),
    }


def _source_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("candidate_subject_id") or ""),
        str(row.get("source_ownership_digest") or ""),
    )


def _case_key(row: dict[str, Any]) -> tuple[str, str]:
    return _source_key(row)


def _case_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return _case_key(row)


def _subject_observations(
    subject: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
    frame_positions: dict[int, list[dict[str, Any]]],
    *,
    allowed_pairs: set[tuple[str, int]] | None = None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    observations: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for tracklet_id in sorted({str(value) for value in subject.get("tracklet_ids") or []}):
        for position in tracklets.get(tracklet_id, {}).get("positions_m") or []:
            if not is_real_detected_position(position):
                rejected["not_detected"] += 1
                continue
            if not is_on_pitch_product_observation(position):
                rejected["outside_play_area"] += 1
                continue
            bbox = position.get("bbox_xyxy") or []
            if len(bbox) != 4:
                rejected["invalid_bbox"] += 1
                continue
            try:
                x1, y1, x2, y2 = (float(value) for value in bbox)
            except (TypeError, ValueError):
                rejected["invalid_bbox"] += 1
                continue
            if x2 - x1 < MIN_BBOX_WIDTH_PX or y2 - y1 < MIN_BBOX_HEIGHT_PX:
                rejected["bbox_too_small"] += 1
                continue
            frame = int(position.get("frame") or 0)
            if allowed_pairs is not None and (tracklet_id, frame) not in allowed_pairs:
                continue
            overlap = _max_overlap(
                bbox,
                [row for row in frame_positions.get(frame, []) if row["tracklet_id"] != tracklet_id],
            )
            observations.append(
                {
                    "tracklet_id": tracklet_id,
                    "frame": frame,
                    "time_sec": _round_or_none(position.get("time_sec"), 3),
                    "bbox_xyxy": [round(value, 2) for value in (x1, y1, x2, y2)],
                    "detection_confidence": _round_or_none(position.get("confidence"), 4),
                    "overlap": overlap,
                    "selection_score": _selection_score(bbox, position.get("confidence"), overlap),
                    "selection_reasons": [
                        "exact_detected_inside_play_observation",
                        "team_attribution_evidence",
                        *( ["overlap_penalized"] if overlap > 0 else [] ),
                    ],
                }
            )
    observations.sort(key=lambda row: (row["frame"], row["tracklet_id"]))
    return observations, rejected


def _source_pairs(
    subject: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
) -> list[tuple[str, int]]:
    return sorted(
        {
            (tracklet_id, int(position.get("frame") or 0))
            for tracklet_id in {str(value) for value in subject.get("tracklet_ids") or []}
            for position in tracklets.get(tracklet_id, {}).get("positions_m") or []
            if is_real_detected_position(position)
            and is_on_pitch_product_observation(position)
        }
    )


def _frame_positions(tracklets: dict[str, dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for tracklet_id, tracklet in tracklets.items():
        for position in tracklet.get("positions_m") or []:
            if not is_real_detected_position(position) or not is_on_pitch_product_observation(position):
                continue
            bbox = position.get("bbox_xyxy") or []
            if len(bbox) == 4:
                try:
                    normalized_bbox = [float(value) for value in bbox]
                except (TypeError, ValueError):
                    continue
                index[int(position.get("frame") or 0)].append(
                    {"tracklet_id": tracklet_id, "bbox_xyxy": normalized_bbox}
                )
    return index


def _select_representative_crops(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not observations:
        return []
    non_duplicate = [
        row for row in observations if float(row.get("overlap") or 0.0) < UNUSABLE_MAX_OVERLAP
    ]
    if not non_duplicate:
        return []
    clean = [
        row for row in non_duplicate if float(row.get("overlap") or 0.0) <= PREFERRED_MAX_OVERLAP
    ]
    pool = clean if len(clean) >= MIN_CROPS_PER_CASE else non_duplicate
    count = min(MAX_CROPS_PER_CASE, len(pool))
    if count <= MIN_CROPS_PER_CASE:
        return list(pool)
    selected: list[dict[str, Any]] = []
    for index in range(count):
        start = round(index * len(pool) / count)
        end = round((index + 1) * len(pool) / count)
        bucket = pool[start:max(start + 1, end)]
        selected.append(max(bucket, key=lambda row: (row["selection_score"], -row["frame"])))
    return sorted(selected, key=lambda row: (row["frame"], row["tracklet_id"]))


def _max_overlap(bbox: list[Any], others: list[dict[str, Any]]) -> float:
    x1, y1, x2, y2 = (float(value) for value in bbox)
    area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if area <= 0:
        return 0.0
    maximum = 0.0
    for row in others:
        ox1, oy1, ox2, oy2 = (float(value) for value in row.get("bbox_xyxy") or [])
        intersection = max(0.0, min(x2, ox2) - max(x1, ox1)) * max(
            0.0, min(y2, oy2) - max(y1, oy1)
        )
        maximum = max(maximum, intersection / area)
    return maximum


def _selection_score(bbox: list[Any], confidence: Any, overlap: float) -> float:
    width = max(0.0, float(bbox[2]) - float(bbox[0]))
    height = max(0.0, float(bbox[3]) - float(bbox[1]))
    return round((width * height) ** 0.5 + 40.0 * float(confidence or 0.0) - 20.0 * overlap, 6)


def source_ownership_digest(subject_id: str, pairs: Any) -> str:
    normalized = sorted({(str(pair[0]), int(pair[1])) for pair in pairs})
    payload = json.dumps(
        {"candidate_subject_id": subject_id, "detected_pairs": normalized},
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _crop_id(
    subject_id: str,
    source_digest: str,
    tracklet_id: str,
    frame: int,
) -> str:
    return "team-attribution:" + hashlib.sha256(
        f"{subject_id}|{source_digest}|{tracklet_id}|{frame}".encode("utf-8")
    ).hexdigest()[:24]


def _artifact_path(subject_id: str, source_digest: str, index: int, frame: int) -> str:
    safe_subject = "".join(char if char.isalnum() or char in "-_" else "-" for char in subject_id)
    return (
        f"team_attribution_evidence/{safe_subject}/{source_digest[:16]}"
        f"/{index:02d}_frame_{frame:06d}.jpg"
    )


def _round_or_none(value: Any, digits: int) -> float | None:
    return round(float(value), digits) if value is not None else None


def _load(path: Path) -> dict[str, Any]:
    value = load_json_cached_or(path, {})
    return value if isinstance(value, dict) else {}


def resolve_current_team_attribution_sources(
    match_path: Path,
    descriptors: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Resolve durable technical descriptors to current exact source pairs.

    Durable progress intentionally omits raw observation pairs. Whole-subject
    ownership is safely recoverable from the current canonical subject and
    tracklet inputs only when the recomputed pair digest equals the persisted
    evidence digest. Other scopes or any mismatch return ``None`` so callers
    use the established full authoritative-progress fallback.
    """
    if not descriptors:
        return None
    candidate_document = _load(match_path / "identity_candidate_shadow.json")
    tracklets_document = _load(match_path / "tracklets.json")
    subjects = {
        str(row.get("candidate_subject_id") or ""): row
        for row in candidate_document.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    tracklets = {
        str(row.get("tracklet_id") or ""): row
        for row in tracklets_document.get("tracklets") or []
        if isinstance(row, dict) and row.get("tracklet_id")
    }
    resolved: list[dict[str, Any]] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            return None
        if str(descriptor.get("scope_kind") or "whole_subject") != "whole_subject":
            return None
        subject_id = str(descriptor.get("candidate_subject_id") or "")
        expected_digest = str(
            descriptor.get("team_attribution_evidence_source_digest") or ""
        )
        subject = subjects.get(subject_id)
        if not subject_id or not expected_digest or not isinstance(subject, dict):
            return None
        pairs = _source_pairs(subject, tracklets)
        if not pairs or source_ownership_digest(subject_id, pairs) != expected_digest:
            return None
        resolved.append({
            "candidate_subject_id": subject_id,
            "scope_kind": "whole_subject",
            "review_target_id": None,
            "continuity_group_id": None,
            "source_team_label": subject.get("team_label"),
            "source_ownership_digest": expected_digest,
            "detected_pairs": pairs,
        })
    return sorted(resolved, key=_case_sort_key)


def _source_inputs_digest(
    candidate_document: dict[str, Any],
    tracklets_document: dict[str, Any],
    roster_review_document: dict[str, Any],
) -> str:
    """Hash only the artifacts that determine crop ownership and eligibility."""
    payload = {
        "subjects": candidate_document.get("subjects") or [],
        "tracklets": tracklets_document.get("tracklets") or [],
        "review_cards": [
            {
                "candidate_subject_id": row.get("candidate_subject_id"),
                "review_status": row.get("review_status"),
                "requires_operator_review": row.get("requires_operator_review"),
            }
            for row in roster_review_document.get("cards") or []
            if row.get("candidate_subject_id")
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cached_evidence_is_current(
    existing: dict[str, Any],
    expected: dict[str, Any],
    match_path: Path,
    video_path: Path,
) -> bool:
    if (
        existing.get("schema_version") != SCHEMA_VERSION
        or existing.get("source_inputs_digest") != expected.get("source_inputs_digest")
        or (existing.get("parameters") or {}).get("evidence_selection_version")
        != EVIDENCE_SELECTION_VERSION
    ):
        return False
    expected_cases = {_case_key(row): row for row in expected.get("cases") or []}
    existing_cases = {_case_key(row): row for row in existing.get("cases") or []}
    if set(expected_cases) != set(existing_cases):
        return False
    for source_key, expected_case in expected_cases.items():
        existing_case = existing_cases[source_key]
        if expected_case.get("status") != "ready_for_team_attribution":
            # If a source video has appeared since a prior unavailable result,
            # retry the lightweight render rather than retaining the blocker.
            if video_path.exists():
                return False
            continue
        rendered = list(existing_case.get("rendered_anchor_crops") or [])
        if len(rendered) < MIN_CROPS_PER_CASE or any(
            not (match_path / str(crop.get("artifact") or "")).is_file()
            for crop in rendered
        ):
            return False
    return True
