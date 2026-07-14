from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.pass_candidates import build_pass_candidate_artifacts

EVENT_SOURCE = "contact_candidates_to_event_candidates_v1"
EVENT_REVIEW_STATUSES = {"needs_review", "accepted", "rejected", "uncertain"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_review_status(value: Any) -> str:
    status = str(value or "needs_review")
    if status not in EVENT_REVIEW_STATUSES:
        return "needs_review"
    return status


def _event_confidence(candidate: dict[str, Any], review_status: str) -> float:
    confidence = float(candidate.get("mean_confidence") or 0.0)
    if review_status == "accepted":
        return round(max(confidence, 0.75), 4)
    if review_status == "uncertain":
        return round(min(confidence, 0.5), 4)
    return round(confidence, 4)


def build_event_candidates_document(contact_candidates_doc: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        candidate
        for candidate in contact_candidates_doc.get("candidates") or []
        if isinstance(candidate, dict)
    ]
    review_counts = Counter(
        _normalize_review_status(candidate.get("review_status") or candidate.get("status"))
        for candidate in candidates
    )
    review_source_counts = Counter(str(candidate.get("review_source") or "unknown") for candidate in candidates)
    events: list[dict[str, Any]] = []
    for candidate in candidates:
        review_status = _normalize_review_status(candidate.get("review_status") or candidate.get("status"))
        if review_status == "rejected":
            continue
        event_id = f"event-{len(events) + 1:04d}"
        events.append(
            {
                "event_id": event_id,
                "event_type": "ball_contact",
                "source": EVENT_SOURCE,
                "source_candidate_id": candidate.get("candidate_id"),
                "review_status": review_status,
                "final_stat_eligible": review_status == "accepted",
                "confidence": _event_confidence(candidate, review_status),
                "source_confidence": candidate.get("mean_confidence"),
                "stable_player_id": candidate.get("stable_player_id"),
                "stable_subject_id": candidate.get("stable_subject_id"),
                "team_label": candidate.get("team_label"),
                "team_id": candidate.get("team_id"),
                "team_name": candidate.get("team_name"),
                "start_frame": candidate.get("start_frame"),
                "end_frame": candidate.get("end_frame"),
                "start_time_sec": candidate.get("start_time_sec"),
                "end_time_sec": candidate.get("end_time_sec"),
                "duration_sec": candidate.get("duration_sec"),
                "start_position_m": candidate.get("start_ball_position_m"),
                "end_position_m": candidate.get("end_ball_position_m"),
                "start_position_px": candidate.get("start_ball_position_px"),
                "end_position_px": candidate.get("end_ball_position_px"),
                "start_player_position_m": candidate.get("start_player_position_m"),
                "end_player_position_m": candidate.get("end_player_position_m"),
                "evidence": {
                    "frames": candidate.get("frames"),
                    "detected_ball_frames": candidate.get("detected_ball_frames"),
                    "detected_player_frames": candidate.get("detected_player_frames"),
                    "interpolated_player_frames": candidate.get("interpolated_player_frames"),
                    "player_source_counts": candidate.get("player_source_counts") or {},
                    "mean_distance_m": candidate.get("mean_distance_m"),
                    "min_distance_m": candidate.get("min_distance_m"),
                    "start_ball_position_m": candidate.get("start_ball_position_m"),
                    "end_ball_position_m": candidate.get("end_ball_position_m"),
                    "start_player_position_m": candidate.get("start_player_position_m"),
                    "end_player_position_m": candidate.get("end_player_position_m"),
                    "review_notes": candidate.get("review_notes") or "",
                    "review_source": candidate.get("review_source"),
                    "auto_review": candidate.get("auto_review") or {},
                },
            }
        )

    event_counts = Counter(str(event.get("review_status") or "needs_review") for event in events)
    summary = {
        "source_contact_candidates": len(candidates),
        "events_total": len(events),
        "ball_contact_events": len(events),
        "events_by_review_status": {status: event_counts.get(status, 0) for status in sorted(EVENT_REVIEW_STATUSES)},
        "contact_review_counts": {status: review_counts.get(status, 0) for status in sorted(EVENT_REVIEW_STATUSES)},
        "contact_review_source_counts": dict(sorted(review_source_counts.items())),
        "auto_reviewed_contacts": sum(
            count
            for source, count in review_source_counts.items()
            if source.startswith("auto_contact_review")
        ),
        "manual_reviewed_contacts": review_source_counts.get("manual", 0) + review_source_counts.get("manual_legacy", 0),
        "accepted_events": event_counts.get("accepted", 0),
        "uncertain_events": event_counts.get("uncertain", 0),
        "needs_review_events": event_counts.get("needs_review", 0),
        "rejected_contacts": review_counts.get("rejected", 0),
        "final_stat_events": event_counts.get("accepted", 0),
        "review_required_events": event_counts.get("needs_review", 0) + event_counts.get("uncertain", 0),
    }
    return {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": EVENT_SOURCE,
        "experimental": True,
        "event_semantics": "reviewed_candidates_not_final_football_events",
        "summary": summary,
        "events": events,
    }


def build_event_review_report(event_candidates_doc: dict[str, Any]) -> dict[str, Any]:
    summary = dict(event_candidates_doc.get("summary") or {})
    warnings: list[str] = []
    if int(summary.get("accepted_events") or 0) == 0:
        warnings.append("No accepted ball-contact events yet; event stats should remain candidate-only.")
    if int(summary.get("review_required_events") or 0) > 0:
        warnings.append("Some ball-contact events still need review before downstream pass/shot logic.")
    if int(summary.get("rejected_contacts") or 0) > 0:
        warnings.append("Rejected contact candidates were excluded from event_candidates.json.")
    return {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": EVENT_SOURCE,
        "experimental": True,
        "summary": summary,
        "warnings": warnings,
        "notes": [
            "Only accepted ball-contact events are eligible for final statistics.",
            "needs_review and uncertain events are retained for debugging and later review, not final stats.",
            "Passes and shots are intentionally not inferred from this artifact yet.",
        ],
    }


def build_event_candidate_artifacts(
    contact_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None = None,
    possession_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_candidates = build_event_candidates_document(contact_candidates_doc)
    event_review_report = build_event_review_report(event_candidates)
    pass_artifacts = build_pass_candidate_artifacts(event_candidates, match_phase_config_doc, possession_doc)
    return {
        "event_candidates": event_candidates,
        "event_review_report": event_review_report,
        "pass_candidates": pass_artifacts["pass_candidates"],
        "pass_review_report": pass_artifacts["pass_review_report"],
        "artifacts": {
            "event_candidates": "event_candidates.json",
            "event_review_report": "event_review_report.json",
            **pass_artifacts["artifacts"],
        },
    }


def write_event_candidate_artifacts(
    match_path: Path,
    contact_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None = None,
    possession_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifacts = build_event_candidate_artifacts(contact_candidates_doc, match_phase_config_doc, possession_doc)
    (match_path / "event_candidates.json").write_text(
        json.dumps(artifacts["event_candidates"], indent=2),
        encoding="utf-8",
    )
    (match_path / "event_review_report.json").write_text(
        json.dumps(artifacts["event_review_report"], indent=2),
        encoding="utf-8",
    )
    (match_path / "pass_candidates.json").write_text(
        json.dumps(artifacts["pass_candidates"], indent=2),
        encoding="utf-8",
    )
    (match_path / "pass_review_report.json").write_text(
        json.dumps(artifacts["pass_review_report"], indent=2),
        encoding="utf-8",
    )
    return artifacts
