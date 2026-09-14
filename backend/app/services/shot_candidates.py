from __future__ import annotations

"""Deterministic, shadow-only shot-candidate generation from canonical evidence.

This module intentionally has no dependency on manual benchmarks or annotations.
It produces reviewable hypotheses only; no result from this module is a
canonical shot or eligible for analytics.
"""

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

from app.services.artifact_lineage import canonical_json_bytes
from app.services.match_phase_config import direction_for_team_at_time


POLICY_VERSION = "shot-candidate-shadow:v2"
PREVIOUS_POLICY_VERSION = "shot-candidate-shadow:v1"
V3_POLICY_VERSION = "shot-candidate-shadow:v3"
V4_CONTINUITY_BRIDGE_POLICY_VERSION = "shot-candidate-shadow:v4-continuity-bridge"
SCHEMA_VERSION = "shot-candidates:v1"
SOURCE = "canonical_ball_contact_trajectory_shadow_v1"
ALLOWED_CONTACT_STATUSES = {"accepted", "uncertain", "needs_review"}
TRUSTED_BALL_SOURCES = {"detected", "interpolated"}
SUPPORTED_ATTACK_DIRECTIONS = {"towards_y_min", "towards_y_max"}
MIN_BALL_CONFIDENCE = 0.35
MAX_LAUNCH_POSITION_DELTA_SEC = 0.5
MAX_TRAJECTORY_SECONDS = 2.75
MAX_TRAJECTORY_GAP_SEC = 0.4
MIN_TRAJECTORY_DISTANCE_M = 1.5
MIN_TRAJECTORY_SPEED_MPS = 2.0
MIN_GOALWARD_PROGRESS_M = 1.0
GOAL_APPROACH_ZONE_M = 12.0
GOAL_CORRIDOR_HALF_WIDTH_M = 8.0
SHORT_PREFIX_MIN_DISTANCE_M = 1.2
SHORT_PREFIX_MIN_SPEED_MPS = 5.0
SHORT_PREFIX_MAX_START_GOAL_DISTANCE_M = 18.0
SAME_TEAM_RECEIVER_SUPPRESSION_SEC = 2.5
DEDUPLICATION_WINDOW_SEC = 0.75
V3_PASS_LIKE_MIN_TRAJECTORY_DISTANCE_M = 6.0
V3_STRONG_TERMINAL_GOAL_DISTANCE_M = 4.0
V3_STRONG_TERMINAL_CORRIDOR_DISTANCE_M = 4.0
V3_LATER_SHOT_HORIZON_SEC = 3.0
V3_LATER_SHOT_MIN_CONFIDENCE_DELTA = 0.18
V3_LATER_SHOT_GOAL_DISTANCE_M = 8.0
V3_LATER_SHOT_CORRIDOR_DISTANCE_M = 6.0
# v4 is deliberately a local, contact-bounded repair experiment.  These are
# not ball-track selector settings and are never applied outside a candidate
# trajectory that has already started at a reviewed contact.
V4_MAX_CONTINUITY_BRIDGE_GAP_SEC = 0.35
V4_MAX_CONTINUITY_BRIDGE_SPEED_MPS = 35.0
V4_SINGLE_PRE_ENDPOINT_MAX_GAP_SEC = 0.30
V4_MIN_POST_ENDPOINT_SUPPORT = 2
V4_MIN_HEADING_COSINE = 0.2


def build_shot_candidates_document(
    event_candidates_doc: Mapping[str, Any],
    ball_tracks_doc: Mapping[str, Any],
    match_phase_config_doc: Mapping[str, Any] | None = None,
    *,
    source_match_id: str | None = None,
    pitch_width_m: float = 30.0,
    pitch_length_m: float = 47.4,
    logical_offset_sec: float = 0.0,
    policy_version: str = POLICY_VERSION,
) -> dict[str, Any]:
    """Build physical-source shot suggestions without persisting or publishing them."""

    _validate_policy_version(policy_version)
    contacts = _contact_events(event_candidates_doc)
    ball_timeline = _ball_timeline(ball_tracks_doc)
    candidates: list[dict[str, Any]] = []
    skipped = Counter()
    for index, event in enumerate(contacts):
        next_event = contacts[index + 1] if index + 1 < len(contacts) else None
        candidate, reason = _candidate_from_contact(
            event,
            next_event,
            ball_timeline,
            match_phase_config_doc,
            source_match_id=source_match_id,
            pitch_width_m=pitch_width_m,
            pitch_length_m=pitch_length_m,
            logical_offset_sec=logical_offset_sec,
            policy_version=policy_version,
        )
        if candidate is None:
            skipped[reason or "insufficient_evidence"] += 1
            continue
        candidates.append(candidate)

    deduplicated = _deduplicate(candidates)
    suppressed: list[dict[str, Any]] = []
    if policy_version == V3_POLICY_VERSION:
        deduplicated, suppressed = _apply_v3_structural_suppression(deduplicated)
    summary = _summary(deduplicated, skipped, suppressed)
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": policy_version,
        "source": SOURCE,
        "experimental": True,
        "candidate_semantics": "shadow_suggestions_only_not_canonical_shots",
        "parameters": {
            "trusted_ball_sources": sorted(TRUSTED_BALL_SOURCES),
            "min_ball_confidence": MIN_BALL_CONFIDENCE,
            "max_trajectory_seconds": MAX_TRAJECTORY_SECONDS,
            "max_trajectory_gap_sec": MAX_TRAJECTORY_GAP_SEC,
            "min_trajectory_distance_m": MIN_TRAJECTORY_DISTANCE_M,
            "min_trajectory_speed_mps": MIN_TRAJECTORY_SPEED_MPS,
            "min_goalward_progress_m": MIN_GOALWARD_PROGRESS_M,
            "short_prefix_min_distance_m": SHORT_PREFIX_MIN_DISTANCE_M,
            "short_prefix_min_speed_mps": SHORT_PREFIX_MIN_SPEED_MPS,
            "short_prefix_max_start_goal_distance_m": SHORT_PREFIX_MAX_START_GOAL_DISTANCE_M,
            "goal_approach_zone_m": GOAL_APPROACH_ZONE_M,
            "same_team_receiver_suppression_sec": SAME_TEAM_RECEIVER_SUPPRESSION_SEC,
            "deduplication_window_sec": DEDUPLICATION_WINDOW_SEC,
            **({
                "pass_like_min_trajectory_distance_m": V3_PASS_LIKE_MIN_TRAJECTORY_DISTANCE_M,
                "later_shot_horizon_sec": V3_LATER_SHOT_HORIZON_SEC,
                "later_shot_min_confidence_delta": V3_LATER_SHOT_MIN_CONFIDENCE_DELTA,
            } if policy_version == V3_POLICY_VERSION else {}),
            **({
                "continuity_bridge_mode": "contact_bounded_trusted_endpoint_interpolation",
                "max_continuity_bridge_gap_sec": V4_MAX_CONTINUITY_BRIDGE_GAP_SEC,
                "max_continuity_bridge_speed_mps": V4_MAX_CONTINUITY_BRIDGE_SPEED_MPS,
                "single_pre_endpoint_max_gap_sec": V4_SINGLE_PRE_ENDPOINT_MAX_GAP_SEC,
                "min_post_endpoint_support": V4_MIN_POST_ENDPOINT_SUPPORT,
                "min_heading_cosine": V4_MIN_HEADING_COSINE,
            } if policy_version == V4_CONTINUITY_BRIDGE_POLICY_VERSION else {}),
        },
        "source_match_id": source_match_id,
        "logical_offset_sec": _round(logical_offset_sec),
        "pitch_dimensions_m": {"width": _round(pitch_width_m), "length": _round(pitch_length_m)},
        "summary": summary,
        "candidates": sorted(deduplicated, key=lambda row: (row["logical_timestamp_sec"], row["candidate_key"])),
        **({"suppressed_candidate_diagnostics": suppressed} if policy_version == V3_POLICY_VERSION else {}),
        "notes": [
            "Every row is a suggested shot candidate requiring future operator confirmation.",
            "final_stat_eligible is always false in the shadow candidate layer.",
            "The generator has no access to manual shot goldsets or their timestamps.",
        ],
    }


