from __future__ import annotations

"""Evaluation-only, reproducible pipeline diagnosis for missed shot anchors.

This module is intentionally outside ``app.services``.  It uses frozen
benchmark and editorial inputs to explain a policy run; runtime candidate
generation never imports it.
"""

from collections import Counter
from copy import deepcopy
from typing import Any, Mapping

from app.services import shot_candidates
from evaluation.shot_candidate_benchmark import DEFAULT_TOLERANCE_SEC, benchmark_shot_candidates
from evaluation.shot_candidate_failure_analysis import analyze_shot_candidate_failures


SCHEMA_VERSION = "shot-miss-deep-dive:v1"
DIRECTION_REJECTIONS = {"not_goalward", "unknown_team_without_goal_approach", "unsupported_attack_axis"}
TRAJECTORY_REJECTIONS = {"missing_continuous_ball_trajectory", "trajectory_not_meaningful"}


def analyze_shot_miss_deep_dive(
    candidates_doc: Mapping[str, Any],
    goldset_doc: Mapping[str, Any],
    sources: list[Mapping[str, Any]],
    *,
    v2_candidates_doc: Mapping[str, Any] | None = None,
    v3_candidates_doc: Mapping[str, Any] | None = None,
    editorial_doc: Mapping[str, Any] | None = None,
    tolerance_sec: float = DEFAULT_TOLERANCE_SEC,
) -> dict[str, Any]:
    """Diagnose a policy run without feeding any diagnosis back into it."""

    failures = analyze_shot_candidate_failures(candidates_doc, goldset_doc, sources, tolerance_sec=tolerance_sec)
    benchmark = benchmark_shot_candidates(candidates_doc, goldset_doc, tolerance_sec=tolerance_sec)
    source_by_id = {str(source.get("source_match_id") or ""): source for source in sources}
    all_cases = [
        _case_record(trace, candidates_doc, source_by_id.get(str(trace.get("source_match_id") or "")), tolerance_sec)
        for trace in failures.get("gold_shot_traces") or []
        if isinstance(trace, Mapping)
    ]
    misses = [case for case in all_cases if not case["benchmark_matched"]]
    risk = _negative_risk_summary(
        candidates_doc,
        benchmark,
        v2_candidates_doc=v2_candidates_doc,
        v3_candidates_doc=v3_candidates_doc,
        editorial_doc=editorial_doc,
    )
    fix_families = _fix_family_risk_matrix(misses, risk)
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "candidate_policy_version": candidates_doc.get("policy_version"),
        "goldset_schema_version": goldset_doc.get("schema_version"),
        "inference_invoked": False,
        "benchmark": benchmark["summary"],
        "funnel": {
            "all_canonical_shots": _funnel(all_cases),
            "manual_origin_shots": _funnel([case for case in all_cases if case.get("origin") == "manual"]),
        },
        "misses": misses,
        "miss_count": len(misses),
        "primary_cause_distribution": dict(sorted(Counter(case["primary_cause"] for case in misses).items())),
        "weak_detected_boundary_analysis": [
            item
            for case in misses
            for item in case["weak_detected_boundaries"]
        ],
        "trajectory_not_meaningful_analysis": [
            item
            for case in misses
            for item in case["trajectory_not_meaningful"]
        ],
        "not_goalward_analysis": [
            item
            for case in misses
            for item in case["not_goalward_evidence"]
        ],
        "negative_risk": risk,
        "fix_family_risk_matrix": fix_families,
        "recommended_next_experiment": _recommend(fix_families),
        "limitations": [
            "Primary causes are deterministic evidence classifications, not visual confirmation of ball identity.",
            "Counterfactual weak-boundary rows are local evaluation copies; canonical tracks and candidate artifacts are never mutated.",
            "Goldset and operator-review data are read only by this evaluation module, never by runtime candidate generation.",
        ],
    }


