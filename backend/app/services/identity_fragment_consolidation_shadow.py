from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

from app.services.identity_fragment_endpoint_reliability import (
    assess_fragment_endpoint_reliability,
    summarize_endpoint_pair,
)


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_fragment_consolidation_shadow"
ALGORITHM_VERSION = "0.2.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "max_gap_seconds": 3.0,
    "max_boundary_overlap_frames": 2,
    "recommended_gap_seconds": 1.0,
    "recommended_endpoint_distance_m": 3.0,
    "recommended_required_speed_mps": 12.0,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_identity_fragment_consolidation_shadow(
    candidate_doc: dict[str, Any],
    candidate_overlay_doc: dict[str, Any],
    active_roster_doc: dict[str, Any],
    *,
    fps: float,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Propose auditable adjacent-fragment links without merging candidate subjects."""
    safe_fps = max(float(fps), 1e-6)
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or now_iso()
    overlay_by_subject = {
        str(row.get("candidate_subject_id") or row.get("stable_subject_id") or ""): row
        for row in candidate_overlay_doc.get("players") or []
    }
    active_by_subject = {
        str(row.get("candidate_subject_id") or ""): row
        for row in active_roster_doc.get("subjects") or []
    }

    eligible_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    excluded_subjects: list[dict[str, Any]] = []
    for subject in candidate_doc.get("subjects") or []:
        exclusion = _subject_exclusion(subject)
        if exclusion:
            excluded_subjects.append(_excluded_subject(subject, exclusion))
            continue
        anchor = str((subject.get("production_subject_ids") or [""])[0])
        team = str(subject.get("team_label") or "U")
        eligible_groups[(team, anchor)].append(subject)

    proposals: list[dict[str, Any]] = []
    rejected_pairs: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    for (team, anchor), fragments in sorted(eligible_groups.items()):
        ordered = sorted(
            fragments,
            key=lambda row: (
                int(row.get("start_frame") or 0),
                int(row.get("end_frame") or 0),
                str(row.get("candidate_subject_id") or ""),
            ),
        )
        for source, target in zip(ordered, ordered[1:], strict=False):
            result = _evaluate_pair(
                source,
                target,
                team=team,
                anchor=anchor,
                source_overlay=overlay_by_subject.get(str(source.get("candidate_subject_id") or "")) or {},
                target_overlay=overlay_by_subject.get(str(target.get("candidate_subject_id") or "")) or {},
                source_active=active_by_subject.get(str(source.get("candidate_subject_id") or "")) or {},
                target_active=active_by_subject.get(str(target.get("candidate_subject_id") or "")) or {},
                fps=safe_fps,
                params=params,
            )
            if result["decision"] == "reject":
                reason = str(result["rejection_reason"])
                rejection_counts[reason] += 1
                rejected_pairs.append(result)
            else:
                proposals.append(result)

    proposals.sort(key=lambda row: (int(row["source_end_frame"]), str(row["proposal_key"])))
    rejected_pairs.sort(key=lambda row: (int(row["source_end_frame"]), str(row["pair_key"])))
    endpoint_quality_counts = Counter(
        str((row.get("endpoint_reliability") or {}).get("quality") or "missing")
        for row in proposals
    )
    summary = {
        "candidate_subjects": len(candidate_doc.get("subjects") or []),
        "eligible_fragment_subjects": sum(len(rows) for rows in eligible_groups.values()),
        "anchor_groups": len(eligible_groups),
        "multi_fragment_anchor_groups": sum(len(rows) > 1 for rows in eligible_groups.values()),
        "proposed_links": len(proposals),
        "recommended_for_review": sum(row["decision"] == "recommended_review" for row in proposals),
        "needs_review": sum(row["decision"] == "needs_review" for row in proposals),
        "rejected_pairs": len(rejected_pairs),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "excluded_subjects": len(excluded_subjects),
        "endpoint_quality_counts": dict(sorted(endpoint_quality_counts.items())),
        "promotion_readiness": "blocked_pending_visual_audit",
    }
    proposal_doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_fragment_consolidation_proposals",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": params},
        "source": {
            "candidate_algorithm": candidate_doc.get("algorithm") or {},
            "active_roster_algorithm": active_roster_doc.get("algorithm") or {},
        },
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatically_merges_fragments": False,
            "eligible_for_player_stats": False,
        },
        "summary": summary,
        "proposals": proposals,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_fragment_consolidation_proposals",
        "algorithm": proposal_doc["algorithm"],
        "status": "ready_for_visual_audit",
        "summary": summary,
        "gates": {
            "production_identity_untouched": True,
            "candidate_identity_untouched": True,
            "no_cross_team_proposals": all(row["source_team_label"] == row["target_team_label"] for row in proposals),
            "no_parallel_fragment_proposals": all(int(row["overlap_frames"]) <= int(params["max_boundary_overlap_frames"]) for row in proposals),
            "all_proposals_require_visual_review": all(bool(row["requires_visual_review"]) for row in proposals),
            "endpoint_quality_is_advisory_only": all(
                not bool((row.get("endpoint_reliability") or {}).get("safe_for_automatic_identity_merge"))
                for row in proposals
            ),
        },
        "excluded_subjects": excluded_subjects,
        "rejected_pairs": rejected_pairs[:500],
        "limitations": [
            "A shared production anchor is weak evidence and never authorizes an automatic merge.",
            "Appearance ReID is not available in this stage, so every proposal requires visual review.",
            "Local endpoint consistency cannot distinguish a person from a consistently tracked ball or background fragment.",
            "Rejected and excluded fragments remain unchanged in the candidate identity artifacts.",
        ],
    }
    return {
        "identity_fragment_consolidation_shadow": proposal_doc,
        "identity_fragment_consolidation_shadow_report": report,
    }


def _subject_exclusion(subject: dict[str, Any]) -> str | None:
    team = str(subject.get("team_label") or "U")
    anchors = [str(value) for value in subject.get("production_subject_ids") or [] if value]
    flags = {str(value) for value in subject.get("quality_flags") or []}
    if team not in {"A", "B"}:
        return "unknown_team"
    if "production_anchor_team_mismatch" in flags:
        return "production_anchor_team_mismatch"
    if len(anchors) != 1:
        return "requires_exactly_one_safe_anchor"
    return None


def _excluded_subject(subject: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "candidate_subject_id": subject.get("candidate_subject_id"),
        "candidate_player_id": subject.get("candidate_player_id"),
        "team_label": subject.get("team_label"),
        "reason": reason,
    }


def _evaluate_pair(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    team: str,
    anchor: str,
    source_overlay: dict[str, Any],
    target_overlay: dict[str, Any],
    source_active: dict[str, Any],
    target_active: dict[str, Any],
    fps: float,
    params: dict[str, Any],
) -> dict[str, Any]:
    source_id = str(source.get("candidate_subject_id") or "")
    target_id = str(target.get("candidate_subject_id") or "")
    source_end = int(source.get("end_frame") or 0)
    target_start = int(target.get("start_frame") or 0)
    overlap_frames = max(0, source_end - target_start + 1)
    gap_frames = max(0, target_start - source_end - 1)
    gap_seconds = gap_frames / fps
    pair_payload = {"source": source_id, "target": target_id, "anchor": anchor}
    pair_digest = hashlib.sha256(
        json.dumps(pair_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    base = {
        "pair_key": f"identity-fragment-pair:v1:{pair_digest}",
        "source_candidate_subject_id": source_id,
        "target_candidate_subject_id": target_id,
        "source_candidate_player_id": source.get("candidate_player_id"),
        "target_candidate_player_id": target.get("candidate_player_id"),
        "source_team_label": team,
        "target_team_label": team,
        "shared_production_anchor": anchor,
        "source_end_frame": source_end,
        "target_start_frame": target_start,
        "gap_frames": gap_frames,
        "gap_seconds": round(gap_seconds, 4),
        "overlap_frames": overlap_frames,
        "source_active_ratio": _active_ratio(source_active),
        "target_active_ratio": _active_ratio(target_active),
    }
    if overlap_frames > int(params["max_boundary_overlap_frames"]):
        return {**base, "decision": "reject", "rejection_reason": "parallel_temporal_overlap"}
    if gap_seconds > float(params["max_gap_seconds"]):
        return {**base, "decision": "reject", "rejection_reason": "gap_too_long"}

    source_position = _endpoint_position(source_overlay, at_end=True)
    target_position = _endpoint_position(target_overlay, at_end=False)
    source_reliability = assess_fragment_endpoint_reliability(
        source_overlay,
        at_end=True,
        fps=fps,
    )
    target_reliability = assess_fragment_endpoint_reliability(
        target_overlay,
        at_end=False,
        fps=fps,
    )
    distance = _pitch_distance(
        source_position.get("pitch_m") if source_position else None,
        target_position.get("pitch_m") if target_position else None,
    )
    elapsed_seconds = max((target_start - source_end) / fps, 1.0 / fps)
    required_speed = distance / elapsed_seconds if distance is not None else None
    evidence = ["same_team", "same_safe_production_anchor", "adjacent_anchor_fragments"]
    reasons: list[str] = []
    if distance is None:
        reasons.append("missing_reliable_endpoint_pitch")
    else:
        evidence.append("endpoint_pitch_available")
    recommended = (
        gap_seconds <= float(params["recommended_gap_seconds"])
        and distance is not None
        and distance <= float(params["recommended_endpoint_distance_m"])
        and required_speed is not None
        and required_speed <= float(params["recommended_required_speed_mps"])
    )
    if not recommended:
        if gap_seconds > float(params["recommended_gap_seconds"]):
            reasons.append("gap_requires_review")
        if distance is not None and distance > float(params["recommended_endpoint_distance_m"]):
            reasons.append("endpoint_distance_requires_review")
        if required_speed is not None and required_speed > float(params["recommended_required_speed_mps"]):
            reasons.append("required_speed_requires_review")
    confidence = _proposal_confidence(
        gap_seconds=gap_seconds,
        distance=distance,
        required_speed=required_speed,
        params=params,
    )
    proposal_key = f"identity-fragment-link:v1:{pair_digest}"
    return {
        **base,
        "proposal_key": proposal_key,
        "decision": "recommended_review" if recommended else "needs_review",
        "confidence": confidence,
        "endpoint_distance_m": round(distance, 4) if distance is not None else None,
        "required_speed_mps": round(required_speed, 4) if required_speed is not None else None,
        "source_endpoint": _compact_endpoint(source_position),
        "target_endpoint": _compact_endpoint(target_position),
        "source_endpoint_reliability": source_reliability,
        "target_endpoint_reliability": target_reliability,
        "endpoint_reliability": summarize_endpoint_pair(source_reliability, target_reliability),
        "evidence": evidence,
        "reason_codes": sorted(set(reasons)),
        "requires_visual_review": True,
        "review_status": "pending",
    }


def _endpoint_position(player: dict[str, Any], *, at_end: bool) -> dict[str, Any] | None:
    positions = [
        row
        for row in player.get("overlay_positions") or []
        if str(row.get("source") or "detected") == "detected"
        and isinstance(row.get("pitch_m"), (list, tuple))
        and len(row["pitch_m"]) >= 2
    ]
    if not positions:
        return None
    key = lambda row: int(row.get("frame") or 0)
    return max(positions, key=key) if at_end else min(positions, key=key)


def _compact_endpoint(position: dict[str, Any] | None) -> dict[str, Any] | None:
    if position is None:
        return None
    return {
        "frame": int(position.get("frame") or 0),
        "pitch_m": [round(float(value), 3) for value in position.get("pitch_m")[:2]],
        "bbox_xyxy": position.get("bbox_xyxy"),
        "confidence": position.get("confidence"),
        "footpoint_reliable": bool(position.get("footpoint_reliable")),
        "appearance_reliable": bool(position.get("appearance_reliable")),
        "play_area_status": position.get("play_area_status"),
        "quality_class": position.get("quality_class"),
    }


def _active_ratio(subject: dict[str, Any]) -> float | None:
    active = int(subject.get("active_frames") or 0)
    suppressed = int(subject.get("suppressed_frames") or 0)
    total = active + suppressed
    return round(active / total, 4) if total else None


def _pitch_distance(left: Any, right: Any) -> float | None:
    if not isinstance(left, (list, tuple)) or len(left) < 2:
        return None
    if not isinstance(right, (list, tuple)) or len(right) < 2:
        return None
    return math.hypot(float(left[0]) - float(right[0]), float(left[1]) - float(right[1]))


def _proposal_confidence(
    *,
    gap_seconds: float,
    distance: float | None,
    required_speed: float | None,
    params: dict[str, Any],
) -> float:
    gap_score = max(0.0, 1.0 - gap_seconds / max(float(params["max_gap_seconds"]), 1e-6))
    distance_score = (
        max(0.0, 1.0 - distance / max(float(params["recommended_endpoint_distance_m"]) * 2.0, 1e-6))
        if distance is not None
        else 0.0
    )
    speed_score = (
        max(0.0, 1.0 - required_speed / max(float(params["recommended_required_speed_mps"]) * 2.0, 1e-6))
        if required_speed is not None
        else 0.0
    )
    return round(0.45 * gap_score + 0.30 * distance_score + 0.25 * speed_score, 4)
