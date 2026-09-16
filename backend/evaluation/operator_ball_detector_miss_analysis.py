from __future__ import annotations

"""Read-only forensics for shadow-evaluator ``detector_miss`` anchors.

This deliberately consumes the same durable operator anchors and exact-frame
resolution/distance contract as :mod:`operator_ball_anchor_shadow`.  It does
not invoke inference and never writes match artifacts.
"""

from collections import Counter, defaultdict
import math
from pathlib import Path
from typing import Any, Mapping

from evaluation.operator_ball_anchor_shadow import (
    DEFAULT_WINDOW_SEC,
    anchor_distance_threshold_px,
    evaluate_operator_ball_anchors,
    resolve_operator_anchor_frame,
)

SCHEMA_VERSION = "operator-ball-detector-miss-analysis:v1"

FRAME_NOT_PROCESSED = "FRAME_NOT_PROCESSED"
RAW_DETECTOR_MISS = "RAW_DETECTOR_MISS"
CANDIDATE_FILTERED = "CANDIDATE_FILTERED"
ACCEPTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR = "ACCEPTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR"
REJECTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR = "REJECTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR"
INCONSISTENT_EVIDENCE = "INCONSISTENT_EVIDENCE"


def analyze_operator_ball_detector_misses(
    anchors: list[Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
    *,
    window_sec: float = DEFAULT_WINDOW_SEC,
) -> dict[str, Any]:
    """Explain only upstream ``detector_miss`` rows from persisted evidence."""

    if window_sec <= 0:
        raise ValueError("window_sec must be positive")
    upstream = evaluate_operator_ball_anchors(anchors, sources, window_sec=window_sec)
    rows: list[dict[str, Any]] = []
    for upstream_row in upstream["anchors"]:
        if upstream_row["classification"] != "detector_miss":
            continue
        anchor = _mapping(upstream_row.get("anchor"))
        source = sources.get(str(anchor.get("source_match_id") or ""))
        rows.append(_analyze_miss(anchor, _mapping(source), upstream_row, window_sec=window_sec))
    rows.sort(
        key=lambda row: (
            row["anchor"]["published_id"],
            row["anchor"]["source_match_id"],
            row["anchor"]["source_time_sec"],
            row["anchor"]["canonical_shot_id"],
        )
    )
    categories = Counter(row["primary_category"] for row in rows)
    rejection_reasons = Counter(reason for row in rows for reason in row["exact_frame"]["rejection_reasons"])
    configurations: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        config = row["detector_configuration"]
        configurations[str(config.get("detector") or "unknown")][row["primary_category"]] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "inference_invoked": False,
        "canonical_artifacts_mutated": False,
        "window_sec": round(window_sec, 3),
        "upstream_anchor_count": upstream["anchor_count"],
        "detector_miss_count": len(rows),
        "misses": rows,
        "summary": {
            "primary_category_counts": dict(sorted(categories.items())),
            "detector_configuration_breakdown": {
                detector: dict(sorted(counts.items())) for detector, counts in sorted(configurations.items())
            },
            "rejection_reason_counts": dict(sorted(rejection_reasons.items())),
        },
    }


