from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_same_match_reid import (
    collect_reid_runtime_capabilities,
    JsonEmbeddingCache,
    PersonReIdEmbedder,
    load_default_embedder,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_approved_appearance_reid_shadow"
ALGORITHM_VERSION = "0.1.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "min_embeddings_per_subject": 2,
    "min_embeddings_per_player": 3,
    "max_crops_per_candidate_subject": 3,
    "max_prototype_dispersion": 0.38,
    "ranking_top_k": 3,
    "minimum_calibration_queries": 8,
    "minimum_calibration_top1_accuracy": 0.75,
}


@dataclass(frozen=True)
class PortableAppearanceEmbedder:
    """Deterministic OpenCV fallback for runtimes without usable OpenVINO."""

    model_name: str = "portable-appearance-descriptor"
    model_version: str = "opencv-color-texture-v1"
    embedding_dimension: int = 192

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        if crop_bgr.size == 0:
            raise ValueError("Cannot embed an empty crop")
        resized = cv2.resize(
            crop_bgr,
            (64, 128),
            interpolation=cv2.INTER_AREA,
        )
        normalized = resized.astype(np.float32) / 255.0
        spatial_features: list[float] = []
        for row_index in range(4):
            for column_index in range(2):
                cell = normalized[
                    row_index * 32 : (row_index + 1) * 32,
                    column_index * 32 : (column_index + 1) * 32,
                ]
                spatial_features.extend(cell.mean(axis=(0, 1)).tolist())
                spatial_features.extend(cell.std(axis=(0, 1)).tolist())

        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
        histogram_features = [
            *_normalized_histogram(hsv[:, :, 0], 12, (0, 180)),
            *_normalized_histogram(hsv[:, :, 1], 8, (0, 256)),
            *_normalized_histogram(hsv[:, :, 2], 8, (0, 256)),
            *_normalized_histogram(lab[:, :, 1], 8, (0, 256)),
            *_normalized_histogram(lab[:, :, 2], 8, (0, 256)),
        ]

        grayscale = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY).astype(
            np.float32
        )
        low_frequency = cv2.dct(
            cv2.resize(grayscale, (16, 16), interpolation=cv2.INTER_AREA)
            / 255.0
        )[:8, :8].reshape(-1)

        gradient_x = cv2.Sobel(
            grayscale,
            cv2.CV_32F,
            1,
            0,
            ksize=3,
        )
        gradient_y = cv2.Sobel(
            grayscale,
            cv2.CV_32F,
            0,
            1,
            ksize=3,
        )
        magnitude, angle = cv2.cartToPolar(
            gradient_x,
            gradient_y,
            angleInDegrees=True,
        )
        orientation_features: list[float] = []
        for row_index in range(2):
            for column_index in range(2):
                row_slice = slice(row_index * 64, (row_index + 1) * 64)
                column_slice = slice(
                    column_index * 32,
                    (column_index + 1) * 32,
                )
                histogram, _ = np.histogram(
                    angle[row_slice, column_slice],
                    bins=9,
                    range=(0.0, 360.0),
                    weights=magnitude[row_slice, column_slice],
                )
                histogram = histogram.astype(np.float32)
                histogram /= max(float(histogram.sum()), 1e-6)
                orientation_features.extend(histogram.tolist())

        vector = np.asarray(
            [
                *spatial_features,
                *histogram_features,
                *low_frequency.tolist(),
                *orientation_features,
            ],
            dtype=np.float32,
        )
        if vector.size != self.embedding_dimension:
            raise ValueError(
                "Portable appearance descriptor dimension mismatch: "
                f"{vector.size} != {self.embedding_dimension}"
            )
        norm = float(np.linalg.norm(vector))
        return vector / max(norm, 1e-12)


