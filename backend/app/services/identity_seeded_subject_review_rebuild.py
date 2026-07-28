from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.identity_approved_appearance_gallery import (
    build_identity_approved_appearance_gallery,
)
from app.services.identity_approved_appearance_reid import (
    build_identity_approved_appearance_reid,
    load_approved_appearance_embedder,
)
from app.services.identity_initial_audit_store import (
    find_identity_artifact,
    load_identity_json,
    production_identity_snapshot,
    write_identity_json_atomic,
)
from app.services.identity_roster_anchor_crop_renderer import (
    render_identity_roster_anchor_crops,
)
from app.services.identity_roster_anchor_crops_shadow import (
    build_identity_roster_anchor_crops_shadow,
)
from app.services.identity_roster_anchor_shadow import (
    build_identity_roster_anchor_shadow,
)
from app.services.identity_roster_subject_review_shadow import (
    build_identity_roster_subject_review_shadow,
)
from app.services.identity_roster_subject_review_store import (
    load_identity_roster_subject_review,
)
from app.services.identity_same_match_reid import (
    JsonEmbeddingCache,
)


CANDIDATE_FILENAME = "identity_candidate_shadow.json"
TIMELINE_FILENAME = "identity_offline_shadow_timeline.json"
SEEDED_ASSIGNMENTS_FILENAME = "identity_seeded_candidate_assignments.json"
OCCLUSIONS_FILENAME = "identity_occlusion_events.json"
REID_FUSION_FILENAME = "identity_same_match_reid_fusion_shadow.json"
JERSEY_CONSENSUS_FILENAME = "identity_jersey_number_consensus_shadow.json"
MATCH_PHASE_FILENAME = "match_phase_config.json"
APPROVED_APPEARANCE_CACHE_FILENAME = (
    "identity_approved_appearance_embeddings_cache.json"
)