def write_shot_candidates_artifact(
    match_path: Path,
    event_candidates_doc: Mapping[str, Any],
    ball_tracks_doc: Mapping[str, Any],
    match_phase_config_doc: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Persist only the shadow candidate artifact alongside physical evidence."""

    document = build_shot_candidates_document(
        event_candidates_doc,
        ball_tracks_doc,
        match_phase_config_doc,
        **kwargs,
    )
    document["generated_at"] = _now_iso()
    (match_path / "shot_candidates.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return document


def build_logical_shot_candidates_document(
    sources: Iterable[Mapping[str, Any]],
    *,
    timeline_span_sec: float,
    policy_version: str = POLICY_VERSION,
) -> dict[str, Any]:
    """Combine source candidate documents without discarding source provenance."""

    _validate_policy_version(policy_version)
    candidates: list[dict[str, Any]] = []
    for source in sources:
        source_id = str(source.get("source_match_id") or "")
        offset = _number(source.get("logical_offset_sec"), 0.0)
        document = source.get("shot_candidates")
        if not isinstance(document, Mapping):
            continue
        source_policy_version = str(document.get("policy_version") or policy_version)
        if source_policy_version != policy_version:
            raise ValueError("Cannot combine shot candidates generated by different policy versions")
        for physical in document.get("candidates") or []:
            if not isinstance(physical, Mapping):
                continue
            source_time = _number(physical.get("source_timestamp_sec"), _number(physical.get("candidate_timestamp_sec"), 0.0))
            logical_time = offset + source_time
            key = _key("shot-logical", {"source_candidate_key": physical.get("candidate_key"), "source_match_id": source_id, "logical_offset_sec": _round(offset)})
            candidates.append({
                **dict(physical),
                "candidate_id": f"shot-{key.rsplit(':', 1)[-1][:12]}",
                "candidate_key": key,
                "source_match_id": source_id or physical.get("source_match_id"),
                "source_timestamp_sec": _round(source_time),
                "logical_timestamp_sec": _round(logical_time),
                "logical_context_start_sec": _round(offset + _number(physical.get("source_context_start_sec"), source_time)),
                "logical_context_end_sec": _round(offset + _number(physical.get("source_context_end_sec"), source_time)),
                "physical_candidate_key": physical.get("candidate_key"),
            })
    candidates.sort(key=lambda row: (row["logical_timestamp_sec"], row["candidate_key"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": policy_version,
        "source": SOURCE,
        "experimental": True,
        "candidate_semantics": "logical_projection_of_shadow_suggestions_only_not_canonical_shots",
        "timeline_span_sec": _round(timeline_span_sec),
        "summary": {
            "candidates_total": len(candidates),
            "source_match_count": len({str(row.get("source_match_id") or "") for row in candidates}),
            "candidates_per_10_minutes": _round(len(candidates) / (timeline_span_sec / 600.0)) if timeline_span_sec > 0 else None,
        },
        "candidates": candidates,
        "notes": [
            "Logical timestamps are source-local timestamps rebased with the canonical logical offset.",
            "Candidates remain suggestions and are excluded from all canonical analytics.",
        ],
    }


def _candidate_from_contact(
    event: Mapping[str, Any],
    next_event: Mapping[str, Any] | None,
    ball_timeline: list[dict[str, Any]],
    phase_config: Mapping[str, Any] | None,
    *,
    source_match_id: str | None,
    pitch_width_m: float,
    pitch_length_m: float,
    logical_offset_sec: float,
    policy_version: str,
) -> tuple[dict[str, Any] | None, str | None]:
    launch_time = _number(event.get("end_time_sec"), _number(event.get("start_time_sec"), 0.0))
    launch = _nearest_trusted_position(ball_timeline, launch_time, MAX_LAUNCH_POSITION_DELTA_SEC)
    if launch is None:
        return None, "missing_launch_ball_position"
    trajectory, bridge_diagnostics = _trajectory_for_policy(
        ball_timeline,
        launch,
        launch_time,
        policy_version=policy_version,
        next_contact_time=_number(next_event.get("start_time_sec"), _number(next_event.get("end_time_sec"), -1.0)) if next_event else None,
    )
    if trajectory is None:
        return None, "missing_continuous_ball_trajectory"
    trajectory_summary = _trajectory_summary(trajectory)

    team_label = _text(event.get("team_label"))
    team_name = _text(event.get("team_name"))
    phase = direction_for_team_at_time(phase_config, team_label, launch_time) if team_label else {
        "period_id": None,
        "attack_direction": "unknown",
        "direction_source": "missing_team_attribution",
    }
    direction = str(phase.get("attack_direction") or "unknown")
    if direction != "unknown" and direction not in SUPPORTED_ATTACK_DIRECTIONS:
        return None, "unsupported_attack_axis"
    goalward_progress = _goalward_progress(trajectory_summary["start_position_m"], trajectory_summary["end_position_m"], direction)
    standard_trajectory = (
        trajectory_summary["distance_m"] >= MIN_TRAJECTORY_DISTANCE_M
        and trajectory_summary["mean_speed_mps"] >= MIN_TRAJECTORY_SPEED_MPS
    )
    short_prefix = policy_version in {POLICY_VERSION, V3_POLICY_VERSION, V4_CONTINUITY_BRIDGE_POLICY_VERSION} and _is_strong_short_prefix(
        trajectory_summary,
        ball_timeline,
        trajectory,
        direction,
        pitch_length_m,
        goalward_progress,
    )
    uses_short_prefix = not standard_trajectory and short_prefix
    if not standard_trajectory and not uses_short_prefix:
        return None, "trajectory_not_meaningful"
    endpoint_goal_distance = _goal_distance(trajectory_summary["end_position_m"], direction, pitch_length_m)
    corridor_distance = _goal_corridor_distance(trajectory_summary["end_position_m"], pitch_width_m)
    has_goal_approach = endpoint_goal_distance is not None and endpoint_goal_distance <= GOAL_APPROACH_ZONE_M
    cross_like = _is_cross_like(trajectory_summary["displacement_m"], has_goal_approach)

    receiver = _receiver_context(next_event, launch_time)
    source_team_identity = _text(event.get("team_id")) or team_label
    next_team_identity = receiver.pop("next_contact_team_identity", None)
    receiver["same_team_receiver_before_goal"] = bool(
        source_team_identity
        and next_team_identity == source_team_identity
        and receiver["next_contact_gap_sec"] is not None
        and receiver["next_contact_gap_sec"] <= SAME_TEAM_RECEIVER_SUPPRESSION_SEC
    )
    receiver["opponent_receiver"] = bool(
        source_team_identity
        and next_team_identity
        and next_team_identity != source_team_identity
        and receiver["next_contact_gap_sec"] is not None
        and receiver["next_contact_gap_sec"] <= SAME_TEAM_RECEIVER_SUPPRESSION_SEC
    )
    if receiver["same_team_receiver_before_goal"] and not has_goal_approach:
        return None, "same_team_receiver_before_goal"
    team_attribution_conflict = direction != "unknown" and goalward_progress < MIN_GOALWARD_PROGRESS_M
    if team_attribution_conflict:
        return None, "not_goalward"
    if direction == "unknown" and not has_goal_approach:
        return None, "unknown_team_without_goal_approach"

    reasons = ["contact_release", "continuous_ball_trajectory", "meaningful_ball_speed"]
    if uses_short_prefix:
        reasons.append("strong_trusted_prefix_before_boundary")
    if bridge_diagnostics.get("applied"):
        reasons.append("continuity_bridge_applied")
    if direction != "unknown":
        reasons.append("towards_opponent_goal")
    else:
        reasons.append("team_attribution_unknown")
    if has_goal_approach:
        reasons.append("trajectory_approaches_goal_area")
    if corridor_distance is not None and corridor_distance <= GOAL_CORRIDOR_HALF_WIDTH_M:
        reasons.append("trajectory_aligned_with_goal_corridor")
    if cross_like:
        reasons.append("cross_like_trajectory")
    if receiver["same_team_receiver_before_goal"]:
        reasons.append("same_team_receiver_after_goal_approach")
    if receiver["opponent_receiver"]:
        reasons.append("opponent_intervention_or_receiver")

    score = 0.28
    score += min(0.16, trajectory_summary["mean_speed_mps"] / 16.0 * 0.16)
    score += min(0.18, max(0.0, goalward_progress) / 14.0 * 0.18)
    score += 0.16 if has_goal_approach else 0.0
    score += 0.08 if corridor_distance is not None and corridor_distance <= GOAL_CORRIDOR_HALF_WIDTH_M else 0.0
    score += 0.06 if str(event.get("review_status") or "") == "accepted" else 0.0
    score -= 0.14 if cross_like else 0.0
    score -= 0.18 if receiver["same_team_receiver_before_goal"] else 0.0
    score = _clamp(score, 0.0, 1.0)
    if score < 0.38:
        return None, "insufficient_shot_evidence"

    source_event_key = _text(event.get("source_candidate_key")) or _text(event.get("event_id")) or f"frame:{event.get('end_frame')}"
    candidate_key = _key("shot", {
        "source_match_id": source_match_id,
        "source_event_key": source_event_key,
        "launch_frame": event.get("end_frame"),
        "launch_time_sec": _round(launch_time),
    })
    source_start = min(_number(row.get("time_sec"), launch_time) for row in trajectory)
    source_end = max(_number(row.get("time_sec"), launch_time) for row in trajectory)
    source_player = _text(event.get("stable_player_id")) if team_label is not None else None
    return {
        "candidate_id": f"shot-{candidate_key.rsplit(':', 1)[-1][:12]}",
        "candidate_key": candidate_key,
        "event_type": "shot_candidate",
        "policy_version": policy_version,
        "source": SOURCE,
        "source_match_id": source_match_id,
        "source_event_id": event.get("event_id"),
        "source_event_key": source_event_key,
        "source_contact_review_status": event.get("review_status"),
        "source_timestamp_sec": _round(launch_time),
        "candidate_timestamp_sec": _round(launch_time),
        "logical_timestamp_sec": _round(logical_offset_sec + launch_time),
        "source_context_start_sec": _round(source_start),
        "source_context_end_sec": _round(source_end),
        "logical_context_start_sec": _round(logical_offset_sec + source_start),
        "logical_context_end_sec": _round(logical_offset_sec + source_end),
        "suggested_team_label": team_label,
        "suggested_team_name": team_name,
        "suggested_player_id": source_player,
        "suggested_outcome": None,
        "confidence": _round(score),
        "reasons": reasons,
        "review_status": "needs_review",
        "final_stat_eligible": False,
        "match_phase_period_id": phase.get("period_id"),
        "attack_direction": direction,
        "direction_source": phase.get("direction_source"),
        "start_position_m": trajectory_summary["start_position_m"],
        "end_position_m": trajectory_summary["end_position_m"],
        "trajectory_evidence": {
            **trajectory_summary,
            "goalward_progress_m": _round(goalward_progress),
            "endpoint_goal_distance_m": _round_or_none(endpoint_goal_distance),
            "goal_corridor_distance_m": _round_or_none(corridor_distance),
            "cross_like": cross_like,
            "trusted_sources": sorted({str(row.get("source") or "") for row in trajectory}),
            "frames": [int(_number(row.get("frame"), -1)) for row in trajectory if _number(row.get("frame"), -1) >= 0],
            **({"continuity_bridge": bridge_diagnostics} if bridge_diagnostics.get("applied") else {}),
        },
        "receiver_evidence": receiver,
        "source_evidence_refs": {
            "event_id": event.get("event_id"),
            "event_candidate_key": event.get("source_candidate_key"),
            "event_end_frame": event.get("end_frame"),
            "ball_start_frame": launch.get("frame"),
            "ball_end_frame": trajectory[-1].get("frame"),
        },
        "deduplicated_hypothesis_count": 1,
        "merged_candidate_keys": [],
        "notes": [
            "Suggested candidate only; a future operator must confirm team and outcome before canonical analytics.",
            "No automatic outcome is inferred by the shadow generator.",
        ],
    }, None


def _contact_events(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in document.get("events") or []
        if isinstance(row, Mapping)
        and str(row.get("event_type") or "") == "ball_contact"
        and str(row.get("review_status") or "needs_review") in ALLOWED_CONTACT_STATUSES
    ]
    return sorted(rows, key=lambda row: (_number(row.get("end_time_sec"), 0.0), _number(row.get("end_frame"), 0.0), _text(row.get("event_id")) or ""))


def _ball_timeline(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep explicit unknown/untrusted rows so they remain hard trajectory boundaries."""

    positions = []
    for row in document.get("positions") or []:
        if not isinstance(row, Mapping):
            continue
        positions.append(dict(row))
    return sorted(positions, key=lambda row: (_number(row.get("time_sec"), 0.0), _number(row.get("frame"), 0.0)))


def _is_trusted_ball_position(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("source") or "") in TRUSTED_BALL_SOURCES
        and _number(row.get("confidence"), 0.0) >= MIN_BALL_CONFIDENCE
        and _valid_position(row.get("position_m"))
    )


