from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import (
    canonical_digest,
    normalize_jersey_number,
    stable_key,
    team_label,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_dataset_manifest"
ALGORITHM_VERSION = "1.0.0"
DEFAULT_PARAMETERS = {
    "maximum_visibility_episode_gap_frames": 45,
    "fallback_split_ratios": {
        "train": 0.65,
        "validation": 0.20,
        "heldout": 0.15,
    },
}


def build_identity_jersey_number_dataset_manifest(
    sources: list[dict[str, Any]],
    *,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, provenance-preserving jersey-number dataset manifest."""
    params = {
        **DEFAULT_PARAMETERS,
        **(parameters or {}),
        "fallback_split_ratios": {
            **DEFAULT_PARAMETERS["fallback_split_ratios"],
            **((parameters or {}).get("fallback_split_ratios") or {}),
        },
    }
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    samples: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []

    for source in sources:
        match_key = str(source.get("source_match_key") or "").strip()
        video_key = str(source.get("source_video_key") or "").strip()
        crop_root = Path(str(source.get("crop_root") or ""))
        cards_doc = source.get("cards_doc") or {}
        reviewed_doc = source.get("reviewed_observations_doc") or {}
        if not match_key or not video_key:
            raise ValueError("Every dataset source requires source_match_key and source_video_key")
        cards = _flatten_cards(cards_doc)
        reviewed = {
            str(row.get("anchor_crop_id")): row
            for row in reviewed_doc.get("observations") or []
            if isinstance(row, dict) and row.get("anchor_crop_id")
        }
        matched = 0
        missing_cards = 0
        for crop_id, review in sorted(reviewed.items()):
            card = cards.get(crop_id)
            if card is None:
                missing_cards += 1
                continue
            matched += 1
            samples.append(
                _sample_from_review(
                    source_match_key=match_key,
                    source_video_key=video_key,
                    crop_root=crop_root,
                    card=card,
                    review=review,
                )
            )
        source_rows.append(
            {
                "source_match_key": match_key,
                "source_video_key": video_key,
                "crop_root": str(crop_root),
                "cards_digest": canonical_digest(cards_doc),
                "reviewed_observations_digest": canonical_digest(reviewed_doc),
                "reviewed_observations": len(reviewed),
                "matched_samples": matched,
                "missing_cards": missing_cards,
            }
        )

    _assign_visibility_episodes(
        samples,
        maximum_gap_frames=int(params["maximum_visibility_episode_gap_frames"]),
    )
    split_contract = _assign_splits(samples, params["fallback_split_ratios"])
    samples.sort(
        key=lambda row: (
            row["source_match_key"],
            row["source_video_key"],
            int(row["frame"]),
            row["anchor_crop_id"],
        )
    )
    dataset_digest = canonical_digest(
        [
            {
                key: row.get(key)
                for key in (
                    "sample_key",
                    "source_match_key",
                    "source_video_key",
                    "candidate_subject_id",
                    "tracklet_id",
                    "frame",
                    "team_label",
                    "label_state",
                    "number",
                    "view",
                    "visibility_episode_id",
                    "split",
                    "artifact_digest",
                )
            }
            for row in samples
        ]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_training_dataset",
        "dataset_version": f"jersey-number-dataset:v1:{dataset_digest}",
        "dataset_digest": dataset_digest,
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "split_contract": split_contract,
        "production_gate": {
            "eligible": split_contract["production_eligible"],
            "reason_codes": split_contract["reason_codes"],
        },
        "summary": _summary(samples, source_rows),
        "sources": source_rows,
        "samples": samples,
    }


def _flatten_cards(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    flattened: dict[str, dict[str, Any]] = {}
    for row in document.get("cards") or []:
        if not isinstance(row, dict):
            continue
        if row.get("anchor_crop_id"):
            flattened[str(row["anchor_crop_id"])] = dict(row)
            continue
        for crop in row.get("anchor_crops") or []:
            if not isinstance(crop, dict) or not crop.get("anchor_crop_id"):
                continue
            flattened[str(crop["anchor_crop_id"])] = {
                **crop,
                "candidate_subject_id": row.get("candidate_subject_id"),
                "team_label": row.get("team_label"),
                "role": row.get("role"),
            }
    return flattened


def _sample_from_review(
    *,
    source_match_key: str,
    source_video_key: str,
    crop_root: Path,
    card: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    crop_id = str(card["anchor_crop_id"])
    artifact = str(card.get("torso_artifact") or card.get("artifact") or "")
    artifact_kind = "torso_crop" if card.get("torso_artifact") else "anchor_crop"
    artifact_path = crop_root / artifact
    state = str(review.get("state") or "number_unreadable")
    number = normalize_jersey_number(review.get("number"))
    if state != "number_confirmed":
        number = None
    subject_id = str(card.get("candidate_subject_id") or "")
    tracklet_id = str(card.get("tracklet_id") or "")
    frame = int(card.get("frame") or 0)
    source_kind = str(review.get("source") or "unknown")
    return {
        "sample_key": stable_key(
            "jersey-dataset-sample",
            {
                "source_match_key": source_match_key,
                "source_video_key": source_video_key,
                "anchor_crop_id": crop_id,
            },
        ),
        "anchor_crop_id": crop_id,
        "source_match_key": source_match_key,
        "source_video_key": source_video_key,
        "candidate_subject_id": subject_id,
        "tracklet_id": tracklet_id,
        "frame": frame,
        "team_label": team_label(card.get("team_label")),
        "role": card.get("role"),
        "bbox_xyxy": card.get("bbox_xyxy"),
        "artifact": artifact,
        "artifact_kind": artifact_kind,
        "artifact_root": str(crop_root),
        "artifact_digest": _file_digest(artifact_path),
        "artifact_available": artifact_path.is_file(),
        "label_state": state,
        "number": number,
        "view": str(review.get("view") or "unknown"),
        "clean_jersey_visible": bool(review.get("clean_jersey_visible")),
        "number_panel_visible": bool(review.get("number_panel_visible")),
        "annotation_confidence": round(float(review.get("confidence") or 0.0), 4),
        "annotation_source": {
            "kind": "manual_review",
            "source": source_kind,
            "review_schema_version": review.get("schema_version"),
        },
    }


def _file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assign_visibility_episodes(
    samples: list[dict[str, Any]],
    *,
    maximum_gap_frames: int,
) -> None:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        groups[
            (
                row["source_match_key"],
                row["source_video_key"],
                row["candidate_subject_id"],
                row["tracklet_id"],
            )
        ].append(row)
    for group_key, rows in groups.items():
        episode_index = 0
        previous_frame: int | None = None
        for row in sorted(rows, key=lambda item: (int(item["frame"]), item["anchor_crop_id"])):
            frame = int(row["frame"])
            if previous_frame is None or frame - previous_frame > maximum_gap_frames:
                episode_index += 1
            row["visibility_episode_id"] = stable_key(
                "jersey-visibility-episode",
                {
                    "source_match_key": group_key[0],
                    "source_video_key": group_key[1],
                    "candidate_subject_id": group_key[2],
                    "tracklet_id": group_key[3],
                    "episode_index": episode_index,
                },
            )
            previous_frame = frame


def _assign_splits(
    samples: list[dict[str, Any]],
    ratios: dict[str, float],
) -> dict[str, Any]:
    match_keys = sorted({row["source_match_key"] for row in samples})
    if len(match_keys) >= 3:
        split_method = "source_match"
        group_field = "source_match_key"
        group_values = match_keys
        production_eligible = True
        leakage_risk = "low"
        reason_codes: list[str] = []
    else:
        split_method = "subject_group_fallback"
        group_field = "subject_split_group"
        for row in samples:
            row[group_field] = stable_key(
                "jersey-subject-split-group",
                {
                    "source_match_key": row["source_match_key"],
                    "candidate_subject_id": row["candidate_subject_id"],
                },
            )
        group_values = sorted({row[group_field] for row in samples})
        production_eligible = False
        leakage_risk = "high"
        reason_codes = ["insufficient_independent_source_matches"]

    assignments = _deterministic_group_assignments(group_values, ratios)
    for row in samples:
        row["split"] = assignments[row[group_field]]
        row["split_group"] = row[group_field]
        row.pop("subject_split_group", None)
    counts = Counter(row["split"] for row in samples)
    return {
        "method": split_method,
        "group_field": group_field,
        "production_eligible": production_eligible,
        "leakage_risk": leakage_risk,
        "reason_codes": reason_codes,
        "independent_source_matches": len(match_keys),
        "source_match_keys": match_keys,
        "groups": len(group_values),
        "sample_counts": dict(sorted(counts.items())),
        "group_assignments_digest": canonical_digest(assignments),
    }


def _deterministic_group_assignments(
    group_values: list[str],
    ratios: dict[str, float],
) -> dict[str, str]:
    ordered = sorted(group_values, key=lambda value: canonical_digest(value))
    total = len(ordered)
    train_end = max(1, round(total * float(ratios["train"]))) if total else 0
    validation_count = max(1, round(total * float(ratios["validation"]))) if total >= 3 else 0
    validation_end = min(total, train_end + validation_count)
    if total >= 3 and validation_end == total:
        train_end = max(1, train_end - 1)
        validation_end = total - 1
    assignments: dict[str, str] = {}
    for index, value in enumerate(ordered):
        assignments[value] = (
            "train"
            if index < train_end
            else "validation"
            if index < validation_end
            else "heldout"
        )
    return assignments


def _summary(
    samples: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "samples": len(samples),
        "available_artifacts": sum(bool(row["artifact_available"]) for row in samples),
        "missing_artifacts": sum(not row["artifact_available"] for row in samples),
        "source_documents": len(sources),
        "source_matches": len({row["source_match_key"] for row in samples}),
        "source_videos": len({row["source_video_key"] for row in samples}),
        "subjects": len({(row["source_match_key"], row["candidate_subject_id"]) for row in samples}),
        "visibility_episodes": len({row["visibility_episode_id"] for row in samples}),
        "states": dict(sorted(Counter(row["label_state"] for row in samples).items())),
        "numbers": dict(
            sorted(Counter(row["number"] for row in samples if row["number"] is not None).items())
        ),
        "views": dict(sorted(Counter(row["view"] for row in samples).items())),
        "teams": dict(sorted(Counter(row["team_label"] for row in samples).items())),
        "splits": dict(sorted(Counter(row["split"] for row in samples).items())),
    }
