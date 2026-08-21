from __future__ import annotations

"""Exact, server-owned observation sources for Reviewed Identity actions."""

from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_mixed_store import (
    current_mixed_subject_digest,
    observations_for_case,
    render_mixed_review_evidence,
    temporal_evidence_for_observations,
)
from app.services.identity_reviewed_segments import build_segment_review_document, load_segment_review, target_for_id


class ReviewedIdentityReviewSourceError(ValueError):
    """The requested correction source no longer has its exact ownership."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def resolve_review_source(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    candidate_subject_id: str,
    review_target_id: str | None = None,
    source_ownership_digest: str | None = None,
    continuity_group_id: str | None = None,
) -> dict[str, Any]:
    """Rebuild the exact parent set; callers never supply observation pairs."""
    if review_target_id:
        review = load_segment_review(match_path) or build_segment_review_document(match_path, match_doc)
        target = target_for_id(review, review_target_id)
        if not isinstance(target, dict) or str(target.get("candidate_subject_id") or "") != candidate_subject_id:
            raise ReviewedIdentityReviewSourceError("review_target_stale")
        digest = str(target.get("source_ownership_digest") or "")
        if not digest or source_ownership_digest != digest:
            raise ReviewedIdentityReviewSourceError("review_target_stale")
        return _from_owned_observations(
            match_path,
            candidate_subject_id=candidate_subject_id,
            scope_kind="canonical_segment",
            digest=digest,
            review_target_id=review_target_id,
            owned_observations=list(target.get("owned_observations") or []),
            source_team_label=str(target.get("effective_team_label") or target.get("source_team_label") or "U"),
        )
    if continuity_group_id or candidate_subject_id.startswith("continuity:"):
        from app.services.identity_reviewed_progress import build_reviewed_identity_progress

        group_id = continuity_group_id or candidate_subject_id
        progress = build_reviewed_identity_progress(match_path, match_doc, include_internal_units=True)
        unit = next(
            (
                row for row in progress.get("_internal_review_units") or []
                if isinstance(row, dict)
                and row.get("scope_kind") == "material_continuity"
                and str(row.get("continuity_group_id") or "") == group_id
            ),
            None,
        )
        digest = str((unit or {}).get("source_ownership_digest") or "")
        if not isinstance(unit, dict):
            # A resolved inline split intentionally suppresses recreation of
            # its material parent. Its persisted exact source is still the
            # server-owned authority required to edit it or mark it complex.
            from app.services.identity_reviewed_mixed_store import load_mixed_player_cases

            stored_source = next(
                (
                    row.get("source")
                    for row in load_mixed_player_cases(match_path).get("cases") or []
                    if str(row.get("original_issue") or "") == "inline_temporal_split"
                    and isinstance(row.get("source"), dict)
                    and str((row.get("source") or {}).get("scope_kind") or "")
                    == "material_continuity"
                    and str((row.get("source") or {}).get("candidate_subject_id") or "")
                    == candidate_subject_id
                    and str((row.get("source") or {}).get("continuity_group_id") or "")
                    == group_id
                    and str((row.get("source") or {}).get("source_ownership_digest") or "")
                    == str(source_ownership_digest or "")
                ),
                None,
            )
            if not isinstance(stored_source, dict):
                raise ReviewedIdentityReviewSourceError("material_continuity_target_stale")
            return _from_owned_observations(
                match_path,
                candidate_subject_id=candidate_subject_id,
                scope_kind="material_continuity",
                digest=str(stored_source["source_ownership_digest"]),
                continuity_group_id=group_id,
                owned_observations=list(stored_source.get("owned_observations") or []),
                source_team_label=str(stored_source.get("source_team_label") or "U"),
            )
        if not digest or source_ownership_digest != digest:
            raise ReviewedIdentityReviewSourceError("material_continuity_target_stale")
        return _from_owned_observations(
            match_path,
            candidate_subject_id=candidate_subject_id,
            scope_kind="material_continuity",
            digest=digest,
            continuity_group_id=group_id,
            owned_observations=list(unit.get("owned_observations") or []),
            source_team_label=str(unit.get("effective_team_label") or "U"),
        )

    # The legacy source helper already applies real-detection and pitch safety
    # filters. Reuse it by giving it the minimal compatible case shape.
    current_digest = current_mixed_subject_digest(match_path, candidate_subject_id)
    if source_ownership_digest and source_ownership_digest != current_digest:
        raise ReviewedIdentityReviewSourceError("review_target_stale")
    observations = observations_for_case(match_path, {"candidate_subject_id": candidate_subject_id})
    if not observations:
        raise ReviewedIdentityReviewSourceError("review_source_empty")
    return _source_document(
        candidate_subject_id=candidate_subject_id,
        scope_kind="whole_subject",
        digest=current_digest,
        observations=observations,
    )


def source_case_id(source: dict[str, Any]) -> str:
    """Stable key for a generic split decision over one exact parent source."""
    return "inline-temporal-split:v1:" + canonical_digest(
        {
            "scope_kind": source["scope_kind"],
            "candidate_subject_id": source["candidate_subject_id"],
            "review_target_id": source.get("review_target_id"),
            "continuity_group_id": source.get("continuity_group_id"),
            "source_ownership_digest": source["source_ownership_digest"],
        }
    )


def build_review_source_boundary_refinement(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    candidate_subject_id: str,
    review_target_id: str | None,
    continuity_group_id: str | None,
    source_ownership_digest: str,
    after_frame: int,
    before_frame: int,
    limit: int = 10,
) -> dict[str, Any]:
    """Return denser evidence for one adjacent pair from an inline split UI."""
    source = resolve_review_source(
        match_path,
        match_doc,
        candidate_subject_id=candidate_subject_id,
        review_target_id=review_target_id,
        continuity_group_id=continuity_group_id,
        source_ownership_digest=source_ownership_digest,
    )
    if after_frame >= before_frame:
        raise ValueError("Refinement interval must have increasing frame boundaries")
    overview = temporal_evidence_for_observations(
        candidate_subject_id,
        list(source["observations"]),
        limit=12,
    )
    overview_frames = [int(crop["frame"]) for crop in overview]
    if (after_frame, before_frame) not in set(zip(overview_frames, overview_frames[1:])):
        raise ValueError("Refinement interval must use neighboring overview samples")
    interval = [
        row for row in source["observations"]
        if after_frame < int(row["frame"]) <= before_frame
    ]
    if not interval:
        raise ValueError("No detected observations in the selected refinement interval")
    crops = temporal_evidence_for_observations(
        candidate_subject_id,
        interval,
        limit=max(3, min(limit, 16)),
    )
    render_mixed_review_evidence(
        match_path,
        match_doc,
        {"cases": [{"temporal_evidence": {"anchor_crops": crops}}]},
    )
    return {
        "schema_version": "1.0.0",
        "mode": "reviewed_identity_inline_boundary_refinement",
        "match_id": str(match_doc.get("id") or match_path.name),
        "candidate_subject_id": candidate_subject_id,
        "review_target_id": review_target_id,
        "continuity_group_id": continuity_group_id,
        "source_ownership_digest": source["source_ownership_digest"],
        "after_frame": after_frame,
        "before_frame": before_frame,
        "anchor_crops": crops,
    }


def _from_owned_observations(
    match_path: Path,
    *,
    candidate_subject_id: str,
    scope_kind: str,
    digest: str,
    owned_observations: list[dict[str, Any]],
    source_team_label: str,
    review_target_id: str | None = None,
    continuity_group_id: str | None = None,
) -> dict[str, Any]:
    wanted = {
        (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
        for row in owned_observations
        if isinstance(row, dict) and row.get("tracklet_id") is not None and row.get("frame") is not None
    }
    observations = _observations_by_pair(match_path, wanted)
    if not wanted or set((str(row["tracklet_id"]), int(row["frame"])) for row in observations) != wanted:
        raise ReviewedIdentityReviewSourceError("review_target_stale")
    return _source_document(
        candidate_subject_id=candidate_subject_id,
        scope_kind=scope_kind,
        digest=digest,
        observations=observations,
        source_team_label=source_team_label,
        review_target_id=review_target_id,
        continuity_group_id=continuity_group_id,
    )


def _source_document(
    *,
    candidate_subject_id: str,
    scope_kind: str,
    digest: str,
    observations: list[dict[str, Any]],
    source_team_label: str | None = None,
    review_target_id: str | None = None,
    continuity_group_id: str | None = None,
) -> dict[str, Any]:
    ordered = sorted(observations, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))
    if not ordered:
        raise ReviewedIdentityReviewSourceError("review_source_empty")
    teams = {str(row.get("team_label") or "U").upper() for row in ordered}
    return {
        "candidate_subject_id": candidate_subject_id,
        "scope_kind": scope_kind,
        "review_target_id": review_target_id,
        "continuity_group_id": continuity_group_id,
        "source_ownership_digest": digest,
        "source_team_label": source_team_label or (next(iter(teams)) if len(teams) == 1 else "U"),
        "observations": ordered,
        "owned_observations": [
            {"tracklet_id": str(row["tracklet_id"]), "frame": int(row["frame"])}
            for row in ordered
        ],
        "frame_start": int(ordered[0]["frame"]),
        "frame_end": int(ordered[-1]["frame"]),
        "detected_observation_count": len(ordered),
    }


def _observations_by_pair(match_path: Path, wanted: set[tuple[str, int]]) -> list[dict[str, Any]]:
    import json

    tracklets = json.loads((match_path / "tracklets.json").read_text(encoding="utf-8")).get("tracklets") or []
    output: list[dict[str, Any]] = []
    for tracklet in tracklets:
        tracklet_id = str(tracklet.get("tracklet_id") or "")
        for position in tracklet.get("positions_m") or []:
            pair = (tracklet_id, int(position.get("frame") or 0))
            if pair in wanted:
                output.append({**position, "tracklet_id": tracklet_id, "team_label": str(tracklet.get("team_label") or "U")})
    return output