def _nearest_trusted_position(rows: list[dict[str, Any]], time_sec: float, tolerance: float) -> dict[str, Any] | None:
    choices = [
        row
        for row in rows
        if _is_trusted_ball_position(row)
        and abs(_number(row.get("time_sec"), -99999.0) - time_sec) <= tolerance
    ]
    return min(choices, key=lambda row: (abs(_number(row.get("time_sec"), 0.0) - time_sec), _number(row.get("frame"), 0.0))) if choices else None


def _trajectory_from_launch(rows: list[dict[str, Any]], launch: Mapping[str, Any], launch_time: float) -> list[dict[str, Any]] | None:
    horizon = launch_time + MAX_TRAJECTORY_SECONDS
    trajectory: list[dict[str, Any]] = []
    previous: float | None = None
    started = False
    for row in rows:
        time_sec = _number(row.get("time_sec"), -1.0)
        if not started:
            if row is launch:
                started = True
                trajectory.append(row)
                previous = time_sec
            continue
        if time_sec > horizon:
            break
        if not _is_trusted_ball_position(row):
            # An explicit unknown, predicted or otherwise invalid canonical row
            # is evidence of a discontinuity even when its timestamp is close.
            break
        if previous is None or time_sec <= previous + 0.001:
            continue
        if time_sec - previous > MAX_TRAJECTORY_GAP_SEC:
            break
        trajectory.append(row)
        previous = time_sec
    return trajectory if len(trajectory) >= 3 else None


