from __future__ import annotations

"""Evaluation-only traces for explaining shadow shot-candidate misses.

This module reads frozen benchmark inputs and persisted source evidence.  It
must never be imported by production generation code: its output is a review
artifact, not an event or a source of policy decisions.
"""

from collections import Counter
from typing import Any, Mapping

from app.services import shot_candidates
from app.services.match_phase_config import direction_for_team_at_time
from evaluation.shot_candidate_benchmark import DEFAULT_TOLERANCE_SEC, benchmark_shot_candidates


FAILURE_ANALYSIS_SCHEMA_VERSION = "shot-candidate-failure-analysis:v1"
CONTACT_SEARCH_WINDOW_SEC = 2.0


def analyze_shot_candidate_failures(
    candidates_doc: Mapping[str, Any],
    goldset_doc: Mapping[str, Any],
    sources: list[Mapping[str, Any]],
    *,
    tolerance_sec: float = DEFAULT_TOLERANCE_SEC,
) -> dict[str, Any]:
    """Trace every frozen gold shot through the persisted candidate pipeline."""

    benchmark = benchmark_shot_candidates(candidates_doc, goldset_doc, tolerance_sec=tolerance_sec)
    policy_version = str(candidates_doc.get("policy_version") or shot_candidates.POLICY_VERSION)
    shot_candidates._validate_policy_version(policy_version)
    matched_ids = {str(row.get("gold_shot_id") or "") for row in benchmark["matches"]}
    traces = [
        _trace_gold_shot(dict(gold), sources, str(gold.get("id") or "") in matched_ids, policy_version)
        for gold in goldset_doc.get("shots") or []
        if isinstance(gold, Mapping)
    ]
    counts = Counter(
        str(row["classification"]["primary_category"])
        for row in traces
        if not row["benchmark_matched"]
    )
    return {
        "schema_version": FAILURE_ANALYSIS_SCHEMA_VERSION,
        "evaluation_only": True,
        "candidate_policy_version": candidates_doc.get("policy_version"),
        "benchmark": benchmark,
        "summary": {
            "gold_shots": len(traces),
            "matched_gold_shots": len(matched_ids),
            "missed_gold_shots": len(traces) - len(matched_ids),
            "miss_root_cause_distribution": dict(sorted(counts.items())),
        },
        "gold_shot_traces": traces,
        "limitations": [
            "The closest contact and raw ball evidence are temporal evidence, not a manual identity of a visual ball bbox.",
            "Manual video notes are intentionally excluded from classifications; they may be compared by an operator after this artifact is generated.",
        ],
    }


def _trace_gold_shot(
    gold: dict[str, Any],
    sources: list[Mapping[str, Any]],
    matched: bool,
    policy_version: str,
) -> dict[str, Any]:
    logical_time = _number(gold.get("timestamp_sec"), 0.0)
    source = _source_for_logical_time(sources, logical_time)
    if source is None:
        return _unmapped_trace(gold, logical_time, matched)
    offset = _number(source.get("logical_start_sec"), 0.0)
    source_time = logical_time - offset
    events_document = _mapping(source.get("event_candidates"))
    ball_tracks_document = _mapping(source.get("ball_tracks"))
    contacts = shot_candidates._contact_events(events_document)
    timeline = shot_candidates._ball_timeline(ball_tracks_document)
    ball_candidates_document = _mapping(source.get("ball_candidates"))
    contact_paths = [
        _trace_contact_path(
            contact,
            contacts[index + 1] if index + 1 < len(contacts) else None,
            source_time=source_time,
            timeline=timeline,
            ball_candidates_document=ball_candidates_document,
            phase_config=_mapping(source.get("match_phase_config")),
            source_match_id=_text(source.get("source_match_id")),
            pitch_width_m=_number(source.get("pitch_width_m"), 30.0),
            pitch_length_m=_number(source.get("pitch_length_m"), 47.4),
            logical_offset_sec=offset,
            policy_version=policy_version,
        )
        for index, contact in enumerate(contacts)
        if abs(_number(contact.get("end_time_sec"), source_time) - source_time) <= CONTACT_SEARCH_WINDOW_SEC
    ]
    contact_paths.sort(key=lambda row: (abs(_number(row["contact"].get("timing_delta_sec"), 0.0)), str(row["contact"].get("nearest_contact_event_id") or "")))
    nearest_path = contact_paths[0] if contact_paths else None
    gold_time_ball_evidence = _raw_ball_evidence(ball_candidates_document, source_time, timeline)
    classification = _classify_failure(
        matched=matched,
        gold_team=_text(gold.get("team")),
        contact_paths=contact_paths,
        gold_time_ball_evidence=gold_time_ball_evidence,
    )
    return {
        "gold_shot_id": gold.get("id"),
        "gold_team": gold.get("team"),
        "logical_timestamp_sec": _round(logical_time),
        "timestamp_display": gold.get("timestamp_display"),
        "source_match_id": source.get("source_match_id"),
        "source_timestamp_sec": _round(source_time),
        "benchmark_matched": matched,
        "contact": nearest_path["contact"] if nearest_path else _contact_trace(None, None),
        "contact_paths": contact_paths,
        "gold_time_ball_evidence": gold_time_ball_evidence,
        "ball_launch": nearest_path["ball_launch"] if nearest_path else {"exists": False, "valid_for_shot_generator": False},
        "raw_ball_evidence": nearest_path["raw_ball_evidence"] if nearest_path else gold_time_ball_evidence,
        "selected_ball_position_near_gold": gold_time_ball_evidence.get("nearest_selected_position"),
        "trajectory": nearest_path["trajectory"] if nearest_path else {},
        "phase": nearest_path["phase"] if nearest_path else {},
        "receiver_context": nearest_path["receiver_context"] if nearest_path else {},
        "shot_policy": nearest_path["shot_policy"] if nearest_path else {"generated": False, "rejection_stage": "contact", "rejection_reason": "no_plausible_contact", "confidence_if_scored": None},
        "classification": classification,
    }


