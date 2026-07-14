from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PASS_SOURCE = "ball_contact_events_to_pass_candidates_v1"
PASS_EVENT_STATUSES = {"accepted", "uncertain"}
PASS_REVIEW_STATUSES = {"needs_review", "accepted", "rejected", "uncertain"}
MIN_PASS_GAP_SEC = 0.05
MAX_PASS_GAP_SEC = 5.0
FORWARD_PASS_MIN_PROGRESS_M = 1.5
PROGRESSIVE_PASS_MIN_PROGRESS_M = 5.0
PASS_ATTEMPT_OUTCOMES = {"completed_pass", "failed_pass"}
MIN_PASS_RELEASE_DURATION_SEC = 0.12
MIN_PASS_RELEASE_DISTANCE_M = 0.85
MIN_PASS_RELEASE_PATH_DISTANCE_M = 0.85
MIN_PASS_RELEASE_MEAN_SPEED_MPS = 1.15
MIN_PASS_RELEASE_SOURCE_CLEARANCE_M = 0.9
MIN_PASS_RELEASE_STRAIGHTNESS = 0.3
IMMEDIATE_CONTESTED_FRAMES = 4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_pass_candidates_document(
    event_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None = None,
    possession_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contact_events = _sorted_contact_events(event_candidates_doc)
    possession_frames_by_frame = _possession_frames_by_frame(possession_doc)
    candidates: list[dict[str, Any]] = []
    skipped_reasons: Counter[str] = Counter()

    for source_event, target_event in zip(contact_events, contact_events[1:]):
        reason = _skip_reason(source_event, target_event)
        if reason:
            skipped_reasons[reason] += 1
            continue
        pass_type = _pass_type(source_event, target_event)
        start_position_m = _event_end_position_m(source_event)
        end_position_m = _event_start_position_m(target_event)
        displacement_m = _displacement_m(start_position_m, end_position_m)
        phase = _direction_for_team_at_time(
            match_phase_config_doc,
            source_event.get("team_label"),
            source_event.get("end_time_sec") if source_event.get("end_time_sec") is not None else source_event.get("start_time_sec"),
        )
        forward_progress_m = _forward_progress_m(start_position_m, end_position_m, phase["attack_direction"])
        direction = _pass_direction(forward_progress_m)
        release_evidence = _pass_release_evidence(source_event, target_event, possession_frames_by_frame)
        outcome = _classify_pass_outcome(source_event, target_event, pass_type, release_evidence)
        auto_review_status = _pass_auto_review_status(source_event, target_event, outcome)
        review_status = _initial_pass_review_status(auto_review_status)
        candidate_id = f"pass-{len(candidates) + 1:04d}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "event_type": "pass_candidate",
                "pass_type": pass_type,
                "outcome": outcome["outcome"],
                "count_for_team_label": outcome["count_for_team_label"],
                "completed": outcome["completed"],
                "failed": outcome["failed"],
                "from_restart": False,
                "excluded_reason": outcome["excluded_reason"],
                "source": PASS_SOURCE,
                "source_event_id": source_event.get("event_id"),
                "target_event_id": target_event.get("event_id"),
                "source_candidate_id": source_event.get("source_candidate_id"),
                "target_candidate_id": target_event.get("source_candidate_id"),
                "from_stable_player_id": source_event.get("stable_player_id"),
                "from_stable_subject_id": source_event.get("stable_subject_id"),
                "from_team_label": source_event.get("team_label"),
                "from_team_id": source_event.get("team_id"),
                "from_team_name": source_event.get("team_name"),
                "to_stable_player_id": target_event.get("stable_player_id"),
                "to_stable_subject_id": target_event.get("stable_subject_id"),
                "to_team_label": target_event.get("team_label"),
                "to_team_id": target_event.get("team_id"),
                "to_team_name": target_event.get("team_name"),
                "start_frame": source_event.get("end_frame"),
                "end_frame": target_event.get("start_frame"),
                "start_time_sec": source_event.get("end_time_sec"),
                "end_time_sec": target_event.get("start_time_sec"),
                "duration_sec": _round(_time_gap_sec(source_event, target_event), 3),
                "start_position_m": start_position_m,
                "end_position_m": end_position_m,
                "displacement_m": displacement_m,
                "distance_m": _distance_m(start_position_m, end_position_m),
                "match_phase_period_id": phase["period_id"],
                "attack_direction": phase["attack_direction"],
                "direction_source": phase["direction_source"],
                "forward_progress_m": forward_progress_m,
                "direction": direction,
                "is_progressive": forward_progress_m is not None and forward_progress_m >= PROGRESSIVE_PASS_MIN_PROGRESS_M,
                "confidence": _pass_confidence(source_event, target_event, pass_type, outcome),
                "auto_review_status": auto_review_status,
                "review_status": review_status,
                "review_source": "generated",
                "review_notes": "",
                "final_stat_eligible": False,
                "release_evidence": release_evidence.get("release_evidence"),
                "receiver_evidence": release_evidence.get("receiver_evidence"),
                "trajectory_evidence": release_evidence.get("trajectory_evidence"),
                "rejection_reasons": outcome["rejection_reasons"],
                "source_event_review_statuses": [
                    source_event.get("review_status"),
                    target_event.get("review_status"),
                ],
                "notes": [
                    "Candidate only. Do not count as final pass statistic until pass model/review is implemented.",
                ],
            }
        )

    return {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": PASS_SOURCE,
        "experimental": True,
        "candidate_semantics": "pass_candidates_from_consecutive_ball_contacts_not_final_stats",
        "parameters": {
            "min_pass_gap_sec": MIN_PASS_GAP_SEC,
            "max_pass_gap_sec": MAX_PASS_GAP_SEC,
            "forward_pass_min_progress_m": FORWARD_PASS_MIN_PROGRESS_M,
            "progressive_pass_min_progress_m": PROGRESSIVE_PASS_MIN_PROGRESS_M,
            "min_pass_release_duration_sec": MIN_PASS_RELEASE_DURATION_SEC,
            "min_pass_release_distance_m": MIN_PASS_RELEASE_DISTANCE_M,
            "min_pass_release_path_distance_m": MIN_PASS_RELEASE_PATH_DISTANCE_M,
            "min_pass_release_mean_speed_mps": MIN_PASS_RELEASE_MEAN_SPEED_MPS,
            "min_pass_release_source_clearance_m": MIN_PASS_RELEASE_SOURCE_CLEARANCE_M,
            "min_pass_release_straightness": MIN_PASS_RELEASE_STRAIGHTNESS,
            "immediate_contested_frames": IMMEDIATE_CONTESTED_FRAMES,
            "allowed_source_event_statuses": sorted(PASS_EVENT_STATUSES),
        },
        "summary": _pass_summary(contact_events, candidates, skipped_reasons),
        "candidates": candidates,
    }