def _case_record(
    trace: Mapping[str, Any],
    candidates_doc: Mapping[str, Any],
    source: Mapping[str, Any] | None,
    tolerance_sec: float,
) -> dict[str, Any]:
    team_paths = _team_paths(trace)
    proximity = _candidate_proximity(trace, candidates_doc, tolerance_sec)
    weak_boundaries = [
        _weak_boundary(path, source, _number(trace.get("logical_timestamp_sec"), 0.0))
        for path in team_paths
    ]
    weak_boundaries = [item for item in weak_boundaries if item is not None]
    trajectory_details = [_trajectory_not_meaningful(path) for path in team_paths]
    trajectory_details = [item for item in trajectory_details if item is not None]
    # Direction evidence is retained for every local contact path.  A wrong-team
    # contact can still explain why the canonical team did not get a viable
    # hypothesis, and hiding its geometry would make the diagnostic incomplete.
    direction_details = [
        _not_goalward(path, source, _text(trace.get("gold_team")))
        for path in trace.get("contact_paths") or []
        if isinstance(path, Mapping)
    ]
    direction_details = [item for item in direction_details if item is not None]
    primary, secondary, stage, confidence, explanation = _classify(
        trace,
        team_paths,
        proximity,
        weak_boundaries,
        trajectory_details,
        direction_details,
    )
    return {
        "gold_shot_id": trace.get("gold_shot_id"),
        "timestamp_display": trace.get("timestamp_display"),
        "logical_timestamp_sec": trace.get("logical_timestamp_sec"),
        "source_match_id": trace.get("source_match_id"),
        "source_timestamp_sec": trace.get("source_timestamp_sec"),
        "team": trace.get("gold_team"),
        "outcome": trace.get("gold_outcome"),
        "origin": trace.get("gold_origin"),
        "player": _gold_player(trace),
        "benchmark_matched": bool(trace.get("benchmark_matched")),
        "primary_cause": primary,
        "secondary_causes": secondary,
        "failure_stage": stage,
        "confidence": confidence,
        "evidence_explanation": explanation,
        "contact_layer": [_contact_summary(path) for path in trace.get("contact_paths") or [] if isinstance(path, Mapping)],
        "pipeline_funnel": _case_funnel(trace, team_paths),
        "candidate_proximity": proximity,
        "weak_detected_boundaries": weak_boundaries,
        "trajectory_not_meaningful": trajectory_details,
        "not_goalward_evidence": direction_details,
        "v4_bridge": [_bridge_summary(path) for path in team_paths],
    }