def _trace_contact_path(
    contact: Mapping[str, Any],
    next_contact: Mapping[str, Any] | None,
    *,
    source_time: float,
    timeline: list[dict[str, Any]],
    ball_candidates_document: Mapping[str, Any],
    phase_config: Mapping[str, Any],
    source_match_id: str | None,
    pitch_width_m: float,
    pitch_length_m: float,
    logical_offset_sec: float,
    policy_version: str,
) -> dict[str, Any]:
    launch_time = _number(contact.get("end_time_sec"), source_time)
    launch = shot_candidates._nearest_trusted_position(timeline, launch_time, shot_candidates.MAX_LAUNCH_POSITION_DELTA_SEC)
    trajectory = shot_candidates._trajectory_from_launch(timeline, launch, launch_time) if launch is not None else None
    team_label = _text(contact.get("team_label"))
    phase = dict(direction_for_team_at_time(phase_config, team_label, launch_time)) if team_label else {"attack_direction": "unknown", "direction_source": "missing_team_attribution"}
    candidate, rejection_reason = shot_candidates._candidate_from_contact(
        contact,
        next_contact,
        timeline,
        phase_config,
        source_match_id=source_match_id,
        pitch_width_m=pitch_width_m,
        pitch_length_m=pitch_length_m,
        logical_offset_sec=logical_offset_sec,
        policy_version=policy_version,
    )
    return {
        "contact": _contact_trace(contact, _round(launch_time - source_time)),
        "ball_launch": _launch_trace(launch, contact),
        "raw_ball_evidence": _raw_ball_evidence(ball_candidates_document, launch_time, timeline),
        "selected_ball_position_near_launch": _position_trace(_nearest_row(timeline, launch_time), launch_time),
        "trajectory": _trajectory_trace(timeline, launch, launch_time),
        "phase": {"team_label": team_label, **phase},
        "receiver_context": _receiver_for_trace(next_contact, launch_time, contact),
        "shot_policy": {
            "generated": candidate is not None,
            "rejection_stage": _rejection_stage(rejection_reason),
            "rejection_reason": rejection_reason,
            "confidence_if_scored": candidate.get("confidence") if candidate else None,
            "candidate_key_if_generated": candidate.get("candidate_key") if candidate else None,
        },
    }


def _source_for_logical_time(sources: list[Mapping[str, Any]], logical_time: float) -> Mapping[str, Any] | None:
    ordered = sorted(sources, key=lambda row: (_number(row.get("logical_start_sec"), 0.0), str(row.get("source_match_id") or "")))
    for index, source in enumerate(ordered):
        start = _number(source.get("logical_start_sec"), 0.0)
        end = _number(source.get("logical_end_sec"), start)
        if start <= logical_time < end or (index == len(ordered) - 1 and start <= logical_time <= end):
            return source
    return None


