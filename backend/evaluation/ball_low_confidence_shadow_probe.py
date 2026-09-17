"""Read-only localized detector-confidence sweep for operator ball anchors.

Inference is injected through ``runner`` so the evaluation cannot write a
match's canonical ball-candidate or ball-track artifacts.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from evaluation.operator_ball_anchor_shadow import (
    anchor_distance_threshold_px,
    build_local_trusted_anchor_path,
    evaluate_operator_ball_anchors,
)
from evaluation.operator_ball_detector_miss_analysis import (
    RAW_DETECTOR_MISS,
    analyze_operator_ball_detector_misses,
)

DEFAULT_THRESHOLDS = (0.03, 0.02, 0.01, 0.005, 0.001)
RECOVERY_CLASSES = {
    "NOT_RECOVERED",
    "RECOVERED_RAW_BUT_FILTERED",
    "RECOVERED_ACCEPTED_ISOLATED",
    "RECOVERED_ACCEPTED_WITH_LOCAL_CONTINUITY",
    "AMBIGUOUS_LOW_CONFIDENCE_RECOVERY",
    "CONTROL_RUNTIME_MISMATCH",
}


def normalize_thresholds(
    values: list[float] | tuple[float, ...], *, production_conf: float
) -> list[float]:
    """Validate a sweep and always include the persisted production threshold."""
    normalized = {round(float(value), 6) for value in values}
    production = round(float(production_conf), 6)
    if production <= 0 or production > 1 or any(value <= 0 or value > 1 for value in normalized):
        raise ValueError("thresholds_must_be_between_zero_and_one")
    normalized.add(production)
    return sorted(normalized, reverse=True)


def probe_low_confidence(
    anchors: list[Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
    *,
    thresholds: list[float] | tuple[float, ...] = DEFAULT_THRESHOLDS,
    window_sec: float = 1.0,
    runner: Callable[[Mapping[str, Any], float, float], Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare localized production-equivalent detector runs without mutation.

    Persisted ``RAW_DETECTOR_MISS`` anchors are the recovery targets. Other
    anchor classes run as controls so the report can quantify the candidate
    growth cost at each confidence without relabelling them as detector misses.
    """
    if window_sec <= 0:
        raise ValueError("window_sec_must_be_positive")

    upstream = evaluate_operator_ball_anchors(anchors, sources, window_sec=window_sec)
    forensic = analyze_operator_ball_detector_misses(anchors, sources, window_sec=window_sec)
    miss_ids = {
        str(row["anchor"]["canonical_shot_id"])
        for row in forensic["misses"]
        if row["primary_category"] == RAW_DETECTOR_MISS
    }
    sweep = sorted(
        {
            value
            for anchor in anchors
            for value in normalize_thresholds(
                thresholds,
                production_conf=_source_production_confidence(
                    sources.get(str(anchor.get("source_match_id"))) or {}
                ),
            )
        },
        reverse=True,
    )
    rows: list[dict[str, Any]] = []

    for upstream_row in upstream["anchors"]:
        anchor = upstream_row["anchor"]
        shot_id = str(anchor["canonical_shot_id"])
        source_id = str(anchor["source_match_id"])
        source = sources.get(source_id)
        if source is None:
            raise KeyError(f"missing_source_artifacts:{source_id}")
        runner_source = {
            **source,
            "_source_match_id": source_id,
            "_anchor_time": float(anchor["source_time_sec"]),
        }
        production_conf = _source_production_confidence(source)
        anchor_sweep = normalize_thresholds(thresholds, production_conf=production_conf)
        threshold_rows = [
            _threshold_result(
                anchor,
                runner(runner_source, threshold, window_sec),
                threshold,
                source,
            )
            for threshold in anchor_sweep
        ]
        rows.append(
            {
                "anchor": anchor,
                "upstream_classification": upstream_row["classification"],
                "role": "raw_detector_miss" if shot_id in miss_ids else "control",
                "production_ball_conf": production_conf,
                "thresholds": threshold_rows,
                "recovery": _recovery(threshold_rows, production_conf) if shot_id in miss_ids else None,
            }
        )

    return {
        "schema_version": "ball-low-confidence-shadow-probe:v1",
        "evaluation_only": True,
        "inference_invoked": bool(rows),
        "canonical_artifacts_mutated": False,
        "thresholds": sweep,
        "window_sec": float(window_sec),
        "production_ball_conf_by_source": {
            source_id: _source_production_confidence(source)
            for source_id, source in sorted(sources.items())
        },
        "source_models": _source_models(rows),
        "upstream_classification_counts": upstream["summary"]["classification_counts"],
        "anchors": rows,
        "threshold_summary": _summary(rows, sweep),
    }


