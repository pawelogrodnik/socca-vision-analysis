from __future__ import annotations

"""Evaluation-only root-cause traces for ball-continuity shot misses.

This module is intentionally downstream of the frozen shot benchmark.  It
does not provide runtime selection advice and must never be imported by the
production ball or shot pipeline.
"""

from collections import Counter
from typing import Any, Mapping

from app.services import shot_candidates


SCHEMA_VERSION = "ball-continuity-root-cause-analysis:v1"
CONTINUITY_CATEGORIES = {"BALL_CONTINUITY_FAILURE", "MIXED"}
TRACKLET_WINDOW_SEC = 3.0
SELECTION_LOOKBACK_SEC = 6.0
MAX_TRACKLET_SPEED_MPS = 22.0


def analyze_ball_continuity_root_causes(
    failure_analysis: Mapping[str, Any],
    sources: list[Mapping[str, Any]],
    *,
    focus_gold_shot_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Explain continuity-related benchmark misses from persisted evidence."""

    source_by_id = {str(source.get("source_match_id") or ""): source for source in sources}
    relevant = [
        trace
        for trace in failure_analysis.get("gold_shot_traces") or []
        if isinstance(trace, Mapping) and _is_relevant_continuity_miss(trace)
    ]
    rows = [_trace_root_cause(dict(trace), source_by_id.get(str(trace.get("source_match_id") or ""))) for trace in relevant]
    focus_ids = focus_gold_shot_ids or set()
    focus_rows = [
        _trace_root_cause(dict(trace), source_by_id.get(str(trace.get("source_match_id") or "")))
        for trace in failure_analysis.get("gold_shot_traces") or []
        if isinstance(trace, Mapping) and str(trace.get("gold_shot_id") or "") in focus_ids
    ]
    counts = Counter(str(row["diagnosis"]["primary_category"]) for row in rows)
    validation_packets = [
        packet
        for row in rows
        for packet in row["human_validation_packets"]
    ]
    refinement_changes = [
        row
        for row in rows
        if any(path.get("player_refinement_changed") for path in row.get("path_diagnoses") or [])
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "inference_invoked": False,
        "continuity_cases_analyzed": len(rows),
        "subtype_counts": dict(sorted(counts.items())),
        "cases": rows,
        "focus_cases": focus_rows,
        "human_validation_packets": validation_packets,
        "player_overlap_refinement_summary": {
            "changed_near_launch": len(refinement_changes),
            "suppressed_near_launch": sum(
                any(_mapping(path.get("selector")).get("suppression") for path in row.get("path_diagnoses") or [])
                for row in rows
            ),
        },
        "limitations": [
            "Accepted candidate geometry alone cannot establish which physical football is the match ball.",
            "Candidate tracklets are short-lived diagnostic hypotheses, not production tracks or selector inputs.",
        ],
    }


def analyze_selector_policy_regressions(
    baseline_failure_analysis: Mapping[str, Any],
    comparison_failure_analysis: Mapping[str, Any],
    baseline_sources: list[Mapping[str, Any]],
    comparison_sources: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compare benchmark regressions without treating either policy as truth.

    A regression means a frozen gold shot matched by the baseline and missed by
    the comparison.  The output identifies the first different canonical row,
    not an inferred physical-ball identity.
    """

    baseline_by_id = {
        str(trace.get("gold_shot_id") or ""): trace
        for trace in baseline_failure_analysis.get("gold_shot_traces") or []
        if isinstance(trace, Mapping)
    }
    comparison_by_id = {
        str(trace.get("gold_shot_id") or ""): trace
        for trace in comparison_failure_analysis.get("gold_shot_traces") or []
        if isinstance(trace, Mapping)
    }
    baseline_source_by_id = {str(row.get("source_match_id") or ""): row for row in baseline_sources}
    comparison_source_by_id = {str(row.get("source_match_id") or ""): row for row in comparison_sources}
    rows: list[dict[str, Any]] = []
    for gold_shot_id, baseline_trace in sorted(baseline_by_id.items()):
        comparison_trace = comparison_by_id.get(gold_shot_id)
        if not comparison_trace or not bool(baseline_trace.get("benchmark_matched")) or bool(comparison_trace.get("benchmark_matched")):
            continue
        source_match_id = str(baseline_trace.get("source_match_id") or "")
        baseline_source = baseline_source_by_id.get(source_match_id)
        comparison_source = comparison_source_by_id.get(str(comparison_trace.get("source_match_id") or source_match_id))
        if baseline_source is None or comparison_source is None:
            continue
        source_time = _number(baseline_trace.get("source_timestamp_sec"), 0.0)
        divergence = _policy_divergence(
            _mapping(baseline_source.get("ball_tracks")),
            _mapping(comparison_source.get("ball_tracks")),
            source_time,
        )
        candidates = _mapping(baseline_source.get("ball_candidates"))
        rows.append(
            {
                "gold_shot_id": gold_shot_id,
                "timestamp_display": baseline_trace.get("timestamp_display"),
                "source_match_id": source_match_id,
                "source_timestamp_sec": baseline_trace.get("source_timestamp_sec"),
                "first_divergence": divergence,
                "competing_candidates": _candidates_at_frame(candidates, divergence.get("frame")),
                "v1_trusted_history": _trusted_history(_mapping(baseline_source.get("ball_tracks")), divergence.get("time_sec")),
                "v2_trusted_history": _trusted_history(_mapping(comparison_source.get("ball_tracks")), divergence.get("time_sec")),
                "v1_shot_layer": _nearest_contact_path_for_policy_display(baseline_trace).get("shot_policy") if _nearest_contact_path_for_policy_display(baseline_trace) else {},
                "v2_shot_layer": _nearest_contact_path_for_policy_display(comparison_trace).get("shot_policy") if _nearest_contact_path_for_policy_display(comparison_trace) else {},
                "requires_human_validation": False,
                "interpretation": "The rows show canonical-output divergence only; they do not assert which physical ball is correct.",
            }
        )
    return rows


def _is_relevant_continuity_miss(trace: Mapping[str, Any]) -> bool:
    if bool(trace.get("benchmark_matched")):
        return False
    classification = _mapping(trace.get("classification"))
    primary = str(classification.get("primary_category") or "")
    contributors = {str(item) for item in classification.get("contributing_categories") or []}
    return primary == "BALL_CONTINUITY_FAILURE" or (primary == "MIXED" and "BALL_CONTINUITY_FAILURE" in contributors)


def _trace_root_cause(trace: dict[str, Any], source: Mapping[str, Any] | None) -> dict[str, Any]:
    source_time = _number(trace.get("source_timestamp_sec"), 0.0)
    tracks = _mapping(source.get("ball_tracks") if source else {})
    pre_tracks = _mapping(source.get("pre_player_refinement_tracks") if source else {})
    candidates = _mapping(source.get("ball_candidates") if source else {})
    paths = _team_consistent_contact_paths(trace)
    path_diagnoses = [
        _contact_path_root_cause(trace, path, candidates, pre_tracks, tracks, source)
        for path in paths
    ]
    if not path_diagnoses:
        path_diagnoses = [
            _evidence_root_cause(
                trace,
                path=None,
                candidates=candidates,
                pre_tracks=pre_tracks,
                tracks=tracks,
                source=source,
                source_time=source_time,
            )
        ]
    diagnosis = _case_diagnosis(path_diagnoses, has_team_consistent_paths=bool(paths))
    validation_packets = [
        packet
        for path in path_diagnoses
        for packet in [path.get("human_validation_packet")]
        if packet is not None
    ]
    return {
        "gold_shot_id": trace.get("gold_shot_id"),
        "gold_team": trace.get("gold_team"),
        "logical_timestamp_sec": trace.get("logical_timestamp_sec"),
        "timestamp_display": trace.get("timestamp_display"),
        "source_match_id": trace.get("source_match_id"),
        "source_timestamp_sec": trace.get("source_timestamp_sec"),
        "contact_paths": [_compact_contact_path(item) for item in trace.get("contact_paths") or [] if isinstance(item, Mapping)],
        "team_consistent_contact_path_count": len(paths),
        "path_diagnoses": path_diagnoses,
        "diagnosis": diagnosis,
        "human_validation_packets": validation_packets,
    }


def _contact_path_root_cause(
    trace: Mapping[str, Any],
    path: Mapping[str, Any],
    candidates: Mapping[str, Any],
    pre_tracks: Mapping[str, Any],
    tracks: Mapping[str, Any],
    source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return _evidence_root_cause(
        trace,
        path=path,
        candidates=candidates,
        pre_tracks=pre_tracks,
        tracks=tracks,
        source=source,
        source_time=_number(trace.get("source_timestamp_sec"), 0.0),
    )


def _evidence_root_cause(
    trace: Mapping[str, Any],
    *,
    path: Mapping[str, Any] | None,
    candidates: Mapping[str, Any],
    pre_tracks: Mapping[str, Any],
    tracks: Mapping[str, Any],
    source: Mapping[str, Any] | None,
    source_time: float,
) -> dict[str, Any]:
    raw = _mapping(path.get("raw_ball_evidence")) if path else _mapping(trace.get("raw_ball_evidence"))
    launch = _mapping(path.get("ball_launch")) if path else _mapping(trace.get("ball_launch"))
    trajectory = _mapping(path.get("trajectory")) if path else _mapping(trace.get("trajectory"))
    shot_policy = _mapping(path.get("shot_policy")) if path else _mapping(trace.get("shot_policy"))
    target_time = _number(launch.get("time_sec"), source_time)
    pre_position = _nearest_position(pre_tracks, target_time)
    post_position = _nearest_position(tracks, target_time)
    suppression = _suppression_for_position(tracks, pre_position, target_time)
    hypotheses = _candidate_tracklets(candidates, target_time)
    diagnosis = _diagnose(
        raw=raw,
        pre_position=pre_position,
        post_position=post_position,
        suppression=suppression,
        trajectory=trajectory,
        shot_policy=shot_policy,
        hypotheses=hypotheses,
        has_contact_path=path is not None,
    )
    divergence = _selection_divergence(candidates, pre_tracks, tracks, target_time, hypotheses)
    diagnosis = _apply_human_validation(diagnosis, _mapping(source.get("human_validation") if source else {}), divergence)
    if _has_simultaneous_accepted_candidates(raw) and len(hypotheses) >= 2:
        diagnosis["requires_human_validation"] = True
    return {
        "contact_path": _compact_contact_path(path) if path else None,
        "relevant_frames": list(raw.get("frames") or []),
        "selector": {
            "policy": _mapping(tracks.get("parameters")).get("ball_selection_policy"),
            "pre_player_refinement_position": {
                "reconstruction": "evaluation_rebuild_from_persisted_candidates",
                "position": pre_position,
            },
            "post_player_refinement_position": post_position,
            "suppression": suppression,
        },
        "interpolation": _interpolation_near(tracks, int(_number(post_position.get("frame") if post_position else pre_position.get("frame") if pre_position else -1, -1.0))),
        "shot_layer": {
            "launch_row": launch,
            "trajectory": trajectory,
            "rejection_stage": shot_policy.get("rejection_stage"),
            "rejection_reason": shot_policy.get("rejection_reason"),
        },
        "candidate_tracklets": hypotheses,
        "divergence": divergence,
        "diagnosis": diagnosis,
        "player_refinement_changed": _position_identity(pre_position) != _position_identity(post_position),
        "human_validation_packet": _validation_packet(
            trace,
            raw,
            hypotheses,
            divergence,
            diagnosis,
            source_center=target_time,
        ),
    }


def _team_consistent_contact_paths(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    gold_team = str(trace.get("gold_team") or "").strip()
    paths = [dict(path) for path in trace.get("contact_paths") or [] if isinstance(path, Mapping)]
    return [
        path
        for path in paths
        if gold_team and str(_mapping(path.get("contact")).get("team") or "").strip() == gold_team
    ]


def _nearest_contact_path_for_policy_display(trace: Mapping[str, Any]) -> dict[str, Any] | None:
    """Only used by the separate v1/v2 regression display, never by root cause."""

    paths = [dict(path) for path in trace.get("contact_paths") or [] if isinstance(path, Mapping)]
    return min(paths, key=lambda path: abs(_number(_mapping(path.get("contact")).get("timing_delta_sec"), 9999.0))) if paths else None


def _case_diagnosis(path_diagnoses: list[dict[str, Any]], *, has_team_consistent_paths: bool) -> dict[str, Any]:
    if not has_team_consistent_paths:
        return dict(path_diagnoses[0]["diagnosis"])
    diagnoses = [dict(path["diagnosis"]) for path in path_diagnoses]
    categories = {str(diagnosis.get("primary_category") or "") for diagnosis in diagnoses}
    if len(categories) == 1:
        diagnosis = dict(diagnoses[0])
        diagnosis["requires_human_validation"] = any(item.get("requires_human_validation") for item in diagnoses)
        return diagnosis
    return {
        "primary_category": "MIXED",
        "secondary_categories": sorted(categories),
        "first_material_failure": "team_consistent_contact_path_disagreement",
        "confidence": "ambiguous",
        "requires_human_validation": True,
        "explanation": "Team-consistent contact paths reach materially different precise diagnoses.",
    }


def _nearest_position(tracks: Mapping[str, Any], target_time: float) -> dict[str, Any] | None:
    rows = [dict(row) for row in tracks.get("positions") or [] if isinstance(row, Mapping)]
    if not rows:
        return None
    return min(rows, key=lambda row: abs(_number(row.get("time_sec"), target_time) - target_time))


def _suppression_for_position(tracks: Mapping[str, Any], pre_position: Mapping[str, Any] | None, target_time: float) -> dict[str, Any] | None:
    candidate_id = str(_mapping(pre_position).get("candidate_id") or "")
    examples = _mapping(tracks.get("post_filter")).get("suppressed_examples") or []
    matches = [dict(row) for row in examples if isinstance(row, Mapping) and (candidate_id and str(row.get("candidate_id") or "") == candidate_id)]
    if matches:
        return matches[0]
    near = [dict(row) for row in examples if isinstance(row, Mapping) and abs(_number(row.get("time_sec"), -9999.0) - target_time) <= shot_candidates.MAX_LAUNCH_POSITION_DELTA_SEC]
    return near[0] if near else None


def _candidate_tracklets(candidates: Mapping[str, Any], target_time: float) -> list[dict[str, Any]]:
    """Build bounded, deterministic hypotheses only for inspection."""

    rows = [
        dict(candidate)
        for frame in candidates.get("frames") or []
        if isinstance(frame, Mapping) and abs(_number(frame.get("time_sec"), -9999.0) - target_time) <= TRACKLET_WINDOW_SEC
        for candidate in frame.get("candidates") or []
        if isinstance(candidate, Mapping)
    ]
    rows.sort(key=lambda row: (_number(row.get("time_sec"), 0.0), str(row.get("candidate_id") or "")))
    tracklets: list[list[dict[str, Any]]] = []
    for candidate in rows:
        compatible: list[tuple[float, int]] = []
        for index, tracklet in enumerate(tracklets):
            previous = tracklet[-1]
            dt = _number(candidate.get("time_sec"), 0.0) - _number(previous.get("time_sec"), 0.0)
            distance = _distance(previous.get("position_m"), candidate.get("position_m"))
            if dt > 0 and dt <= 0.25 and distance is not None and distance / dt <= MAX_TRACKLET_SPEED_MPS:
                compatible.append((distance, index))
        if compatible:
            _, index = min(compatible, key=lambda item: (item[0], item[1]))
            tracklets[index].append(candidate)
        else:
            tracklets.append([candidate])
    coherent = [tracklet for tracklet in tracklets if len(tracklet) >= 2]
    coherent.sort(
        key=lambda tracklet: (
            -len(tracklet),
            -sum(_number(row.get("confidence"), 0.0) for row in tracklet) / len(tracklet),
            _number(tracklet[0].get("time_sec"), 0.0),
            str(tracklet[0].get("candidate_id") or ""),
        )
    )
    return [
        {
            "tracklet_id": f"diagnostic-ball-{index:02d}",
            "frames": [row.get("frame") for row in tracklet],
            "candidate_ids": [row.get("candidate_id") for row in tracklet],
            "start_time_sec": _round(_number(tracklet[0].get("time_sec"), 0.0)),
            "end_time_sec": _round(_number(tracklet[-1].get("time_sec"), 0.0)),
            "detections": len(tracklet),
            "mean_confidence": _round(sum(_number(row.get("confidence"), 0.0) for row in tracklet) / len(tracklet)),
            "observations": [
                {
                    "frame": row.get("frame"),
                    "time_sec": _round(_number(row.get("time_sec"), 0.0)),
                    "candidate_id": row.get("candidate_id"),
                    "confidence": row.get("confidence"),
                    "position_m": row.get("position_m"),
                }
                for row in _sample_tracklet_observations(tracklet, target_time)
            ],
        }
        for index, tracklet in enumerate(coherent[:4])
    ]


def _diagnose(**values: Any) -> dict[str, Any]:
    raw = _mapping(values["raw"])
    pre = _mapping(values["pre_position"])
    post = _mapping(values["post_position"])
    trajectory = _mapping(values["trajectory"])
    policy = _mapping(values["shot_policy"])
    suppression = values["suppression"]
    accepted = int(_number(raw.get("accepted_candidate_count"), 0.0))
    rejected = int(_number(raw.get("rejected_candidate_count"), 0.0))
    raw_count = int(_number(raw.get("raw_prediction_count"), 0.0))
    boundary = str(trajectory.get("boundary_reason") or "")
    post_source = str(post.get("source") or "")
    if raw_count == 0:
        return _diagnosis("RAW_DETECTOR_MISS", "raw_detector", False, "No raw ball prediction exists in the launch window.")
    if accepted == 0 and rejected:
        return _diagnosis("BALL_CANDIDATE_FILTERED", "candidate_filter", False, "Raw predictions were rejected before candidate selection.")
    if suppression is not None:
        return _diagnosis("PLAYER_OVERLAP_SUPPRESSED", "player_overlap_refinement", False, "A selected pre-refinement candidate was suppressed by the player-overlap filter.")
    if values.get("has_contact_path") is False:
        return _diagnosis("CONTACT_OR_ATTRIBUTION", "contact_attribution", False, "No plausible contact path exists for the frozen shot timestamp.")
    if accepted and pre.get("source") == "detected" and post.get("source") == "unknown":
        return _diagnosis(
            "ACCEPTED_CANDIDATE_NOT_SELECTED",
            "post_player_refinement_or_recovery",
            True,
            "The evaluation reconstruction selected accepted evidence, but the post-refinement canonical row differs.",
        )
    if post_source == "unknown" or boundary.endswith("unknown"):
        return _diagnosis("UNKNOWN_GAP", "ball_timeline", False, "No usable observation remains and the timeline does not bridge the gap.")
    if post_source == "interpolated" or boundary.endswith("interpolated"):
        return _diagnosis("INTERPOLATION_BOUNDARY", "trajectory_boundary", False, "Interpolation forms the first unusable trajectory boundary.")
    if boundary.startswith("untrusted_detected") or (post_source == "detected" and _number(post.get("confidence"), 0.0) < shot_candidates.MIN_BALL_CONFIDENCE):
        return _diagnosis("LOW_CONFIDENCE_CONTINUITY_BREAK", "trust_semantics", False, "The canonical row exists but is below the shot trust threshold.")
    if not bool(policy.get("generated")):
        return _diagnosis("SHOT_LAYER_AFTER_VALID_BALL_TRACK", "shot_layer", False, "The canonical launch is credible; the shot layer rejected it later.")
    return _diagnosis("MIXED", "insufficient_artifact_evidence", True, "Artifacts do not establish one safe first material failure.")


def _selection_divergence(candidates: Mapping[str, Any], pre_tracks: Mapping[str, Any], tracks: Mapping[str, Any], target_time: float, hypotheses: list[dict[str, Any]]) -> dict[str, Any]:
    """Find the first post-refinement change without guessing ball identity."""

    pre_by_frame = {
        int(_number(row.get("frame"), -1.0)): dict(row)
        for row in pre_tracks.get("positions") or []
        if isinstance(row, Mapping)
    }
    post_by_frame = {
        int(_number(row.get("frame"), -1.0)): dict(row)
        for row in tracks.get("positions") or []
        if isinstance(row, Mapping)
    }
    shared_frames = sorted(
        frame
        for frame in pre_by_frame.keys() & post_by_frame.keys()
        if target_time - SELECTION_LOOKBACK_SEC <= _number(post_by_frame[frame].get("time_sec"), -9999.0) <= target_time
    )
    changed = [
        frame
        for frame in shared_frames
        if _position_identity(pre_by_frame[frame]) != _position_identity(post_by_frame[frame])
    ]
    divergence_frame = changed[0] if changed else None
    divergence_pre = pre_by_frame.get(divergence_frame) if divergence_frame is not None else None
    divergence_post = post_by_frame.get(divergence_frame) if divergence_frame is not None else None
    positions = [dict(row) for row in tracks.get("positions") or [] if isinstance(row, Mapping)]
    nearby = [row for row in positions if _number(row.get("time_sec"), -9999.0) <= target_time and str(row.get("source") or "") == "detected"]
    current = nearby[-1] if nearby else None
    alternatives = [item for item in hypotheses if len(item.get("candidate_ids") or []) >= 2]
    segment = _canonical_segment_at(tracks, target_time)
    segment_start_frame = segment.get("start_frame") if segment else None
    segment_start = post_by_frame.get(segment_start_frame) if isinstance(segment_start_frame, int) else None
    return {
        "divergence_frame": divergence_frame,
        "divergence_time_sec": divergence_post.get("time_sec") if divergence_post else None,
        "pre_refinement_position": divergence_pre,
        "post_refinement_position": divergence_post,
        "selected_candidate_id": current.get("candidate_id") if current else None,
        "canonical_segment": {
            **segment,
            "selected_position_at_start": segment_start,
            "accepted_candidates_at_start": _candidates_at_frame(candidates, segment_start_frame),
        } if segment else None,
        "target_proximate_canonical_run": _target_proximate_run(tracks, target_time),
        "plausible_alternative_tracklet_ids": [item.get("tracklet_id") for item in alternatives],
        "requires_human_validation": False,
    }


def _position_identity(position: Mapping[str, Any]) -> tuple[str, str | None]:
    return str(position.get("source") or ""), str(position.get("candidate_id")) if position.get("candidate_id") else None


def _canonical_segment_at(tracks: Mapping[str, Any], target_time: float) -> dict[str, Any] | None:
    positions = [row for row in tracks.get("positions") or [] if isinstance(row, Mapping)]
    target = min(positions, key=lambda row: abs(_number(row.get("time_sec"), target_time) - target_time)) if positions else None
    if target is None:
        return None
    target_frame = int(_number(target.get("frame"), -1.0))
    segments = _mapping(tracks.get("selection_diagnostics")).get("post_filter_segments") or []
    matching = [
        dict(segment)
        for segment in segments
        if isinstance(segment, Mapping)
        and int(_number(segment.get("start_frame"), -1.0)) <= target_frame <= int(_number(segment.get("end_frame"), -1.0))
    ]
    return matching[0] if matching else None


def _target_proximate_run(tracks: Mapping[str, Any], target_time: float) -> dict[str, Any] | None:
    """Describe the run that stays near the selected target position.

    This is intentionally geometric evidence, not a declaration of the real
    match ball.  It helps an operator see whether the selected branch was
    already stable well before the shot window.
    """

    positions = [dict(row) for row in tracks.get("positions") or [] if isinstance(row, Mapping)]
    target = _nearest_position(tracks, target_time)
    target_position = _mapping(target).get("position_m")
    if not isinstance(target_position, list):
        return None
    eligible = [
        row
        for row in positions
        if _number(row.get("time_sec"), -9999.0) <= target_time
        and str(row.get("source") or "") == "detected"
        and _distance(row.get("position_m"), target_position) is not None
        and _distance(row.get("position_m"), target_position) <= 1.0
    ]
    if not eligible:
        return None
    runs: list[list[dict[str, Any]]] = []
    for row in eligible:
        if not runs or int(_number(row.get("frame"), -1.0)) > int(_number(runs[-1][-1].get("frame"), -1.0)) + 3:
            runs.append([row])
        else:
            runs[-1].append(row)
    run = runs[-1]
    return {
        "distance_threshold_m": 1.0,
        "start_frame": run[0].get("frame"),
        "start_time_sec": run[0].get("time_sec"),
        "start_candidate_id": run[0].get("candidate_id"),
        "end_frame": run[-1].get("frame"),
        "end_time_sec": run[-1].get("time_sec"),
        "end_candidate_id": run[-1].get("candidate_id"),
        "detected_rows": len(run),
    }


def _has_simultaneous_accepted_candidates(raw: Mapping[str, Any]) -> bool:
    return any(
        len(_mapping(frame).get("accepted_candidate_ids") or []) > 1
        for frame in raw.get("frames") or []
        if isinstance(frame, Mapping)
    )


def _sample_tracklet_observations(tracklet: list[dict[str, Any]], target_time: float) -> list[dict[str, Any]]:
    nearest = min(tracklet, key=lambda row: abs(_number(row.get("time_sec"), target_time) - target_time))
    selected = tracklet[:3] + [nearest] + tracklet[-3:]
    unique: dict[tuple[Any, Any], dict[str, Any]] = {}
    for row in selected:
        unique[(row.get("frame"), row.get("candidate_id"))] = row
    return list(unique.values())


def _interpolation_near(tracks: Mapping[str, Any], target_frame: int) -> list[dict[str, Any]]:
    return [
        dict(gap)
        for gap in tracks.get("interpolation_gaps") or []
        if isinstance(gap, Mapping)
        and int(_number(gap.get("start_frame"), -9999.0)) - 15 <= target_frame <= int(_number(gap.get("end_frame"), -9999.0)) + 15
    ][:5]


def _policy_divergence(
    baseline_tracks: Mapping[str, Any],
    comparison_tracks: Mapping[str, Any],
    source_time: float,
) -> dict[str, Any]:
    baseline_by_frame = {
        int(_number(row.get("frame"), -1.0)): dict(row)
        for row in baseline_tracks.get("positions") or []
        if isinstance(row, Mapping)
    }
    comparison_by_frame = {
        int(_number(row.get("frame"), -1.0)): dict(row)
        for row in comparison_tracks.get("positions") or []
        if isinstance(row, Mapping)
    }
    frames = sorted(
        frame
        for frame in baseline_by_frame.keys() & comparison_by_frame.keys()
        if source_time - 2.0 <= _number(baseline_by_frame[frame].get("time_sec"), -9999.0) <= source_time + 2.0
    )
    for frame in frames:
        baseline = baseline_by_frame[frame]
        comparison = comparison_by_frame[frame]
        if _position_signature(baseline) != _position_signature(comparison):
            return {
                "frame": frame,
                "time_sec": baseline.get("time_sec"),
                "v1_position": baseline,
                "v2_position": comparison,
            }
    return {"frame": None, "time_sec": None, "v1_position": None, "v2_position": None}


def _position_signature(position: Mapping[str, Any]) -> tuple[Any, ...]:
    point = position.get("position_m")
    normalized_point = tuple(point) if isinstance(point, list) else None
    return (
        str(position.get("source") or ""),
        str(position.get("candidate_id") or ""),
        normalized_point,
    )


def _candidates_at_frame(candidates: Mapping[str, Any], frame: Any) -> list[dict[str, Any]]:
    if not isinstance(frame, int):
        return []
    for item in candidates.get("frames") or []:
        if isinstance(item, Mapping) and int(_number(item.get("frame"), -1.0)) == frame:
            return [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "confidence": candidate.get("confidence"),
                    "position_m": candidate.get("position_m"),
                    "bbox": candidate.get("bbox"),
                }
                for candidate in item.get("candidates") or []
                if isinstance(candidate, Mapping)
            ]
    return []


def _trusted_history(tracks: Mapping[str, Any], target_time: Any) -> dict[str, Any]:
    if not isinstance(target_time, (int, float)):
        return {"trusted_detected_rows": 0, "window_sec": [None, None]}
    rows = [
        row
        for row in tracks.get("positions") or []
        if isinstance(row, Mapping)
        and float(target_time) - 0.5 <= _number(row.get("time_sec"), -9999.0) <= float(target_time)
        and str(row.get("source") or "") == "detected"
        and _number(row.get("confidence"), 0.0) >= shot_candidates.MIN_BALL_CONFIDENCE
    ]
    return {
        "trusted_detected_rows": len(rows),
        "window_sec": [_round(float(target_time) - 0.5), _round(float(target_time))],
        "candidate_ids": [row.get("candidate_id") for row in rows],
    }


def _compact_contact_path(path: Mapping[str, Any]) -> dict[str, Any]:
    contact = _mapping(path.get("contact"))
    launch = _mapping(path.get("ball_launch"))
    return {
        "event_id": contact.get("nearest_contact_event_id"),
        "timestamp_sec": launch.get("time_sec"),
        "team": contact.get("team"),
        "player": contact.get("player"),
        "time_delta_sec": contact.get("timing_delta_sec"),
    }


def _validation_packet(
    trace: Mapping[str, Any],
    raw: Mapping[str, Any],
    hypotheses: list[dict[str, Any]],
    divergence: Mapping[str, Any],
    diagnosis: Mapping[str, Any],
    *,
    source_center: float,
) -> dict[str, Any] | None:
    if not diagnosis.get("requires_human_validation"):
        return None
    logical_center = _number(trace.get("logical_timestamp_sec"), 0.0)
    frames = [frame for frame in raw.get("frames") or [] if isinstance(frame, Mapping)]
    closest_frame = min(frames, key=lambda frame: abs(_number(frame.get("time_sec"), source_center) - source_center)) if frames else {}
    selected = _mapping(closest_frame.get("canonical_selected"))
    candidates = [
        str(candidate.get("candidate_id"))
        for candidate in closest_frame.get("accepted_candidates") or []
        if isinstance(candidate, Mapping) and candidate.get("candidate_id")
    ]
    selected_id = selected.get("candidate_id") or divergence.get("selected_candidate_id")
    return {
        "gold_shot_id": trace.get("gold_shot_id"),
        "logical_window_sec": [_round(max(0.0, logical_center - 0.5)), _round(logical_center + 0.5)],
        "source_window_sec": [_round(max(0.0, source_center - 0.5)), _round(source_center + 0.5)],
        "candidate_tracklets": hypotheses,
        "frame": closest_frame.get("frame"),
        "source_time_sec": closest_frame.get("time_sec"),
        "accepted_candidate_ids": candidates,
        "question": (
            f"At logical {logical_center - 0.5:.3f}-{logical_center + 0.5:.3f}s "
            f"(source {source_center - 0.5:.3f}-{source_center + 0.5:.3f}s), is selected candidate "
            f"{selected_id or 'none'} the active match ball, rather than one of {candidates or ['no accepted candidate']}?"
        ),
        "selected_candidate_id": selected_id,
    }


def _diagnosis(category: str, stage: str, human: bool, explanation: str) -> dict[str, Any]:
    return {"primary_category": category, "secondary_categories": [], "first_material_failure": stage, "confidence": "artifact_supported" if not human else "ambiguous", "requires_human_validation": human, "explanation": explanation}


def _apply_human_validation(
    diagnosis: dict[str, Any],
    validation: Mapping[str, Any],
    divergence: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply an explicit operator conclusion; never infer it from geometry alone."""

    if validation.get("canonical_candidate_is_active") is not False:
        return diagnosis
    if divergence.get("divergence_frame") is None:
        return diagnosis
    return _diagnosis(
        "WRONG_ACTIVE_TRACK_ALREADY_ESTABLISHED",
        "human_validated_pre_shot_track_divergence",
        False,
        "An operator-confirmed active-ball identity disagrees with a canonical divergence established before the target window.",
    )


def _distance(first: Any, second: Any) -> float | None:
    if not isinstance(first, list) or not isinstance(second, list) or len(first) != 2 or len(second) != 2:
        return None
    return ((float(first[0]) - float(second[0])) ** 2 + (float(first[1]) - float(second[1])) ** 2) ** 0.5


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _round(value: float) -> float:
    return round(value, 3)
