from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_fragment_consolidation_visual_audit"
ALGORITHM_VERSION = "0.1.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_identity_fragment_consolidation_audit_manifest(
    consolidation_doc: dict[str, Any],
    *,
    benchmark_id: str,
    benchmark_label: str,
    video_path: str,
    video_time_offset_sec: float = 0.0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Adapt P1.8 proposals to the established large-card identity audit UI."""
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    proposals = sorted(
        consolidation_doc.get("proposals") or [],
        key=lambda row: (
            int(row.get("source_end_frame") or 0),
            str(row.get("proposal_key") or ""),
        ),
    )
    for proposal in proposals:
        source_endpoint = proposal.get("source_endpoint")
        target_endpoint = proposal.get("target_endpoint")
        if not isinstance(source_endpoint, dict) or not isinstance(target_endpoint, dict):
            skipped.append(
                {
                    "proposal_key": proposal.get("proposal_key"),
                    "reason": "missing_endpoint",
                }
            )
            continue
        source_time = int(source_endpoint.get("frame") or 0) / _source_fps(proposal)
        target_time = int(target_endpoint.get("frame") or 0) / _source_fps(proposal)
        proposal_key = str(proposal.get("proposal_key") or "")
        items.append(
            {
                "audit_index": len(items) + 1,
                "candidate_key": proposal_key,
                "card_filename": f"{len(items) + 1:03d}-{_short_key(proposal_key)}.jpg",
                "source": _endpoint_payload(
                    proposal,
                    source_endpoint,
                    side="source",
                    source_time_sec=source_time,
                    video_time_offset_sec=video_time_offset_sec,
                ),
                "transition": {
                    "source_time_sec": round((source_time + target_time) / 2.0, 3),
                    "video_time_sec": round(
                        max(0.0, ((source_time + target_time) / 2.0) - video_time_offset_sec),
                        3,
                    ),
                    "gap_sec": proposal.get("gap_seconds"),
                },
                "target": _endpoint_payload(
                    proposal,
                    target_endpoint,
                    side="target",
                    source_time_sec=target_time,
                    video_time_offset_sec=video_time_offset_sec,
                ),
                "decision": {
                    "current_identity_relation": "shared_anchor_fragment_proposal",
                    "source_stable_subject_ids": [proposal.get("source_candidate_subject_id")],
                    "target_stable_subject_ids": [proposal.get("target_candidate_subject_id")],
                    "source_quality_class": proposal.get("decision"),
                    "target_quality_class": proposal.get("decision"),
                    "cost": round(1.0 - float(proposal.get("confidence") or 0.0), 4),
                    "base_confidence": proposal.get("confidence"),
                    "recommendation_votes": None,
                    "recommendation_votes_required": None,
                    "distance_m": proposal.get("endpoint_distance_m"),
                    "required_speed_mps": proposal.get("required_speed_mps"),
                    "velocity_prediction_distance_m": None,
                    "appearance_distance_rgb": None,
                    "bbox_area_ratio": None,
                    "feature_costs": {},
                    "bonuses": {},
                    "penalties": {},
                    "evidence": proposal.get("evidence") or [],
                    "occlusion_event_ids": [],
                    "shared_production_anchor": proposal.get("shared_production_anchor"),
                    "reason_codes": proposal.get("reason_codes") or [],
                    "source_active_ratio": proposal.get("source_active_ratio"),
                    "target_active_ratio": proposal.get("target_active_ratio"),
                },
                "manual_review": {
                    "status": "pending",
                    "same_person": None,
                    "reviewer": None,
                    "reviewed_at": None,
                    "notes": "",
                },
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "mode": "developer_visual_audit",
        "audit_kind": "fragment_consolidation",
        "benchmark": {
            "benchmark_id": benchmark_id,
            "label": benchmark_label,
            "video_path": video_path,
            "video_time_offset_sec": round(float(video_time_offset_sec), 3),
        },
        "source": {
            "consolidation_algorithm": consolidation_doc.get("algorithm") or {},
            "proposals": len(proposals),
        },
        "ui": {
            "title": f"P1.8 fragment consolidation audit: {benchmark_label}",
            "description": (
                "Decide only whether SOURCE and TARGET show the same real person. "
                "Production identity remains unchanged."
            ),
            "download_filename": f"identity_fragment_consolidation_audit_reviewed_{benchmark_label}.json",
        },
        "summary": {
            "review_items": len(items),
            "pending": len(items),
            "reviewed": 0,
            "skipped": len(skipped),
        },
        "items": items,
        "skipped": skipped,
    }


def _endpoint_payload(
    proposal: dict[str, Any],
    endpoint: dict[str, Any],
    *,
    side: str,
    source_time_sec: float,
    video_time_offset_sec: float,
) -> dict[str, Any]:
    player_key = f"{side}_candidate_player_id"
    subject_key = f"{side}_candidate_subject_id"
    return {
        "tracklet_id": str(proposal.get(player_key) or proposal.get(subject_key) or ""),
        "raw_tracker_id": proposal.get(subject_key),
        "team_label": proposal.get(f"{side}_team_label") or "U",
        "role": "candidate_fragment",
        "frame": int(endpoint.get("frame") or 0),
        "source_time_sec": round(source_time_sec, 3),
        "video_time_sec": round(max(0.0, source_time_sec - video_time_offset_sec), 3),
        "bbox_xyxy": endpoint.get("bbox_xyxy"),
        "pitch_m": endpoint.get("pitch_m"),
        "confidence": endpoint.get("confidence"),
    }


def _source_fps(proposal: dict[str, Any]) -> float:
    source_end = int(proposal.get("source_end_frame") or 0)
    target_start = int(proposal.get("target_start_frame") or 0)
    gap_frames = int(proposal.get("gap_frames") or 0)
    gap_seconds = float(proposal.get("gap_seconds") or 0.0)
    if gap_frames > 0 and gap_seconds > 0:
        return max(gap_frames / gap_seconds, 1e-6)
    frame_delta = target_start - source_end
    if frame_delta > 1 and gap_seconds > 0:
        return max((frame_delta - 1) / gap_seconds, 1e-6)
    return 30.0


def _short_key(value: str) -> str:
    return value.rsplit(":", 1)[-1][:10] if value else "unknown"
