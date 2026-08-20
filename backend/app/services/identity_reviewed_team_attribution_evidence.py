from __future__ import annotations

"""Sparse, exact-observation visual evidence for unknown-team review.

Roster identity crops deliberately reject weak detections because they are used
to name a person.  That is stricter than the evidence needed for an operator to
say "this is Team A", "this is Team B", "referee", or "false detection".
This module creates that second, narrower evidence channel without changing
candidate identity, tracklets, or reviewed output.
"""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

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
EVIDENCE_SELECTION_VERSION = "1.1.0"
MAX_CROPS_PER_CASE = 5
MIN_CROPS_PER_CASE = 3
MIN_BBOX_WIDTH_PX = 8
MIN_BBOX_HEIGHT_PX = 18
PREFERRED_MAX_OVERLAP = 0.12
UNUSABLE_MAX_OVERLAP = 0.85


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
    cases: list[dict[str, Any]] = []
    for subject in sorted(
        candidate_document.get("subjects") or [],
        key=lambda row: str(row.get("candidate_subject_id") or ""),
    ):
        subject_id = str(subject.get("candidate_subject_id") or "")
        card = cards.get(subject_id) or {}
        if (
            not subject_id
            or str(subject.get("team_label") or "U").upper() != "U"
            or str(card.get("review_status") or "") != "no_visual_evidence"
            or card.get("requires_operator_review") is False
        ):
            continue
        observations, rejected = _subject_observations(subject, tracklets, frame_positions)
        selected = _select_representative_crops(observations)
        if observations and not selected:
            rejected["overlaps_nearby_person"] += len(observations)
        pairs = _source_pairs(subject, tracklets)
        source_digest = _ownership_digest(subject_id, pairs)
        crops = [
            {
                "anchor_crop_id": _crop_id(subject_id, row["tracklet_id"], row["frame"]),
                "artifact": _artifact_path(subject_id, index, row["frame"]),
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
                "tracklet_ids": sorted(
                    {str(value) for value in subject.get("tracklet_ids") or []}
                ),
                "source_team_label": "U",
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
                row.get("status") != "ready_for_team_attribution" for row in cases
            ),
        },
        "cases": cases,
    }


def materialize_team_attribution_evidence(match_path: Path) -> dict[str, Any]:
    """Render the selected Team-U crops and persist the read-only artifact."""
    candidate_document = _load(match_path / "identity_candidate_shadow.json")
    tracklets_document = _load(match_path / "tracklets.json")
    roster_review_document = _load(
        match_path / "identity_roster_subject_review_shadow.json"
    )
    document = build_team_attribution_evidence(
        candidate_document,
        tracklets_document,
        roster_review_document,
    )
    video_path = match_path / "video.mp4"
    existing = load_team_attribution_evidence(match_path)
    if _cached_evidence_is_current(existing, document, match_path, video_path):
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
            row["status"] = "source_video_unavailable"
    document["summary"]["rendered_reviewable_cases"] = sum(
        len(row.get("rendered_anchor_crops") or []) >= MIN_CROPS_PER_CASE
        for row in document.get("cases") or []
    )
    write_identity_json_atomic(match_path / FILENAME, document)
    return document


def visual_evidence_for_unit(
    document: dict[str, Any],
    *,
    candidate_subject_id: str,
    detected_pairs: list[tuple[str, int]] | set[tuple[str, int]],
) -> dict[str, Any] | None:
    """Return rendered evidence only when it still belongs to the same inputs."""
    expected_digest = _ownership_digest(candidate_subject_id, detected_pairs)
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
    expected_digest = _ownership_digest(candidate_subject_id, detected_pairs)
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


def _subject_observations(
    subject: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
    frame_positions: dict[int, list[dict[str, Any]]],
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


def _ownership_digest(subject_id: str, pairs: Any) -> str:
    normalized = sorted({(str(pair[0]), int(pair[1])) for pair in pairs})
    payload = json.dumps(
        {"candidate_subject_id": subject_id, "detected_pairs": normalized},
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _crop_id(subject_id: str, tracklet_id: str, frame: int) -> str:
    return "team-attribution:" + hashlib.sha256(
        f"{subject_id}|{tracklet_id}|{frame}".encode("utf-8")
    ).hexdigest()[:24]


def _artifact_path(subject_id: str, index: int, frame: int) -> str:
    safe_subject = "".join(char if char.isalnum() or char in "-_" else "-" for char in subject_id)
    return f"team_attribution_evidence/{safe_subject}/{index:02d}_frame_{frame:06d}.jpg"


def _round_or_none(value: Any, digits: int) -> float | None:
    return round(float(value), digits) if value is not None else None


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


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
    expected_cases = {
        str(row.get("candidate_subject_id") or ""): row
        for row in expected.get("cases") or []
    }
    existing_cases = {
        str(row.get("candidate_subject_id") or ""): row
        for row in existing.get("cases") or []
    }
    if set(expected_cases) != set(existing_cases):
        return False
    for subject_id, expected_case in expected_cases.items():
        existing_case = existing_cases[subject_id]
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