def load_approved_appearance_embedder(
    models_dir: Path,
) -> tuple[PersonReIdEmbedder, dict[str, Any]]:
    """Load a preferred runtime by capability, then fall back safely.

    Platform identity is diagnostic context only.  Every supported runtime is
    attempted when local model files are present, including on Apple Silicon.
    """
    capabilities = collect_reid_runtime_capabilities(models_dir)
    embedder, load_status = load_default_embedder(models_dir)
    model_status = {**capabilities, **load_status}
    if embedder is not None:
        return embedder, {
            **model_status,
            "quality_tier": "preferred_reid_model",
            "fallback_used": False,
        }
    fallback = PortableAppearanceEmbedder()
    return fallback, {
        **model_status,
        "model_name": fallback.model_name,
        "model_version": fallback.model_version,
        "available": True,
        "runtime": "portable_opencv_descriptor",
        "quality_tier": "baseline_fallback",
        "fallback_used": True,
        "fallback_reason": model_status.get("reason"),
        "preferred_model_status": model_status,
    }


def _normalized_histogram(
    channel: np.ndarray,
    bins: int,
    value_range: tuple[int, int],
) -> list[float]:
    histogram = cv2.calcHist(
        [channel],
        [0],
        None,
        [bins],
        [float(value_range[0]), float(value_range[1])],
    ).reshape(-1)
    histogram = histogram.astype(np.float32)
    histogram /= max(float(histogram.sum()), 1e-6)
    return histogram.tolist()


def build_identity_approved_appearance_reid(
    gallery_doc: dict[str, Any],
    anchor_crops_doc: dict[str, Any],
    *,
    match_path: Path,
    embedder: PersonReIdEmbedder | None,
    model_status: dict[str, Any],
    embedding_cache: JsonEmbeddingCache | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build advisory player prototypes and rank unresolved subjects."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    accepted_subject_to_player = {
        str(subject_id): {
            "player_id": player.get("player_id"),
            "player_name": player.get("player_name"),
            "team_label": player.get("team_label"),
        }
        for player in gallery_doc.get("players") or []
        for subject_id in player.get("candidate_subject_ids") or []
    }
    if embedder is None:
        artifact = _unavailable_document(
            gallery_doc,
            model_status=model_status,
            generated_at=generated,
            parameters=params,
        )
        return _documents(artifact)

    subject_vectors, embedding_rows, rejected = _embed_candidate_subjects(
        anchor_crops_doc,
        match_path=match_path,
        embedder=embedder,
        embedding_cache=embedding_cache,
        parameters=params,
    )
    if embedding_cache is not None:
        embedding_cache.save()

    subject_prototypes = {
        subject_id: _prototype(vectors)
        for subject_id, vectors in subject_vectors.items()
        if len(vectors) >= int(params["min_embeddings_per_subject"])
    }
    player_profiles = _player_profiles(
        gallery_doc,
        subject_vectors=subject_vectors,
        embedding_rows=embedding_rows,
        parameters=params,
    )
    reliable_player_prototypes = {
        str(row["player_id"]): np.asarray(
            row["prototype"], dtype=np.float32
        )
        for row in player_profiles
        if row.get("status") == "ready" and row.get("prototype")
    }
    player_index = {
        str(row["player_id"]): row for row in player_profiles
    }
    unresolved_rankings = _rank_unresolved_subjects(
        anchor_crops_doc,
        subject_prototypes=subject_prototypes,
        accepted_subject_to_player=accepted_subject_to_player,
        player_profiles=player_profiles,
        player_prototypes=reliable_player_prototypes,
        parameters=params,
    )
    evaluation = _leave_one_subject_out_evaluation(
        subject_vectors,
        accepted_subject_to_player=accepted_subject_to_player,
        player_index=player_index,
        parameters=params,
    )
    cross_domain = _cross_domain_evidence(player_profiles)
    ranking_rows = [
        row
        for row in unresolved_rankings
        if row.get("status") == "ranked"
    ]
    summary = {
        "embedded_crops": len(embedding_rows),
        "rejected_crops": sum(rejected.values()),
        "candidate_subjects_with_prototype": len(subject_prototypes),
        "players_with_prototype": len(reliable_player_prototypes),
        "unresolved_subjects_ranked": len(ranking_rows),
        "cross_domain_players": len(cross_domain),
        "automatic_merges": 0,
        "operator_actions_required": 0,
        "leave_one_subject_out_queries": evaluation["queries"],
        "leave_one_subject_out_top1_accuracy": evaluation[
            "top1_accuracy"
        ],
        "leave_one_subject_out_top3_accuracy": evaluation[
            "top3_accuracy"
        ],
    }
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "model": model_status,
        "source": {
            "gallery_algorithm": gallery_doc.get("algorithm") or {},
            "anchor_crops_algorithm": (
                anchor_crops_doc.get("algorithm") or {}
            ),
        },
        "safety": {
            "advisory_only": True,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_merges": 0,
            "eligible_for_player_stats": False,
        },
        "summary": summary,
        "embedding_cache": (
            embedding_cache.summary() if embedding_cache is not None else None
        ),
        "rejected_crops": dict(sorted(rejected.items())),
        "player_profiles": player_profiles,
        "unresolved_rankings": unresolved_rankings,
        "cross_domain_evidence": cross_domain,
        "evaluation": evaluation,
    }
    return _documents(artifact)