def _raw_ball_evidence(
    document: Mapping[str, Any],
    source_time: float,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    """Inspect only the production launch-neighborhood and aligned frames."""

    frames = [
        dict(row) for row in document.get("frames") or []
        if isinstance(row, Mapping)
        and abs(_number(row.get("time_sec"), -9999.0) - source_time) <= shot_candidates.MAX_LAUNCH_POSITION_DELTA_SEC
    ]
    accepted = [dict(candidate) for row in frames for candidate in row.get("candidates") or [] if isinstance(candidate, Mapping)]
    rejected = [dict(candidate) for row in frames for candidate in row.get("rejected_candidates") or [] if isinstance(candidate, Mapping)]
    raw_predictions = sum(int(_number(row.get("raw_predictions"), 0.0)) for row in frames)
    reasons = Counter(str(item.get("reason") or "unknown") for item in rejected)
    selected_by_frame = {int(_number(row.get("frame"), -1.0)): row for row in timeline}
    frame_traces = []
    for frame in frames:
        accepted_rows = [dict(row) for row in frame.get("candidates") or [] if isinstance(row, Mapping)]
        rejected_rows = [dict(row) for row in frame.get("rejected_candidates") or [] if isinstance(row, Mapping)]
        selected = selected_by_frame.get(int(_number(frame.get("frame"), -1.0)))
        frame_traces.append({
            "frame": frame.get("frame"),
            "time_sec": frame.get("time_sec"),
            "raw_predictions": frame.get("raw_predictions"),
            "accepted_candidates": [_candidate_trace(row) for row in accepted_rows],
            "accepted_candidate_ids": [row.get("candidate_id") for row in accepted_rows],
            "trusted_accepted_candidate_ids": [
                row.get("candidate_id")
                for row in accepted_rows
                if _number(row.get("confidence"), 0.0) >= shot_candidates.MIN_BALL_CONFIDENCE
            ],
            "low_confidence_accepted_candidate_ids": [
                row.get("candidate_id")
                for row in accepted_rows
                if _number(row.get("confidence"), 0.0) < shot_candidates.MIN_BALL_CONFIDENCE
            ],
            "rejected_candidates": [_candidate_trace(row) for row in rejected_rows],
            "canonical_selected": _position_trace(selected, _number(frame.get("time_sec"), source_time)),
        })
    trusted_accepted = [row for row in accepted if _number(row.get("confidence"), 0.0) >= shot_candidates.MIN_BALL_CONFIDENCE]
    simultaneous = [
        row for row in frame_traces
        if len(row["accepted_candidate_ids"]) > 1
    ]
    return {
        "window_sec": shot_candidates.MAX_LAUNCH_POSITION_DELTA_SEC,
        "frame_count": len(frames),
        "raw_prediction_count": raw_predictions,
        "accepted_candidate_count": len(accepted),
        "trusted_accepted_candidate_count": len(trusted_accepted),
        "rejected_candidate_count": len(rejected),
        "closest_candidate": _candidate_trace(_nearest_by_time(accepted, source_time)),
        "closest_rejected_candidate": _candidate_trace(_nearest_by_time(rejected, source_time)),
        "rejection_reasons": dict(sorted(reasons.items())),
        "nearest_selected_position": _position_trace(_nearest_row(timeline, source_time), source_time),
        "frames": frame_traces,
        "simultaneous_accepted_candidate_frames": simultaneous,
    }


def _trajectory_trace(timeline: list[dict[str, Any]], launch: dict[str, Any] | None, launch_time: float) -> dict[str, Any]:
    if launch is None:
        return {"samples": 0, "detected_samples": 0, "interpolated_samples": 0, "first_boundary": None, "boundary_reason": "missing_launch_ball_position"}
    prefix = _trusted_prefix_from_launch(timeline, launch, launch_time)
    boundary = _first_trajectory_boundary(timeline, launch, launch_time)
    summary = shot_candidates._trajectory_summary(prefix) if len(prefix) >= 2 else None
    return {
        "start": _position_trace(launch, launch_time),
        "samples": len(prefix),
        "detected_samples": sum(str(row.get("source") or "") == "detected" for row in prefix),
        "interpolated_samples": sum(str(row.get("source") or "") == "interpolated" for row in prefix),
        "first_boundary": _position_trace(boundary[0], launch_time) if boundary[0] else None,
        "boundary_reason": boundary[1],
        "summary": summary,
    }


def _trusted_prefix_from_launch(
    timeline: list[dict[str, Any]],
    launch: Mapping[str, Any],
    launch_time: float,
) -> list[dict[str, Any]]:
    """Return observed trusted rows before a boundary, including sub-policy prefixes."""

    prefix: list[dict[str, Any]] = []
    previous_time: float | None = None
    started = False
    for row in timeline:
        time_sec = _number(row.get("time_sec"), -1.0)
        if not started:
            if row is launch:
                started = True
                prefix.append(row)
                previous_time = time_sec
            continue
        if time_sec > launch_time + shot_candidates.MAX_TRAJECTORY_SECONDS:
            break
        if not shot_candidates._is_trusted_ball_position(row):
            break
        if previous_time is None or time_sec <= previous_time + 0.001:
            continue
        if time_sec - previous_time > shot_candidates.MAX_TRAJECTORY_GAP_SEC:
            break
        prefix.append(row)
        previous_time = time_sec
    return prefix


def _first_trajectory_boundary(timeline: list[dict[str, Any]], launch: Mapping[str, Any], launch_time: float) -> tuple[dict[str, Any] | None, str | None]:
    started = False
    previous_time: float | None = None
    for row in timeline:
        time_sec = _number(row.get("time_sec"), -1.0)
        if not started:
            if row is launch:
                started = True
                previous_time = time_sec
            continue
        if time_sec > launch_time + shot_candidates.MAX_TRAJECTORY_SECONDS:
            return row, "horizon_reached"
        if not shot_candidates._is_trusted_ball_position(row):
            return row, f"untrusted_{str(row.get('source') or 'unknown')}"
        if previous_time is not None and time_sec - previous_time > shot_candidates.MAX_TRAJECTORY_GAP_SEC:
            return row, "time_gap"
        previous_time = time_sec
    return None, None


def _classify_failure(
    *,
    matched: bool,
    gold_team: str | None,
    contact_paths: list[Mapping[str, Any]],
    gold_time_ball_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify a miss from every eligible production contact path."""

    if matched:
        return {"primary_category": "EMITTED_OR_NEARBY_CANDIDATE", "first_failing_stage": None, "contributing_categories": []}
    if not contact_paths:
        contributors = ["BALL_TRACK_SELECTION_FAILURE"] if _has_same_frame_selection_divergence(gold_time_ball_evidence) else []
        return {"primary_category": "CONTACT_OR_ATTRIBUTION_FAILURE", "first_failing_stage": "contact", "contributing_categories": contributors}
    team_consistent_paths = [
        row for row in contact_paths
        if not gold_team or _text(_mapping(row.get("contact")).get("team")) == gold_team
    ]
    if not team_consistent_paths:
        return {"primary_category": "CONTACT_OR_ATTRIBUTION_FAILURE", "first_failing_stage": "contact_attribution", "contributing_categories": []}
    path_categories = [_path_failure_category(row) for row in team_consistent_paths]
    unique_categories = {(row["primary_category"], row["first_failing_stage"]) for row in path_categories}
    if len(unique_categories) == 1:
        return path_categories[0]
    return {
        "primary_category": "MIXED",
        "first_failing_stage": "ambiguous_contact_paths",
        "contributing_categories": sorted({row["primary_category"] for row in path_categories}),
    }


def _path_failure_category(path: Mapping[str, Any]) -> dict[str, Any]:
    policy = _mapping(path.get("shot_policy"))
    if bool(policy.get("generated")):
        return {"primary_category": "MIXED", "first_failing_stage": "candidate_matching", "contributing_categories": ["EMITTED_BUT_NOT_ASSIGNED_TO_THIS_GOLD"]}
    reason = _text(policy.get("rejection_reason"))
    raw_evidence = _mapping(path.get("raw_ball_evidence"))
    if reason == "missing_launch_ball_position":
        return _launch_failure_category(raw_evidence)
    if reason in {"missing_continuous_ball_trajectory", "trajectory_not_meaningful"}:
        return {"primary_category": "BALL_CONTINUITY_FAILURE", "first_failing_stage": "trajectory", "contributing_categories": []}
    if reason in {"unknown_team_without_goal_approach", "unsupported_attack_axis"}:
        return {"primary_category": "CONTACT_OR_ATTRIBUTION_FAILURE", "first_failing_stage": "direction_or_attribution", "contributing_categories": []}
    if reason:
        return {"primary_category": "SHOT_HEURISTIC_FAILURE", "first_failing_stage": "shot_heuristic", "contributing_categories": []}
    return {"primary_category": "MIXED", "first_failing_stage": "candidate_matching", "contributing_categories": []}


def _launch_failure_category(raw_evidence: Mapping[str, Any]) -> dict[str, Any]:
    raw_count = int(_number(raw_evidence.get("raw_prediction_count"), 0.0))
    accepted = int(_number(raw_evidence.get("accepted_candidate_count"), 0.0))
    trusted_accepted = int(_number(raw_evidence.get("trusted_accepted_candidate_count"), 0.0))
    rejected = int(_number(raw_evidence.get("rejected_candidate_count"), 0.0))
    if raw_count == 0:
        return _launch_classification("RAW_DETECTOR_MISS", "no_raw_launch_evidence")
    if not accepted:
        return _launch_classification("BALL_CANDIDATE_FILTERED", "launch_candidate_filtered")
    selection_states = _low_confidence_selection_states(raw_evidence)
    if trusted_accepted == 0:
        if rejected or len(selection_states) > 1:
            return _launch_classification("MIXED", "contradictory_launch_evidence")
        if selection_states == {"selected_low_confidence_candidate"}:
            return _launch_classification("BALL_CONTINUITY_FAILURE", "low_confidence_launch_evidence")
        if selection_states == {"selected_another_candidate"}:
            return _launch_classification("BALL_TRACK_SELECTION_FAILURE", "low_confidence_candidate_not_selected")
        if selection_states == {"canonical_selection_missing"}:
            return _launch_classification("BALL_TRACK_SELECTION_FAILURE", "low_confidence_candidate_without_canonical_selection")
        return _launch_classification("MIXED", "low_confidence_candidate_identity_unavailable")
    if rejected:
        return _launch_classification("MIXED", "accepted_and_filtered_launch_evidence")
    if _has_same_frame_selection_divergence(raw_evidence):
        return _launch_classification("BALL_TRACK_SELECTION_FAILURE", "simultaneous_ball_candidate_selection")
    if trusted_accepted:
        return _launch_classification("BALL_TRACK_SELECTION_FAILURE", "accepted_launch_candidate_not_usable_by_canonical_track")
    return _launch_classification("MIXED", "unclassified_launch_evidence")


def _launch_classification(primary_category: str, diagnostic_reason: str) -> dict[str, Any]:
    return {
        "primary_category": primary_category,
        "first_failing_stage": "launch_selection",
        "contributing_categories": [],
        "diagnostic_reason": diagnostic_reason,
    }


def _low_confidence_selection_states(raw_evidence: Mapping[str, Any]) -> set[str]:
    """Relate low-confidence accepted detections to canonical selection per frame."""

    states: set[str] = set()
    for frame in raw_evidence.get("frames") or []:
        if not isinstance(frame, Mapping):
            continue
        low_confidence_ids = {
            str(candidate_id)
            for candidate_id in frame.get("low_confidence_accepted_candidate_ids") or []
            if candidate_id
        }
        if not low_confidence_ids:
            continue
        selected = _mapping(frame.get("canonical_selected"))
        selected_id = _text(selected.get("candidate_id"))
        if selected_id is None:
            states.add("canonical_selection_missing")
        elif selected_id in low_confidence_ids:
            states.add("selected_low_confidence_candidate")
        else:
            states.add("selected_another_candidate")
    return states


def _has_same_frame_selection_divergence(raw_evidence: Mapping[str, Any]) -> bool:
    for frame in raw_evidence.get("simultaneous_accepted_candidate_frames") or []:
        if not isinstance(frame, Mapping):
            continue
        selected = _mapping(frame.get("canonical_selected"))
        selected_id = _text(selected.get("candidate_id"))
        accepted_ids = {str(item) for item in frame.get("accepted_candidate_ids") or [] if item}
        if selected_id and selected_id in accepted_ids and len(accepted_ids) > 1:
            return True
    return False


def _contact_trace(contact: Mapping[str, Any] | None, delta: float | None) -> dict[str, Any]:
    if contact is None:
        return {"exists": False, "timing_delta_sec": delta, "nearest_contact_event_id": None, "status": None, "team": None, "player": None}
    return {"exists": True, "timing_delta_sec": delta, "nearest_contact_event_id": contact.get("event_id"), "status": contact.get("review_status"), "team": contact.get("team_name"), "team_label": contact.get("team_label"), "player": contact.get("stable_player_id")}


def _launch_trace(launch: Mapping[str, Any] | None, contact: Mapping[str, Any] | None) -> dict[str, Any]:
    if launch is None:
        return {"exists": False, "valid_for_shot_generator": False}
    launch_time = _number(contact.get("end_time_sec"), _number(launch.get("time_sec"), 0.0)) if contact else _number(launch.get("time_sec"), 0.0)
    return {"exists": True, **_position_trace(launch, launch_time), "valid_for_shot_generator": shot_candidates._is_trusted_ball_position(launch)}


def _position_trace(row: Mapping[str, Any] | None, anchor_time: float) -> dict[str, Any] | None:
    if row is None:
        return None
    time_sec = _number(row.get("time_sec"), anchor_time)
    return {"frame": row.get("frame"), "time_sec": _round(time_sec), "timing_delta_sec": _round(time_sec - anchor_time), "source": row.get("source"), "confidence": row.get("confidence"), "candidate_id": row.get("candidate_id"), "position_m": row.get("position_m")}


def _candidate_trace(candidate: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {key: candidate.get(key) for key in ("candidate_id", "frame", "time_sec", "confidence", "position_m", "reason")}


def _receiver_for_trace(next_contact: Mapping[str, Any] | None, launch_time: float, contact: Mapping[str, Any]) -> dict[str, Any]:
    context = shot_candidates._receiver_context(next_contact, launch_time)
    team = _text(contact.get("team_id")) or _text(contact.get("team_label"))
    next_team = context.pop("next_contact_team_identity", None)
    context["same_team_receiver_before_goal"] = bool(team and team == next_team and context.get("next_contact_gap_sec") is not None and context["next_contact_gap_sec"] <= shot_candidates.SAME_TEAM_RECEIVER_SUPPRESSION_SEC)
    context["opponent_receiver"] = bool(team and next_team and team != next_team and context.get("next_contact_gap_sec") is not None and context["next_contact_gap_sec"] <= shot_candidates.SAME_TEAM_RECEIVER_SUPPRESSION_SEC)
    return context


def _rejection_stage(reason: str | None) -> str | None:
    if reason in {"missing_launch_ball_position"}:
        return "ball_launch"
    if reason in {"missing_continuous_ball_trajectory", "trajectory_not_meaningful"}:
        return "trajectory"
    if reason in {"not_goalward", "unknown_team_without_goal_approach", "unsupported_attack_axis"}:
        return "direction"
    if reason:
        return "shot_heuristic"
    return None


def _nearest_row(rows: list[dict[str, Any]], time_sec: float) -> dict[str, Any] | None:
    return min(rows, key=lambda row: (abs(_number(row.get("time_sec"), 0.0) - time_sec), _number(row.get("frame"), 0.0))) if rows else None


def _nearest_by_time(rows: list[dict[str, Any]], time_sec: float) -> dict[str, Any] | None:
    return _nearest_row(rows, time_sec)


def _unmapped_trace(gold: Mapping[str, Any], logical_time: float, matched: bool) -> dict[str, Any]:
    return {"gold_shot_id": gold.get("id"), "gold_team": gold.get("team"), "logical_timestamp_sec": _round(logical_time), "source_match_id": None, "source_timestamp_sec": None, "benchmark_matched": matched, "contact": _contact_trace(None, None), "ball_launch": {"exists": False, "valid_for_shot_generator": False}, "raw_ball_evidence": {}, "selected_ball_position_near_gold": None, "trajectory": {}, "phase": {}, "receiver_context": {}, "shot_policy": {"generated": False, "rejection_stage": "source_mapping", "rejection_reason": "logical_source_not_found", "confidence_if_scored": None}, "classification": {"primary_category": "MIXED", "first_failing_stage": "source_mapping", "contributing_categories": []}}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _round(value: float) -> float:
    return round(float(value), 3)