def _trajectory_for_policy(
    rows: list[dict[str, Any]],
    launch: Mapping[str, Any],
    launch_time: float,
    *,
    policy_version: str,
    next_contact_time: float | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Return the ordinary trajectory, optionally with a local v4 bridge.

    v1/v2/v3 intentionally use the original function unchanged.  v4 only
    considers the first discontinuity after this particular contact launch;
    it does not repair or persist a global ball timeline.
    """

    ordinary = _trajectory_from_launch(rows, launch, launch_time)
    if policy_version != V4_CONTINUITY_BRIDGE_POLICY_VERSION or ordinary is not None:
        return ordinary, {"considered": False, "applied": False}
    bridged, diagnostics = _contact_bounded_continuity_bridge(rows, launch, launch_time, next_contact_time=next_contact_time)
    return bridged if diagnostics.get("applied") else ordinary, diagnostics


def _contact_bounded_continuity_bridge(
    rows: list[dict[str, Any]],
    launch: Mapping[str, Any],
    launch_time: float,
    *,
    next_contact_time: float | None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Interpolate one short trusted-endpoint gap after a contact launch.

    The synthetic rows live only in this returned trajectory.  They never
    alter canonical ball tracks and are rejected unless a nearby trusted
    continuation provides two post-gap endpoint samples.
    """

    horizon = launch_time + MAX_TRAJECTORY_SECONDS
    start_index = next((index for index, row in enumerate(rows) if row is launch), None)
    if start_index is None:
        return None, _bridge_diagnostics(False, "launch_not_in_timeline")

    prefix = [dict(launch)]
    previous_time = _number(launch.get("time_sec"), launch_time)
    boundary_index: int | None = None
    for index in range(start_index + 1, len(rows)):
        row = rows[index]
        time_sec = _number(row.get("time_sec"), -1.0)
        if time_sec > horizon:
            return None, _bridge_diagnostics(False, "horizon_reached")
        if not _is_trusted_ball_position(row) or time_sec - previous_time > MAX_TRAJECTORY_GAP_SEC:
            boundary_index = index
            break
        if time_sec > previous_time + 0.001:
            prefix.append(dict(row))
            previous_time = time_sec
    if boundary_index is None:
        return None, _bridge_diagnostics(False, "no_discontinuity")

    # A weak but still "detected" row can be an alternate physical-ball
    # hypothesis.  Treating it as a gap would turn ordinary low-confidence
    # detector ambiguity into invented continuity.  v4 only bridges explicit
    # unknown/interpolated continuity breaks.
    boundary_source = str(rows[boundary_index].get("source") or "")
    if boundary_source not in {"unknown", "interpolated"}:
        return None, _bridge_diagnostics(False, "detector_evidence_boundary")

    pre = prefix[-1]
    post_index = next(
        (
            index
            for index in range(boundary_index, len(rows))
            if _number(rows[index].get("time_sec"), horizon + 1.0) <= horizon
            and _is_trusted_ball_position(rows[index])
        ),
        None,
    )
    if post_index is None:
        return None, _bridge_diagnostics(False, "no_trusted_post_endpoint")
    post = rows[post_index]
    pre_time = _number(pre.get("time_sec"), launch_time)
    post_time = _number(post.get("time_sec"), pre_time)
    gap_sec = post_time - pre_time
    if gap_sec <= 0 or gap_sec > V4_MAX_CONTINUITY_BRIDGE_GAP_SEC:
        return None, _bridge_diagnostics(False, "gap_too_long", pre=pre, post=post)
    if next_contact_time is not None and pre_time < next_contact_time < post_time:
        return None, _bridge_diagnostics(False, "contact_boundary", pre=pre, post=post)
    if len(prefix) == 1 and gap_sec > V4_SINGLE_PRE_ENDPOINT_MAX_GAP_SEC:
        return None, _bridge_diagnostics(False, "insufficient_pre_endpoint_support", pre=pre, post=post)
    if not _same_timeline_segment(pre, post):
        return None, _bridge_diagnostics(False, "timeline_boundary", pre=pre, post=post)

    post_support = _trusted_post_support(rows, post_index, horizon)
    if len(post_support) < V4_MIN_POST_ENDPOINT_SUPPORT:
        return None, _bridge_diagnostics(False, "insufficient_post_endpoint_support", pre=pre, post=post)
    distance_m = _distance(list(pre["position_m"]), list(post["position_m"]))
    if distance_m / gap_sec > V4_MAX_CONTINUITY_BRIDGE_SPEED_MPS:
        return None, _bridge_diagnostics(False, "implausible_bridge_speed", pre=pre, post=post)
    if len(prefix) >= 2 and not _bridge_heading_is_consistent(prefix[-2], pre, post, post_support[1]):
        return None, _bridge_diagnostics(False, "contradictory_heading", pre=pre, post=post)

    missing_rows = [
        row for row in rows[boundary_index:post_index]
        if pre_time < _number(row.get("time_sec"), pre_time) < post_time
    ]
    bridge_rows = [_interpolated_bridge_row(pre, post, row) for row in missing_rows]
    if not bridge_rows:
        bridge_rows = [_interpolated_bridge_row(pre, post, {"time_sec": (pre_time + post_time) / 2.0, "frame": None})]
    trajectory = prefix + bridge_rows
    continuation, _ = _trusted_continuation(rows, post_index, post_time, horizon)
    trajectory.extend(continuation)
    diagnostics = _bridge_diagnostics(
        True,
        None,
        pre=pre,
        post=post,
        pre_support_count=len(prefix),
        post_support=post_support,
        bridge_rows=bridge_rows,
    )
    return (trajectory if len(trajectory) >= 3 else None), diagnostics


def _trusted_continuation(rows: list[dict[str, Any]], start_index: int, previous_time: float, horizon: float) -> tuple[list[dict[str, Any]], int]:
    continuation: list[dict[str, Any]] = []
    for index in range(start_index, len(rows)):
        row = rows[index]
        time_sec = _number(row.get("time_sec"), horizon + 1.0)
        if time_sec > horizon or not _is_trusted_ball_position(row) or time_sec - previous_time > MAX_TRAJECTORY_GAP_SEC:
            return continuation, index
        if time_sec > previous_time + 0.001:
            continuation.append(dict(row))
            previous_time = time_sec
    return continuation, len(rows)


def _trusted_post_support(rows: list[dict[str, Any]], start_index: int, horizon: float) -> list[dict[str, Any]]:
    support: list[dict[str, Any]] = []
    previous_time: float | None = None
    for row in rows[start_index:]:
        time_sec = _number(row.get("time_sec"), horizon + 1.0)
        if time_sec > horizon or not _is_trusted_ball_position(row):
            break
        if previous_time is not None and time_sec - previous_time > MAX_TRAJECTORY_GAP_SEC:
            break
        support.append(row)
        previous_time = time_sec
        if len(support) >= V4_MIN_POST_ENDPOINT_SUPPORT:
            break
    return support


def _same_timeline_segment(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Reject explicit source/segment changes while tolerating absent metadata."""

    for key in ("source_match_id", "timeline_segment_id", "source_segment_id", "segment_id"):
        first_value = _text(first.get(key))
        second_value = _text(second.get(key))
        if first_value is not None and second_value is not None and first_value != second_value:
            return False
    return True


def _bridge_heading_is_consistent(pre_previous: Mapping[str, Any], pre: Mapping[str, Any], post: Mapping[str, Any], post_next: Mapping[str, Any]) -> bool:
    before = _displacement(list(pre_previous["position_m"]), list(pre["position_m"]))
    bridge = _displacement(list(pre["position_m"]), list(post["position_m"]))
    after = _displacement(list(post["position_m"]), list(post_next["position_m"]))
    return _heading_cosine(before, bridge) >= V4_MIN_HEADING_COSINE and _heading_cosine(bridge, after) >= V4_MIN_HEADING_COSINE


def _heading_cosine(first: list[float], second: list[float]) -> float:
    first_length = math.hypot(*first)
    second_length = math.hypot(*second)
    if first_length < 0.001 or second_length < 0.001:
        return -1.0
    return (first[0] * second[0] + first[1] * second[1]) / (first_length * second_length)


def _interpolated_bridge_row(pre: Mapping[str, Any], post: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    pre_time = _number(pre.get("time_sec"), 0.0)
    post_time = _number(post.get("time_sec"), pre_time)
    target_time = _number(target.get("time_sec"), (pre_time + post_time) / 2.0)
    fraction = _clamp((target_time - pre_time) / max(0.001, post_time - pre_time), 0.0, 1.0)
    start = list(pre["position_m"])
    end = list(post["position_m"])
    target_frame = target.get("frame")
    frame = int(_number(target_frame, -1)) if _number(target_frame, -1) >= 0 else None
    return {
        "frame": frame,
        "time_sec": _round(target_time),
        "position_m": [_round(start[0] + (end[0] - start[0]) * fraction), _round(start[1] + (end[1] - start[1]) * fraction)],
        "source": "continuity_bridge",
        "confidence": _round(min(_number(pre.get("confidence"), 0.0), _number(post.get("confidence"), 0.0))),
    }


def _bridge_diagnostics(
    applied: bool,
    rejection_reason: str | None,
    *,
    pre: Mapping[str, Any] | None = None,
    post: Mapping[str, Any] | None = None,
    pre_support_count: int | None = None,
    post_support: list[Mapping[str, Any]] | None = None,
    bridge_rows: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    pre_time = _number(pre.get("time_sec"), 0.0) if pre else None
    post_time = _number(post.get("time_sec"), 0.0) if post else None
    return {
        "considered": True,
        "applied": applied,
        "rejection_reason": rejection_reason,
        "gap_frames": (int(_number(post.get("frame"), 0.0) - _number(pre.get("frame"), 0.0) - 1) if pre and post and _number(pre.get("frame"), -1) >= 0 and _number(post.get("frame"), -1) >= 0 else None),
        "gap_sec": _round(post_time - pre_time) if pre_time is not None and post_time is not None else None,
        "distance_m": _round(_distance(list(pre["position_m"]), list(post["position_m"]))) if pre and post else None,
        "endpoint_support": {
            "pre_trusted_samples": pre_support_count if pre is not None else None,
            "post_trusted_samples": len(post_support or []),
            "bridge_samples": len(bridge_rows or []),
        },
        "pre_endpoint_frame": pre.get("frame") if pre else None,
        "post_endpoint_frame": post.get("frame") if post else None,
    }


def _is_strong_short_prefix(
    summary: Mapping[str, Any],
    timeline: list[dict[str, Any]],
    trajectory: list[dict[str, Any]],
    direction: str,
    pitch_length_m: float,
    goalward_progress: float,
) -> bool:
    """Accept only a strong, attacking trusted prefix ending at a real boundary.

    The check never extends a trajectory over the next unknown row. It is a
    narrow fallback for a rapid release whose observed flight is cut short by
    an explicit canonical-track discontinuity.
    """

    if direction not in SUPPORTED_ATTACK_DIRECTIONS:
        return False
    if _number(summary.get("distance_m"), 0.0) < SHORT_PREFIX_MIN_DISTANCE_M:
        return False
    if _number(summary.get("mean_speed_mps"), 0.0) < SHORT_PREFIX_MIN_SPEED_MPS:
        return False
    if goalward_progress < MIN_GOALWARD_PROGRESS_M:
        return False
    start_goal_distance = _goal_distance(list(summary["start_position_m"]), direction, pitch_length_m)
    if start_goal_distance is None or start_goal_distance > SHORT_PREFIX_MAX_START_GOAL_DISTANCE_M:
        return False
    last_index = next((index for index, row in enumerate(timeline) if row is trajectory[-1]), None)
    if last_index is None or last_index + 1 >= len(timeline):
        return False
    return not _is_trusted_ball_position(timeline[last_index + 1])


def _trajectory_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points = [row["position_m"] for row in rows]
    distance = sum(_distance(points[index - 1], points[index]) for index in range(1, len(points)))
    displacement = _displacement(points[0], points[-1])
    duration = max(0.001, _number(rows[-1].get("time_sec"), 0.0) - _number(rows[0].get("time_sec"), 0.0))
    straight = _distance(points[0], points[-1]) / distance if distance > 0 else 0.0
    instantaneous = [
        _distance(points[index - 1], points[index]) / max(0.001, _number(rows[index].get("time_sec"), 0.0) - _number(rows[index - 1].get("time_sec"), 0.0))
        for index in range(1, len(points))
    ]
    return {
        "sample_count": len(rows),
        "duration_sec": _round(duration),
        "distance_m": _round(distance),
        "displacement_m": [_round(displacement[0]), _round(displacement[1])],
        "straightness": _round(straight),
        "mean_speed_mps": _round(distance / duration),
        "max_step_speed_mps": _round(max(instantaneous, default=0.0)),
        "start_position_m": [_round(points[0][0]), _round(points[0][1])],
        "end_position_m": [_round(points[-1][0]), _round(points[-1][1])],
    }


def _receiver_context(next_event: Mapping[str, Any] | None, launch_time: float) -> dict[str, Any]:
    if not isinstance(next_event, Mapping):
        return {"next_contact_event_id": None, "next_contact_gap_sec": None, "same_team_receiver_before_goal": False, "opponent_receiver": False}
    target_start = _number(next_event.get("start_time_sec"), _number(next_event.get("end_time_sec"), -1.0))
    gap = target_start - launch_time
    source_team = _text(next_event.get("team_id")) or _text(next_event.get("team_label"))
    return {
        "next_contact_event_id": next_event.get("event_id"),
        "next_contact_gap_sec": _round_or_none(gap if gap >= 0 else None),
        "next_contact_team_label": _text(next_event.get("team_label")),
        "next_contact_player_id": _text(next_event.get("stable_player_id")),
        # Resolved against source team in _candidate_from_contact once team is known.
        "next_contact_team_identity": source_team,
        "same_team_receiver_before_goal": False,
        "opponent_receiver": False,
    }


def _goalward_progress(start: list[float], end: list[float], direction: str) -> float:
    if direction == "towards_y_min":
        return start[1] - end[1]
    if direction == "towards_y_max":
        return end[1] - start[1]
    return 0.0


def _goal_distance(position: list[float], direction: str, pitch_length_m: float) -> float | None:
    if direction == "towards_y_min":
        return max(0.0, position[1])
    if direction == "towards_y_max":
        return max(0.0, pitch_length_m - position[1])
    return min(max(0.0, position[1]), max(0.0, pitch_length_m - position[1]))


def _goal_corridor_distance(position: list[float], pitch_width_m: float) -> float | None:
    return abs(position[0] - pitch_width_m / 2.0)


def _is_cross_like(displacement: list[float], approaches_goal: bool) -> bool:
    return approaches_goal and abs(displacement[0]) > max(2.0, abs(displacement[1]) * 1.15)


def _deduplicate(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        grouped.setdefault(str(row.get("source_match_id") or ""), []).append(row)
    result: list[dict[str, Any]] = []
    for source_id, rows in sorted(grouped.items()):
        cluster: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: (item["source_timestamp_sec"], item["candidate_key"])):
            if cluster and row["source_timestamp_sec"] - cluster[-1]["source_timestamp_sec"] > DEDUPLICATION_WINDOW_SEC:
                result.append(_merge_cluster(cluster, source_id))
                cluster = []
            cluster.append(row)
        if cluster:
            result.append(_merge_cluster(cluster, source_id))
    return result


def _merge_cluster(cluster: list[dict[str, Any]], source_id: str) -> dict[str, Any]:
    if len(cluster) == 1:
        return cluster[0]
    # A v4 bridge is an additional hypothesis, not permission to replace a
    # fully observed v1/v2/v3 hypothesis at the same contact.  Preserve the
    # observed candidate as the representative of an existing deduplication
    # cluster; bridge-only clusters remain reviewable additions.
    observed = [row for row in cluster if "continuity_bridge_applied" not in row.get("reasons", [])]
    primary = max(observed or cluster, key=lambda row: (row["confidence"], row["trajectory_evidence"]["distance_m"], -row["source_timestamp_sec"], row["candidate_key"]))
    member_keys = sorted(str(row["candidate_key"]) for row in cluster)
    key = _key("shot", {"source_match_id": source_id, "merged_hypotheses": member_keys})
    merged = dict(primary)
    merged.update({
        "candidate_id": f"shot-{key.rsplit(':', 1)[-1][:12]}",
        "candidate_key": key,
        "deduplicated_hypothesis_count": len(cluster),
        "merged_candidate_keys": member_keys,
        "reasons": sorted({reason for row in cluster for reason in row["reasons"]}),
    })
    return merged


def _apply_v3_structural_suppression(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Suppress only explainably pass-like or pre-shot signals in v3.

    This bounded post-processing sees the existing candidate evidence only. It
    deliberately does not read operator decisions, canonical shots, or any
    benchmark fixture. A later signal can suppress an earlier one only when
    it has materially stronger terminal evidence for the same attacking team.
    """

    by_source: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_source.setdefault(str(candidate.get("source_match_id") or ""), []).append(candidate)
    retained: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for source_candidates in by_source.values():
        ordered = sorted(source_candidates, key=lambda row: (_candidate_timestamp(row), str(row.get("candidate_key") or "")))
        for index, candidate in enumerate(ordered):
            reason = _v3_suppression_reason(candidate, ordered[index + 1 :])
            if reason is None:
                if _v3_strong_terminal_goal_approach(candidate):
                    candidate = {
                        **candidate,
                        "reasons": sorted({*candidate.get("reasons", []), "terminal_goal_approach"}),
                    }
                retained.append(candidate)
                continue
            suppressed.append({
                "candidate_id": candidate.get("candidate_id"),
                "candidate_key": candidate.get("candidate_key"),
                "source_match_id": candidate.get("source_match_id"),
                "source_timestamp_sec": candidate.get("source_timestamp_sec"),
                "suppression_reason": reason,
            })
    return retained, sorted(suppressed, key=lambda row: (_number(row.get("source_timestamp_sec"), 0.0), str(row.get("candidate_key") or "")))


def _v3_suppression_reason(candidate: Mapping[str, Any], later_candidates: list[Mapping[str, Any]]) -> str | None:
    if _v3_pass_like_same_team_receiver(candidate):
        return "pass_like_same_team_receiver"
    if _v3_has_stronger_later_shot(candidate, later_candidates):
        return "stronger_later_shot_action"
    return None


def _v3_pass_like_same_team_receiver(candidate: Mapping[str, Any]) -> bool:
    receiver = candidate.get("receiver_evidence") if isinstance(candidate.get("receiver_evidence"), Mapping) else {}
    trajectory = candidate.get("trajectory_evidence") if isinstance(candidate.get("trajectory_evidence"), Mapping) else {}
    return (
        bool(receiver.get("same_team_receiver_before_goal"))
        and _number(trajectory.get("distance_m"), 0.0) >= V3_PASS_LIKE_MIN_TRAJECTORY_DISTANCE_M
        and not _v3_strong_terminal_goal_approach(candidate)
    )


def _v3_has_stronger_later_shot(candidate: Mapping[str, Any], later_candidates: list[Mapping[str, Any]]) -> bool:
    if _v3_later_shot_goal_approach(candidate):
        return False
    source_team = _candidate_team_identity(candidate)
    timestamp = _candidate_timestamp(candidate)
    for later in later_candidates:
        if _candidate_timestamp(later) - timestamp > V3_LATER_SHOT_HORIZON_SEC:
            break
        if source_team is None or source_team != _candidate_team_identity(later):
            continue
        if _number(later.get("confidence"), 0.0) < _number(candidate.get("confidence"), 0.0) + V3_LATER_SHOT_MIN_CONFIDENCE_DELTA:
            continue
        if _v3_later_shot_goal_approach(later):
            return True
    return False


def _v3_strong_terminal_goal_approach(candidate: Mapping[str, Any]) -> bool:
    trajectory = candidate.get("trajectory_evidence") if isinstance(candidate.get("trajectory_evidence"), Mapping) else {}
    return (
        _number(trajectory.get("endpoint_goal_distance_m"), math.inf) <= V3_STRONG_TERMINAL_GOAL_DISTANCE_M
        and _number(trajectory.get("goal_corridor_distance_m"), math.inf) <= V3_STRONG_TERMINAL_CORRIDOR_DISTANCE_M
    )


def _v3_later_shot_goal_approach(candidate: Mapping[str, Any]) -> bool:
    trajectory = candidate.get("trajectory_evidence") if isinstance(candidate.get("trajectory_evidence"), Mapping) else {}
    return (
        _number(trajectory.get("endpoint_goal_distance_m"), math.inf) <= V3_LATER_SHOT_GOAL_DISTANCE_M
        and _number(trajectory.get("goal_corridor_distance_m"), math.inf) <= V3_LATER_SHOT_CORRIDOR_DISTANCE_M
    )


def _candidate_timestamp(candidate: Mapping[str, Any]) -> float:
    return _number(candidate.get("source_timestamp_sec"), _number(candidate.get("candidate_timestamp_sec"), 0.0))


def _candidate_team_identity(candidate: Mapping[str, Any]) -> str | None:
    return _text(candidate.get("suggested_team_label")) or _text(candidate.get("suggested_team_name"))


def _summary(candidates: list[dict[str, Any]], skipped: Counter[str], suppressed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    confidences = [float(row["confidence"]) for row in candidates]
    return {
        "candidates_total": len(candidates),
        "needs_review": len(candidates),
        "final_stat_eligible": 0,
        "team_attribution_available": sum(bool(row.get("suggested_team_label")) for row in candidates),
        "player_attribution_available": sum(bool(row.get("suggested_player_id")) for row in candidates),
        "deduplicated_hypotheses": sum(int(row.get("deduplicated_hypothesis_count") or 1) for row in candidates),
        "reason_distribution": dict(sorted(Counter(reason for row in candidates for reason in row["reasons"]).items())),
        "confidence": {"min": _round(min(confidences)) if confidences else None, "median": _round(median(confidences)) if confidences else None, "max": _round(max(confidences)) if confidences else None},
        "skipped_evidence_reasons": dict(sorted(skipped.items())),
        "suppressed_evidence_reasons": dict(sorted(Counter(str(row.get("suppression_reason") or "unknown") for row in suppressed or []).items())),
    }


def _validate_policy_version(policy_version: str) -> None:
    if policy_version not in {PREVIOUS_POLICY_VERSION, POLICY_VERSION, V3_POLICY_VERSION, V4_CONTINUITY_BRIDGE_POLICY_VERSION}:
        raise ValueError(f"Unsupported shot candidate policy version: {policy_version}")


def _key(kind: str, payload: Mapping[str, Any]) -> str:
    return f"{kind}:v1:{hashlib.sha256(canonical_json_bytes(dict(payload))).hexdigest()}"


def _valid_position(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in value)


def _distance(first: list[float], second: list[float]) -> float:
    return math.hypot(second[0] - first[0], second[1] - first[1])


def _displacement(first: list[float], second: list[float]) -> list[float]:
    return [second[0] - first[0], second[1] - first[1]]


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else default


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _round(value: float) -> float:
    return round(float(value), 3)


def _round_or_none(value: float | None) -> float | None:
    return _round(value) if value is not None else None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
