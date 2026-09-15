from __future__ import annotations

"""Safe, local production resolution of automatic ball tracks from Shot Review anchors.

``ball_tracks.json`` is immutable generated evidence.  This module creates a
separate derived read model from it and current persisted candidates; an
operator click is authoritative only for its one physical source frame.
"""

import copy
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from app import config
from app.services.ball_tracking import (
    DEFAULT_MAX_INTERPOLATION_GAP_SEC,
    DEFAULT_MAX_INTERPOLATION_SPEED_MPS,
    build_ball_positions,
)
from evaluation.operator_ball_anchor_shadow import (
    DEFAULT_WINDOW_SEC,
    evaluate_operator_ball_anchors,
    extract_operator_ball_anchors,
)

RESOLVED_BALL_TRACKS_FILENAME = "resolved_ball_tracks.json"
RESOLUTION_SCHEMA_VERSION = "resolved-ball-tracks:v1"
OPERATOR_ANCHOR_POLICY = "operator-anchor-reassociation:v1"


def resolve_ball_tracks_document(
    source_match_id: str,
    automatic_tracks: Mapping[str, Any],
    ball_candidates: Mapping[str, Any],
    *,
    fps: float,
    anchors: list[Mapping[str, Any]] | None = None,
    window_sec: float = DEFAULT_WINDOW_SEC,
) -> dict[str, Any]:
    """Build a deterministic final track without mutating automatic evidence.

    Callers may supply anchors for a bounded rebuild/test.  Otherwise the
    durable Shot Review sidecars are read and deduplicated by physical
    frame/time/pixel identity before evaluation.
    """

    automatic = copy.deepcopy(dict(automatic_tracks))
    candidates = copy.deepcopy(dict(ball_candidates))
    if not source_match_id or fps <= 0:
        return _unresolved_copy(automatic, reason="source_artifacts_unavailable")
    source_anchors = _deduplicate_anchors(
        [dict(anchor) for anchor in (anchors if anchors is not None else load_durable_operator_ball_anchors())
         if str(anchor.get("source_match_id") or "") == source_match_id]
    )
    if not source_anchors:
        return _resolved_copy(automatic, anchor_rows=[], applied=[], skipped=[], positions=None, interpolation_gaps=None, affected_regions=[])

    parameters = _record(automatic.get("parameters"))
    candidate_parameters = _record(candidates.get("parameters"))
    evaluation = evaluate_operator_ball_anchors(
        source_anchors,
        {
            source_match_id: {
                "fps": fps,
                "max_link_speed_mps": parameters.get("max_link_speed_mps") or candidate_parameters.get("max_link_speed_mps"),
                "min_start_conf": parameters.get("min_start_conf") or candidate_parameters.get("min_start_conf"),
                "ball_selection_policy": parameters.get("ball_selection_policy") or candidate_parameters.get("ball_selection_policy"),
                "ball_candidates": candidates,
                "ball_tracks": automatic,
            }
        },
        window_sec=window_sec,
    )
    anchor_rows = [dict(row) for row in evaluation.get("anchors") or [] if isinstance(row, Mapping)]
    max_gap_sec = float(parameters.get("max_interpolation_gap_sec") or DEFAULT_MAX_INTERPOLATION_GAP_SEC)
    max_link_speed_mps = float(parameters.get("max_link_speed_mps") or candidate_parameters.get("max_link_speed_mps") or 22.0)
    processed_frames = set(_processed_frames(automatic))
    proposals = [
        proposal
        for row in anchor_rows
        if (proposal := _proposal(row, fps=fps, max_gap_sec=max_gap_sec, max_link_speed_mps=max_link_speed_mps, processed_frames=processed_frames)) is not None
    ]
    accepted, conflicts = _non_conflicting_proposals(proposals)
    skipped: list[dict[str, Any]] = [
        _anchor_status(row, applied=False, reason=_production_skip_reason(row, fps=fps, max_gap_sec=max_gap_sec, max_link_speed_mps=max_link_speed_mps, processed_frames=processed_frames))
        for row in anchor_rows
        if _proposal(row, fps=fps, max_gap_sec=max_gap_sec, max_link_speed_mps=max_link_speed_mps, processed_frames=processed_frames) is None
    ]
    skipped.extend(_anchor_status(proposal["row"], applied=False, reason="conflict") for proposal in conflicts)

    if not accepted:
        return _resolved_copy(automatic, anchor_rows=anchor_rows, applied=[], skipped=skipped, positions=None, interpolation_gaps=None, affected_regions=[])

    selected = _automatic_detected_rows(automatic)
    applied: list[dict[str, Any]] = []
    for proposal in accepted:
        for candidate in proposal["path"]:
            selected[int(candidate["frame"])] = dict(candidate)
        applied.append(
            _anchor_status(
                proposal["row"],
                applied=True,
                reason=proposal["classification"],
                path=proposal["path"],
            )
        )

    rebuilt_positions, rebuilt_interpolation_gaps = build_ball_positions(
        selected,
        processed_frames=_processed_frames(automatic),
        fps=fps,
        max_interpolation_gap_sec=float(parameters.get("max_interpolation_gap_sec") or DEFAULT_MAX_INTERPOLATION_GAP_SEC),
        max_interpolation_speed_mps=float(parameters.get("max_interpolation_speed_mps") or DEFAULT_MAX_INTERPOLATION_SPEED_MPS),
    )
    corrected_frames = {int(candidate["frame"]) for proposal in accepted for candidate in proposal["path"]}
    affected_regions = _affected_local_regions(
        corrected_frames,
        automatic.get("interpolation_gaps") or [],
        rebuilt_interpolation_gaps,
    )
    positions, interpolation_gaps = _merge_local_rebuild(
        automatic,
        rebuilt_positions,
        rebuilt_interpolation_gaps,
        regions=affected_regions,
        corrected_frames=corrected_frames,
    )
    for row in positions:
        if int(row.get("frame") or -1) in corrected_frames and row.get("source") == "detected":
            row["resolution_source"] = "operator_anchor_reassociated"
    return _resolved_copy(
        automatic,
        anchor_rows=anchor_rows,
        applied=applied,
        skipped=skipped,
        positions=positions,
        interpolation_gaps=interpolation_gaps,
        affected_regions=affected_regions,
    )