def build_pass_review_report(pass_candidates_doc: dict[str, Any]) -> dict[str, Any]:
    summary = dict(pass_candidates_doc.get("summary") or {})
    warnings: list[str] = []
    if int(summary.get("pass_candidates") or 0) == 0:
        warnings.append("No pass candidates were generated from reviewed ball-contact events.")
    if int(summary.get("turnover_or_interception_candidates") or 0) > 0:
        warnings.append("Some candidate sequences switch team and should be treated as turnover/interception candidates.")
    if int(summary.get("needs_review_pass_candidates") or 0) > 0:
        warnings.append("Some pass candidates still need review before they can be used in final pass stats.")
    warnings.append("Only accepted same-team pass candidates are eligible for final pass stats.")
    return {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": PASS_SOURCE,
        "experimental": True,
        "summary": summary,
        "warnings": warnings,
        "notes": [
            "This layer is fully automatic and intentionally conservative.",
            "final_stat_eligible is true only for accepted same-team pass candidates.",
        ],
    }


def build_pass_candidate_artifacts(
    event_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None = None,
    possession_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pass_candidates = build_pass_candidates_document(event_candidates_doc, match_phase_config_doc, possession_doc)
    pass_review_report = build_pass_review_report(pass_candidates)
    return {
        "pass_candidates": pass_candidates,
        "pass_review_report": pass_review_report,
        "artifacts": {
            "pass_candidates": "pass_candidates.json",
            "pass_review_report": "pass_review_report.json",
        },
    }


def write_pass_candidate_artifacts(
    match_path: Path,
    event_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None = None,
    possession_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_reviews = _load_existing_pass_reviews(match_path)
    artifacts = build_pass_candidate_artifacts(event_candidates_doc, match_phase_config_doc, possession_doc)
    _apply_existing_pass_reviews(artifacts["pass_candidates"], existing_reviews)
    artifacts["pass_review_report"] = build_pass_review_report(artifacts["pass_candidates"])
    (match_path / "pass_candidates.json").write_text(
        json.dumps(artifacts["pass_candidates"], indent=2),
        encoding="utf-8",
    )
    (match_path / "pass_review_report.json").write_text(
        json.dumps(artifacts["pass_review_report"], indent=2),
        encoding="utf-8",
    )
    return artifacts


def _sorted_contact_events(event_candidates_doc: dict[str, Any]) -> list[dict[str, Any]]:
    events = [
        event
        for event in event_candidates_doc.get("events") or []
        if isinstance(event, dict)
        and event.get("event_type") == "ball_contact"
        and str(event.get("review_status") or "needs_review") in PASS_EVENT_STATUSES
    ]
    return sorted(events, key=lambda event: (float(event.get("start_time_sec") or 0.0), int(event.get("start_frame") or 0)))


def _skip_reason(source_event: dict[str, Any], target_event: dict[str, Any]) -> str | None:
    if not source_event.get("stable_player_id") or not target_event.get("stable_player_id"):
        return "missing_stable_player"
    if source_event.get("stable_player_id") == target_event.get("stable_player_id"):
        return "same_player_consecutive_contacts"
    gap = _time_gap_sec(source_event, target_event)
    if gap < MIN_PASS_GAP_SEC:
        return "gap_too_short"
    if gap > MAX_PASS_GAP_SEC:
        return "gap_too_long"
    if not _valid_pair(_event_end_position_m(source_event)) or not _valid_pair(_event_start_position_m(target_event)):
        return "missing_position"
    return None


def _pass_type(source_event: dict[str, Any], target_event: dict[str, Any]) -> str:
    source_team = source_event.get("team_id") or source_event.get("team_label")
    target_team = target_event.get("team_id") or target_event.get("team_label")
    if not source_team or not target_team:
        return "unknown_team_pass"
    if source_team == target_team:
        return "same_team_pass"
    return "turnover_or_interception"


def _pass_auto_review_status(source_event: dict[str, Any], target_event: dict[str, Any], outcome: dict[str, Any]) -> str:
    if outcome.get("outcome") == "excluded_non_pass":
        return "excluded_non_pass"
    statuses = {str(source_event.get("review_status") or "needs_review"), str(target_event.get("review_status") or "needs_review")}
    if statuses == {"accepted"}:
        return "strong_candidate"
    if "uncertain" in statuses:
        return "uncertain"
    return "candidate"


def _initial_pass_review_status(auto_review_status: str) -> str:
    if auto_review_status == "excluded_non_pass":
        return "rejected"
    if auto_review_status == "uncertain":
        return "uncertain"
    return "needs_review"


def _pass_confidence(
    source_event: dict[str, Any],
    target_event: dict[str, Any],
    pass_type: str,
    outcome: dict[str, Any] | None = None,
) -> float:
    source_confidence = float(source_event.get("confidence") or 0.0)
    target_confidence = float(target_event.get("confidence") or 0.0)
    confidence = min(source_confidence, target_confidence)
    if pass_type == "turnover_or_interception":
        confidence *= 0.75
    if outcome and outcome.get("outcome") == "excluded_non_pass":
        confidence *= 0.35
    if _pass_auto_review_status(source_event, target_event, outcome or {}) == "uncertain":
        confidence *= 0.7
    return _round(confidence, 4)


def normalize_pass_review_status(value: Any) -> str:
    status = str(value or "needs_review")
    if status in PASS_REVIEW_STATUSES:
        return status
    if status == "uncertain":
        return "uncertain"
    return "needs_review"


def update_pass_candidate_summary(document: dict[str, Any]) -> None:
    candidates = [candidate for candidate in document.get("candidates") or [] if isinstance(candidate, dict)]
    summary = dict(document.get("summary") or {})
    source_events = int(summary.get("source_contact_events") or 0)
    skipped_reasons = Counter(summary.get("skipped_reasons") if isinstance(summary.get("skipped_reasons"), dict) else {})
    summary.update(_pass_summary([{}] * source_events, candidates, skipped_reasons))
    document["summary"] = summary


def is_final_stat_pass_candidate(candidate: dict[str, Any]) -> bool:
    return (
        normalize_pass_review_status(candidate.get("review_status")) == "accepted"
        and str(candidate.get("pass_type") or "") == "same_team_pass"
        and str(candidate.get("outcome") or "completed_pass") == "completed_pass"
    )


def normalize_pass_candidate_review_fields(candidate: dict[str, Any]) -> None:
    raw_status = str(candidate.get("review_status") or "needs_review")
    if raw_status in {"strong_candidate", "candidate"}:
        candidate.setdefault("auto_review_status", raw_status)
        candidate["review_status"] = "needs_review"
    else:
        candidate["review_status"] = normalize_pass_review_status(raw_status)
    candidate.setdefault("review_source", "generated")
    candidate.setdefault("review_notes", "")
    candidate["final_stat_eligible"] = is_final_stat_pass_candidate(candidate)


def _pass_summary(
    contact_events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    skipped_reasons: Counter[str],
) -> dict[str, Any]:
    for candidate in candidates:
        normalize_pass_candidate_review_fields(candidate)
    type_counts = Counter(str(candidate.get("pass_type") or "unknown") for candidate in candidates)
    review_counts = Counter(normalize_pass_review_status(candidate.get("review_status")) for candidate in candidates)
    auto_review_counts = Counter(str(candidate.get("auto_review_status") or "unknown") for candidate in candidates)
    direction_counts = Counter(str(candidate.get("direction") or "unknown") for candidate in candidates)
    outcome_counts = Counter(str(candidate.get("outcome") or _legacy_outcome(candidate)) for candidate in candidates)
    attempt_candidates = [candidate for candidate in candidates if _is_pass_attempt_candidate(candidate)]
    completed_candidates = [candidate for candidate in attempt_candidates if _candidate_completed(candidate)]
    failed_candidates = [candidate for candidate in attempt_candidates if _candidate_failed(candidate)]
    team_attempts = Counter(str(candidate.get("count_for_team_label") or "") for candidate in attempt_candidates)
    team_completed = Counter(str(candidate.get("count_for_team_label") or "") for candidate in completed_candidates)
    team_failed = Counter(str(candidate.get("count_for_team_label") or "") for candidate in failed_candidates)
    restart_attempts = [candidate for candidate in attempt_candidates if candidate.get("from_restart") is True]
    final_candidates = [candidate for candidate in candidates if is_final_stat_pass_candidate(candidate)]
    return {
        "source_contact_events": len(contact_events),
        "pass_candidates": len(candidates),
        "pass_type_counts": dict(sorted(type_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "review_status_counts": dict(sorted(review_counts.items())),
        "auto_review_status_counts": dict(sorted(auto_review_counts.items())),
        "same_team_pass_candidates": type_counts.get("same_team_pass", 0),
        "turnover_or_interception_candidates": type_counts.get("turnover_or_interception", 0),
        "unknown_team_pass_candidates": type_counts.get("unknown_team_pass", 0),
        "pass_attempts": len(attempt_candidates),
        "completed_passes": len(completed_candidates),
        "failed_passes": len(failed_candidates),
        "excluded_non_pass_candidates": outcome_counts.get("excluded_non_pass", 0),
        "restart_pass_attempts": len(restart_attempts),
        "restart_completed_passes": sum(1 for candidate in restart_attempts if _candidate_completed(candidate)),
        "restart_failed_passes": sum(1 for candidate in restart_attempts if _candidate_failed(candidate)),
        "team_pass_attempts": _counter_without_empty(team_attempts),
        "team_completed_passes": _counter_without_empty(team_completed),
        "team_failed_passes": _counter_without_empty(team_failed),
        "completion_rate": _ratio(len(completed_candidates), len(attempt_candidates)),
        "direction_counts": dict(sorted(direction_counts.items())),
        "forward_pass_candidates": direction_counts.get("forward", 0),
        "backward_pass_candidates": direction_counts.get("backward", 0),
        "lateral_pass_candidates": direction_counts.get("lateral", 0),
        "unknown_direction_pass_candidates": direction_counts.get("unknown", 0),
        "progressive_pass_candidates": sum(1 for candidate in candidates if candidate.get("is_progressive")),
        "progressive_passes": sum(1 for candidate in attempt_candidates if candidate.get("is_progressive")),
        "skipped_sequences": sum(skipped_reasons.values()),
        "skipped_reasons": dict(sorted(skipped_reasons.items())),
        "accepted_pass_candidates": review_counts.get("accepted", 0),
        "rejected_pass_candidates": review_counts.get("rejected", 0),
        "uncertain_pass_candidates": review_counts.get("uncertain", 0),
        "needs_review_pass_candidates": review_counts.get("needs_review", 0),
        "final_stat_passes": len(final_candidates),
        "final_forward_passes": sum(1 for candidate in final_candidates if candidate.get("direction") == "forward"),
        "final_progressive_passes": sum(1 for candidate in final_candidates if candidate.get("is_progressive")),
        "candidates_with_positions": sum(
            1 for candidate in candidates if candidate.get("start_position_m") and candidate.get("end_position_m")
        ),
        "candidates_with_direction": sum(
            1 for candidate in candidates if candidate.get("direction") not in {None, "unknown"}
        ),
    }


def _possession_frames_by_frame(possession_doc: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not isinstance(possession_doc, dict):
        return {}
    frames = possession_doc.get("frames")
    if not isinstance(frames, list):
        return {}
    return {
        int(frame.get("frame") or 0): frame
        for frame in frames
        if isinstance(frame, dict) and frame.get("frame") is not None
    }


def _pass_release_evidence(
    source_event: dict[str, Any],
    target_event: dict[str, Any],
    possession_frames_by_frame: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    start_frame = int(source_event.get("end_frame") or source_event.get("start_frame") or 0)
    end_frame = int(target_event.get("start_frame") or target_event.get("end_frame") or start_frame)
    rows = [
        possession_frames_by_frame[frame_idx]
        for frame_idx in range(min(start_frame, end_frame), max(start_frame, end_frame) + 1)
        if frame_idx in possession_frames_by_frame
    ]
    positions = [row.get("ball_position_m") for row in rows if _valid_pair(row.get("ball_position_m"))]
    start_position = _event_end_position_m(source_event)
    end_position = _event_start_position_m(target_event)
    if len(positions) < 2 and _valid_pair(start_position) and _valid_pair(end_position):
        positions = [start_position, end_position]
    duration = _time_gap_sec(source_event, target_event)
    path_distance = _path_distance_m(positions)
    displacement = _distance_m(positions[0], positions[-1]) if len(positions) >= 2 else _distance_m(start_position, end_position)
    straightness = float(displacement or 0.0) / max(float(path_distance or 0.0), 0.001)
    mean_speed = float(path_distance or 0.0) / max(duration, 0.001)
    source_reference = _event_end_player_position_m(source_event) or start_position
    source_clearance = _max_distance_from_point(positions, source_reference)
    status_counts = Counter(str(row.get("status") or "unknown") for row in rows)
    owner_counts = Counter(
        str(row.get("stable_player_id") or "")
        for row in rows
        if row.get("status") == "controlled" and row.get("stable_player_id")
    )
    immediate_rows = rows[:IMMEDIATE_CONTESTED_FRAMES]
    immediate_contested = sum(1 for row in immediate_rows if row.get("status") == "contested")
    receiver_min_distance = _event_evidence_number(target_event, "min_distance_m")
    return {
        "release_evidence": {
            "method": "possession_frames" if rows else "event_endpoints_only",
            "start_frame": start_frame,
            "end_frame": end_frame,
            "duration_sec": _round(duration, 3),
            "source_clearance_m": _round(source_clearance, 3) if source_clearance is not None else None,
            "immediate_contested_frames": immediate_contested,
            "controlled_by_source_frames": owner_counts.get(str(source_event.get("stable_player_id") or ""), 0),
            "controlled_by_target_frames": owner_counts.get(str(target_event.get("stable_player_id") or ""), 0),
        },
        "receiver_evidence": {
            "stable_player_id": target_event.get("stable_player_id"),
            "team_label": target_event.get("team_label"),
            "min_distance_m": receiver_min_distance,
            "confidence": target_event.get("confidence"),
        },
        "trajectory_evidence": {
            "sampled_frames": len(rows),
            "ball_path_distance_m": _round(path_distance or 0.0, 3),
            "ball_displacement_m": _round(displacement or 0.0, 3),
            "mean_ball_speed_mps": _round(mean_speed, 3),
            "ball_path_straightness": _round(straightness, 4),
            "status_counts": dict(sorted(status_counts.items())),
        },
    }


def _classify_pass_outcome(
    source_event: dict[str, Any],
    target_event: dict[str, Any],
    pass_type: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    release = evidence.get("release_evidence") if isinstance(evidence.get("release_evidence"), dict) else {}
    trajectory = evidence.get("trajectory_evidence") if isinstance(evidence.get("trajectory_evidence"), dict) else {}
    rejection_reasons = _pass_like_rejection_reasons(release, trajectory)
    source_team = source_event.get("team_label")
    count_for_team = source_team if source_team in {"A", "B"} and not rejection_reasons else None
    if rejection_reasons:
        return {
            "outcome": "excluded_non_pass",
            "count_for_team_label": None,
            "completed": False,
            "failed": False,
            "excluded_reason": rejection_reasons[0],
            "rejection_reasons": rejection_reasons,
        }
    if pass_type == "same_team_pass":
        return {
            "outcome": "completed_pass",
            "count_for_team_label": count_for_team,
            "completed": True,
            "failed": False,
            "excluded_reason": None,
            "rejection_reasons": [],
        }
    if pass_type == "turnover_or_interception":
        return {
            "outcome": "failed_pass",
            "count_for_team_label": count_for_team,
            "completed": False,
            "failed": True,
            "excluded_reason": None,
            "rejection_reasons": [],
        }
    return {
        "outcome": "unknown_pass_attempt",
        "count_for_team_label": count_for_team,
        "completed": False,
        "failed": False,
        "excluded_reason": None,
        "rejection_reasons": [],
    }


def _pass_like_rejection_reasons(release: dict[str, Any], trajectory: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    duration = float(release.get("duration_sec") or 0.0)
    path_distance = float(trajectory.get("ball_path_distance_m") or 0.0)
    displacement = float(trajectory.get("ball_displacement_m") or 0.0)
    mean_speed = float(trajectory.get("mean_ball_speed_mps") or 0.0)
    straightness = float(trajectory.get("ball_path_straightness") or 0.0)
    source_clearance = release.get("source_clearance_m")
    immediate_contested = int(release.get("immediate_contested_frames") or 0)
    if duration < MIN_PASS_RELEASE_DURATION_SEC and displacement < MIN_PASS_RELEASE_DISTANCE_M:
        reasons.append("release_too_short")
    if displacement < MIN_PASS_RELEASE_DISTANCE_M:
        reasons.append("ball_displacement_too_short")
    if path_distance < MIN_PASS_RELEASE_PATH_DISTANCE_M:
        reasons.append("ball_path_too_short")
    if mean_speed < MIN_PASS_RELEASE_MEAN_SPEED_MPS and path_distance < MIN_PASS_RELEASE_PATH_DISTANCE_M * 1.8:
        reasons.append("ball_release_too_slow")
    if source_clearance is not None and float(source_clearance) < MIN_PASS_RELEASE_SOURCE_CLEARANCE_M:
        reasons.append("ball_never_left_source_player")
    if immediate_contested >= 2 and path_distance < MIN_PASS_RELEASE_PATH_DISTANCE_M * 2.0:
        reasons.append("immediate_contested_tackle")
    if straightness < MIN_PASS_RELEASE_STRAIGHTNESS and path_distance < MIN_PASS_RELEASE_PATH_DISTANCE_M * 2.5:
        reasons.append("ball_path_not_pass_like")
    return reasons


def _event_end_player_position_m(event: dict[str, Any]) -> list[float] | None:
    return _rounded_pair(event.get("end_player_position_m"))


def _event_evidence_number(event: dict[str, Any], key: str) -> float | None:
    evidence = event.get("evidence")
    if not isinstance(evidence, dict):
        return None
    value = evidence.get(key)
    if value is None:
        return None
    try:
        return _round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _path_distance_m(positions: list[Any]) -> float | None:
    valid = [position for position in positions if _valid_pair(position)]
    if len(valid) < 2:
        return None
    return sum(float(_distance_m(valid[index - 1], valid[index]) or 0.0) for index in range(1, len(valid)))


def _max_distance_from_point(positions: list[Any], point: Any) -> float | None:
    if not _valid_pair(point):
        return None
    distances = [float(_distance_m(position, point) or 0.0) for position in positions if _valid_pair(position)]
    return max(distances, default=None)


def _is_pass_attempt_candidate(candidate: dict[str, Any]) -> bool:
    outcome = str(candidate.get("outcome") or _legacy_outcome(candidate))
    return outcome in PASS_ATTEMPT_OUTCOMES


def _candidate_completed(candidate: dict[str, Any]) -> bool:
    if candidate.get("completed") is not None:
        return bool(candidate.get("completed"))
    return _legacy_outcome(candidate) == "completed_pass"


def _candidate_failed(candidate: dict[str, Any]) -> bool:
    if candidate.get("failed") is not None:
        return bool(candidate.get("failed"))
    return _legacy_outcome(candidate) == "failed_pass"


def _legacy_outcome(candidate: dict[str, Any]) -> str:
    pass_type = str(candidate.get("pass_type") or "")
    if pass_type == "same_team_pass":
        return "completed_pass"
    if pass_type == "turnover_or_interception":
        return "failed_pass"
    return "unknown_pass_attempt"


def _counter_without_empty(counter: Counter[str]) -> dict[str, int]:
    return {key: value for key, value in sorted(counter.items()) if key}


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _round(float(numerator) / float(denominator), 4)


def _load_existing_pass_reviews(match_path: Path) -> dict[tuple[Any, Any], dict[str, Any]]:
    path = match_path / "pass_candidates.json"
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(document, dict):
        return {}
    reviews: dict[tuple[Any, Any], dict[str, Any]] = {}
    for candidate in document.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        key = _candidate_pair_key(candidate)
        if key == (None, None):
            continue
        reviews[key] = candidate
    return reviews


def _apply_existing_pass_reviews(document: dict[str, Any], existing_reviews: dict[tuple[Any, Any], dict[str, Any]]) -> None:
    if not existing_reviews:
        update_pass_candidate_summary(document)
        return
    for candidate in document.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        existing = existing_reviews.get(_candidate_pair_key(candidate))
        if not existing:
            continue
        for key in ["review_status", "review_source", "review_notes", "reviewed_at"]:
            if key in existing:
                candidate[key] = existing.get(key)
    update_pass_candidate_summary(document)


def _candidate_pair_key(candidate: dict[str, Any]) -> tuple[Any, Any]:
    return (candidate.get("source_event_id"), candidate.get("target_event_id"))


def _direction_for_team_at_time(
    config: dict[str, Any] | None,
    team_label: Any,
    time_sec: Any,
) -> dict[str, Any]:
    if not config:
        return {"period_id": None, "attack_direction": "unknown", "direction_source": "missing_match_phase_config"}
    label = str(team_label or "").upper()
    time_value = float(time_sec or 0.0)
    for period in config.get("periods") or []:
        if not isinstance(period, dict):
            continue
        start = float(period.get("start_time_sec") or 0.0)
        end = period.get("end_time_sec")
        end_value = float(end) if end is not None else float("inf")
        if start <= time_value <= end_value:
            directions = period.get("team_attack_directions") if isinstance(period.get("team_attack_directions"), dict) else {}
            return {
                "period_id": period.get("period_id"),
                "attack_direction": _normalize_attack_direction(directions.get(label)),
                "direction_source": period.get("direction_source") or "match_phase_config",
            }
    return {"period_id": None, "attack_direction": "unknown", "direction_source": "outside_configured_periods"}


def _normalize_attack_direction(value: Any) -> str:
    direction = str(value or "unknown")
    allowed = {"towards_y_min", "towards_y_max", "towards_x_min", "towards_x_max", "unknown"}
    return direction if direction in allowed else "unknown"


def _forward_progress_m(start: Any, end: Any, attack_direction: str) -> float | None:
    if not _valid_pair(start) or not _valid_pair(end):
        return None
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    if attack_direction == "towards_y_min":
        return _round(sy - ey, 3)
    if attack_direction == "towards_y_max":
        return _round(ey - sy, 3)
    if attack_direction == "towards_x_min":
        return _round(sx - ex, 3)
    if attack_direction == "towards_x_max":
        return _round(ex - sx, 3)
    return None


def _pass_direction(forward_progress_m: float | None) -> str:
    if forward_progress_m is None:
        return "unknown"
    if forward_progress_m >= FORWARD_PASS_MIN_PROGRESS_M:
        return "forward"
    if forward_progress_m <= -FORWARD_PASS_MIN_PROGRESS_M:
        return "backward"
    return "lateral"


def _time_gap_sec(source_event: dict[str, Any], target_event: dict[str, Any]) -> float:
    return float(target_event.get("start_time_sec") or 0.0) - float(source_event.get("end_time_sec") or 0.0)


def _event_start_position_m(event: dict[str, Any]) -> list[float] | None:
    return _rounded_pair(event.get("start_position_m"))


def _event_end_position_m(event: dict[str, Any]) -> list[float] | None:
    return _rounded_pair(event.get("end_position_m"))


def _rounded_pair(value: Any) -> list[float] | None:
    if not _valid_pair(value):
        return None
    return [_round(float(value[0]), 3), _round(float(value[1]), 3)]


def _valid_pair(value: Any) -> bool:
    return isinstance(value, list | tuple) and len(value) == 2 and value[0] is not None and value[1] is not None


def _distance_m(start: Any, end: Any) -> float | None:
    if not _valid_pair(start) or not _valid_pair(end):
        return None
    dx, dy = _displacement_m(start, end) or [0.0, 0.0]
    return _round((dx**2 + dy**2) ** 0.5, 3)


def _displacement_m(start: Any, end: Any) -> list[float] | None:
    if not _valid_pair(start) or not _valid_pair(end):
        return None
    return [_round(float(end[0]) - float(start[0]), 3), _round(float(end[1]) - float(start[1]), 3)]


def _round(value: float, digits: int) -> float:
    return round(float(value), digits)