def seeded_assignments_as_roster_assignments(
    candidate_doc: dict[str, Any],
    seeded_assignments_doc: dict[str, Any],
) -> dict[str, Any]:
    """Adapt certain observation seeds to the existing roster-anchor contract."""
    candidates = {
        str(row.get("candidate_subject_id")): row
        for row in candidate_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    assignments: list[dict[str, Any]] = []
    unresolved_subjects: list[str] = []
    for accepted in seeded_assignments_doc.get("accepted_assignments") or []:
        if not isinstance(accepted, dict):
            continue
        candidate_id = str(accepted.get("candidate_subject_id") or "")
        candidate = candidates.get(candidate_id)
        assigned_player = accepted.get("assigned_player") or {}
        player_id = str(assigned_player.get("player_id") or "")
        if candidate is None or not player_id:
            if candidate_id:
                unresolved_subjects.append(candidate_id)
            continue
        observation_keys = sorted(
            str(row.get("observation_key"))
            for row in accepted.get("seed_observations") or []
            if isinstance(row, dict) and row.get("observation_key")
        )
        for stable_subject_id in sorted(
            str(value)
            for value in candidate.get("production_subject_ids") or []
            if value
        ):
            assignments.append(
                {
                    "stint_id": (
                        f"initial-audit:{candidate_id}:{stable_subject_id}"
                    ),
                    "status": "assigned",
                    "player_id": player_id,
                    "player_name": assigned_player.get("player_name"),
                    "team_label": str(
                        assigned_player.get("team_label")
                        or accepted.get("team_label")
                        or candidate.get("team_label")
                        or "U"
                    ),
                    "stable_subject_id": stable_subject_id,
                    "start_frame": int(
                        accepted.get("start_frame")
                        if accepted.get("start_frame") is not None
                        else candidate.get("start_frame") or 0
                    ),
                    "end_frame": int(
                        accepted.get("end_frame")
                        if accepted.get("end_frame") is not None
                        else candidate.get("end_frame") or 0
                    ),
                    "anchor_artifacts": observation_keys,
                    "anchor_confidence": 1.0,
                    "assignment_source": "initial_identity_audit",
                    "candidate_subject_id": candidate_id,
                }
            )
    return {
        "schema_version": "0.1.0",
        "mode": "initial_identity_audit_shadow_adapter",
        "source": {
            "candidate_algorithm": candidate_doc.get("algorithm") or {},
            "seeded_assignments_algorithm": (
                seeded_assignments_doc.get("algorithm") or {}
            ),
        },
        "summary": {
            "assignments": len(assignments),
            "accepted_subjects": len(
                {
                    str(row.get("candidate_subject_id"))
                    for row in seeded_assignments_doc.get(
                        "accepted_assignments"
                    )
                    or []
                    if isinstance(row, dict) and row.get("candidate_subject_id")
                }
            ),
            "unresolved_subjects": len(set(unresolved_subjects)),
        },
        "assignments": assignments,
        "unresolved_candidate_subject_ids": sorted(set(unresolved_subjects)),
    }


def rebuild_identity_seeded_subject_review(
    match_path: Path,
    match_document: dict[str, Any],
    *,
    video_path: Path | None = None,
    crop_renderer: Callable[[Path, Path, dict[str, Any]], set[str]] = (
        render_identity_roster_anchor_crops
    ),
    appearance_embedder_loader: Callable[
        [Path], tuple[Any | None, dict[str, Any]]
    ] = load_approved_appearance_embedder,
) -> dict[str, Any]:
    """Rebuild the whole-subject review from frozen identity artifacts."""
    generated_at = datetime.now(timezone.utc).isoformat()
    production_before = production_identity_snapshot(match_path, match_document)
    candidate_doc = _required_document(
        match_path,
        match_document,
        CANDIDATE_FILENAME,
    )
    timeline_doc = _required_document(
        match_path,
        match_document,
        TIMELINE_FILENAME,
    )
    seeded_doc = _required_document(
        match_path,
        match_document,
        SEEDED_ASSIGNMENTS_FILENAME,
    )
    occlusion_doc = _optional_document(
        match_path,
        match_document,
        OCCLUSIONS_FILENAME,
    )
    reid_doc = _optional_document(
        match_path,
        match_document,
        REID_FUSION_FILENAME,
    )
    jersey_doc = _optional_document(
        match_path,
        match_document,
        JERSEY_CONSENSUS_FILENAME,
    )
    match_phase_doc = _optional_document(
        match_path,
        match_document,
        MATCH_PHASE_FILENAME,
    )

    assignments_doc = seeded_assignments_as_roster_assignments(
        candidate_doc,
        seeded_doc,
    )
    roster_documents = build_identity_roster_anchor_shadow(
        candidate_doc,
        assignments_doc,
        match_document,
        reid_fusion_doc=reid_doc,
        generated_at=generated_at,
    )
    roster_artifact = roster_documents["identity_roster_anchor_shadow"]
    crop_documents = build_identity_roster_anchor_crops_shadow(
        roster_artifact,
        timeline_doc,
        occlusion_doc=occlusion_doc,
        generated_at=generated_at,
    )
    crop_artifact = crop_documents["identity_roster_anchor_crops_shadow"]
    review_documents = build_identity_roster_subject_review_shadow(
        roster_artifact,
        crop_artifact,
        jersey_consensus_doc=jersey_doc,
        generated_at=generated_at,
    )

    documents = {
        **roster_documents,
        **crop_documents,
        **review_documents,
    }
    for name, document in documents.items():
        write_identity_json_atomic(match_path / f"{name}.json", document)

    rendered: set[str] = set()
    if video_path is not None and video_path.exists():
        rendered = crop_renderer(video_path, match_path, crop_artifact)

    appearance_warnings: list[dict[str, str]] = []
    appearance_gallery_summary: dict[str, Any] = {}
    appearance_reid_summary: dict[str, Any] = {}
    try:
        gallery_documents = build_identity_approved_appearance_gallery(
            seeded_doc,
            crop_artifact,
            match_phase_config_doc=match_phase_doc,
            generated_at=generated_at,
        )
        for name, document in gallery_documents.items():
            write_identity_json_atomic(match_path / f"{name}.json", document)
        gallery_artifact = gallery_documents[
            "identity_approved_appearance_gallery"
        ]
        appearance_gallery_summary = gallery_artifact.get("summary") or {}

        try:
            embedder, model_status = appearance_embedder_loader(
                Path(__file__).resolve().parents[2] / "models"
            )
        except Exception as exc:
            embedder = None
            model_status = {
                "available": False,
                "reason": "appearance_embedder_load_failed",
                "error": str(exc),
            }
        embedding_cache = (
            JsonEmbeddingCache.load(
                match_path / APPROVED_APPEARANCE_CACHE_FILENAME,
                model_name=str(embedder.model_name),
                model_version=str(embedder.model_version),
                embedding_dimension=int(embedder.embedding_dimension),
            )
            if embedder is not None
            else None
        )
        reid_documents = build_identity_approved_appearance_reid(
            gallery_artifact,
            crop_artifact,
            match_path=match_path,
            embedder=embedder,
            model_status=model_status,
            embedding_cache=embedding_cache,
            generated_at=generated_at,
        )
        for name, document in reid_documents.items():
            write_identity_json_atomic(match_path / f"{name}.json", document)
        appearance_reid_summary = reid_documents[
            "identity_approved_appearance_reid_shadow"
        ].get("summary") or {}
    except Exception as exc:
        appearance_warnings.append(
            {
                "code": "approved_appearance_shadow_failed",
                "message": str(exc),
            }
        )

    reduced_review = load_identity_roster_subject_review(
        match_path,
        match_doc=match_document,
    )
    production_after = production_identity_snapshot(match_path, match_document)
    if production_before != production_after:
        raise RuntimeError(
            "Shadow Initial Identity Audit rebuild changed production identity"
        )

    integration = reduced_review.get("initial_audit_integration") or {}
    return {
        "status": "fresh",
        "generated_at": generated_at,
        "summary": reduced_review.get("summary") or {},
        "initial_audit_integration": integration,
        "seed_adapter_summary": assignments_doc.get("summary") or {},
        "rendered_anchor_crops": len(rendered),
        "approved_appearance_gallery_summary": (
            appearance_gallery_summary
        ),
        "approved_appearance_reid_summary": appearance_reid_summary,
        "warnings": appearance_warnings,
        "safety": {
            "production_identity_untouched": True,
            "reran_yolo": False,
            "reran_tracking": False,
            "rendered_full_overlay": False,
            "shadow_artifacts_only": True,
        },
    }


def _required_document(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> dict[str, Any]:
    path = find_identity_artifact(match_path, match_document, filename)
    if path is None:
        raise FileNotFoundError(f"Missing required identity artifact: {filename}")
    return load_identity_json(path)


def _optional_document(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> dict[str, Any] | None:
    path = find_identity_artifact(match_path, match_document, filename)
    return load_identity_json(path) if path is not None else None