def compact_operator_ball_detector_miss_analysis(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable review index, without verbose frame context."""

    compact_rows = []
    for row in report.get("misses") or []:
        exact = _mapping(_mapping(row).get("exact_frame"))
        compact_rows.append({
            "canonical_shot_id": _mapping(row.get("anchor")).get("canonical_shot_id"),
            "published_id": _mapping(row.get("anchor")).get("published_id"),
            "source_match_id": _mapping(row.get("anchor")).get("source_match_id"),
            "source_time_sec": _mapping(row.get("anchor")).get("source_time_sec"),
            "resolved_detector_frame": _mapping(exact.get("resolved_detector_frame")),
            "primary_category": row.get("primary_category"),
            "reason": row.get("reason"),
            "raw_prediction_count": exact.get("raw_prediction_count"),
            "accepted_candidate_count": exact.get("accepted_candidate_count"),
            "rejected_candidate_count": exact.get("rejected_candidate_count"),
            "nearest_accepted_candidate": _mapping(exact.get("nearest_accepted_candidate")) or None,
            "nearest_rejected_candidate": _mapping(exact.get("nearest_rejected_candidate")) or None,
        })
    return {
        "schema_version": report.get("schema_version"),
        "evaluation_only": True,
        "inference_invoked": False,
        "canonical_artifacts_mutated": False,
        "detector_miss_count": report.get("detector_miss_count"),
        "summary": _mapping(report.get("summary")),
        "misses": compact_rows,
    }


def operator_ball_detector_miss_markdown(report: Mapping[str, Any]) -> str:
    """Render a small human-readable diagnostic table without changing data."""

    lines = [
        "# Operator ball detector-miss forensics",
        "",
        "| Shot | Source time | Category | Raw / accepted / rejected |",
        "| --- | ---: | --- | ---: |",
    ]
    for row in report.get("misses") or []:
        anchor = _mapping(_mapping(row).get("anchor"))
        exact = _mapping(_mapping(row).get("exact_frame"))
        counts = f"{exact.get('raw_prediction_count')} / {exact.get('accepted_candidate_count')} / {exact.get('rejected_candidate_count')}"
        lines.append(f"| {anchor.get('canonical_shot_id')} | {anchor.get('source_time_sec')} | {row.get('primary_category')} | {counts} |")
    lines.extend(["", "## Summary", "", "```json", _jsonish(_mapping(report.get("summary"))), "```", ""])
    return "\n".join(lines)


def export_detector_miss_evidence(
    report: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]], evidence_dir: Path,
) -> list[dict[str, Any]]:
    """Optionally render exact-frame evidence only in an explicit external dir."""

    evidence_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for row in report.get("misses") or []:
        anchor = _mapping(_mapping(row).get("anchor"))
        exact = _mapping(_mapping(row).get("exact_frame"))
        source = _mapping(sources.get(str(anchor.get("source_match_id") or "")))
        video_path = Path(str(source.get("video_path") or ""))
        frame = _mapping(exact.get("resolved_detector_frame"))
        base = {
            "canonical_shot_id": anchor.get("canonical_shot_id"),
            "source_match_id": anchor.get("source_match_id"),
            "frame": frame.get("frame"),
        }
        if not video_path.is_file() or frame.get("frame") is None:
            results.append({**base, "status": "unavailable", "reason": "video_unavailable"})
            continue
        try:
            import cv2  # Imported only when an operator requested evidence output.
            capture = cv2.VideoCapture(str(video_path))
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame["frame"]))
            ok, image = capture.read()
            capture.release()
        except Exception:
            ok, image = False, None
        if not ok or image is None:
            results.append({**base, "status": "unavailable", "reason": "frame_unavailable"})
            continue
        point = [anchor.get("x_px"), anchor.get("y_px")]
        if all(isinstance(value, (int, float)) for value in point):
            cv2.drawMarker(image, (round(point[0]), round(point[1])), (0, 255, 255), cv2.MARKER_CROSS, 24, 2)
        filename = f"{anchor.get('source_match_id')}-{anchor.get('canonical_shot_id')}-f{frame['frame']}.png"
        output = evidence_dir / filename
        cv2.imwrite(str(output), image)
        results.append({**base, "status": "written", "path": str(output)})
    return results


def _analyze_miss(anchor: Mapping[str, Any], source: Mapping[str, Any], upstream: Mapping[str, Any], *, window_sec: float) -> dict[str, Any]:
    fps = _number(source.get("fps"))
    raw_frames = _raw_frames(source.get("ball_candidates"))
    resolved = (
        resolve_operator_anchor_frame(raw_frames, source_time_sec=float(anchor["source_time_sec"]), fps=fps)
        if fps and fps > 0
        else None
    )
    tracks = _tracks_by_frame(source.get("ball_tracks"))
    if resolved is None:
        exact = _empty_exact_frame()
        category, reason = FRAME_NOT_PROCESSED, "no_persisted_frame_within_anchor_rounding_tolerance"
    else:
        exact = _exact_frame(resolved, anchor, tracks)
        category, reason = _classify_exact_frame(exact)
    return {
        "anchor": dict(anchor),
        "upstream_shadow": {"classification": upstream.get("classification"), "reason": upstream.get("reason")},
        "primary_category": category,
        "reason": reason,
        "detector_configuration": _detector_configuration(source),
        "exact_frame": exact,
        "local_context": _local_context(raw_frames, anchor, window_sec=window_sec),
    }


def _classify_exact_frame(exact: Mapping[str, Any]) -> tuple[str, str]:
    accepted = int(exact["accepted_candidate_count"])
    rejected = int(exact["rejected_candidate_count"])
    raw = exact.get("raw_prediction_count")
    near_accepted = bool(_mapping(exact.get("nearest_accepted_candidate")).get("within_anchor_distance"))
    near_rejected = bool(_mapping(exact.get("nearest_rejected_candidate")).get("within_anchor_distance"))
    if near_accepted:
        return INCONSISTENT_EVIDENCE, "accepted_candidate_within_authoritative_anchor_distance"
    if near_rejected:
        return CANDIDATE_FILTERED, "rejected_candidate_within_authoritative_anchor_distance"
    if raw == 0 and accepted == 0 and rejected == 0:
        return RAW_DETECTOR_MISS, "raw_prediction_count_zero"
    if accepted:
        return ACCEPTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR, "accepted_candidates_outside_authoritative_anchor_distance"
    if rejected:
        return REJECTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR, "rejected_candidates_outside_authoritative_anchor_distance"
    return INCONSISTENT_EVIDENCE, "raw_predictions_without_persisted_candidate_evidence"


def _raw_frames(document: Any) -> list[dict[str, Any]]:
    rows = []
    for value in _mapping(document).get("frames") or []:
        row = _mapping(value)
        frame, time_sec = _number(row.get("frame")), _number(row.get("time_sec"))
        if frame is None or time_sec is None:
            continue
        rows.append({"frame": int(frame), "time_sec": round(time_sec, 6), "raw_predictions": row.get("raw_predictions"), "candidates": list(row.get("candidates") or []), "rejected_candidates": list(row.get("rejected_candidates") or [])})
    return sorted(rows, key=lambda row: row["frame"])


def _exact_frame(frame: Mapping[str, Any], anchor: Mapping[str, Any], tracks: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    accepted = [_candidate_details(value, frame, anchor, tracks, rejected=False) for value in frame.get("candidates") or []]
    rejected = [_candidate_details(value, frame, anchor, tracks, rejected=True) for value in frame.get("rejected_candidates") or []]
    accepted = [row for row in accepted if row is not None]
    rejected = [row for row in rejected if row is not None]
    accepted.sort(key=_nearest_key)
    rejected.sort(key=_nearest_key)
    track = _mapping(tracks.get(int(frame["frame"])))
    track_point = _point(track.get("position_px"))
    return {
        "resolved_detector_frame": {
            "frame": int(frame["frame"]),
            "time_sec": frame["time_sec"],
            "time_delta_sec": round(float(frame["time_sec"]) - float(anchor["source_time_sec"]), 6),
        },
        "raw_prediction_count": _raw_count(frame.get("raw_predictions")),
        "accepted_candidate_count": len(accepted),
        "rejected_candidate_count": len(rejected),
        "rejection_reasons": sorted(str(row.get("rejection_reason") or "unknown") for row in rejected),
        "nearest_accepted_candidate": accepted[0] if accepted else None,
        "nearest_rejected_candidate": rejected[0] if rejected else None,
        "current_ball_track": {
            "candidate_id": str(track.get("candidate_id") or "") or None,
            "source": str(track.get("source") or "") or None,
            "position_px": track_point,
            "position_m": _point(track.get("position_m")),
            "distance_px_to_operator": _distance(track_point, anchor),
        } if track else None,
    }


def _candidate_details(
    value: Any,
    frame: Mapping[str, Any],
    anchor: Mapping[str, Any],
    tracks: Mapping[int, Mapping[str, Any]],
    *,
    rejected: bool,
) -> dict[str, Any] | None:
    candidate = _mapping(value)
    candidate_id = str(candidate.get("candidate_id") or "")
    point = _point(candidate.get("position_px")) or _bbox_center(candidate.get("bbox_xyxy"))
    if not candidate_id or point is None:
        return None
    width, height = _number(candidate.get("width_px")), _number(candidate.get("height_px"))
    if width is None or height is None:
        bbox = _bbox(candidate.get("bbox_xyxy"))
        if bbox is not None:
            width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    normalized = {"width_px": width or 0.0, "height_px": height or 0.0}
    threshold = anchor_distance_threshold_px(normalized)
    distance = _distance(point, anchor)
    track = _mapping(tracks.get(int(frame["frame"])))
    return {
        "candidate_id": candidate_id,
        "frame": int(frame["frame"]),
        "time_sec": round(_number(candidate.get("time_sec")) or float(frame["time_sec"]), 6),
        "position_px": point,
        "position_m": _point(candidate.get("position_m")),
        "confidence": _number(candidate.get("confidence")),
        "width_px": round(width or 0.0, 3),
        "height_px": round(height or 0.0, 3),
        "area_px": round((width or 0.0) * (height or 0.0), 3),
        "distance_px_to_operator": distance,
        "anchor_distance_threshold_px": threshold,
        "within_anchor_distance": distance is not None and distance <= threshold,
        "currently_selected": str(track.get("candidate_id") or "") == candidate_id,
        **({"rejection_reason": str(candidate.get("reason") or "unknown")} if rejected else {}),
    }


def _local_context(frames: list[Mapping[str, Any]], anchor: Mapping[str, Any], *, window_sec: float) -> dict[str, Any]:
    local = [frame for frame in frames if abs(float(frame["time_sec"]) - float(anchor["source_time_sec"])) <= window_sec]
    before = [frame for frame in local if frame["time_sec"] < anchor["source_time_sec"]]
    after = [frame for frame in local if frame["time_sec"] > anchor["source_time_sec"]]
    return {
        "window_sec": round(window_sec, 3),
        "frame_count": len(local),
        "raw_prediction_count": sum(_raw_count(frame.get("raw_predictions")) or 0 for frame in local),
        "accepted_candidate_count": sum(len(frame.get("candidates") or []) for frame in local),
        "rejected_candidate_count": sum(len(frame.get("rejected_candidates") or []) for frame in local),
        "nearest_persisted_frame_before": _frame_brief(before[-1]) if before else None,
        "nearest_persisted_frame_after": _frame_brief(after[0]) if after else None,
        "nearest_accepted_candidate_before": _nearest_context_candidate(before, anchor, "candidates"),
        "nearest_accepted_candidate_after": _nearest_context_candidate(after, anchor, "candidates"),
        "nearest_rejected_candidate_before": _nearest_context_candidate(before, anchor, "rejected_candidates"),
        "nearest_rejected_candidate_after": _nearest_context_candidate(after, anchor, "rejected_candidates"),
    }


def _frame_brief(frame: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "frame": frame["frame"],
        "time_sec": frame["time_sec"],
        "raw_prediction_count": _raw_count(frame.get("raw_predictions")),
        "accepted_candidate_count": len(frame.get("candidates") or []),
        "rejected_candidate_count": len(frame.get("rejected_candidates") or []),
    }


def _nearest_context_candidate(frames: list[Mapping[str, Any]], anchor: Mapping[str, Any], field: str) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for frame in frames:
        for value in frame.get(field) or []:
            row = _candidate_details(value, frame, anchor, {}, rejected=field == "rejected_candidates")
            if row is not None:
                rows.append(row)
    return (
        min(rows, key=lambda row: (abs(row["time_sec"] - float(anchor["source_time_sec"])), *_nearest_key(row)))
        if rows
        else None
    )


def _detector_configuration(source: Mapping[str, Any]) -> dict[str, Any]:
    candidate_parameters = _mapping(_mapping(source.get("ball_candidates")).get("parameters"))
    track_parameters = _mapping(_mapping(source.get("ball_tracks")).get("parameters"))
    return {
        "detector": candidate_parameters.get("detector"),
        "ball_conf": candidate_parameters.get("ball_conf"),
        "imgsz": candidate_parameters.get("imgsz"),
        "frame_stride": candidate_parameters.get("frame_stride"),
        "pitch_filter": candidate_parameters.get("pitch_filter"),
        "size_filter": candidate_parameters.get("size_filter"),
        "ball_selection_policy": source.get("ball_selection_policy") or track_parameters.get("ball_selection_policy") or candidate_parameters.get("ball_selection_policy"),
    }


def _tracks_by_frame(document: Any) -> dict[int, Mapping[str, Any]]:
    rows = {}
    for value in _mapping(document).get("positions") or []:
        row = _mapping(value)
        frame = _number(row.get("frame"))
        if frame is not None:
            rows[int(frame)] = row
    return rows


def _empty_exact_frame() -> dict[str, Any]:
    return {
        "resolved_detector_frame": None,
        "raw_prediction_count": None,
        "accepted_candidate_count": 0,
        "rejected_candidate_count": 0,
        "rejection_reasons": [],
        "nearest_accepted_candidate": None,
        "nearest_rejected_candidate": None,
        "current_ball_track": None,
    }


def _raw_count(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _nearest_key(row: Mapping[str, Any]) -> tuple[float, float, str]:
    return (float(row.get("distance_px_to_operator") or math.inf), -float(row.get("confidence") or 0.0), str(row.get("candidate_id") or ""))


def _distance(point: list[float] | None, anchor: Mapping[str, Any]) -> float | None:
    if point is None:
        return None
    return round(math.hypot(point[0] - float(anchor["x_px"]), point[1] - float(anchor["y_px"])), 3)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x, y = _number(value[0]), _number(value[1])
    return [x, y] if x is not None and y is not None else None


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    numbers = [_number(item) for item in value[:4]]
    return numbers if all(item is not None for item in numbers) else None


def _bbox_center(value: Any) -> list[float] | None:
    bbox = _bbox(value)
    return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2] if bbox else None


def _jsonish(value: Mapping[str, Any]) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