def write_resolved_ball_tracks_artifact(match_dir: Path) -> dict[str, Any] | None:
    """Rebuild the derived artifact from durable state, never from stale IDs."""

    candidates_path, automatic_path, match_path = (
        match_dir / "ball_candidates.json",
        match_dir / "ball_tracks.json",
        match_dir / "match.json",
    )
    if not candidates_path.exists() or not automatic_path.exists() or not match_path.exists():
        return None
    candidates, automatic, match = _read_object(candidates_path), _read_object(automatic_path), _read_object(match_path)
    fps = _number(_record(match.get("video")).get("fps"))
    if fps is None or fps <= 0:
        return None
    resolved = resolve_ball_tracks_document(match_dir.name, automatic, candidates, fps=fps)
    _write_atomic(match_dir / RESOLVED_BALL_TRACKS_FILENAME, resolved)
    return resolved


def operator_anchor_source_ids(document: Mapping[str, Any]) -> set[str]:
    """Return only the physical matches whose resolved projection can change."""

    return {str(anchor["source_match_id"]) for anchor in extract_operator_ball_anchors(document)}


def rebuild_resolved_ball_tracks_for_source_ids(source_match_ids: set[str] | list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Refresh a bounded set of physical source projections from durable state."""

    result: dict[str, dict[str, Any]] = {}
    for source_match_id in sorted({str(source_match_id) for source_match_id in source_match_ids if str(source_match_id)}):
        artifact = write_resolved_ball_tracks_artifact(config.MATCHES_DIR / source_match_id)
        if artifact is not None:
            result[source_match_id] = artifact
    return result


def rebuild_resolved_ball_tracks_for_documents(
    previous_document: Mapping[str, Any],
    document: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Refresh the union of sources before and after one editorial mutation.

    A removed anchor is as meaningful as a newly added one: rebuilding its
    old physical source restores the derived projection to automatic evidence.
    """

    return rebuild_resolved_ball_tracks_for_source_ids(
        operator_anchor_source_ids(previous_document) | operator_anchor_source_ids(document)
    )


def rebuild_resolved_ball_tracks_for_document(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Compatibility wrapper for callers that only have one document."""

    return rebuild_resolved_ball_tracks_for_source_ids(operator_anchor_source_ids(document))


def load_durable_operator_ball_anchors() -> list[dict[str, Any]]:
    """Read all durable Shot Review anchors without making editorial writes."""

    directory = config.STORAGE_DIR / "editorial" / "shots"
    anchors: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        if path.name.endswith(".authority.json"):
            continue
        try:
            document = _read_object(path)
        except (OSError, ValueError):
            continue
        anchors.extend(extract_operator_ball_anchors(document))
    return _deduplicate_anchors(anchors)


def _proposal(
    row: Mapping[str, Any],
    *,
    fps: float,
    max_gap_sec: float,
    max_link_speed_mps: float,
    processed_frames: set[int],
) -> dict[str, Any] | None:
    classification = str(row.get("classification") or "")
    if classification not in {"wrong_candidate_recovered", "stale_track_recovered"}:
        return None
    path = _contiguous_anchor_path(
        [dict(candidate) for candidate in _record(row.get("anchored")).get("local_shadow_path") or [] if isinstance(candidate, Mapping) and int(candidate.get("frame") or -1) in processed_frames],
        anchor_candidate_id=str(_record(row.get("anchored")).get("anchor_candidate_id") or ""),
        fps=fps,
        max_gap_sec=max_gap_sec,
        max_link_speed_mps=max_link_speed_mps,
    )
    continuity = _record(_record(row.get("anchored")).get("continuity"))
    if classification == "stale_track_recovered" and (len(path) < 2 or not _continuity_is_safe(continuity)):
        return None
    if not path or not _continuity_is_safe(continuity):
        return None
    if any(str(candidate.get("candidate_id") or "") == "" for candidate in path):
        return None
    return {"row": dict(row), "classification": classification, "path": path}


def _production_skip_reason(
    row: Mapping[str, Any],
    *,
    fps: float,
    max_gap_sec: float,
    max_link_speed_mps: float,
    processed_frames: set[int],
) -> str:
    classification = str(row.get("classification") or "inconclusive")
    if classification not in {"wrong_candidate_recovered", "stale_track_recovered"}:
        return classification
    candidate_id = str(_record(row.get("anchored")).get("anchor_candidate_id") or "")
    path = _contiguous_anchor_path(
        [dict(candidate) for candidate in _record(row.get("anchored")).get("local_shadow_path") or [] if isinstance(candidate, Mapping) and int(candidate.get("frame") or -1) in processed_frames],
        anchor_candidate_id=candidate_id,
        fps=fps,
        max_gap_sec=max_gap_sec,
        max_link_speed_mps=max_link_speed_mps,
    )
    return "anchor_without_plausible_path" if len(path) < 2 else "production_safety_gate_failed"


def _contiguous_anchor_path(
    path: list[dict[str, Any]],
    *,
    anchor_candidate_id: str,
    fps: float,
    max_gap_sec: float,
    max_link_speed_mps: float,
) -> list[dict[str, Any]]:
    """Keep only the production-compatible segment that contains the anchor."""

    ordered = sorted(path, key=lambda candidate: int(candidate.get("frame") or 0))
    anchor_index = next((index for index, candidate in enumerate(ordered) if str(candidate.get("candidate_id") or "") == anchor_candidate_id), None)
    if anchor_index is None:
        return []
    lower = anchor_index
    while lower > 0 and _can_extend(ordered[lower - 1], ordered[lower], fps=fps, max_gap_sec=max_gap_sec, max_link_speed_mps=max_link_speed_mps):
        lower -= 1
    upper = anchor_index
    while upper + 1 < len(ordered) and _can_extend(ordered[upper], ordered[upper + 1], fps=fps, max_gap_sec=max_gap_sec, max_link_speed_mps=max_link_speed_mps):
        upper += 1
    return ordered[lower : upper + 1]


def _can_extend(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    fps: float,
    max_gap_sec: float,
    max_link_speed_mps: float,
) -> bool:
    if current.get("segment_start_reason"):
        return False
    previous_frame, current_frame = int(previous.get("frame") or 0), int(current.get("frame") or 0)
    dt = (current_frame - previous_frame) / max(fps, 0.001)
    if current_frame <= previous_frame or dt > max_gap_sec:
        return False
    previous_point, current_point = _point(previous.get("position_m")), _point(current.get("position_m"))
    return previous_point is not None and current_point is not None and math.dist(previous_point, current_point) / dt <= max_link_speed_mps


def _non_conflicting_proposals(proposals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_frame: dict[int, set[str]] = {}
    for proposal in proposals:
        for candidate in proposal["path"]:
            by_frame.setdefault(int(candidate["frame"]), set()).add(str(candidate["candidate_id"]))
    conflicts = [
        proposal for proposal in proposals
        if any(len(by_frame[int(candidate["frame"])]) > 1 for candidate in proposal["path"])
    ]
    accepted = [proposal for proposal in proposals if proposal not in conflicts]
    return accepted, conflicts


def _automatic_detected_rows(automatic: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(row["frame"]): copy.deepcopy(dict(row))
        for row in automatic.get("positions") or []
        if isinstance(row, Mapping)
        and row.get("source") == "detected"
        and int(row.get("frame") or -1) >= 0
    }


def _processed_frames(automatic: Mapping[str, Any]) -> list[int]:
    return [int(row.get("frame") or 0) for row in automatic.get("positions") or [] if isinstance(row, Mapping)]


def _continuity_is_safe(continuity: Mapping[str, Any]) -> bool:
    return bool(continuity) and continuity.get("uses_interpolation") is False and _number(continuity.get("max_observed_speed_mps")) is not None


def _resolved_copy(
    automatic: Mapping[str, Any],
    *,
    anchor_rows: list[dict[str, Any]],
    applied: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    positions: list[dict[str, Any]] | None,
    interpolation_gaps: list[dict[str, Any]] | None,
    affected_regions: list[tuple[int, int]],
) -> dict[str, Any]:
    resolved = copy.deepcopy(dict(automatic))
    if positions is not None:
        resolved["positions"] = positions
        resolved["interpolation_gaps"] = interpolation_gaps or []
        _refresh_summary(resolved)
    resolved["resolution"] = {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "operator_anchor_policy": OPERATOR_ANCHOR_POLICY,
        "automatic_track_source": "ball_tracks.json",
        "valid_anchor_count": len(anchor_rows),
        "applied_anchor_count": len(applied),
        "skipped_anchor_count": len(skipped),
        "applied_corrections": applied,
        "skipped_anchors": skipped,
        "affected_local_regions": [
            {"start_frame": start_frame, "end_frame": end_frame}
            for start_frame, end_frame in affected_regions
        ],
    }
    return resolved


def _unresolved_copy(automatic: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return _resolved_copy(dict(automatic), anchor_rows=[], applied=[], skipped=[{"reason": reason}], positions=None, interpolation_gaps=None, affected_regions=[])


def _anchor_status(
    row: Mapping[str, Any],
    *,
    applied: bool,
    reason: str,
    path: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    anchor = _record(row.get("anchor"))
    anchored = _record(row.get("anchored"))
    return {
        "canonical_shot_id": str(anchor.get("canonical_shot_id") or ""),
        "source_match_id": str(anchor.get("source_match_id") or ""),
        "resolved_anchor_frame": _record(anchored.get("resolved_anchor_frame")).get("frame"),
        "anchor_candidate_id": anchored.get("anchor_candidate_id"),
        "classification": str(row.get("classification") or ""),
        "reason": reason,
        "applied": applied,
        "frames": [
            int(candidate.get("frame") or 0)
            for candidate in (path if path is not None else anchored.get("local_shadow_path") or [])
            if isinstance(candidate, Mapping)
        ],
    }


def _refresh_summary(document: dict[str, Any]) -> None:
    positions = [row for row in document.get("positions") or [] if isinstance(row, Mapping)]
    counts = Counter(str(row.get("source") or "unknown") for row in positions)
    total = len(positions)
    detected_confidences = [float(row.get("confidence") or 0.0) for row in positions if row.get("source") == "detected"]
    document["summary"] = {
        **_record(document.get("summary")),
        "processed_frames": total,
        "detected_frames": counts["detected"],
        "interpolated_frames": counts["interpolated"],
        "unknown_frames": counts["unknown"],
        "detected_coverage": _ratio(counts["detected"], total),
        "interpolated_coverage": _ratio(counts["interpolated"], total),
        "known_coverage": _ratio(counts["detected"] + counts["interpolated"], total),
        "mean_detected_confidence": round(sum(detected_confidences) / len(detected_confidences), 4) if detected_confidences else None,
        "interpolation_gaps": len(document.get("interpolation_gaps") or []),
    }


def _merge_local_rebuild(
    automatic: Mapping[str, Any],
    rebuilt_positions: list[dict[str, Any]],
    rebuilt_interpolation_gaps: list[dict[str, Any]],
    *,
    regions: list[tuple[int, int]],
    corrected_frames: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Regenerate just correction-dependent local regions; preserve all else."""

    rebuilt_by_frame = {int(row.get("frame") or 0): row for row in rebuilt_positions}
    positions = []
    for row in automatic.get("positions") or []:
        if not isinstance(row, Mapping):
            continue
        frame = int(row.get("frame") or 0)
        # Stable automatic detections bound interpolation regions but retain
        # their exact existing refinement metadata. Only a changed detection
        # or a dependent non-detected row is replaced by rebuilt output.
        replace = _frame_in_regions(frame, regions) and (
            frame in corrected_frames or row.get("source") != "detected"
        )
        positions.append(copy.deepcopy(rebuilt_by_frame.get(frame, row)) if replace else copy.deepcopy(dict(row)))
    baseline_gaps = [
        copy.deepcopy(dict(gap))
        for gap in automatic.get("interpolation_gaps") or []
        if isinstance(gap, Mapping) and not _gap_overlaps_regions(gap, regions)
    ]
    local_gaps = [
        copy.deepcopy(dict(gap))
        for gap in rebuilt_interpolation_gaps
        if _gap_overlaps_regions(gap, regions)
    ]
    return positions, sorted([*baseline_gaps, *local_gaps], key=lambda gap: (int(gap.get("start_frame") or 0), int(gap.get("end_frame") or 0)))


def _affected_local_regions(
    corrected_frames: set[int],
    baseline_gaps: list[Any],
    rebuilt_gaps: list[Any],
) -> list[tuple[int, int]]:
    """Return the union of changed detections and their dependent gaps.

    Interpolation geometry is a function of its detected endpoints.  We only
    replace a gap when a corrected detection is one of those endpoints; this
    preserves independent automatic refinements between distant corrections.
    """

    regions = [(frame, frame) for frame in sorted(corrected_frames)]
    for gap in [*baseline_gaps, *rebuilt_gaps]:
        if not isinstance(gap, Mapping):
            continue
        start_frame, end_frame = int(gap.get("start_frame") or -1), int(gap.get("end_frame") or -1)
        if start_frame < 0 or end_frame < start_frame:
            continue
        if any(start_frame <= frame <= end_frame for frame in corrected_frames):
            regions.append((start_frame, end_frame))
    return _merge_regions(regions)


def _merge_regions(regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start_frame, end_frame in sorted(regions):
        if not merged or start_frame > merged[-1][1] + 1:
            merged.append((start_frame, end_frame))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_frame))
    return merged


def _frame_in_regions(frame: int, regions: list[tuple[int, int]]) -> bool:
    return any(start_frame <= frame <= end_frame for start_frame, end_frame in regions)


def _gap_overlaps_regions(gap: Mapping[str, Any], regions: list[tuple[int, int]]) -> bool:
    start_frame, end_frame = int(gap.get("start_frame") or -1), int(gap.get("end_frame") or -1)
    return any(end_frame >= start and start_frame <= end for start, end in regions)


def _deduplicate_anchors(anchors: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for anchor in anchors:
        key = (
            str(anchor.get("source_match_id") or ""),
            round(float(anchor.get("source_time_sec") or 0.0), 6),
            round(float(anchor.get("x_px") or 0.0), 3),
            round(float(anchor.get("y_px") or 0.0), 3),
            round(float(anchor.get("frame_width") or 0.0), 3),
            round(float(anchor.get("frame_height") or 0.0), 3),
        )
        if key[0] and key not in unique:
            unique[key] = dict(anchor)
    return sorted(unique.values(), key=lambda row: (str(row["source_match_id"]), float(row["source_time_sec"]), str(row.get("canonical_shot_id") or "")))


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x, y = _number(value[0]), _number(value[1])
    return [x, y] if x is not None and y is not None else None


def _ratio(value: int, total: int) -> float:
    return round(value / total, 4) if total else 0.0
