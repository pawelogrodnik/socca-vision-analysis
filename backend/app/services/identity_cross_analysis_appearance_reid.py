from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.services.identity_approved_appearance_reid import (
    DEFAULT_PARAMETERS,
    JsonEmbeddingCache,
    _embed_candidate_subjects,
    _player_profiles,
    _prototype,
    _rank_unresolved_subjects,
)
from app.services.identity_same_match_reid import PersonReIdEmbedder


SCHEMA_VERSION = "0.1.0"
MODE = "cross_analysis_h1_to_h2_advisory_only"


def build_cross_analysis_appearance_reid(
    reference_gallery: dict[str, Any],
    target_anchor_crops: dict[str, Any],
    target_seeded_assignments: dict[str, Any],
    *,
    reference_match_path: Path,
    target_match_path: Path,
    embedder: PersonReIdEmbedder | None,
    model_status: dict[str, Any],
    reference_embedding_cache: JsonEmbeddingCache | None = None,
    target_embedding_cache: JsonEmbeddingCache | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Rank unresolved H2 subjects using crops physically stored in H1.

    Reference and target crops intentionally have separate roots.  Treating
    H1 gallery artifact paths as H2-local paths silently embedded unrelated
    pixels and made cross-capture identity evidence invalid.
    """

    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    if embedder is None:
        return _documents(
            _unavailable_artifact(
                reference_gallery,
                target_anchor_crops,
                model_status=model_status,
                generated_at=generated,
                parameters=params,
            )
        )

    reference_gallery_for_embedding, reference_anchor_crops = (
        _reference_embedding_inputs(reference_gallery)
    )
    reference_vectors, reference_rows, reference_rejected = (
        _embed_candidate_subjects(
            reference_anchor_crops,
            match_path=reference_match_path,
            embedder=embedder,
            embedding_cache=reference_embedding_cache,
            parameters=params,
        )
    )
    target_vectors, target_rows, target_rejected = _embed_candidate_subjects(
        target_anchor_crops,
        match_path=target_match_path,
        embedder=embedder,
        embedding_cache=target_embedding_cache,
        parameters=params,
    )
    if reference_embedding_cache is not None:
        reference_embedding_cache.save()
    if target_embedding_cache is not None:
        target_embedding_cache.save()

    player_profiles = _player_profiles(
        reference_gallery_for_embedding,
        subject_vectors=reference_vectors,
        embedding_rows=reference_rows,
        parameters=params,
    )
    player_prototypes = {
        str(player["player_id"]): np.asarray(
            player["prototype"], dtype=np.float32
        )
        for player in player_profiles
        if player.get("status") == "ready" and player.get("prototype")
    }
    target_subject_prototypes = {
        subject_id: _prototype(vectors)
        for subject_id, vectors in target_vectors.items()
        if len(vectors) >= int(params["min_embeddings_per_subject"])
    }
    accepted_target_subjects = {
        str(assignment.get("candidate_subject_id") or ""): {
            "player_id": (assignment.get("assigned_player") or {}).get(
                "player_id"
            ),
            "player_name": (assignment.get("assigned_player") or {}).get(
                "player_name"
            ),
            "team_label": assignment.get("team_label"),
        }
        for assignment in target_seeded_assignments.get(
            "accepted_assignments"
        )
        or []
        if isinstance(assignment, dict)
        and assignment.get("candidate_subject_id")
    }
    rankings = _rank_unresolved_subjects(
        target_anchor_crops,
        subject_prototypes=target_subject_prototypes,
        accepted_subject_to_player=accepted_target_subjects,
        player_profiles=player_profiles,
        player_prototypes=player_prototypes,
        parameters=params,
    )
    ranked_rows = [row for row in rankings if row.get("status") == "ranked"]
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": MODE,
        "algorithm": {
            "name": "identity_cross_analysis_appearance_reid",
            "version": "0.1.0",
            "parameters": params,
        },
        "model": model_status,
        "source": {
            "reference_gallery_algorithm": (
                reference_gallery.get("algorithm") or {}
            ),
            "target_anchor_crops_algorithm": (
                target_anchor_crops.get("algorithm") or {}
            ),
            "reference_root": str(reference_match_path),
            "target_root": str(target_match_path),
        },
        "safety": {
            "advisory_only": True,
            "team_safe_ranking": True,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_merges": 0,
            "eligible_for_player_stats": False,
        },
        "summary": {
            "reference_embedded_crops": len(reference_rows),
            "target_embedded_crops": len(target_rows),
            "embedded_crops": len(reference_rows) + len(target_rows),
            "rejected_crops": sum(reference_rejected.values())
            + sum(target_rejected.values()),
            "candidate_subjects_with_prototype": len(
                target_subject_prototypes
            ),
            "players_with_prototype": len(player_prototypes),
            "unresolved_subjects_ranked": len(ranked_rows),
            "cross_domain_players": 0,
            "automatic_merges": 0,
            "operator_actions_required": 0,
        },
        "rejected_crops": {
            "reference": dict(sorted(reference_rejected.items())),
            "target": dict(sorted(target_rejected.items())),
        },
        "player_profiles": player_profiles,
        "unresolved_rankings": rankings,
        "cross_domain_evidence": [],
        "evaluation": {
            "method": "cross_capture_advisory_without_h2_ground_truth",
            "queries": 0,
            "top1_accuracy": None,
            "top3_accuracy": None,
            "rows": [],
        },
    }
    return _documents(artifact)


def _reference_embedding_inputs(
    gallery: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    players: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for player in gallery.get("players") or []:
        if not isinstance(player, dict):
            continue
        player_id = str(player.get("player_id") or "")
        if not player_id:
            continue
        reference_subject_id = f"reference-player:{player_id}"
        domains: list[dict[str, Any]] = []
        crops: list[dict[str, Any]] = []
        for domain in player.get("capture_domains") or []:
            if not isinstance(domain, dict):
                continue
            domain_crops = [
                {
                    **crop,
                    "candidate_subject_id": reference_subject_id,
                }
                for crop in domain.get("crops") or []
                if isinstance(crop, dict) and crop.get("artifact")
            ]
            domains.append({**domain, "crops": domain_crops})
            crops.extend(domain_crops)
        players.append(
            {
                **player,
                "candidate_subject_ids": [reference_subject_id],
                "capture_domains": domains,
            }
        )
        cards.append(
            {
                "candidate_subject_id": reference_subject_id,
                "team_label": player.get("team_label"),
                "anchor_crops": crops,
            }
        )
    return (
        {"players": players, "algorithm": gallery.get("algorithm") or {}},
        {"cards": cards, "algorithm": gallery.get("algorithm") or {}},
    )


def _unavailable_artifact(
    reference_gallery: dict[str, Any],
    target_anchor_crops: dict[str, Any],
    *,
    model_status: dict[str, Any],
    generated_at: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": MODE,
        "algorithm": {
            "name": "identity_cross_analysis_appearance_reid",
            "version": "0.1.0",
            "parameters": parameters,
        },
        "model": model_status,
        "source": {
            "reference_gallery_algorithm": (
                reference_gallery.get("algorithm") or {}
            ),
            "target_anchor_crops_algorithm": (
                target_anchor_crops.get("algorithm") or {}
            ),
        },
        "safety": {
            "advisory_only": True,
            "team_safe_ranking": True,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_merges": 0,
            "eligible_for_player_stats": False,
        },
        "summary": {
            "reference_embedded_crops": 0,
            "target_embedded_crops": 0,
            "embedded_crops": 0,
            "rejected_crops": 0,
            "candidate_subjects_with_prototype": 0,
            "players_with_prototype": 0,
            "unresolved_subjects_ranked": 0,
            "cross_domain_players": 0,
            "automatic_merges": 0,
            "operator_actions_required": 0,
        },
        "rejected_crops": {"reference": {}, "target": {}},
        "player_profiles": [],
        "unresolved_rankings": [],
        "cross_domain_evidence": [],
        "evaluation": {
            "method": "cross_capture_advisory_without_h2_ground_truth",
            "queries": 0,
            "top1_accuracy": None,
            "top3_accuracy": None,
            "rows": [],
        },
    }


def _documents(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary = artifact.get("summary") or {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": artifact.get("generated_at"),
        "mode": MODE,
        "status": (
            "ready"
            if summary.get("players_with_prototype")
            else "model_or_prototypes_unavailable"
        ),
        "algorithm": artifact.get("algorithm") or {},
        "model": artifact.get("model") or {},
        "summary": summary,
        "gates": {
            "advisory_only": True,
            "team_safe_ranking": True,
            "automatic_false_merges": 0,
            "cross_capture_paths_separated": True,
        },
    }
    return {
        "identity_cross_analysis_appearance_reid": artifact,
        "identity_cross_analysis_appearance_reid_report": report,
    }
