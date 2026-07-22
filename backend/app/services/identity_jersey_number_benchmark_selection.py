from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from statistics import median
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_targeted_benchmark_selection"
ALGORITHM_VERSION = "1.1.0"


def build_targeted_jersey_number_benchmark(
    anchor_crops_doc: dict[str, Any],
    candidate_identity_doc: dict[str, Any],
    *,
    team_label: str = "A",
    max_subjects: int = 7,
    max_crops: int = 30,
    min_seed_crops: int = 3,
    min_independent_seed_reads: int = 3,
    minimum_seed_frame_separation: int = 12,
    minimum_visibility_episode_gap_frames: int = 45,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Select one readable seed tracklet per multi-tracklet subject.

    Target tracklet crops are deliberately excluded. They remain unseen number
    evidence, which makes the later N5 propagation result measurable.
    """
    if (
        max_subjects < 1
        or max_crops < 1
        or min_seed_crops < 1
        or min_independent_seed_reads < 1
        or minimum_seed_frame_separation < 1
        or minimum_visibility_episode_gap_frames < 1
    ):
        raise ValueError("Benchmark limits must be positive")

    generated = generated_at or datetime.now(timezone.utc).isoformat()
    candidate_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in candidate_identity_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    eligible: list[dict[str, Any]] = []
    rejection_counts: defaultdict[str, int] = defaultdict(int)

    for source_card in anchor_crops_doc.get("cards") or []:
        if not isinstance(source_card, dict):
            continue
        subject_id = str(source_card.get("candidate_subject_id") or "")
        candidate = candidate_by_subject.get(subject_id)
        if str(source_card.get("team_label") or "U") != team_label:
            rejection_counts["different_team"] += 1
            continue
        if not candidate:
            rejection_counts["candidate_subject_missing"] += 1
            continue
        tracklet_ids = sorted({str(value) for value in candidate.get("tracklet_ids") or [] if value})
        if len(tracklet_ids) < 2:
            rejection_counts["single_tracklet_subject"] += 1
            continue
        if str(source_card.get("status") or "") != "ready_for_visual_audit":
            rejection_counts["anchor_card_not_ready"] += 1
            continue

        crops_by_tracklet: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for crop in source_card.get("anchor_crops") or []:
            if isinstance(crop, dict) and crop.get("tracklet_id"):
                crops_by_tracklet[str(crop["tracklet_id"])].append(crop)
        seed_options = []
        for tracklet_id, crops in crops_by_tracklet.items():
            if len(crops) < min_seed_crops:
                continue
            independent_crops = _independent_seed_crops(
                crops,
                minimum_frame_separation=minimum_seed_frame_separation,
                visibility_episode_gap_frames=minimum_visibility_episode_gap_frames,
            )
            if len(independent_crops) < min_independent_seed_reads:
                continue
            seed_options.append(
                (_tracklet_rank(crops), tracklet_id, crops, independent_crops)
            )
        if not seed_options:
            rejection_counts["no_consensus_eligible_seed_tracklet"] += 1
            continue
        _, seed_tracklet_id, seed_crops, independent_seed_crops = max(
            seed_options,
            key=lambda row: (row[0], row[1]),
        )
        target_tracklet_ids = [value for value in tracklet_ids if value != seed_tracklet_id]
        if not target_tracklet_ids:
            rejection_counts["no_unseen_target_tracklet"] += 1
            continue

        selected_crops = sorted(deepcopy(seed_crops), key=lambda row: (int(row.get("frame") or 0), str(row.get("anchor_crop_id") or "")))
        card = deepcopy(source_card)
        card["anchor_crops"] = selected_crops
        card["selected_crop_count"] = len(selected_crops)
        card["benchmark_selection"] = {
            "purpose": "seed_number_for_n5_propagation",
            "seed_tracklet_id": seed_tracklet_id,
            "target_tracklet_ids": target_tracklet_ids,
            "candidate_tracklet_ids": tracklet_ids,
            "target_crops_intentionally_hidden": True,
            "independent_seed_crop_ids": [
                str(crop.get("anchor_crop_id") or "") for crop in independent_seed_crops
            ],
            "independent_seed_reads": len(independent_seed_crops),
            "consensus_eligible_seed": True,
        }
        eligible.append(
            {
                "rank": _tracklet_rank(selected_crops),
                "subject_id": subject_id,
                "card": card,
            }
        )

    selected: list[dict[str, Any]] = []
    selected_crop_count = 0
    for row in sorted(eligible, key=lambda item: (item["rank"], item["subject_id"]), reverse=True):
        if len(selected) >= max_subjects:
            break
        card_crop_count = len(row["card"]["anchor_crops"])
        if selected_crop_count + card_crop_count > max_crops:
            continue
        selected.append(row["card"])
        selected_crop_count += card_crop_count

    selected.sort(key=lambda row: str(row.get("candidate_subject_id") or ""))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_operator_benchmark",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": {
                "team_label": team_label,
                "max_subjects": max_subjects,
                "max_crops": max_crops,
                "min_seed_crops": min_seed_crops,
                "min_independent_seed_reads": min_independent_seed_reads,
                "minimum_seed_frame_separation": minimum_seed_frame_separation,
                "minimum_visibility_episode_gap_frames": minimum_visibility_episode_gap_frames,
                "ranking": "median_bbox_area_then_mean_detection_confidence_then_crop_count",
            },
        },
        "source": {
            "anchor_crops_digest": canonical_digest(anchor_crops_doc),
            "candidate_identity_digest": canonical_digest(candidate_identity_doc),
        },
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_assignments": 0,
            "target_tracklet_crops_hidden": True,
        },
        "summary": {
            "eligible_multi_tracklet_subjects": len(eligible),
            "selected_subjects": len(selected),
            "selected_seed_tracklets": len(selected),
            "selected_crops": selected_crop_count,
            "unseen_target_tracklets": sum(
                len((card.get("benchmark_selection") or {}).get("target_tracklet_ids") or [])
                for card in selected
            ),
            "rejection_counts": dict(sorted(rejection_counts.items())),
        },
        "cards": selected,
    }


def _tracklet_rank(crops: list[dict[str, Any]]) -> tuple[float, float, int]:
    areas = []
    confidences = []
    for crop in crops:
        bbox = crop.get("bbox_xyxy") or [0, 0, 0, 0]
        if len(bbox) == 4:
            areas.append(max(0.0, (float(bbox[2]) - float(bbox[0])) * (float(bbox[3]) - float(bbox[1]))))
        confidences.append(float(crop.get("detection_confidence") or 0.0))
    return (
        median(areas) if areas else 0.0,
        sum(confidences) / len(confidences) if confidences else 0.0,
        len(crops),
    )


def _independent_seed_crops(
    crops: list[dict[str, Any]],
    *,
    minimum_frame_separation: int,
    visibility_episode_gap_frames: int,
) -> list[dict[str, Any]]:
    """Estimate whether selected crops can satisfy the production consensus gate."""
    selected: list[dict[str, Any]] = []
    used_episodes: set[str] = set()
    last_frame: int | None = None
    for crop in sorted(
        crops,
        key=lambda row: (int(row.get("frame") or 0), str(row.get("anchor_crop_id") or "")),
    ):
        frame = int(crop.get("frame") or 0)
        if last_frame is not None and frame - last_frame < minimum_frame_separation:
            continue
        explicit_episode = crop.get("visibility_episode_id")
        episode = (
            str(explicit_episode)
            if explicit_episode
            else f"legacy:{str(crop.get('tracklet_id') or 'unknown')}:{frame // visibility_episode_gap_frames}"
        )
        if episode in used_episodes:
            continue
        selected.append(crop)
        last_frame = frame
        used_episodes.add(episode)
    return selected