def _embed_candidate_subjects(
    anchor_crops_doc: dict[str, Any],
    *,
    match_path: Path,
    embedder: PersonReIdEmbedder,
    embedding_cache: JsonEmbeddingCache | None,
    parameters: dict[str, Any],
) -> tuple[
    dict[str, list[np.ndarray]],
    list[dict[str, Any]],
    dict[str, int],
]:
    subject_vectors: dict[str, list[np.ndarray]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    rejected: dict[str, int] = defaultdict(int)
    maximum = int(parameters["max_crops_per_candidate_subject"])
    for card in anchor_crops_doc.get("cards") or []:
        if not isinstance(card, dict):
            continue
        subject_id = str(card.get("candidate_subject_id") or "")
        crops = sorted(
            (
                row
                for row in card.get("anchor_crops") or []
                if isinstance(row, dict)
            ),
            key=lambda row: (
                -float(row.get("selection_score") or 0.0),
                int(row.get("frame") or 0),
            ),
        )[:maximum]
        for crop in crops:
            path = match_path / str(crop.get("artifact") or "")
            image = cv2.imread(str(path))
            if image is None or image.size == 0:
                rejected["artifact_missing_or_invalid"] += 1
                continue
            digest = hashlib.sha256(image.tobytes()).hexdigest()
            vector = (
                embedding_cache.get(digest)
                if embedding_cache is not None
                else None
            )
            if vector is None:
                try:
                    vector = _normalize(embedder.embed(image))
                except (ValueError, RuntimeError, cv2.error):
                    rejected["embedding_failed"] += 1
                    continue
                if embedding_cache is not None:
                    embedding_cache.put(digest, vector)
            subject_vectors[subject_id].append(vector)
            rows.append(
                {
                    "candidate_subject_id": subject_id,
                    "anchor_crop_id": crop.get("anchor_crop_id"),
                    "frame": crop.get("frame"),
                    "artifact": crop.get("artifact"),
                    "_vector": vector,
                }
            )
    return dict(subject_vectors), rows, dict(rejected)


def _player_profiles(
    gallery_doc: dict[str, Any],
    *,
    subject_vectors: dict[str, list[np.ndarray]],
    embedding_rows: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for player in gallery_doc.get("players") or []:
        subject_ids = [
            str(value)
            for value in player.get("candidate_subject_ids") or []
        ]
        vectors = [
            vector
            for subject_id in subject_ids
            for vector in subject_vectors.get(subject_id) or []
        ]
        prototype, dispersion = _prototype_with_dispersion(vectors)
        domain_profiles: list[dict[str, Any]] = []
        for domain in player.get("capture_domains") or []:
            domain_crop_keys = {
                (
                    str(crop.get("candidate_subject_id") or ""),
                    str(crop.get("artifact") or ""),
                )
                for crop in domain.get("crops") or []
            }
            domain_vectors = [
                row["_vector"]
                for row in embedding_rows
                if (
                    str(row.get("candidate_subject_id") or ""),
                    str(row.get("artifact") or ""),
                )
                in domain_crop_keys
            ]
            domain_prototype, domain_dispersion = (
                _prototype_with_dispersion(domain_vectors)
            )
            domain_profiles.append(
                {
                    "capture_domain": domain.get("capture_domain"),
                    "embedding_count": len(domain_vectors),
                    "prototype_dispersion": _rounded(
                        domain_dispersion, 6
                    ),
                    "prototype": _vector_values(domain_prototype),
                }
            )
        enough = len(vectors) >= int(
            parameters["min_embeddings_per_player"]
        )
        reliable = (
            enough
            and prototype is not None
            and dispersion is not None
            and dispersion <= float(
                parameters["max_prototype_dispersion"]
            )
        )
        reasons: list[str] = []
        if not enough:
            reasons.append("insufficient_player_embeddings")
        if (
            dispersion is not None
            and dispersion
            > float(parameters["max_prototype_dispersion"])
        ):
            reasons.append("prototype_dispersion_too_high")
        profiles.append(
            {
                "player_id": player.get("player_id"),
                "player_name": player.get("player_name"),
                "team_label": player.get("team_label"),
                "candidate_subject_ids": subject_ids,
                "embedding_count": len(vectors),
                "status": "ready" if reliable else "unavailable",
                "prototype_dispersion": _rounded(dispersion, 6),
                "prototype": _vector_values(prototype),
                "capture_domains": domain_profiles,
                "reason_codes": reasons,
            }
        )
    return profiles


def _rank_unresolved_subjects(
    anchor_crops_doc: dict[str, Any],
    *,
    subject_prototypes: dict[str, np.ndarray],
    accepted_subject_to_player: dict[str, dict[str, Any]],
    player_profiles: list[dict[str, Any]],
    player_prototypes: dict[str, np.ndarray],
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    players_by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in player_profiles:
        player_id = str(player.get("player_id") or "")
        if player_id in player_prototypes:
            players_by_team[str(player.get("team_label") or "U")].append(
                player
            )
    rows: list[dict[str, Any]] = []
    for card in anchor_crops_doc.get("cards") or []:
        if not isinstance(card, dict):
            continue
        subject_id = str(card.get("candidate_subject_id") or "")
        if subject_id in accepted_subject_to_player:
            continue
        prototype = subject_prototypes.get(subject_id)
        team_label = str(card.get("team_label") or "U")
        candidates = players_by_team.get(team_label) or []
        if prototype is None or not candidates:
            rows.append(
                {
                    "candidate_subject_id": subject_id,
                    "team_label": team_label,
                    "status": "unavailable",
                    "suggestions": [],
                    "reason_codes": (
                        ["subject_prototype_unavailable"]
                        if prototype is None
                        else ["no_confirmed_player_prototypes_for_team"]
                    ),
                    "advisory_only": True,
                }
            )
            continue
        suggestions = sorted(
            (
                {
                    "player_id": player.get("player_id"),
                    "player_name": player.get("player_name"),
                    "distance": _rounded(
                        1.0
                        - float(
                            np.clip(
                                np.dot(
                                    prototype,
                                    player_prototypes[
                                        str(player["player_id"])
                                    ],
                                ),
                                -1.0,
                                1.0,
                            )
                        ),
                        6,
                    ),
                }
                for player in candidates
            ),
            key=lambda row: (
                (
                    float(row["distance"])
                    if row.get("distance") is not None
                    else math.inf
                ),
                str(row.get("player_id") or ""),
            ),
        )[: int(parameters["ranking_top_k"])]
        rows.append(
            {
                "candidate_subject_id": subject_id,
                "team_label": team_label,
                "status": "ranked",
                "suggestions": [
                    {**row, "rank": index}
                    for index, row in enumerate(suggestions, start=1)
                ],
                "reason_codes": [],
                "advisory_only": True,
            }
        )
    return rows


def _leave_one_subject_out_evaluation(
    subject_vectors: dict[str, list[np.ndarray]],
    *,
    accepted_subject_to_player: dict[str, dict[str, Any]],
    player_index: dict[str, dict[str, Any]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    query_rows: list[dict[str, Any]] = []
    for subject_id, truth in sorted(accepted_subject_to_player.items()):
        query_vectors = subject_vectors.get(subject_id) or []
        if len(query_vectors) < int(
            parameters["min_embeddings_per_subject"]
        ):
            continue
        query = _prototype(query_vectors)
        if query is None:
            continue
        candidate_prototypes: list[tuple[str, np.ndarray]] = []
        for player_id, player in sorted(player_index.items()):
            if player.get("team_label") != truth.get("team_label"):
                continue
            other_subjects = [
                value
                for value in player.get("candidate_subject_ids") or []
                if str(value) != subject_id
            ]
            vectors = [
                vector
                for other_subject in other_subjects
                for vector in subject_vectors.get(str(other_subject)) or []
            ]
            if len(vectors) >= int(
                parameters["min_embeddings_per_player"]
            ):
                prototype = _prototype(vectors)
                if prototype is not None:
                    candidate_prototypes.append((player_id, prototype))
        if not candidate_prototypes:
            continue
        ranking = sorted(
            (
                (
                    player_id,
                    1.0
                    - float(
                        np.clip(
                            np.dot(query, prototype),
                            -1.0,
                            1.0,
                        )
                    ),
                )
                for player_id, prototype in candidate_prototypes
            ),
            key=lambda item: (item[1], item[0]),
        )
        truth_id = str(truth.get("player_id") or "")
        rank = next(
            (
                index
                for index, (player_id, _) in enumerate(
                    ranking, start=1
                )
                if player_id == truth_id
            ),
            None,
        )
        query_rows.append(
            {
                "candidate_subject_id": subject_id,
                "truth_player_id": truth_id,
                "truth_player_name": truth.get("player_name"),
                "truth_rank": rank,
                "top1_correct": rank == 1,
                "top3_correct": rank is not None and rank <= 3,
                "ranked_players": len(ranking),
            }
        )
    count = len(query_rows)
    return {
        "method": "leave_one_confirmed_subject_out",
        "queries": count,
        "top1_accuracy": _rounded(
            (
                sum(bool(row["top1_correct"]) for row in query_rows)
                / count
            )
            if count
            else None,
            4,
        ),
        "top3_accuracy": _rounded(
            (
                sum(bool(row["top3_correct"]) for row in query_rows)
                / count
            )
            if count
            else None,
            4,
        ),
        "rows": query_rows,
    }


def build_appearance_ranking_calibration(
    gallery_doc: dict[str, Any],
    *,
    subject_vectors: dict[str, list[np.ndarray]],
    model_status: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Decide whether appearance rankings are safe enough to show to users.

    A rank is not an identity claim.  In particular, the portable colour and
    texture descriptor is useful for diagnostics but is not a validated
    cross-camera person-ReID model.  We therefore preserve its diagnostic
    output while suppressing its names in the operator audit.
    """

    player_vectors = {
        str(player.get("player_id") or ""): [
            vector
            for subject_id in player.get("candidate_subject_ids") or []
            for vector in subject_vectors.get(str(subject_id)) or []
        ]
        for player in gallery_doc.get("players") or []
        if isinstance(player, dict) and player.get("player_id")
    }
    players_by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in gallery_doc.get("players") or []:
        if not isinstance(player, dict):
            continue
        player_id = str(player.get("player_id") or "")
        if len(player_vectors.get(player_id) or []) >= 3:
            players_by_team[str(player.get("team_label") or "U")].append(
                player
            )

    rows: list[dict[str, Any]] = []
    for team_label, players in sorted(players_by_team.items()):
        if len(players) < 2:
            continue
        for player in players:
            player_id = str(player.get("player_id") or "")
            vectors = player_vectors[player_id]
            for query_index, query in enumerate(vectors):
                candidates: list[tuple[str, np.ndarray]] = []
                for candidate in players:
                    candidate_id = str(candidate.get("player_id") or "")
                    candidate_vectors = player_vectors[candidate_id]
                    reference_vectors = (
                        [
                            value
                            for index, value in enumerate(candidate_vectors)
                            if index != query_index
                        ]
                        if candidate_id == player_id
                        else candidate_vectors
                    )
                    prototype = _prototype(reference_vectors)
                    if prototype is not None:
                        candidates.append((candidate_id, prototype))
                ranking = sorted(
                    (
                        (
                            candidate_id,
                            1.0
                            - float(
                                np.clip(np.dot(query, prototype), -1.0, 1.0)
                            ),
                        )
                        for candidate_id, prototype in candidates
                    ),
                    key=lambda item: (item[1], item[0]),
                )
                rank = next(
                    (
                        index
                        for index, (candidate_id, _) in enumerate(
                            ranking, start=1
                        )
                        if candidate_id == player_id
                    ),
                    None,
                )
                rows.append(
                    {
                        "team_label": team_label,
                        "player_id": player_id,
                        "query_index": query_index,
                        "truth_rank": rank,
                        "top1_correct": rank == 1,
                        "top3_correct": rank is not None and rank <= 3,
                        "best_distance": _rounded(
                            ranking[0][1] if ranking else None,
                            6,
                        ),
                        "top1_margin": _rounded(
                            (
                                ranking[1][1] - ranking[0][1]
                                if len(ranking) > 1
                                else None
                            ),
                            6,
                        ),
                    }
                )
    count = len(rows)
    top1_accuracy = (
        sum(bool(row["top1_correct"]) for row in rows) / count
        if count
        else None
    )
    reasons: list[str] = []
    if str(model_status.get("quality_tier") or "") == "baseline_fallback":
        reasons.append("baseline_descriptor_not_validated_for_cross_capture")
    if count < int(parameters["minimum_calibration_queries"]):
        reasons.append("insufficient_calibration_queries")
    if (
        top1_accuracy is not None
        and top1_accuracy
        < float(parameters["minimum_calibration_top1_accuracy"])
    ):
        reasons.append("calibration_top1_accuracy_below_threshold")
    per_player = [
        {
            "player_id": player_id,
            "team_label": next(
                (
                    str(player.get("team_label") or "U")
                    for player in gallery_doc.get("players") or []
                    if str(player.get("player_id") or "") == player_id
                ),
                "U",
            ),
            "queries": len(player_rows),
            "top1_accuracy": _rounded(
                sum(bool(row["top1_correct"]) for row in player_rows)
                / len(player_rows),
                4,
            ),
            "top3_accuracy": _rounded(
                sum(bool(row["top3_correct"]) for row in player_rows)
                / len(player_rows),
                4,
            ),
        }
        for player_id, player_rows in sorted(
            (
                (
                    player_id,
                    [
                        row
                        for row in rows
                        if row["player_id"] == player_id
                    ],
                )
                for player_id in player_vectors
            ),
            key=lambda item: item[0],
        )
        if player_rows
    ]
    per_team = [
        {
            "team_label": team_label,
            "queries": len(team_rows),
            "top1_accuracy": _rounded(
                sum(bool(row["top1_correct"]) for row in team_rows)
                / len(team_rows),
                4,
            ),
            "top3_accuracy": _rounded(
                sum(bool(row["top3_correct"]) for row in team_rows)
                / len(team_rows),
                4,
            ),
        }
        for team_label, team_rows in sorted(
            (
                (
                    team_label,
                    [
                        row for row in rows
                        if row["team_label"] == team_label
                    ],
                )
                for team_label in {row["team_label"] for row in rows}
            ),
            key=lambda item: item[0],
        )
        if team_rows
    ]
    return {
        "method": "internal_reference_calibration",
        "queries": count,
        "players": len(per_player),
        "teams": len(per_team),
        "top1_accuracy": _rounded(top1_accuracy, 4),
        "top3_accuracy": _rounded(
            (
                sum(bool(row["top3_correct"]) for row in rows) / count
                if count
                else None
            ),
            4,
        ),
        "rows": rows,
        "per_player_results": per_player,
        "per_team_results": per_team,
        "candidate_count_distribution": dict(
            sorted(
                Counter(
                    sum(
                        1
                        for player in players_by_team.get(
                            row["team_label"],
                        )
                        if player.get("player_id")
                    )
                    for row in rows
                ).items()
            )
        ),
        "display_eligible": not reasons,
        "suppression_reason_codes": reasons,
    }


def _cross_domain_evidence(
    player_profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for player in player_profiles:
        domains = {
            str(row.get("capture_domain")): row
            for row in player.get("capture_domains") or []
            if row.get("prototype")
        }
        if "H1" not in domains or "H2" not in domains:
            continue
        h1 = np.asarray(domains["H1"]["prototype"], dtype=np.float32)
        h2 = np.asarray(domains["H2"]["prototype"], dtype=np.float32)
        rows.append(
            {
                "player_id": player.get("player_id"),
                "player_name": player.get("player_name"),
                "team_label": player.get("team_label"),
                "prototype_distance": _rounded(
                    1.0 - float(np.clip(np.dot(h1, h2), -1.0, 1.0)),
                    6,
                ),
                "advisory_only": True,
            }
        )
    return rows


def _prototype(
    vectors: list[np.ndarray],
) -> np.ndarray | None:
    prototype, _ = _prototype_with_dispersion(vectors)
    return prototype


def _prototype_with_dispersion(
    vectors: list[np.ndarray],
) -> tuple[np.ndarray | None, float | None]:
    if not vectors:
        return None, None
    matrix = np.stack([_normalize(value) for value in vectors])
    distances = 1.0 - np.clip(matrix @ matrix.T, -1.0, 1.0)
    medoid_index = int(np.argmin(np.median(distances, axis=1)))
    prototype = matrix[medoid_index]
    dispersion = float(
        np.median(1.0 - np.clip(matrix @ prototype, -1.0, 1.0))
    )
    return prototype, dispersion


def _normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Cannot normalize an invalid ReID embedding")
    return value / norm


def _vector_values(
    vector: np.ndarray | None,
) -> list[float] | None:
    if vector is None:
        return None
    return [round(float(value), 7) for value in vector]


def _rounded(
    value: float | None,
    digits: int = 4,
) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _unavailable_document(
    gallery_doc: dict[str, Any],
    *,
    model_status: dict[str, Any],
    generated_at: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": "shadow_read_only",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": parameters,
        },
        "model": model_status,
        "source": {
            "gallery_algorithm": gallery_doc.get("algorithm") or {},
        },
        "safety": {
            "advisory_only": True,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_merges": 0,
            "eligible_for_player_stats": False,
        },
        "summary": {
            "embedded_crops": 0,
            "candidate_subjects_with_prototype": 0,
            "players_with_prototype": 0,
            "unresolved_subjects_ranked": 0,
            "cross_domain_players": 0,
            "automatic_merges": 0,
            "operator_actions_required": 0,
        },
        "player_profiles": [],
        "unresolved_rankings": [],
        "cross_domain_evidence": [],
        "evaluation": {
            "method": "leave_one_confirmed_subject_out",
            "queries": 0,
            "top1_accuracy": None,
            "top3_accuracy": None,
            "rows": [],
        },
    }


def _documents(
    artifact: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    summary = artifact.get("summary") or {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": artifact.get("generated_at"),
        "mode": "shadow_read_only",
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
            "automatic_false_merges": 0,
            "top_k_measured": bool(
                summary.get("leave_one_subject_out_queries")
            ),
            "cross_domain_prototype_available": bool(
                summary.get("cross_domain_players")
            ),
        },
    }
    return {
        "identity_approved_appearance_reid_shadow": artifact,
        "identity_approved_appearance_reid_shadow_report": report,
    }