def compact_probe_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return a review-sized projection with no raw boxes or frame dumps."""
    anchors = []
    for row in report.get("anchors") or []:
        anchor = row["anchor"]
        compact_thresholds = [
            {
                key: threshold_row[key]
                for key in (
                    "threshold",
                    "raw_prediction_count",
                    "accepted_candidate_count",
                    "rejected_candidate_count",
                    "near_operator_raw_prediction_count",
                    "near_operator_accepted_count",
                    "near_operator_rejected_count",
                    "local_continuity",
                    "local",
                )
            }
            for threshold_row in row.get("thresholds") or []
        ]
        anchors.append(
            {
                "canonical_shot_id": anchor["canonical_shot_id"],
                "source_match_id": anchor["source_match_id"],
                "source_time_sec": anchor["source_time_sec"],
                "role": row["role"],
                "production_ball_conf": row["production_ball_conf"],
                "upstream_classification": row["upstream_classification"],
                "recovery": row["recovery"],
                "thresholds": compact_thresholds,
            }
        )
    keys = (
        "schema_version",
        "evaluation_only",
        "canonical_artifacts_mutated",
        "thresholds",
        "window_sec",
        "production_ball_conf_by_source",
        "source_models",
        "upstream_classification_counts",
        "threshold_summary",
    )
    return {key: report[key] for key in keys} | {"anchors": anchors}


def render_probe_markdown(report: Mapping[str, Any]) -> str:
    """Render a concise, operator-readable experiment summary."""
    lines = [
        "# Ball low-confidence shadow probe",
        "",
        "Read-only localized detector sweep. Canonical match artifacts were not changed.",
        "",
        f"- Window: ±{float(report['window_sec']):g}s",
        "- Persisted production confidence by source: "
        + ", ".join(
            f"`{source_id}` = `{float(confidence):g}`"
            for source_id, confidence in report["production_ball_conf_by_source"].items()
        ),
        f"- Sweep: {', '.join(f'`{float(value):g}`' for value in report['thresholds'])}",
        "",
        "## RAW_DETECTOR_MISS anchors",
        "",
        "| Source time | Recovery | First threshold | Confidence |",
        "| --- | --- | --- | --- |",
    ]
    misses = [row for row in report.get("anchors") or [] if row["role"] == "raw_detector_miss"]
    if not misses:
        lines.append("| — | No persisted RAW_DETECTOR_MISS anchors | — | — |")
    for row in misses:
        recovery = row["recovery"] or {}
        lines.append(
            "| {time:.3f}s | {classification} | {threshold} | {confidence} |".format(
                time=float(row["anchor"]["source_time_sec"]),
                classification=recovery.get("classification", "—"),
                threshold=_markdown_value(recovery.get("first_recovery_threshold")),
                confidence=_markdown_value(recovery.get("recovered_prediction_confidence")),
            )
        )
    lines.extend(["", "## Candidate volume", "", "| Threshold | Raw / frame | Accepted / frame |", "| --- | --- | --- |"])
    for row in report.get("threshold_summary") or []:
        lines.append(
            f"| {float(row['threshold']):g} | {float(row['raw_per_frame']):.3f} | "
            f"{float(row['accepted_per_frame']):.3f} |"
        )
    return "\n".join(lines) + "\n"


def _source_production_confidence(source: Mapping[str, Any]) -> float:
    parameters = ((source.get("ball_candidates") or {}).get("parameters") or {})
    value = parameters.get("ball_conf")
    if value is None:
        raise ValueError("production_ball_confidence_unavailable")
    return float(value)


def _threshold_result(
    anchor: Mapping[str, Any],
    result: Mapping[str, Any],
    threshold: float,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    frames = list(result.get("frames") or [])
    fps = float(result.get("fps") or 1.0)
    exact_frame = int(round(float(anchor["source_time_sec"]) * fps))
    exact = next(
        (row for row in frames if int(row.get("frame") or -1) == exact_frame),
        {"frame": exact_frame, "time_sec": round(exact_frame / fps, 3)},
    )
    raw = _decorate_rows(exact.get("raw_prediction_rows") or [], anchor)
    accepted = _decorate_rows(exact.get("candidates") or [], anchor)
    rejected = _decorate_rows(exact.get("rejected_candidates") or [], anchor)
    for rows in (raw, accepted, rejected):
        for row in rows:
            row.setdefault("frame", int(exact["frame"]))
            row.setdefault("time_sec", float(exact["time_sec"]))
    near_frames = _near_accepted_frames(frames, anchor)
    near_accepted = [row for row in accepted if row["within_anchor_distance"]]
    continuity = _local_continuity(
        frames,
        near_accepted[0] if len(near_accepted) == 1 else None,
        anchor,
        source,
        fps=fps,
    )
    return {
        "threshold": float(threshold),
        "exact_frame": {"frame": exact.get("frame"), "time_sec": exact.get("time_sec")},
        "raw_prediction_count": len(raw),
        "accepted_candidate_count": len(accepted),
        "rejected_candidate_count": len(rejected),
        "near_operator_raw_prediction_count": sum(row["within_anchor_distance"] for row in raw),
        "near_operator_accepted_count": sum(row["within_anchor_distance"] for row in accepted),
        "near_operator_rejected_count": sum(row["within_anchor_distance"] for row in rejected),
        "raw_predictions": raw,
        "accepted_candidates": accepted,
        "rejected_candidates": rejected,
        "nearest_raw_prediction": _nearest(raw),
        "nearest_accepted_candidate": _nearest(accepted),
        "nearest_rejected_candidate": _nearest(rejected),
        "local_continuity": continuity,
        "model_identity": result.get("model_identity"),
        "local": {
            "processed_frame_count": len(frames),
            "raw_prediction_count": sum(int(row.get("raw_predictions") or 0) for row in frames),
            "accepted_candidate_count": sum(len(row.get("candidates") or []) for row in frames),
            "rejected_candidate_count": sum(len(row.get("rejected_candidates") or []) for row in frames),
            "near_operator_accepted_frames": near_frames,
            "frames_with_near_candidate": len(near_frames),
            "frames_with_multiple_accepted_candidates": sum(
                len(row.get("candidates") or []) > 1 for row in frames
            ),
            "frames_with_more_than_three_accepted_candidates": sum(
                len(row.get("candidates") or []) > 3 for row in frames
            ),
        },
    }


def _local_continuity(
    frames: list[Mapping[str, Any]],
    seed: Mapping[str, Any] | None,
    anchor: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    fps: float,
) -> dict[str, Any] | None:
    if seed is None:
        return None
    prepared_frames = [
        {
            **dict(frame),
            "candidates": [
                {
                    **dict(candidate),
                    "frame": int(candidate.get("frame") or frame["frame"]),
                    "time_sec": float(candidate.get("time_sec") or frame["time_sec"]),
                }
                for candidate in frame.get("candidates") or []
            ],
        }
        for frame in frames
    ]
    path, metrics = build_local_trusted_anchor_path(
        prepared_frames,
        dict(seed),
        anchor_time_sec=float(anchor["source_time_sec"]),
        window_sec=max(
            abs(float(frame.get("time_sec") or 0.0) - float(anchor["source_time_sec"]))
            for frame in frames
        ) if frames else 0.0,
        max_link_speed_mps=float(source.get("max_link_speed_mps") or 22.0),
        min_start_conf=float(source.get("min_start_conf") or 0.08),
        fps=fps,
        policy_version=str(source.get("ball_selection_policy") or "ball-selection:v1"),
    )
    return {
        "candidate_point_count": metrics["candidate_point_count"],
        "start_frame": metrics["start_frame"],
        "end_frame": metrics["end_frame"],
        "max_speed_mps": metrics["max_observed_speed_mps"],
        "plausible": metrics["plausible"],
        "candidate_ids": metrics["candidate_ids"],
        "selection_policy": metrics["selection_policy"],
        "path": [
            {key: candidate.get(key) for key in ("candidate_id", "frame", "time_sec", "position_m", "confidence")}
            for candidate in path
        ],
    }


def _decorate_rows(rows: Any, anchor: Mapping[str, Any]) -> list[dict[str, Any]]:
    decorated = []
    for row in rows:
        item = dict(row)
        item["distance_px"] = _distance(item, anchor)
        item["anchor_distance_threshold_px"] = anchor_distance_threshold_px(item)
        item["within_anchor_distance"] = item["distance_px"] <= item["anchor_distance_threshold_px"]
        decorated.append(item)
    return decorated


def _near_accepted_frames(frames: list[Mapping[str, Any]], anchor: Mapping[str, Any]) -> list[int]:
    return [
        int(frame["frame"])
        for frame in frames
        if any(
            _distance(candidate, anchor) <= anchor_distance_threshold_px(candidate)
            for candidate in frame.get("candidates") or []
        )
    ]


def _nearest(rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return min(
        rows,
        key=lambda row: (
            row["distance_px"],
            -float(row.get("confidence") or 0),
            str(row.get("candidate_id") or ""),
        ),
    )


def _recovery(rows: list[Mapping[str, Any]], production_conf: float) -> dict[str, Any]:
    control = next(row for row in rows if row["threshold"] == production_conf)
    # A RAW_DETECTOR_MISS must replay as raw-empty at persisted configuration.
    if control["raw_prediction_count"] != 0:
        return _recovery_result("CONTROL_RUNTIME_MISMATCH")
    recovered = [row for row in rows if row["near_operator_raw_prediction_count"]]
    if not recovered:
        return _recovery_result("NOT_RECOVERED")

    first = recovered[0]
    nearest = first["nearest_raw_prediction"]
    if first["near_operator_accepted_count"] > 1:
        classification = "AMBIGUOUS_LOW_CONFIDENCE_RECOVERY"
    elif not first["near_operator_accepted_count"]:
        classification = "RECOVERED_RAW_BUT_FILTERED"
    elif (first.get("local_continuity") or {}).get("plausible"):
        classification = "RECOVERED_ACCEPTED_WITH_LOCAL_CONTINUITY"
    else:
        classification = "RECOVERED_ACCEPTED_ISOLATED"
    return _recovery_result(
        classification,
        threshold=float(first["threshold"]),
        confidence=nearest.get("confidence") if nearest else None,
    )


def _recovery_result(
    classification: str, *, threshold: float | None = None, confidence: float | None = None
) -> dict[str, Any]:
    if classification not in RECOVERY_CLASSES:
        raise ValueError(f"unknown_recovery_class:{classification}")
    return {
        "classification": classification,
        "first_recovery_threshold": threshold,
        "recovered_prediction_confidence": confidence,
    }


def _summary(rows: list[Mapping[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    output = []
    raw_misses = [row for row in rows if row["role"] == "raw_detector_miss"]
    controls = [row for row in rows if row["role"] == "control"]
    for threshold in thresholds:
        selected = [_threshold_at(row, threshold) for row in rows]
        selected = [row for row in selected if row is not None]
        miss_selected = [_threshold_at(row, threshold) for row in raw_misses]
        miss_selected = [row for row in miss_selected if row is not None]
        control_selected = [_threshold_at(row, threshold) for row in controls]
        control_selected = [row for row in control_selected if row is not None]
        frames = sum(row["local"]["processed_frame_count"] for row in selected) or 1
        production_rows = [
            _threshold_at(row, float(row["production_ball_conf"]))
            for row in rows
            if _threshold_at(row, threshold) is not None
        ]
        production_rows = [row for row in production_rows if row is not None]
        raw_total = sum(row["local"]["raw_prediction_count"] for row in selected)
        accepted_total = sum(row["local"]["accepted_candidate_count"] for row in selected)
        baseline_raw = sum(row["local"]["raw_prediction_count"] for row in production_rows)
        baseline_accepted = sum(row["local"]["accepted_candidate_count"] for row in production_rows)
        output.append(
            {
                "threshold": threshold,
                "raw_per_frame": round(raw_total / frames, 3),
                "accepted_per_frame": round(accepted_total / frames, 3),
                "rejected_per_frame": round(sum(row["local"]["rejected_candidate_count"] for row in selected) / frames, 3),
                "raw_detector_miss_anchors": len(raw_misses),
                "recoveries_at_threshold": sum(
                    row["near_operator_raw_prediction_count"] > 0 for row in miss_selected
                ),
                "control_anchor_count": len(control_selected),
                "control_anchors_matched_near_operator": sum(
                    row["near_operator_accepted_count"] > 0 for row in control_selected
                ),
                "control_extra_competing_accepted_candidates_near_anchor": sum(
                    max(0, int(row["near_operator_accepted_count"]) - 1)
                    for row in control_selected
                ),
                "frames_with_multiple_accepted_candidates": sum(
                    row["local"]["frames_with_multiple_accepted_candidates"] for row in selected
                ),
                "frames_with_more_than_three_accepted_candidates": sum(
                    row["local"]["frames_with_more_than_three_accepted_candidates"] for row in selected
                ),
                "raw_candidate_multiplier_vs_production": _multiplier(raw_total, baseline_raw),
                "accepted_candidate_multiplier_vs_production": _multiplier(
                    accepted_total,
                    baseline_accepted,
                ),
            }
        )
    return output


def _threshold_at(row: Mapping[str, Any], threshold: float) -> Mapping[str, Any] | None:
    return next(
        (
            threshold_row
            for threshold_row in row["thresholds"]
            if float(threshold_row["threshold"]) == float(threshold)
        ),
        None,
    )


def _multiplier(current: int, baseline: int) -> float | None:
    return round(current / baseline, 3) if baseline else None


def _source_models(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    models = {}
    for row in rows:
        source_id = str(row["anchor"]["source_match_id"])
        threshold_rows = row.get("thresholds") or []
        model = threshold_rows[0].get("model_identity") if threshold_rows else None
        if model is not None:
            models[source_id] = model
    return dict(sorted(models.items()))


def _distance(row: Mapping[str, Any], anchor: Mapping[str, Any]) -> float:
    point = row.get("position_px") or row.get("center_px") or [0, 0]
    return round(
        ((float(point[0]) - float(anchor["x_px"])) ** 2 + (float(point[1]) - float(anchor["y_px"])) ** 2) ** 0.5,
        3,
    )


def _markdown_value(value: Any) -> str:
    return "—" if value is None else f"{float(value):g}"
