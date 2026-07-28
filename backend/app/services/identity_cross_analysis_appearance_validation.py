from __future__ import annotations

"""Read-only appearance validation across two separately analysed videos.

This deliberately does not link or merge production identities.  Its sole
purpose is to measure whether operator-confirmed appearance evidence from one
analysis would rank the same real player highly in another analysis.
"""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_same_match_reid import JsonEmbeddingCache, PersonReIdEmbedder


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_cross_analysis_appearance_validation"
ALGORITHM_VERSION = "0.1.0"
DEFAULT_PARAMETERS: dict[str, Any] = {
    "max_crops_per_subject": 3,
    "max_crops_per_player": 24,
    "min_embeddings_per_subject": 2,
    "min_embeddings_per_player": 3,
    "ranking_top_k": 3,
    "max_prototype_dispersion": 0.38,
}


def build_cross_analysis_appearance_validation(
    source_review_doc: dict[str, Any],
    source_decision_doc: dict[str, Any],
    target_review_doc: dict[str, Any],
    target_decision_doc: dict[str, Any],
    *,
    source_match_path: Path,
    target_match_path: Path,
    source_capture_domain: str,
    target_capture_domain: str,
    embedder: PersonReIdEmbedder,
    model_status: dict[str, Any],
    embedding_cache: JsonEmbeddingCache | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank manually-labelled target subjects against source player profiles."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    source_cards = _cards_by_subject(source_review_doc)
    target_cards = _cards_by_subject(target_review_doc)
    source_assignments = _accepted_assignments(source_decision_doc)
    target_assignments = _accepted_assignments(target_decision_doc)

    source_vectors, source_rejected = _embed_subject_crops(
        source_cards,
        source_match_path,
        embedder,
        embedding_cache,
        params,
    )
    target_vectors, target_rejected = _embed_subject_crops(
        target_cards,
        target_match_path,
        embedder,
        embedding_cache,
        params,
    )
    if embedding_cache is not None:
        embedding_cache.save()

    player_catalog = _player_catalog(source_cards, target_cards)
    source_profiles = _build_player_profiles(
        source_assignments,
        source_vectors,
        player_catalog,
        params,
    )
    ready_profiles = {
        str(row["player_id"]): np.asarray(row["_prototype"], dtype=np.float32)
        for row in source_profiles
        if row["status"] == "ready"
    }
    query_rows = _evaluate_target_assignments(
        target_assignments,
        target_vectors,
        target_cards,
        player_catalog,
        ready_profiles,
        params,
    )
    summary = _summary(query_rows, source_profiles, source_vectors, target_vectors, params)
    status = "ready" if summary["evaluated_queries"] else "insufficient_validated_pairs"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only_cross_analysis_validation",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "status": status,
        "model": model_status,
        "source": {
            "capture_domain": source_capture_domain,
            "review_digest": canonical_digest(source_review_doc),
            "decisions_digest": canonical_digest(source_decision_doc),
            "accepted_assignments": len(source_assignments),
        },
        "target": {
            "capture_domain": target_capture_domain,
            "review_digest": canonical_digest(target_review_doc),
            "decisions_digest": canonical_digest(target_decision_doc),
            "accepted_assignments": len(target_assignments),
        },
        "safety": {
            "advisory_only": True,
            "separate_analysis_inputs": True,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_merges": 0,
            "eligible_for_player_stats": False,
        },
        "summary": summary,
        "embedding_cache": embedding_cache.summary() if embedding_cache else None,
        "source_player_profiles": [_public_profile(row) for row in source_profiles],
        "target_query_rankings": query_rows,
        "rejected_crops": {
            "source": dict(sorted(source_rejected.items())),
            "target": dict(sorted(target_rejected.items())),
        },
    }