def _team_paths(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    team = _text(trace.get("gold_team"))
    paths = [path for path in trace.get("contact_paths") or [] if isinstance(path, Mapping)]
    return [path for path in paths if not team or _text(_mapping(path.get("contact")).get("team")) == team]


def _gold_player(trace: Mapping[str, Any]) -> str | None:
    # Failure traces intentionally omit player in older schema versions.
    return _text(trace.get("gold_player"))


def _contact_summary(path: Mapping[str, Any]) -> dict[str, Any]:
    contact = _mapping(path.get("contact"))
    policy = _mapping(path.get("shot_policy"))
    launch = _mapping(path.get("ball_launch"))
    return {
        "event_id": contact.get("nearest_contact_event_id"),
        "team": contact.get("team"),
        "player": contact.get("player"),
        "status": contact.get("status"),
        "timing_delta_sec": contact.get("timing_delta_sec"),
        "trusted_launch_exists": bool(launch.get("valid_for_shot_generator")),
        "rejection_reason": policy.get("rejection_reason"),
        "candidate_emitted": bool(policy.get("generated")),
    }


def _case_funnel(trace: Mapping[str, Any], team_paths: list[Mapping[str, Any]]) -> dict[str, bool]:
    contact = bool(team_paths)
    launch = contact and any(bool(_mapping(path.get("ball_launch")).get("valid_for_shot_generator")) for path in team_paths)
    usable_trajectory = launch and any(_trajectory_summary(path) is not None for path in team_paths)
    meaningful = usable_trajectory and any(
        _text(_mapping(path.get("shot_policy")).get("rejection_reason")) not in TRAJECTORY_REJECTIONS
        for path in team_paths
    )
    direction = meaningful and any(
        _text(_mapping(path.get("shot_policy")).get("rejection_reason")) not in DIRECTION_REJECTIONS
        for path in team_paths
    )
    emitted = direction and any(bool(_mapping(path.get("shot_policy")).get("generated")) for path in team_paths)
    return {
        "plausible_contact": contact,
        "trusted_launch": launch,
        "usable_trajectory": usable_trajectory,
        "meaningful_trajectory": meaningful,
        "direction_accepted": direction,
        "candidate_emitted": emitted,
        "benchmark_matched": bool(trace.get("benchmark_matched")),
    }


def _funnel(cases: list[Mapping[str, Any]]) -> dict[str, int]:
    keys = ("plausible_contact", "trusted_launch", "usable_trajectory", "meaningful_trajectory", "direction_accepted", "candidate_emitted", "benchmark_matched")
    return {"canonical_shots": len(cases), **{key: sum(bool(_mapping(case.get("pipeline_funnel")).get(key)) for case in cases) for key in keys}}


def _candidate_proximity(trace: Mapping[str, Any], candidates_doc: Mapping[str, Any], tolerance_sec: float) -> dict[str, Any]:
    source_match_id = _text(trace.get("source_match_id"))
    logical_time = _number(trace.get("logical_timestamp_sec"), 0.0)
    candidates = [
        row for row in candidates_doc.get("candidates") or []
        if isinstance(row, Mapping) and _text(row.get("source_match_id")) == source_match_id
    ]
    nearest = min(candidates, key=lambda row: (abs(_number(row.get("logical_timestamp_sec"), 0.0) - logical_time), str(row.get("candidate_id") or ""))) if candidates else None
    delta = _number(nearest.get("logical_timestamp_sec"), 0.0) - logical_time if nearest else None
    return {
        "nearest_candidate_id": nearest.get("candidate_id") if nearest else None,
        "nearest_candidate_timestamp_sec": nearest.get("logical_timestamp_sec") if nearest else None,
        "nearest_candidate_team": nearest.get("suggested_team_name") if nearest else None,
        "nearest_candidate_delta_sec": _round(delta) if delta is not None else None,
        "candidate_within_benchmark_tolerance": bool(delta is not None and abs(delta) <= tolerance_sec),
    }


def _trajectory_summary(path: Mapping[str, Any]) -> Mapping[str, Any] | None:
    trajectory = _mapping(path.get("trajectory"))
    policy_summary = trajectory.get("policy_trajectory_summary")
    if isinstance(policy_summary, Mapping):
        return policy_summary
    summary = trajectory.get("summary")
    return summary if isinstance(summary, Mapping) else None


def _bridge_summary(path: Mapping[str, Any]) -> dict[str, Any] | None:
    bridge = _mapping(_mapping(path.get("trajectory")).get("continuity_bridge"))
    return dict(bridge) if bridge else None


def _trajectory_not_meaningful(path: Mapping[str, Any]) -> dict[str, Any] | None:
    policy = _mapping(path.get("shot_policy"))
    if _text(policy.get("rejection_reason")) != "trajectory_not_meaningful":
        return None
    summary = _trajectory_summary(path) or {}
    distance = _number(summary.get("distance_m"), 0.0)
    speed = _number(summary.get("mean_speed_mps"), 0.0)
    samples = int(_number(summary.get("sample_count"), 0.0))
    failures = []
    if samples < 3:
        failures.append("sample_count")
    if distance < shot_candidates.MIN_TRAJECTORY_DISTANCE_M:
        failures.append("distance")
    if speed < shot_candidates.MIN_TRAJECTORY_SPEED_MPS:
        failures.append("speed")
    return {
        "contact_event_id": _mapping(path.get("contact")).get("nearest_contact_event_id"),
        "failed_conditions": failures or ["short_prefix_or_geometry"],
        "sample_count": samples,
        "distance_m": _round(distance),
        "duration_sec": summary.get("duration_sec"),
        "mean_speed_mps": _round(speed),
        "short_prefix_eligible": bool("strong_trusted_prefix_before_boundary" in _candidate_reasons(path)),
        "raw_evidence_interpretation": "bad_evidence_or_unresolved_semantics" if failures else "requires_semantic_review",
    }


def _not_goalward(
    path: Mapping[str, Any],
    source: Mapping[str, Any] | None,
    canonical_team: str | None,
) -> dict[str, Any] | None:
    policy = _mapping(path.get("shot_policy"))
    if _text(policy.get("rejection_reason")) != "not_goalward":
        return None
    summary = _trajectory_summary(path) or {}
    start = summary.get("start_position_m")
    end = summary.get("end_position_m")
    phase = _mapping(path.get("phase"))
    direction = _text(phase.get("attack_direction")) or "unknown"
    pitch_length = _number(source.get("pitch_length_m"), 47.4) if source else 47.4
    pitch_width = _number(source.get("pitch_width_m"), 30.0) if source else 30.0
    progress = shot_candidates._goalward_progress(start, end, direction) if _valid_position(start) and _valid_position(end) else None
    return {
        "contact_event_id": _mapping(path.get("contact")).get("nearest_contact_event_id"),
        "attacking_team": _mapping(path.get("contact")).get("team"),
        "team_matches_canonical": _text(_mapping(path.get("contact")).get("team")) == canonical_team,
        "attack_direction": direction,
        "goal_used": "y_min" if direction == "towards_y_min" else "y_max" if direction == "towards_y_max" else "unknown",
        "launch_position_m": start,
        "endpoint_position_m": end,
        "signed_goalward_progress_m": _round(progress) if progress is not None else None,
        "endpoint_goal_distance_m": _round_or_none(shot_candidates._goal_distance(end, direction, pitch_length) if _valid_position(end) else None),
        "goal_corridor_distance_m": _round_or_none(shot_candidates._goal_corridor_distance(end, pitch_width) if _valid_position(end) else None),
        "interpretation": "trajectory_evidence_moves_away_from_resolved_goal" if progress is not None and progress < 0 else "direction_or_evidence_ambiguous",
    }


def _weak_boundary(
    path: Mapping[str, Any],
    source: Mapping[str, Any] | None,
    canonical_logical_time: float,
) -> dict[str, Any] | None:
    bridge = _bridge_summary(path) or {}
    if bridge.get("rejection_reason") != "detector_evidence_boundary" or source is None:
        return None
    contact_id = _text(_mapping(path.get("contact")).get("nearest_contact_event_id"))
    events = shot_candidates._contact_events(_mapping(source.get("event_candidates")))
    current_index = next((index for index, event in enumerate(events) if _text(event.get("event_id")) == contact_id), None)
    if current_index is None:
        return None
    contact = events[current_index]
    next_contact = events[current_index + 1] if current_index + 1 < len(events) else None
    timeline = shot_candidates._ball_timeline(_mapping(source.get("ball_tracks")))
    launch_time = _number(contact.get("end_time_sec"), 0.0)
    launch = shot_candidates._nearest_trusted_position(timeline, launch_time, shot_candidates.MAX_LAUNCH_POSITION_DELTA_SEC)
    if launch is None:
        return None
    prefix, boundary_index = _trusted_prefix_and_boundary(timeline, launch, launch_time)
    if boundary_index is None:
        return None
    boundary = timeline[boundary_index]
    if str(boundary.get("source") or "") != "detected" or shot_candidates._is_trusted_ball_position(boundary):
        return None
    post = next((row for row in timeline[boundary_index + 1 :] if shot_candidates._is_trusted_ball_position(row)), None)
    pre = prefix[-1]
    weak_position = boundary.get("position_m")
    pre_position = pre.get("position_m")
    post_position = post.get("position_m") if post else None
    counterfactual = _counterfactual_weak_boundary(
        source,
        contact,
        next_contact,
        timeline,
        boundary_index,
    )
    counterfactual_time = _number(counterfactual.get("candidate_timestamp_sec"), canonical_logical_time)
    counterfactual["benchmark_delta_sec"] = _round(counterfactual_time - canonical_logical_time) if counterfactual.get("candidate_emitted") else None
    counterfactual["would_recover_within_tolerance"] = bool(
        counterfactual.get("candidate_emitted")
        and abs(_number(counterfactual.get("benchmark_delta_sec"), 999.0)) <= DEFAULT_TOLERANCE_SEC
    )
    return {
        "contact_event_id": contact_id,
        "boundary_frame": boundary.get("frame"),
        "boundary_time_sec": boundary.get("time_sec"),
        "boundary_confidence": boundary.get("confidence"),
        "pre_time_sec": pre.get("time_sec"),
        "weak_time_sec": boundary.get("time_sec"),
        "post_time_sec": post.get("time_sec") if post else None,
        "pre_to_post_gap_sec": _round(_number(post.get("time_sec"), 0.0) - _number(pre.get("time_sec"), 0.0)) if post else None,
        "pre_position_m": pre_position,
        "weak_position_m": weak_position,
        "post_position_m": post_position,
        "pre_to_weak_m": _distance_or_none(pre_position, weak_position),
        "weak_to_post_m": _distance_or_none(weak_position, post_position),
        "pre_to_post_m": _distance_or_none(pre_position, post_position),
        "spatial_classification": _weak_spatial_classification(pre, boundary, post),
        "counterfactual_without_weak_detected": counterfactual,
    }


def _trusted_prefix_and_boundary(
    timeline: list[dict[str, Any]], launch: Mapping[str, Any], launch_time: float
) -> tuple[list[dict[str, Any]], int | None]:
    prefix = [dict(launch)]
    previous_time = _number(launch.get("time_sec"), launch_time)
    started = False
    for index, row in enumerate(timeline):
        if not started:
            started = row is launch
            continue
        time_sec = _number(row.get("time_sec"), launch_time + shot_candidates.MAX_TRAJECTORY_SECONDS + 1.0)
        if time_sec > launch_time + shot_candidates.MAX_TRAJECTORY_SECONDS:
            return prefix, index
        if not shot_candidates._is_trusted_ball_position(row) or time_sec - previous_time > shot_candidates.MAX_TRAJECTORY_GAP_SEC:
            return prefix, index
        if time_sec > previous_time + 0.001:
            prefix.append(dict(row))
            previous_time = time_sec
    return prefix, None


def _counterfactual_weak_boundary(
    source: Mapping[str, Any],
    contact: Mapping[str, Any],
    next_contact: Mapping[str, Any] | None,
    timeline: list[dict[str, Any]],
    boundary_index: int,
) -> dict[str, Any]:
    copied_timeline = deepcopy(timeline)
    copied_timeline[boundary_index].update({"source": "unknown", "position_m": None, "confidence": 0.0})
    candidate, reason = shot_candidates._candidate_from_contact(
        contact,
        next_contact,
        copied_timeline,
        _mapping(source.get("match_phase_config")),
        source_match_id=_text(source.get("source_match_id")),
        pitch_width_m=_number(source.get("pitch_width_m"), 30.0),
        pitch_length_m=_number(source.get("pitch_length_m"), 47.4),
        logical_offset_sec=_number(source.get("logical_start_sec"), 0.0),
        policy_version=shot_candidates.V4_CONTINUITY_BRIDGE_POLICY_VERSION,
    )
    return {
        "candidate_emitted": candidate is not None,
        "rejection_reason": reason,
        "candidate_timestamp_sec": candidate.get("logical_timestamp_sec") if candidate else None,
    }


def _weak_spatial_classification(
    pre: Mapping[str, Any], weak: Mapping[str, Any], post: Mapping[str, Any] | None
) -> str:
    pre_position = pre.get("position_m")
    weak_position = weak.get("position_m")
    post_position = post.get("position_m") if post else None
    if not _valid_position(pre_position) or not _valid_position(weak_position) or not _valid_position(post_position):
        return "ambiguous_missing_endpoint"
    gap_sec = _number(post.get("time_sec"), 0.0) - _number(pre.get("time_sec"), 0.0)
    speed = (_distance_or_none(pre_position, post_position) or 0.0) / gap_sec if gap_sec > 0 else float("inf")
    segment = _distance_or_none(pre_position, post_position) or 0.0
    residual = abs((_distance_or_none(pre_position, weak_position) or 0.0) + (_distance_or_none(weak_position, post_position) or 0.0) - segment)
    if gap_sec > shot_candidates.V4_MAX_CONTINUITY_BRIDGE_GAP_SEC or speed > shot_candidates.V4_MAX_CONTINUITY_BRIDGE_SPEED_MPS:
        return "ambiguous_or_unsafe_post_gap"
    if residual <= 0.75:
        return "likely_correct_ball_low_confidence"
    if residual >= 3.0:
        return "likely_alternate_or_wrong_ball"
    return "ambiguous_ball_hypothesis"


def _classify(
    trace: Mapping[str, Any],
    team_paths: list[Mapping[str, Any]],
    proximity: Mapping[str, Any],
    weak_boundaries: list[Mapping[str, Any]],
    trajectory_details: list[Mapping[str, Any]],
    direction_details: list[Mapping[str, Any]],
) -> tuple[str, list[str], str, str, str]:
    if bool(trace.get("benchmark_matched")):
        return "MATCHED", [], "benchmark", "HIGH", "Candidate matched inside the frozen benchmark tolerance."
    all_paths = [path for path in trace.get("contact_paths") or [] if isinstance(path, Mapping)]
    if not all_paths:
        return "CONTACT_MISSING", ["BALL_TRACK_SELECTION_FAILURE"] if _mapping(trace.get("classification")).get("contributing_categories") else [], "contact", "HIGH", "No plausible contact exists inside the local contact search window."
    if not team_paths:
        return "CONTACT_WRONG_SUBJECT", [], "contact_attribution", "HIGH", "Nearby contacts exist, but none has the canonical shooting team."
    reasons = [
        _text(_mapping(path.get("shot_policy")).get("rejection_reason"))
        for path in team_paths
    ]
    reasons = [reason for reason in reasons if reason]
    if any(bool(_mapping(path.get("shot_policy")).get("generated")) for path in team_paths):
        if bool(proximity.get("candidate_within_benchmark_tolerance")):
            return "MATCHING_TIMING_ISSUE", [], "benchmark_matching", "MEDIUM", "A team-consistent candidate exists within tolerance but was consumed by deterministic one-to-one matching or differs in provenance."
        return "MATCHING_TIMING_ISSUE", [], "benchmark_matching", "HIGH", "A team-consistent candidate was emitted but its nearest timestamp is outside benchmark tolerance."
    if len(set(reasons)) > 1:
        secondary = sorted({_cause_from_reason(reason) for reason in reasons if _cause_from_reason(reason) != "UNKNOWN"})
        return "MULTI_STAGE_FAILURE", secondary, "multiple", "LOW", "Multiple team-consistent contact paths fail at different pipeline stages."
    reason = reasons[0] if reasons else None
    if reason == "missing_launch_ball_position":
        raw = _mapping(team_paths[0].get("raw_ball_evidence"))
        category = "LAUNCH_BALL_WRONG_HYPOTHESIS" if raw.get("simultaneous_accepted_candidate_frames") else "LAUNCH_BALL_MISSING"
        return category, [], "launch_selection", "MEDIUM", "No trusted selected launch position is available for the team-consistent contact."
    if weak_boundaries:
        return "WEAK_DETECTED_BOUNDARY", [], "trajectory_boundary", "HIGH", "The first trajectory boundary is a low-confidence detected row; v4 intentionally does not reinterpret detected ambiguity as a missing gap."
    if reason == "missing_continuous_ball_trajectory":
        return "SHORT_GAP_NOT_BRIDGEABLE", [], "trajectory", "HIGH", "The trusted trajectory ends before three usable samples and has no v4-safe bridge."
    if reason == "trajectory_not_meaningful":
        details = trajectory_details[0] if trajectory_details else {}
        failed = set(details.get("failed_conditions") or [])
        category = "TRAJECTORY_TOO_SHORT" if "sample_count" in failed else "TRAJECTORY_TOO_SLOW" if "speed" in failed else "TRAJECTORY_GEOMETRY_BAD"
        return category, [], "trajectory_quality", "HIGH", "A trajectory exists but fails the recorded meaningfulness condition(s)."
    if reason == "not_goalward":
        return "DIRECTION_WRONG", [], "direction", "MEDIUM", "Resolved attack direction and observed endpoint produce negative goalward progress."
    if reason == "same_team_receiver_before_goal":
        return "PASS_RECEIVER_CONFLICT", [], "receiver", "HIGH", "A same-team receiver occurs before a qualifying goal approach."
    if reason == "insufficient_shot_evidence":
        return "CANDIDATE_SCORE_FILTER", [], "candidate_scoring", "HIGH", "The pipeline reached scoring but did not meet the minimum suggestion score."
    return "UNKNOWN", [], "unknown", "LOW", "The persisted local evidence does not identify one safe primary failing stage."


def _cause_from_reason(reason: str) -> str:
    return {
        "missing_continuous_ball_trajectory": "SHORT_GAP_NOT_BRIDGEABLE",
        "trajectory_not_meaningful": "TRAJECTORY_GEOMETRY_BAD",
        "not_goalward": "DIRECTION_WRONG",
        "same_team_receiver_before_goal": "PASS_RECEIVER_CONFLICT",
        "insufficient_shot_evidence": "CANDIDATE_SCORE_FILTER",
        "missing_launch_ball_position": "LAUNCH_BALL_MISSING",
    }.get(reason, "UNKNOWN")


def _candidate_reasons(path: Mapping[str, Any]) -> list[str]:
    return list(_mapping(path.get("shot_policy")).get("candidate_reasons") or [])


def _negative_risk_summary(
    candidates_doc: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    *,
    v2_candidates_doc: Mapping[str, Any] | None,
    v3_candidates_doc: Mapping[str, Any] | None,
    editorial_doc: Mapping[str, Any] | None,
) -> dict[str, Any]:
    summary = _mapping(benchmark.get("summary"))
    output: dict[str, Any] = {
        "hard_negative_hits": summary.get("hard_negative_hits"),
        "new_v4_candidates_without_review_lineage": None,
        "historical_rejected_candidates": None,
        "v3_pass_suppression_context": "v3 previously removed 12 historical rejected candidates while preserving accepted suggestions.",
    }
    if v2_candidates_doc is None or v3_candidates_doc is None or editorial_doc is None:
        return output
    from evaluation.shot_candidate_operator_review import evaluate_operator_review_three_way

    review = evaluate_operator_review_three_way(v2_candidates_doc, v3_candidates_doc, candidates_doc, editorial_doc)
    v4 = _mapping(_mapping(review.get("policies")).get("v4"))
    baseline = _mapping(review.get("operator_review_baseline"))
    output.update({
        "historical_rejected_candidates": baseline.get("rejected"),
        "new_v4_candidates_without_review_lineage": v4.get("unreviewed_or_unmapped_candidates"),
        "historical_rejected_surviving": v4.get("rejected_reviewed_candidates_surviving"),
        "review_clusters": v4.get("review_clusters"),
    })
    return output


def _fix_family_risk_matrix(misses: list[Mapping[str, Any]], risk: Mapping[str, Any]) -> list[dict[str, Any]]:
    cause_count = Counter(str(case.get("primary_cause") or "") for case in misses)
    weak = sum(
        any(bool(_mapping(item.get("counterfactual_without_weak_detected")).get("would_recover_within_tolerance")) for item in case.get("weak_detected_boundaries") or [])
        for case in misses
    )
    trajectory = sum(cause_count[key] for key in ("SHORT_GAP_NOT_BRIDGEABLE", "TRAJECTORY_TOO_SHORT", "TRAJECTORY_TOO_SLOW", "TRAJECTORY_GEOMETRY_BAD"))
    direction = cause_count["DIRECTION_WRONG"]
    contact = cause_count["CONTACT_MISSING"] + cause_count["CONTACT_WRONG_SUBJECT"] + cause_count["LAUNCH_BALL_MISSING"] + cause_count["LAUNCH_BALL_WRONG_HYPOTHESIS"]
    return [
        {"fix_family": "weak_detected_boundary_reinterpretation", "potential_canonical_recoveries_upper_bound": weak, "known_negative_risk": "medium: weak detections can be alternate physical balls", "evidence_confidence": "MEDIUM" if weak else "LOW"},
        {"fix_family": "trajectory_meaningfulness_semantics", "potential_canonical_recoveries_upper_bound": trajectory, "known_negative_risk": "high: global relaxation can admit pass-like paths", "evidence_confidence": "LOW"},
        {"fix_family": "attack_direction_goal_semantics", "potential_canonical_recoveries_upper_bound": direction, "known_negative_risk": "high: direction is shared across all candidate hypotheses", "evidence_confidence": "LOW"},
        {"fix_family": "contact_launch_attribution_recovery", "potential_canonical_recoveries_upper_bound": contact, "known_negative_risk": "medium-high: wrong contact ownership changes source evidence", "evidence_confidence": "MEDIUM" if contact else "LOW"},
        {"hard_negative_hits": risk.get("hard_negative_hits"), "historical_rejected_candidates": risk.get("historical_rejected_candidates"), "new_unreviewed_v4_candidates": risk.get("new_v4_candidates_without_review_lineage")},
    ]


def _recommend(families: list[Mapping[str, Any]]) -> str:
    # This is a recommendation about the next evaluation only, not an automatic policy choice.
    weak = next((row for row in families if row.get("fix_family") == "weak_detected_boundary_reinterpretation"), {})
    if int(_number(weak.get("potential_canonical_recoveries_upper_bound"), 0.0)) >= 2:
        return "NEXT: weak-detected-boundary shadow reinterpretation"
    return "NEXT: contact/launch attribution recovery shadow experiment"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _valid_position(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)


def _distance_or_none(first: Any, second: Any) -> float | None:
    return _round(shot_candidates._distance(list(first), list(second))) if _valid_position(first) and _valid_position(second) else None


def _round(value: float) -> float:
    return round(float(value), 3)


def _round_or_none(value: float | None) -> float | None:
    return _round(value) if value is not None else None
