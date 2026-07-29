from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import shutil
from typing import Any

import cv2
import numpy as np

from app.services.identity_jersey_number_common import (
    canonical_digest,
    normalize_jersey_number_annotation,
    normalize_normalized_bbox,
    stable_key,
)
from app.services.identity_jersey_number_dataset import (
    identity_jersey_number_dataset_digest,
)


SCHEMA_VERSION = "0.4.0"
ALGORITHM_NAME = "identity_jersey_number_discovery_audit"
ALGORITHM_VERSION = "1.5.1"
MANIFEST_FILENAME = "identity_jersey_number_discovery_audit.json"
REVIEWED_FILENAME = "identity_jersey_number_discovery_audit_reviewed.json"
INDEX_FILENAME = "index.html"


def prepare_jersey_number_discovery_audit(
    dataset_doc: dict[str, Any],
    *,
    output_root: Path,
    roster_choices: list[dict[str, str]],
    target_cards: int = 80,
    target_confirmations: int = 60,
    target_negatives: int = 0,
    team_label: str = "A",
    unreviewed_only: bool = False,
    audit_purpose: str = "research_discovery",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Prepare a research-only number discovery gate from existing crop artifacts.

    This deliberately differs from the product panel audit: the operator can create
    a new number label from a visible crop instead of merely boxing an existing label.
    """
    if target_cards < 1:
        raise ValueError("target_cards must be positive")
    if target_confirmations < 1:
        raise ValueError("target_confirmations must be positive")
    if target_negatives < 0:
        raise ValueError("target_negatives must not be negative")
    normalized_choices = _normalize_roster_choices(roster_choices)
    selected, selection_diagnostics = _select_samples(
        dataset_doc,
        team_label=team_label,
        target_cards=target_cards,
        unreviewed_only=unreviewed_only,
        require_panel_likelihood=audit_purpose == "panel_readiness_recovery",
    )
    output_root.mkdir(parents=True, exist_ok=True)
    image_root = output_root / "images"
    image_root.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    for index, sample in enumerate(selected, start=1):
        sample_key = str(sample["sample_key"])
        image_filename = f"{index:03d}-{_safe_filename(sample_key)}.jpg"
        copied = _copy_sample_artifact(sample, image_root / image_filename)
        existing = normalize_jersey_number_annotation(sample, allow_missing=True)
        items.append(
            {
                "audit_index": index,
                "sample_key": sample_key,
                "anchor_crop_id": sample.get("anchor_crop_id"),
                "source_match_key": sample.get("source_match_key"),
                "source_video_key": sample.get("source_video_key"),
                "candidate_subject_id": sample.get("candidate_subject_id"),
                "frame": int(sample.get("frame") or 0),
                "team_label": str(sample.get("team_label") or "U"),
                "visibility_episode_id": sample.get("visibility_episode_id"),
                "image_filename": f"images/{image_filename}" if copied else None,
                "image_available": copied,
                "existing_annotation": {
                    **existing,
                    "number_panel_bbox_normalized": sample.get("number_panel_bbox_normalized"),
                },
                "manual_review": {
                    "status": "pending",
                    "jersey_number_state": None,
                    "jersey_number": None,
                    "number_panel_bbox_normalized": None,
                    "reviewed_at": None,
                },
            }
        )

    contract = {
        "schema_version": SCHEMA_VERSION,
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "dataset_digest": str(dataset_doc.get("dataset_digest") or ""),
        "team_label": team_label,
        "target_confirmations": target_confirmations,
        "target_negatives": target_negatives,
        "audit_purpose": audit_purpose,
        "selection_mode": "unreviewed_only" if unreviewed_only else "all_available",
        "roster_choices": normalized_choices,
        "items": [
            {
                key: item.get(key)
                for key in (
                    "audit_index", "sample_key", "anchor_crop_id", "source_match_key",
                    "source_video_key", "candidate_subject_id", "frame", "team_label",
                    "visibility_episode_id", "image_filename", "image_available",
                    "existing_annotation",
                )
            }
            for item in items
        ],
    }
    manifest = {
        **contract,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "mode": "research_jersey_number_discovery_gate",
        "audit_digest": canonical_digest(contract),
        "summary": {
            "target_cards": target_cards,
            "target_confirmations": target_confirmations,
            "target_negatives": target_negatives,
            "audit_purpose": audit_purpose,
            "selected_cards": len(items),
            "available_images": sum(bool(item["image_available"]) for item in items),
            "unreviewed_only": unreviewed_only,
            "preexisting_confirmed": sum(
                item["existing_annotation"]["jersey_number_state"] == "number_confirmed"
                for item in items
            ),
            "unique_visibility_episodes": len(
                {item.get("visibility_episode_id") for item in items if item.get("visibility_episode_id")}
            ),
            **selection_diagnostics,
        },
        "items": items,
    }
    (output_root / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_root / INDEX_FILENAME).write_text(_render_audit_html(manifest), encoding="utf-8")
    return manifest


def derive_jersey_number_recovery_targets(
    readiness_doc: dict[str, Any],
    *,
    card_cap: int = 80,
) -> dict[str, Any]:
    """Derive a bounded collection gate from a canonical panel-readiness report.

    The operator should collect only the examples the current data is missing.
    It is intentionally a research/admin workflow and never a prerequisite for
    publishing a normal match.
    """
    if card_cap < 1:
        raise ValueError("card_cap must be positive")
    summary = readiness_doc.get("summary") or {}
    gap = summary.get("collection_gap") or {}
    confirmed = max(0, int(gap.get("additional_confirmed_labels_needed_from_new_source") or 0))
    negatives = max(0, int(gap.get("additional_negative_panels_needed") or 0))
    if confirmed == 0 and negatives == 0:
        raise ValueError("readiness report does not require additional jersey-number examples")
    required_examples = confirmed + negatives
    # Leave room for front-facing shirts and unreadable panels without asking for
    # an open-ended manual labeling project.
    target_cards = min(card_cap, max(required_examples + 24, 64))
    return {
        "target_cards": target_cards,
        "target_confirmations": confirmed,
        "target_negatives": negatives,
        "audit_purpose": "panel_readiness_recovery",
        "recommended_stop_rule": (
            f"Finish once {confirmed} confirmed readable number panels and "
            f"{negatives} negative panels are collected; skipping remains allowed."
        ),
    }


def build_discovery_dataset_from_subject_review(
    subject_review_doc: dict[str, Any],
    *,
    artifact_root: Path,
    source_match_key: str,
    source_video_key: str,
    team_label_value: str = "A",
    episode_window_frames: int = 300,
) -> dict[str, Any]:
    """Turn stored roster-review crops into a fresh number-discovery source.

    The review document is already a curated set of player-sized crops.  We keep
    their provenance, but intentionally leave jersey annotations empty: this gate
    is for discovering new readable numbers rather than revisiting old labels.
    """
    if episode_window_frames < 1:
        raise ValueError("episode_window_frames must be positive")
    normalized_team = str(team_label_value or "A").upper()
    rows: list[dict[str, Any]] = []
    for card in subject_review_doc.get("cards") or []:
        if not isinstance(card, dict):
            continue
        card_team = str(card.get("team_label") or "U").upper()
        if card_team != normalized_team:
            continue
        subject_id = str(card.get("candidate_subject_id") or "").strip()
        for crop in ((card.get("visual_evidence") or {}).get("anchor_crops") or []):
            if not isinstance(crop, dict):
                continue
            artifact = str(crop.get("artifact") or "").strip()
            crop_id = str(crop.get("anchor_crop_id") or "").strip()
            if not artifact or not crop_id:
                continue
            frame = int(crop.get("frame") or 0)
            bbox = crop.get("bbox_xyxy") or []
            height = _bbox_height(bbox)
            selection_score = float(crop.get("selection_score") or 0.0)
            confidence = float(crop.get("detection_confidence") or 0.0)
            tracklet_id = crop.get("tracklet_id")
            episode_id = stable_key(
                "jersey-discovery-episode",
                {
                    "match": source_match_key,
                    "subject": subject_id or crop_id,
                    "tracklet": tracklet_id,
                    "window": frame // episode_window_frames,
                },
            )
            rows.append(
                {
                    "sample_key": stable_key(
                        "jersey-discovery-sample",
                        {"match": source_match_key, "anchor_crop_id": crop_id},
                    ),
                    "anchor_crop_id": crop_id,
                    "source_match_key": source_match_key,
                    "source_video_key": source_video_key,
                    "candidate_subject_id": subject_id or None,
                    "tracklet_id": tracklet_id,
                    "frame": frame,
                    "team_label": normalized_team,
                    "role": card.get("role"),
                    "artifact": artifact,
                    "artifact_root": str(artifact_root),
                    "artifact_available": (artifact_root / artifact).is_file(),
                    "visibility_episode_id": episode_id,
                    # Larger, crisp crops are more likely to expose readable digits.
                    "discovery_priority_score": round(
                        selection_score + confidence * 0.35 + min(height, 140.0) / 140.0,
                        6,
                    ),
                    "bbox_height_px": round(height, 3),
                    "jersey_number_state": None,
                    "jersey_number": None,
                    "label_state": None,
                    "number": None,
                    "view": "unknown",
                    "clean_jersey_visible": None,
                    "number_panel_visible": None,
                    "annotation_confidence": 0.0,
                    "number_panel_bbox_normalized": None,
                }
            )
    rows.sort(
        key=lambda row: (
            -float(row["discovery_priority_score"]),
            -float(row["bbox_height_px"]),
            int(row["frame"]),
            str(row["sample_key"]),
        )
    )
    digest = identity_jersey_number_dataset_digest(rows)
    return {
        "dataset_version": f"jersey-number-discovery-source:v1:{digest}",
        "dataset_digest": digest,
        "summary": {
            "samples": len(rows),
            "team_label": normalized_team,
            "source": "identity_roster_subject_review_shadow",
        },
        "samples": rows,
    }


def build_discovery_dataset_from_review_gallery(
    gallery_doc: dict[str, Any],
    *,
    artifact_root: Path,
    source_match_key: str,
    source_video_key: str,
    team_label_value: str = "A",
    episode_window_frames: int = 300,
) -> dict[str, Any]:
    """Turn the full identity-review gallery into a diverse number-discovery source.

    The roster-subject review contains only a handful of curated anchors.  The
    gallery already has representative crops across all stints, so it is the right
    source for a bounded recovery gate when the original anchor set is exhausted.
    """
    if episode_window_frames < 1:
        raise ValueError("episode_window_frames must be positive")
    normalized_team = str(team_label_value or "A").upper()
    rows: list[dict[str, Any]] = []
    for player in gallery_doc.get("players") or []:
        if not isinstance(player, dict):
            continue
        if str(player.get("team_label") or "U").upper() != normalized_team:
            continue
        subject_id = str(player.get("stable_subject_id") or "").strip()
        stable_player_id = str(player.get("stable_player_id") or "").strip()
        for stint in player.get("stints") or []:
            if not isinstance(stint, dict):
                continue
            stint_id = str(stint.get("stint_id") or "").strip()
            for crop_index, crop in enumerate(stint.get("crops") or [], start=1):
                if not isinstance(crop, dict):
                    continue
                artifact = str(crop.get("artifact") or "").strip()
                if not artifact:
                    continue
                frame = int(crop.get("frame") or 0)
                track_id = crop.get("track_id")
                height = _bbox_height(crop.get("bbox_xyxy"))
                confidence = float(crop.get("confidence") or 0.0)
                crop_id = stable_key(
                    "jersey-discovery-gallery-crop",
                    {
                        "match": source_match_key,
                        "artifact": artifact,
                        "frame": frame,
                        "subject": subject_id or stable_player_id,
                    },
                )
                episode_id = stable_key(
                    "jersey-discovery-episode",
                    {
                        "match": source_match_key,
                        "subject": subject_id or stable_player_id or crop_id,
                        "window": frame // episode_window_frames,
                    },
                )
                rows.append(
                    {
                        "sample_key": stable_key(
                            "jersey-discovery-sample",
                            {"match": source_match_key, "artifact": artifact, "frame": frame},
                        ),
                        "anchor_crop_id": crop_id,
                        "source_match_key": source_match_key,
                        "source_video_key": source_video_key,
                        "candidate_subject_id": subject_id or None,
                        "stable_player_id": stable_player_id or None,
                        "tracklet_id": str(track_id) if track_id is not None else None,
                        "frame": frame,
                        "team_label": normalized_team,
                        "role": "field_player",
                        "artifact": artifact,
                        "artifact_root": str(artifact_root),
                        "artifact_available": (artifact_root / artifact).is_file(),
                        "visibility_episode_id": episode_id,
                        "discovery_priority_score": round(
                            confidence * 0.35 + min(height, 160.0) / 160.0 + 0.15,
                            6,
                        ),
                        "bbox_height_px": round(height, 3),
                        "source_stint_id": stint_id or None,
                        "source_crop_index": crop_index,
                        "jersey_number_state": None,
                        "jersey_number": None,
                        "label_state": None,
                        "number": None,
                        "view": "unknown",
                        "clean_jersey_visible": None,
                        "number_panel_visible": None,
                        "annotation_confidence": 0.0,
                        "number_panel_bbox_normalized": None,
                    }
                )
    return _discovery_dataset(rows, normalized_team, "identity_review_gallery")


def combine_discovery_datasets(*datasets: dict[str, Any]) -> dict[str, Any]:
    """Merge compatible discovery sources while preserving manual labels.

    The source-aware artifact identity prevents the same crop being counted twice
    while allowing two different matches to use the same relative crop filename.
    """
    rows_by_artifact: dict[tuple[str, str, str], dict[str, Any]] = {}
    team_label = "A"
    sources: list[str] = []
    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        summary = dataset.get("summary") or {}
        team_label = str(summary.get("team_label") or team_label).upper()
        source = str(summary.get("source") or "unknown")
        if source not in sources:
            sources.append(source)
        for row in dataset.get("samples") or []:
            if not isinstance(row, dict):
                continue
            artifact_key = _source_aware_artifact_key(row)
            if not artifact_key:
                continue
            existing = rows_by_artifact.get(artifact_key)
            if existing is None or _sample_merge_sort_key(row) < _sample_merge_sort_key(existing):
                rows_by_artifact[artifact_key] = deepcopy(row)
    combined = _discovery_dataset(
        list(rows_by_artifact.values()),
        team_label,
        "+".join(sources) or "combined_review_sources",
    )
    _refresh_summary(combined)
    return combined


def _discovery_dataset(rows: list[dict[str, Any]], team_label: str, source: str) -> dict[str, Any]:
    rows.sort(
        key=lambda row: (
            -float(row.get("discovery_priority_score") or 0.0),
            -float(row.get("bbox_height_px") or 0.0),
            int(row.get("frame") or 0),
            str(row.get("sample_key") or ""),
        )
    )
    digest = identity_jersey_number_dataset_digest(rows)
    return {
        "dataset_version": f"jersey-number-discovery-source:v2:{digest}",
        "dataset_digest": digest,
        "summary": {"samples": len(rows), "team_label": team_label, "source": source},
        "samples": rows,
    }


def apply_jersey_number_discovery_audit(
    dataset_doc: dict[str, Any],
    reviewed_doc: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Apply explicit research-gate labels while preserving all skipped samples."""
    expected_digest = str(dataset_doc.get("dataset_digest") or "")
    if str(reviewed_doc.get("dataset_digest") or "") != expected_digest:
        raise ValueError("reviewed discovery audit dataset digest mismatch")
    contract = _contract_from_review(reviewed_doc)
    if canonical_digest(contract) != str(reviewed_doc.get("audit_digest") or ""):
        raise ValueError("reviewed discovery audit contract digest mismatch")

    decisions: dict[str, dict[str, Any]] = {}
    for item in reviewed_doc.get("items") or []:
        if not isinstance(item, dict) or not item.get("sample_key"):
            continue
        key = str(item["sample_key"])
        if key in decisions:
            raise ValueError("reviewed discovery audit contains duplicate sample decisions")
        decisions[key] = item

    result = deepcopy(dataset_doc)
    counts = {"confirmed": 0, "absent": 0, "unreadable": 0, "skipped": 0, "pending": 0}
    for sample in result.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        item = decisions.get(str(sample.get("sample_key") or ""))
        if item is None:
            continue
        review = item.get("manual_review") or {}
        status = str(review.get("status") or "pending")
        if status in {"pending", "skipped"}:
            counts["skipped" if status == "skipped" else "pending"] += 1
            # A deliberate skip is still a completed operator decision. Keep it
            # out of later targeted follow-ups without inventing a label.
            if status == "skipped":
                sample["discovery_review_status"] = "skipped"
            continue
        annotation = normalize_jersey_number_annotation(review, allow_missing=False)
        bbox = normalize_normalized_bbox(
            review.get("number_panel_bbox_normalized"),
            field_name="number_panel_bbox_normalized",
        )
        if bbox is None:
            raise ValueError("saved discovery decision requires a number panel box")
        sample.update(annotation)
        sample["label_state"] = annotation["jersey_number_state"]
        sample["number"] = annotation["jersey_number"]
        sample["number_panel_bbox_normalized"] = bbox
        sample["annotation_confidence"] = 1.0
        sample["annotation_source"] = {
            "kind": "manual_review",
            "source": "operator_discovery_gate",
            "review_schema_version": SCHEMA_VERSION,
        }
        if annotation["jersey_number_state"] == "number_confirmed":
            counts["confirmed"] += 1
        elif annotation["jersey_number_state"] == "number_absent":
            counts["absent"] += 1
        else:
            counts["unreadable"] += 1

    result["generated_at"] = generated_at or datetime.now(timezone.utc).isoformat()
    result["dataset_digest"] = identity_jersey_number_dataset_digest(result.get("samples") or [])
    result["dataset_version"] = f"jersey-number-dataset:v3:{result['dataset_digest']}"
    result["discovery_audit_import"] = {
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source_dataset_digest": expected_digest,
        "audit_digest": reviewed_doc.get("audit_digest"),
        "reviewed_document_digest": canonical_digest(reviewed_doc),
        **counts,
    }
    _refresh_summary(result)
    return result


def _select_samples(
    dataset_doc: dict[str, Any],
    *,
    team_label: str,
    target_cards: int,
    unreviewed_only: bool = False,
    require_panel_likelihood: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
    candidates = [
        row for row in dataset_doc.get("samples") or []
        if isinstance(row, dict)
        and str(row.get("team_label") or "").upper() == team_label.upper()
        and bool(row.get("artifact_available"))
        and (not unreviewed_only or _is_unreviewed(row))
    ]
    enriched = [_with_panel_likelihood(row) for row in candidates]
    raw_crop_candidates = [row for row in enriched if _is_raw_anchor_crop(row)]
    panel_candidates = _select_recovery_panel_candidates(raw_crop_candidates)
    selected_pool = panel_candidates if require_panel_likelihood else enriched
    # First pass preserves episode diversity; later passes fill the requested budget.
    selected_pool.sort(key=_sample_sort_key)
    selected: list[dict[str, Any]] = []
    seen_episodes: set[str] = set()
    for sample in selected_pool:
        episode = str(sample.get("visibility_episode_id") or sample.get("sample_key"))
        if episode in seen_episodes:
            continue
        selected.append(sample)
        seen_episodes.add(episode)
        if len(selected) >= target_cards:
            return selected, _selection_diagnostics(
                candidates=candidates,
                raw_crop_candidates=raw_crop_candidates,
                panel_candidates=panel_candidates,
                require_panel_likelihood=require_panel_likelihood,
            )
    selected_keys = {str(sample.get("sample_key")) for sample in selected}
    for sample in selected_pool:
        if str(sample.get("sample_key")) in selected_keys:
            continue
        selected.append(sample)
        if len(selected) >= target_cards:
            break
    return selected, _selection_diagnostics(
        candidates=candidates,
        raw_crop_candidates=raw_crop_candidates,
        panel_candidates=panel_candidates,
        require_panel_likelihood=require_panel_likelihood,
    )


_RECOVERY_PANEL_TOP_PERCENTILE = 0.94


def _is_raw_anchor_crop(sample: dict[str, Any]) -> bool:
    """Return whether an artifact is an unmodified person crop.

    Review-gallery artifacts are useful for identity review, but can contain
    diagnostics and padding that look like number strokes.  A number-panel
    recovery gate must only show the original anchor crop.
    """
    artifact = str(sample.get("artifact") or "").replace("\\", "/")
    return artifact.startswith("anchor_crops/")


def _select_recovery_panel_candidates(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the small, visually strongest tail of raw crop candidates.

    The score is a ranking signal, not an OCR result.  Selecting the upper tail
    avoids wasting the final collection gate on generic front-facing or blank
    shirts when only a handful of readable panel confirmations remain.
    """
    scored = [
        row for row in samples
        if row.get("panel_likelihood") is not None
    ]
    if not scored:
        return []
    scores = np.asarray([float(row["panel_likelihood"]) for row in scored], dtype=np.float32)
    cutoff = float(np.quantile(scores, _RECOVERY_PANEL_TOP_PERCENTILE))
    return [row for row in scored if float(row["panel_likelihood"]) >= cutoff]


def _selection_diagnostics(
    *,
    candidates: list[dict[str, Any]],
    raw_crop_candidates: list[dict[str, Any]],
    panel_candidates: list[dict[str, Any]],
    require_panel_likelihood: bool,
) -> dict[str, int | bool]:
    return {
        "eligible_samples": len(candidates),
        "raw_anchor_crop_candidates": len(raw_crop_candidates),
        "panel_likelihood_candidates": len(panel_candidates),
        "panel_likelihood_required": require_panel_likelihood,
    }


def _with_panel_likelihood(sample: dict[str, Any]) -> dict[str, Any]:
    """Add a conservative visual number-panel likelihood to an audit candidate.

    This is deliberately only a discovery prefilter.  It does not read a jersey
    number and never becomes an identity signal.  Its only job is to avoid sending
    an operator generic player crops when the remaining dataset goal is readable
    number panels.
    """
    enriched = dict(sample)
    enriched["panel_likelihood"] = _estimate_panel_likelihood(sample)
    return enriched


def _estimate_panel_likelihood(sample: dict[str, Any]) -> float | None:
    artifact = str(sample.get("artifact") or "").strip()
    artifact_root = str(sample.get("artifact_root") or "").strip()
    if not artifact or not artifact_root:
        return None
    image = cv2.imread(str(Path(artifact_root) / artifact), cv2.IMREAD_GRAYSCALE)
    if image is None or image.shape[0] < 24 or image.shape[1] < 16:
        return None

    height, width = image.shape[:2]
    # The back/torso sits in this central region for the player crops generated by
    # the identity pipeline.  Restricting the search avoids shoes, grass and heads.
    torso = image[int(height * 0.22):int(height * 0.76), int(width * 0.18):int(width * 0.82)]
    if torso.size < 120:
        return None
    torso = cv2.GaussianBlur(torso, (3, 3), 0)
    low, high = np.percentile(torso, [12, 88])
    contrast = min(1.0, max(0.0, float(high - low) / 92.0))
    sharpness = min(1.0, float(cv2.Laplacian(torso, cv2.CV_64F).var()) / 180.0)

    def component_score(mask: np.ndarray) -> float:
        labels, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        torso_area = float(torso.shape[0] * torso.shape[1])
        score = 0.0
        for label in range(1, labels):
            x, y, component_width, component_height, area = stats[label]
            if area < 3 or area > torso_area * 0.16 or component_height < 3:
                continue
            aspect = component_width / max(1.0, float(component_height))
            if not 0.08 <= aspect <= 1.9:
                continue
            center_x = float(centroids[label][0]) / max(1.0, torso.shape[1])
            center_y = float(centroids[label][1]) / max(1.0, torso.shape[0])
            centrality = max(0.0, 1.0 - abs(center_x - 0.5) * 1.6) * max(0.0, 1.0 - abs(center_y - 0.5))
            score += min(0.28, float(area) / torso_area * 4.0) * centrality
        return min(1.0, score)

    # Black numbers on light bibs and light numbers on dark shirts are both present
    # in the material, so score compact components at both ends of the luminance range.
    dark_mask = (torso <= low).astype(np.uint8)
    light_mask = (torso >= high).astype(np.uint8)
    digit_structure = max(component_score(dark_mask), component_score(light_mask))
    return round(0.52 * digit_structure + 0.30 * contrast + 0.18 * sharpness, 6)


def _is_unreviewed(sample: dict[str, Any]) -> bool:
    if sample.get("discovery_review_status") == "skipped":
        return False
    return not any(
        sample.get(field) is not None
        for field in (
            "jersey_number_state",
            "label_state",
            "jersey_number",
            "number",
            "number_panel_bbox_normalized",
        )
    )


def _sample_sort_key(sample: dict[str, Any]) -> tuple[Any, ...]:
    state = str(sample.get("jersey_number_state") or sample.get("label_state") or "")
    state_rank = {"number_confirmed": 0, "number_unreadable": 1, "number_absent": 2}.get(state, 3)
    view_rank = 0 if str(sample.get("view") or "") == "back" else 1
    return (
        state_rank,
        view_rank,
        not bool(sample.get("clean_jersey_visible")),
        not bool(sample.get("number_panel_visible")),
        -float(sample.get("panel_likelihood") or -1.0),
        -float(sample.get("discovery_priority_score") or 0),
        -float(sample.get("bbox_height_px") or 0),
        -float(sample.get("annotation_confidence") or 0),
        str(sample.get("visibility_episode_id") or ""),
        int(sample.get("frame") or 0),
        str(sample.get("sample_key") or ""),
    )


def _sample_merge_sort_key(sample: dict[str, Any]) -> tuple[Any, ...]:
    """Prefer an operator decision over an equivalent unreviewed crop."""
    review_rank = 0 if str(sample.get("discovery_review_status") or "") == "labeled" else 1
    return (review_rank, *_sample_sort_key(sample))


def _source_aware_artifact_key(sample: dict[str, Any]) -> tuple[str, str, str] | None:
    artifact = str(sample.get("artifact") or "").strip()
    if not artifact:
        return None
    source_match = str(sample.get("source_match_key") or "").strip()
    source_video = str(sample.get("source_video_key") or "").strip()
    if source_match or source_video:
        return (source_match, source_video, artifact)
    return (str(sample.get("artifact_root") or "").strip(), "", artifact)


def _normalize_roster_choices(values: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        number = str(value.get("number") or "").strip()
        label = str(value.get("label") or number).strip()
        if not number.isdigit() or not 1 <= len(number) <= 3 or number in seen:
            raise ValueError("roster choices require unique numeric jersey numbers")
        normalized.append({"number": str(int(number)), "label": label})
        seen.add(number)
    if not normalized:
        raise ValueError("at least one roster choice is required")
    return normalized


def _contract_from_review(reviewed_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": reviewed_doc.get("schema_version"),
        "algorithm": reviewed_doc.get("algorithm"),
        "dataset_digest": reviewed_doc.get("dataset_digest"),
        "team_label": reviewed_doc.get("team_label"),
        "target_confirmations": reviewed_doc.get("target_confirmations"),
        "target_negatives": reviewed_doc.get("target_negatives"),
        "audit_purpose": reviewed_doc.get("audit_purpose"),
        "selection_mode": reviewed_doc.get("selection_mode"),
        "roster_choices": reviewed_doc.get("roster_choices"),
        "items": [
            {
                key: item.get(key)
                for key in (
                    "audit_index", "sample_key", "anchor_crop_id", "source_match_key",
                    "source_video_key", "candidate_subject_id", "frame", "team_label",
                    "visibility_episode_id", "image_filename", "image_available",
                    "existing_annotation",
                )
            }
            for item in reviewed_doc.get("items") or []
            if isinstance(item, dict)
        ],
    }


def _refresh_summary(document: dict[str, Any]) -> None:
    samples = [row for row in document.get("samples") or [] if isinstance(row, dict)]
    states: dict[str, int] = {}
    numbers: dict[str, int] = {}
    for sample in samples:
        state = str(sample.get("jersey_number_state") or sample.get("label_state") or "unknown")
        states[state] = states.get(state, 0) + 1
        number = sample.get("jersey_number", sample.get("number"))
        if state == "number_confirmed" and number is not None:
            value = str(number)
            numbers[value] = numbers.get(value, 0) + 1
    summary = dict(document.get("summary") or {})
    summary["states"] = dict(sorted(states.items()))
    summary["numbers"] = dict(sorted(numbers.items(), key=lambda item: int(item[0])))
    summary["number_panel_bbox_samples"] = sum(
        sample.get("number_panel_bbox_normalized") is not None for sample in samples
    )
    document["summary"] = summary


def _copy_sample_artifact(sample: dict[str, Any], destination: Path) -> bool:
    root_value = str(sample.get("artifact_root") or "").strip()
    artifact_value = str(sample.get("artifact") or "").strip()
    if not root_value or not artifact_value:
        return False
    root = Path(root_value).expanduser().resolve()
    source = (root / artifact_value).resolve()
    try:
        source.relative_to(root)
    except ValueError:
        return False
    if not source.is_file():
        return False
    shutil.copy2(source, destination)
    return True


def _bbox_height(value: Any) -> float:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return 0.0
    try:
        return max(0.0, float(value[3]) - float(value[1]))
    except (TypeError, ValueError):
        return 0.0


def _safe_filename(value: str) -> str:
    safe = "".join(character if character.isalnum() else "-" for character in value)
    return safe[:72].strip("-") or "sample"


def _render_audit_html(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, ensure_ascii=True).replace("</", "<\\/")
    title = html.escape("J8.4 Gate Odkrywania Numerow Koszulek")
    return f"""<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root {{ color-scheme: dark; font-family: Inter,system-ui,sans-serif; }} * {{ box-sizing:border-box; }}
body {{ margin:0; background:#08111f; color:#eef5ff; }} header {{ position:sticky;top:0;z-index:4;display:flex;gap:16px;align-items:center;padding:14px 20px;background:#0d1a2d;border-bottom:1px solid #2b405f; }}
h1 {{ margin:0;font-size:20px; }} .progress {{ flex:1;height:8px;background:#263650; }} .progress>div {{ height:100%;background:#28c76f; }}
main {{ max-width:1180px;margin:0 auto;padding:20px; }} .meta {{ color:#aebed3;margin-bottom:12px; }} .instruction {{ padding:12px;background:#13233b;border-left:4px solid #39bdf8; }}
.canvas-wrap {{ margin:14px 0;height:74vh;min-height:620px;display:grid;place-items:center;background:#020817;border:1px solid #2a3c59;overflow:auto; }} canvas {{ cursor:crosshair;touch-action:none; }}
.choices,.actions {{ display:flex;flex-wrap:wrap;gap:10px;margin:12px 0; }} button {{ min-height:43px;padding:0 15px;border:1px solid #496487;background:#18304f;color:#fff;font-weight:700;cursor:pointer; }} button.selected {{ outline:3px solid #ffd028;background:#30501f; }} button.primary {{ background:#168a4b;border-color:#2ad477; }} button.warn {{ background:#76520d;border-color:#d4a22a; }} button:disabled {{ opacity:.4;cursor:not-allowed; }} .state {{ color:#ffd85a;font-weight:700; }} details {{ color:#aebed3;margin-top:14px; }}
</style></head><body><header><h1>{title}</h1><div class="progress"><div id="progressBar"></div></div><strong id="confirmationText"></strong><strong id="progressText"></strong><button id="download">Zakoncz i pobierz</button></header>
<main><div class="meta" id="meta"></div><div class="instruction">1. Wybierz tylko numer, który widzisz pewnie. 2. Narysuj ciasny prostokąt wokół cyfr albo panelu numeru. 3. Zapisz. Nie zgaduj z tożsamości zawodnika.</div>
<div class="choices" id="numberChoices"></div><div class="choices"><button data-state="number_absent">Brak numeru na koszulce</button><button data-state="number_unreadable">Panel/numer nieczytelny</button></div>
<div class="canvas-wrap"><canvas id="canvas"></canvas></div><div class="state" id="state"></div><div class="actions"><button id="previous">Poprzedni</button><button id="save" class="primary">Zapisz obserwacje</button><button id="clear">Wyczysc box</button><button id="skip" class="warn">Pomin / nie wiem</button><button id="next">Nastepny</button></div>
<details><summary>Jak oznaczać</summary><p><b>Numer:</b> wybierz wyłącznie wtedy, gdy same cyfry są widoczne na cropie. <b>Brak numeru:</b> widać czysty panel koszulki bez cyfr. <b>Nieczytelny:</b> panel jest widoczny, lecz cyfr nie da się przeczytać. W obu ostatnich przypadkach obrysuj panel. <b>Pomiń:</b> nie ma bezpiecznego panelu do obrysowania.</p></details></main>
<script>const audit={payload};const key='number-discovery:'+audit.audit_digest;const stored=JSON.parse(localStorage.getItem(key)||'{{}}');let index=Math.max(0,Math.min(audit.items.length-1,stored.index||0));let decisions=stored.decisions||{{}};let image=new Image(),draftBox=null,dragStart=null,selected=null;const canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d'),wrap=document.querySelector('.canvas-wrap');
function saveLocal(){{localStorage.setItem(key,JSON.stringify({{index,decisions}}));}}function current(){{return audit.items[index];}}function decision(i){{return decisions[i.sample_key]||i.manual_review||{{status:'pending'}};}}function done(){{return audit.items.filter(i=>decision(i).status!=='pending').length;}}function confirmed(){{return audit.items.filter(i=>{{const r=decision(i);return r.status==='labeled'&&r.jersey_number_state==='number_confirmed';}}).length;}}function negatives(){{return audit.items.filter(i=>{{const r=decision(i);return r.status==='labeled'&&(r.jersey_number_state==='number_absent'||r.jersey_number_state==='number_unreadable');}}).length;}}function point(e){{const r=canvas.getBoundingClientRect();return[Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];}}
function selectChoice(state,number){{selected={{state,number:number||null}};document.querySelectorAll('[data-choice],[data-state]').forEach(b=>{{const value=b.dataset.choice||b.dataset.state;b.classList.toggle('selected',value===state+':' +(number||'')||value===state);}});renderState();}}function renderChoices(){{const root=document.getElementById('numberChoices');root.innerHTML='';audit.roster_choices.forEach(c=>{{const b=document.createElement('button');b.dataset.choice='number_confirmed:'+c.number;b.textContent=c.label;b.onclick=()=>selectChoice('number_confirmed',c.number);root.appendChild(b);}});document.querySelectorAll('[data-state]').forEach(b=>{{b.onclick=()=>selectChoice(b.dataset.state,null);}});}}
function renderState(){{const r=decision(current());document.getElementById('state').textContent=selected?`Wybrano: ${{selected.state==='number_confirmed' ? '#'+selected.number : selected.state==='number_absent'?'brak numeru':'nieczytelny panel'}}${{draftBox?' | panel zaznaczony':''}}`:(r.status==='skipped'?'Pominięto':r.status==='pending'?'Wybierz stan i zaznacz panel':'Zapisano');document.getElementById('save').disabled=!(selected&&draftBox&&current().image_available);}}
function draw(){{if(!image.complete||!image.naturalWidth)return;ctx.drawImage(image,0,0,canvas.width,canvas.height);if(!draftBox)return;const[x1,y1,x2,y2]=draftBox;ctx.strokeStyle='#ffd028';ctx.lineWidth=Math.max(2,canvas.width/180);ctx.strokeRect(x1*canvas.width,y1*canvas.height,(x2-x1)*canvas.width,(y2-y1)*canvas.height);ctx.fillStyle='rgba(255,208,40,.13)';ctx.fillRect(x1*canvas.width,y1*canvas.height,(x2-x1)*canvas.width,(y2-y1)*canvas.height);}}
function render(){{const item=current(),r=decision(item);draftBox=r.number_panel_bbox_normalized||null;selected=null;document.getElementById('meta').textContent=`#${{item.audit_index}} / ${{audit.items.length}} | klatka ${{item.frame}} | Team ${{item.team_label}} | wczesniejsza etykieta ukryta`;const n=done(),c=confirmed(),neg=negatives(),goal=Number(audit.target_confirmations||audit.summary?.target_confirmations||0),negGoal=Number(audit.target_negatives||audit.summary?.target_negatives||0),numbersDone=c>=goal,negativesDone=neg>=negGoal;document.getElementById('progressText').textContent=`Przejrzane: ${{n}}/${{audit.items.length}}`;document.getElementById('confirmationText').textContent=negGoal?`Pewne numery: ${{c}}/${{goal}} | negatywy: ${{neg}}/${{negGoal}}${{numbersDone&&negativesDone?' - cel osiagniety':''}}`:(goal?`Pewne numery: ${{c}}/${{goal}}${{numbersDone?' - cel osiagniety':''}}`:`Pewne numery: ${{c}}`);document.getElementById('confirmationText').style.color=numbersDone&&negativesDone?'#28c76f':'#ffd85a';document.getElementById('progressBar').style.width=`${{100*n/audit.items.length}}%`;document.getElementById('previous').disabled=index===0;document.getElementById('next').disabled=index===audit.items.length-1;renderState();if(!item.image_filename){{canvas.width=800;canvas.height=520;ctx.fillStyle='#020817';ctx.fillRect(0,0,800,520);return;}}image=new Image();image.onload=()=>{{canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;const fit=Math.min((wrap.clientWidth-40)/image.naturalWidth,(wrap.clientHeight-40)/image.naturalHeight);const readable=Math.min(12,680/image.naturalHeight);const z=Math.max(1,Math.min(20,Math.max(fit,readable)));canvas.style.width=Math.round(image.naturalWidth*z)+'px';canvas.style.height=Math.round(image.naturalHeight*z)+'px';draw();}};image.src=item.image_filename;saveLocal();}}
canvas.addEventListener('pointerdown',e=>{{if(!current().image_available)return;dragStart=point(e);canvas.setPointerCapture(e.pointerId);}});canvas.addEventListener('pointermove',e=>{{if(!dragStart)return;const q=point(e);draftBox=[Math.min(dragStart[0],q[0]),Math.min(dragStart[1],q[1]),Math.max(dragStart[0],q[0]),Math.max(dragStart[1],q[1])];draw();renderState();}});canvas.addEventListener('pointerup',e=>{{if(!dragStart)return;const q=point(e);draftBox=[Math.min(dragStart[0],q[0]),Math.min(dragStart[1],q[1]),Math.max(dragStart[0],q[0]),Math.max(dragStart[1],q[1])];if(draftBox[2]-draftBox[0]<.01||draftBox[3]-draftBox[1]<.01)draftBox=null;dragStart=null;draw();renderState();}});
function move(d){{index=Math.max(0,Math.min(audit.items.length-1,index+d));render();}}document.getElementById('previous').onclick=()=>move(-1);document.getElementById('next').onclick=()=>move(1);document.getElementById('clear').onclick=()=>{{draftBox=null;draw();renderState();}};document.getElementById('skip').onclick=()=>{{decisions[current().sample_key]={{status:'skipped',jersey_number_state:null,jersey_number:null,number_panel_bbox_normalized:null,reviewed_at:new Date().toISOString()}};move(1);}};document.getElementById('save').onclick=()=>{{if(!(selected&&draftBox))return;decisions[current().sample_key]={{status:'labeled',jersey_number_state:selected.state,jersey_number:selected.number,number_panel_bbox_normalized:draftBox.map(v=>Number(v.toFixed(6))),reviewed_at:new Date().toISOString()}};move(1);}};function download(){{const out=JSON.parse(JSON.stringify(audit));out.reviewed_at=new Date().toISOString();out.items.forEach(i=>i.manual_review=decisions[i.sample_key]||i.manual_review);out.summary.reviewed=done();const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)+'\\n'],{{type:'application/json'}}));a.download='{REVIEWED_FILENAME}';a.click();URL.revokeObjectURL(a.href);}}document.getElementById('download').onclick=download;renderChoices();render();</script></body></html>"""