def rank_player_profiles(
    query_vector: np.ndarray,
    player_profiles: dict[str, np.ndarray],
    player_teams: dict[str, str],
    team_label: str,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """Return deterministic same-team cosine-distance rankings."""
    query = _normalize(query_vector)
    rows = [
        {
            "player_id": player_id,
            "distance": _rounded(1.0 - float(np.clip(np.dot(query, _normalize(vector)), -1.0, 1.0)), 6),
        }
        for player_id, vector in player_profiles.items()
        if player_teams.get(player_id) == team_label
    ]
    rows.sort(key=lambda row: (float(row["distance"]), str(row["player_id"])))
    return [{**row, "rank": index} for index, row in enumerate(rows[:top_k], start=1)]


def _cards_by_subject(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(card.get("candidate_subject_id")): card
        for card in document.get("cards") or []
        if isinstance(card, dict) and card.get("candidate_subject_id")
    }


def _accepted_assignments(document: dict[str, Any]) -> dict[str, str]:
    return {
        str(row["candidate_subject_id"]): str(row["player_id"])
        for row in document.get("decisions") or []
        if isinstance(row, dict)
        and row.get("decision") in {"assign_roster_player", "confirm_recommended_player"}
        and row.get("candidate_subject_id")
        and row.get("player_id")
    }


def _player_catalog(*card_maps: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for cards in card_maps:
        for card in cards.values():
            for candidate in card.get("roster_candidates") or []:
                if not isinstance(candidate, dict) or not candidate.get("player_id"):
                    continue
                player_id = str(candidate["player_id"])
                catalog.setdefault(
                    player_id,
                    {
                        "player_name": str(candidate.get("player_name") or player_id),
                        "team_label": str(candidate.get("team_label") or card.get("team_label") or "U"),
                    },
                )
    return catalog


def _embed_subject_crops(
    cards: dict[str, dict[str, Any]],
    match_path: Path,
    embedder: PersonReIdEmbedder,
    cache: JsonEmbeddingCache | None,
    parameters: dict[str, Any],
) -> tuple[dict[str, list[np.ndarray]], dict[str, int]]:
    vectors: dict[str, list[np.ndarray]] = defaultdict(list)
    rejected: dict[str, int] = defaultdict(int)
    maximum = int(parameters["max_crops_per_subject"])
    for subject_id, card in sorted(cards.items()):
        crops = sorted(
            (
                crop
                for crop in ((card.get("visual_evidence") or {}).get("anchor_crops") or [])
                if isinstance(crop, dict) and crop.get("selection_eligible") is not False
            ),
            key=lambda crop: (-float(crop.get("selection_score") or 0.0), int(crop.get("frame") or 0)),
        )[:maximum]
        for crop in crops:
            image = cv2.imread(str(match_path / str(crop.get("artifact") or "")))
            if image is None or image.size == 0:
                rejected["artifact_missing_or_invalid"] += 1
                continue
            digest = hashlib.sha256(image.tobytes()).hexdigest()
            vector = cache.get(digest) if cache else None
            if vector is None:
                try:
                    vector = _normalize(embedder.embed(image))
                except (ValueError, RuntimeError, cv2.error):
                    rejected["embedding_failed"] += 1
                    continue
                if cache:
                    cache.put(digest, vector)
            vectors[subject_id].append(vector)
    return dict(vectors), dict(rejected)


def _build_player_profiles(
    assignments: dict[str, str],
    subject_vectors: dict[str, list[np.ndarray]],
    catalog: dict[str, dict[str, str]],
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    player_vectors: dict[str, list[np.ndarray]] = defaultdict(list)
    player_subjects: dict[str, list[str]] = defaultdict(list)
    maximum = int(parameters["max_crops_per_player"])
    for subject_id, player_id in sorted(assignments.items()):
        vectors = subject_vectors.get(subject_id) or []
        if not vectors:
            continue
        player_vectors[player_id].extend(vectors)
        player_subjects[player_id].append(subject_id)
    profiles: list[dict[str, Any]] = []
    for player_id in sorted(player_vectors):
        vectors = player_vectors[player_id][:maximum]
        prototype, dispersion = _prototype_with_dispersion(vectors)
        ready = (
            prototype is not None
            and len(vectors) >= int(parameters["min_embeddings_per_player"])
            and dispersion is not None
            and dispersion <= float(parameters["max_prototype_dispersion"])
        )
        details = catalog.get(player_id) or {"player_name": player_id, "team_label": "U"}
        profiles.append(
            {
                "player_id": player_id,
                "player_name": details["player_name"],
                "team_label": details["team_label"],
                "source_subjects": sorted(player_subjects[player_id]),
                "embedding_count": len(vectors),
                "prototype_dispersion": _rounded(dispersion, 6),
                "status": "ready" if ready else "unavailable",
                "reason_codes": [] if ready else ["insufficient_or_incoherent_source_embeddings"],
                "_prototype": prototype,
            }
        )
    return profiles


def _evaluate_target_assignments(
    assignments: dict[str, str],
    subject_vectors: dict[str, list[np.ndarray]],
    cards: dict[str, dict[str, Any]],
    catalog: dict[str, dict[str, str]],
    profiles: dict[str, np.ndarray],
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    player_teams = {player_id: details["team_label"] for player_id, details in catalog.items()}
    rows: list[dict[str, Any]] = []
    for subject_id, truth_player_id in sorted(assignments.items()):
        query, dispersion = _prototype_with_dispersion(subject_vectors.get(subject_id) or [])
        card = cards.get(subject_id) or {}
        truth = catalog.get(truth_player_id) or {"player_name": truth_player_id, "team_label": str(card.get("team_label") or "U")}
        if query is None or len(subject_vectors.get(subject_id) or []) < int(parameters["min_embeddings_per_subject"]):
            rows.append({
                "candidate_subject_id": subject_id,
                "truth_player_id": truth_player_id,
                "truth_player_name": truth["player_name"],
                "team_label": truth["team_label"],
                "status": "unavailable",
                "reason_codes": ["target_subject_prototype_unavailable"],
                "advisory_only": True,
            })
            continue
        ranking = rank_player_profiles(query, profiles, player_teams, truth["team_label"], top_k=int(parameters["ranking_top_k"]))
        truth_rank = next((row["rank"] for row in ranking if row["player_id"] == truth_player_id), None)
        top_distance = ranking[0]["distance"] if ranking else None
        second_distance = ranking[1]["distance"] if len(ranking) > 1 else None
        rows.append({
            "candidate_subject_id": subject_id,
            "truth_player_id": truth_player_id,
            "truth_player_name": truth["player_name"],
            "team_label": truth["team_label"],
            "status": "ranked" if ranking else "unavailable",
            "target_embedding_count": len(subject_vectors.get(subject_id) or []),
            "target_prototype_dispersion": _rounded(dispersion, 6),
            "truth_rank": truth_rank,
            "top1_correct": truth_rank == 1,
            "top3_correct": truth_rank is not None and truth_rank <= 3,
            "margin": _rounded((second_distance - top_distance) if second_distance is not None and top_distance is not None else None, 6),
            "suggestions": ranking,
            "reason_codes": [] if ranking else ["no_source_player_profiles_for_team"],
            "advisory_only": True,
        })
    return rows


def _summary(
    rows: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    source_vectors: dict[str, list[np.ndarray]],
    target_vectors: dict[str, list[np.ndarray]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    ranked = [row for row in rows if row.get("status") == "ranked"]
    count = len(ranked)
    ready_profiles_by_team: dict[str, int] = defaultdict(int)
    for profile in profiles:
        if profile.get("status") == "ready":
            ready_profiles_by_team[str(profile.get("team_label") or "U")] += 1
    top_k = int(parameters["ranking_top_k"])
    random_top1 = _random_same_team_baseline(ranked, ready_profiles_by_team, top_k=1)
    random_topk = _random_same_team_baseline(ranked, ready_profiles_by_team, top_k=top_k)
    top1_accuracy = _rounded(sum(bool(row["top1_correct"]) for row in ranked) / count if count else None, 4)
    top3_accuracy = _rounded(sum(bool(row["top3_correct"]) for row in ranked) / count if count else None, 4)
    return {
        "source_subjects_embedded": len(source_vectors),
        "target_subjects_embedded": len(target_vectors),
        "source_player_profiles_ready": sum(row["status"] == "ready" for row in profiles),
        "evaluated_queries": count,
        "top1_accuracy": top1_accuracy,
        "top3_accuracy": top3_accuracy,
        "same_team_random_baseline": {
            "top1_accuracy": random_top1,
            "top_k": top_k,
            "top_k_accuracy": random_topk,
        },
        "ranking_vs_random_baseline": {
            "top1_lift": _rounded((top1_accuracy / random_top1) if top1_accuracy is not None and random_top1 else None, 3),
            "top_k_lift": _rounded((top3_accuracy / random_topk) if top3_accuracy is not None and random_topk else None, 3),
            "outperforms_random_same_team_baseline": bool(
                top1_accuracy is not None
                and top3_accuracy is not None
                and random_top1 is not None
                and random_topk is not None
                and top1_accuracy > random_top1
                and top3_accuracy > random_topk
            ),
        },
        "automatic_merges": 0,
    }


def _random_same_team_baseline(
    ranked_rows: list[dict[str, Any]],
    ready_profiles_by_team: dict[str, int],
    *,
    top_k: int,
) -> float | None:
    probabilities = [
        min(int(top_k), ready_profiles_by_team.get(str(row.get("team_label") or "U"), 0))
        / ready_profiles_by_team[str(row.get("team_label") or "U")]
        for row in ranked_rows
        if ready_profiles_by_team.get(str(row.get("team_label") or "U"), 0) > 0
    ]
    return _rounded(sum(probabilities) / len(probabilities) if probabilities else None, 4)


def _prototype_with_dispersion(vectors: list[np.ndarray]) -> tuple[np.ndarray | None, float | None]:
    if not vectors:
        return None, None
    matrix = np.stack([_normalize(vector) for vector in vectors])
    distances = 1.0 - np.clip(matrix @ matrix.T, -1.0, 1.0)
    medoid = matrix[int(np.argmin(np.median(distances, axis=1)))]
    return medoid, float(np.median(1.0 - np.clip(matrix @ medoid, -1.0, 1.0)))


def _normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Cannot normalize an invalid ReID embedding")
    return value / norm


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in profile.items() if key != "_prototype"}


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(float(value)) else None
