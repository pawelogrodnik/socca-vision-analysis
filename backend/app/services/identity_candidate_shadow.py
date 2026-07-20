from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_candidate_shadow"
ALGORITHM_VERSION = "0.1.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_identity_candidate_shadow(
    offline_identity_doc: dict[str, Any],
    resolved_timeline_doc: dict[str, Any],
    global_identity: dict[str, Any],
    *,
    fps: float,
    generated_at: str | None = None,
    include_overlay: bool = False,
) -> dict[str, dict[str, Any]]:
    """Build a visual-only stable-subject candidate from the P1 shadow timeline."""
    safe_fps = max(float(fps), 1e-6)
    generated = generated_at or now_iso()
    production = _production_identity_index(global_identity)
    subject_rows = _candidate_subject_rows(
        offline_identity_doc,
        resolved_timeline_doc,
        production=production,
        fps=safe_fps,
    )
    _assign_visual_labels(subject_rows)

    overlay_players = [_overlay_player(row, fps=safe_fps) for row in subject_rows]
    review_items = [_review_item(row) for row in subject_rows if row["requires_review"]]
    active_counts = _active_counts(overlay_players)
    status_counts = Counter(
        str(position.get("source") or "unknown")
        for player in overlay_players
        for position in player.get("overlay_positions") or []
    )
    report = _candidate_report(
        subject_rows,
        active_counts=active_counts,
        status_counts=status_counts,
        review_items=review_items,
        fps=safe_fps,
        generated_at=generated,
    )
    candidate = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_visual_validation",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source": {
            "offline_identity_algorithm": offline_identity_doc.get("algorithm") or {},
            "resolved_timeline_algorithm": resolved_timeline_doc.get("algorithm") or {},
            "production_identity_usage": "label_anchor_and_comparison_only",
        },
        "safety": {
            "mutates_production_identity": False,
            "eligible_for_player_stats": False,
            "eligible_for_roster_assignment": False,
            "requires_visual_validation": True,
        },
        "summary": report["summary"],
        "subjects": [
            {
                key: value
                for key, value in row.items()
                if key not in {
                    "observations",
                    "state_runs",
                    "production_votes",
                    "production_label_by_subject",
                }
            }
            for row in subject_rows
        ],
        "review_items": review_items,
    }
    overlay = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_visual_validation",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "summary": report["summary"],
        "identity_semantics": {
            "detected": "Observed candidate identity position.",
            "predicted": "Short visual-only gap interpolation; never used for statistics.",
            "occluded": "Occlusion-supported visual-only gap; never used for statistics.",
            "missing": "Not rendered because continuity or position evidence is insufficient.",
        },
        "players": overlay_players,
    }
    documents = {
        "identity_candidate_shadow": candidate,
        "identity_candidate_shadow_report": report,
    }
    if include_overlay:
        documents["identity_candidate_shadow_overlay"] = overlay
    return documents


def _production_identity_index(global_identity: dict[str, Any]) -> dict[str, Any]:
    subject_to_label: dict[str, str] = {}
    tracklet_to_subject: dict[str, str] = {}
    role_by_subject: dict[str, str] = {}
    for slot in global_identity.get("slots") or []:
        subject_id = str(slot.get("stable_subject_id") or "")
        label = str(slot.get("stable_player_id") or slot.get("slot_id") or "")
        if not subject_id:
            continue
        if label:
            subject_to_label[subject_id] = label
        role_by_subject[subject_id] = str(slot.get("role") or "field_player")
        for tracklet_id in slot.get("tracklet_ids") or []:
            tracklet_to_subject[str(tracklet_id)] = subject_id
    return {
        "subject_to_label": subject_to_label,
        "tracklet_to_subject": tracklet_to_subject,
        "role_by_subject": role_by_subject,
    }


def _candidate_subject_rows(
    offline_identity_doc: dict[str, Any],
    resolved_timeline_doc: dict[str, Any],
    *,
    production: dict[str, Any],
    fps: float,
) -> list[dict[str, Any]]:
    graph_by_id = {
        str(row.get("shadow_subject_id")): row
        for row in offline_identity_doc.get("subjects") or []
    }
    transition_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in resolved_timeline_doc.get("transition_events") or []:
        transition_by_subject[str(event.get("shadow_subject_id") or "")].append(event)

    rows: list[dict[str, Any]] = []
    for timeline_subject in resolved_timeline_doc.get("subjects") or []:
        subject_id = str(timeline_subject.get("shadow_subject_id") or "")
        graph_subject = graph_by_id.get(subject_id) or {}
        team_label = str(timeline_subject.get("team_label") or graph_subject.get("team_label") or "U")
        observations = list(timeline_subject.get("observations") or [])
        state_runs = list(timeline_subject.get("state_runs") or [])
        tracklet_ids = sorted(set(str(value) for value in timeline_subject.get("tracklet_ids") or []))
        production_votes: Counter[str] = Counter()
        for observation in observations:
            production_subject = production["tracklet_to_subject"].get(str(observation.get("tracklet_id") or ""))
            if production_subject:
                production_votes[production_subject] += 1
        production_subject_ids = sorted(
            set(str(value) for value in timeline_subject.get("production_subject_ids") or [])
            | set(production_votes)
        )
        transitions = transition_by_subject.get(subject_id) or []
        uncertain_transitions = sum(
            str(event.get("identity_continuity_status") or "supported") != "supported"
            or bool(event.get("requires_review"))
            for event in transitions
        )
        cross_production_transitions = sum(
            event.get("current_identity_relation") == "different_subjects"
            for event in transitions
        )
        flags: set[str] = set(timeline_subject.get("quality_flags") or [])
        if len(production_subject_ids) > 1:
            flags.add("merges_production_subjects")
        if cross_production_transitions:
            flags.add("cross_production_transition")
        if uncertain_transitions:
            flags.add("uncertain_transition")
        if not production_subject_ids:
            flags.add("no_production_anchor")
        production_player_ids = sorted(
            production["subject_to_label"][value]
            for value in production_subject_ids
            if value in production["subject_to_label"]
        )
        if any(not _label_matches_team(label, team_label) for label in production_player_ids):
            flags.add("production_anchor_team_mismatch")
        role_votes = Counter(
            production["role_by_subject"].get(subject_id, "field_player")
            for subject_id in production_subject_ids
        )
        detected_frames = len(observations)
        rows.append(
            {
                "candidate_subject_id": subject_id,
                "team_label": team_label,
                "role": role_votes.most_common(1)[0][0] if role_votes else "field_player",
                "tracklet_ids": tracklet_ids,
                "production_subject_ids": production_subject_ids,
                "production_player_ids": production_player_ids,
                "production_label_by_subject": {
                    value: production["subject_to_label"][value]
                    for value in production_subject_ids
                    if value in production["subject_to_label"]
                },
                "start_frame": timeline_subject.get("start_frame"),
                "end_frame": timeline_subject.get("end_frame"),
                "detected_frames": detected_frames,
                "detected_seconds": round(detected_frames / fps, 3),
                "transition_events": len(transitions),
                "uncertain_transitions": uncertain_transitions,
                "cross_production_transitions": cross_production_transitions,
                "requires_review": bool(flags),
                "quality_flags": sorted(flags),
                "production_votes": production_votes,
                "observations": observations,
                "state_runs": state_runs,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row["team_label"]),
            int(row.get("start_frame") or 0),
            str(row["candidate_subject_id"]),
        ),
    )


def _assign_visual_labels(rows: list[dict[str, Any]]) -> None:
    preferred: dict[str, str | None] = {}
    for row in rows:
        votes: Counter[str] = row["production_votes"]
        ranked_subjects = sorted(votes, key=lambda value: (-votes[value], value))
        ranked_labels = [
            label
            for subject in ranked_subjects
            if (label := _production_label_for_row(row, subject))
            and _label_matches_team(label, str(row.get("team_label") or "U"))
        ]
        if not ranked_labels:
            ranked_labels = sorted(
                str(value)
                for value in row.get("production_player_ids") or []
                if _label_matches_team(str(value), str(row.get("team_label") or "U"))
            )
        preferred[str(row["candidate_subject_id"])] = ranked_labels[0] if ranked_labels else None

    by_preferred: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        label = preferred[str(row["candidate_subject_id"])]
        if label:
            by_preferred[label].append(row)
    for label, candidates in by_preferred.items():
        candidates.sort(
            key=lambda row: (
                -int(row.get("detected_frames") or 0),
                int(row.get("start_frame") or 0),
                str(row["candidate_subject_id"]),
            )
        )
        for index, row in enumerate(candidates, start=1):
            row["candidate_player_id"] = label if index == 1 else f"{label}~{index}"
            row["label_source"] = "production_anchor" if index == 1 else "production_split_fragment"
            if len(candidates) > 1:
                row["quality_flags"] = sorted(set(row["quality_flags"]) | {"splits_production_subject"})
                row["requires_review"] = True

    next_by_team: Counter[str] = Counter()
    used = {str(row.get("candidate_player_id")) for row in rows if row.get("candidate_player_id")}
    for row in rows:
        if row.get("candidate_player_id"):
            continue
        team = str(row.get("team_label") or "U")
        while True:
            next_by_team[team] += 1
            label = f"{team}-new{next_by_team[team]:02d}"
            if label not in used:
                break
        used.add(label)
        row["candidate_player_id"] = label
        row["label_source"] = "unanchored_candidate"


def _production_label_for_row(row: dict[str, Any], subject_id: str) -> str | None:
    return (row.get("production_label_by_subject") or {}).get(subject_id)


def _label_matches_team(label: str, team_label: str) -> bool:
    normalized_label = str(label or "").strip().upper()
    normalized_team = str(team_label or "U").strip().upper()
    if normalized_team not in {"A", "B"}:
        return False
    return normalized_label.startswith(normalized_team)


def _overlay_player(row: dict[str, Any], *, fps: float) -> dict[str, Any]:
    observations = sorted(row["observations"], key=lambda item: int(item.get("frame") or 0))
    positions = [
        {
            "frame": int(observation.get("frame") or 0),
            "time_sec": round(float(observation.get("time_sec") or 0.0), 3),
            "bbox_xyxy": observation.get("bbox_xyxy"),
            "pitch_m": observation.get("pitch_m"),
            "confidence": observation.get("confidence"),
            "status": "detected",
            "source": "detected",
            "eligible_for_distance": bool(observation.get("eligible_for_distance")),
            "eligible_for_heatmap": bool(observation.get("eligible_for_heatmap")),
            "play_area_status": observation.get("play_area_status"),
            "footpoint_reliable": bool(observation.get("footpoint_reliable")),
            "appearance_reliable": bool(observation.get("appearance_reliable")),
            "quality_class": observation.get("quality_class"),
            "tracklet_id": observation.get("tracklet_id"),
        }
        for observation in observations
        if observation.get("bbox_xyxy") is not None
    ]
    observation_by_frame = {int(row.get("frame") or 0): row for row in observations}
    for run in row["state_runs"]:
        status = str(run.get("status") or "")
        if status not in {"predicted", "occluded"}:
            continue
        start_frame = int(run.get("start_frame") or 0)
        end_frame = int(run.get("end_frame") or start_frame)
        previous = _nearest_observation(observation_by_frame, start_frame - 1, direction=-1)
        following = _nearest_observation(observation_by_frame, end_frame + 1, direction=1)
        if previous is None or following is None:
            continue
        previous_bbox = previous.get("bbox_xyxy")
        following_bbox = following.get("bbox_xyxy")
        if not _valid_vector(previous_bbox, 4) or not _valid_vector(following_bbox, 4):
            continue
        span = int(following["frame"]) - int(previous["frame"])
        if span <= 0:
            continue
        for frame in range(start_frame, end_frame + 1):
            ratio = (frame - int(previous["frame"])) / span
            positions.append(
                {
                    "frame": frame,
                    "time_sec": round(frame / fps, 3),
                    "bbox_xyxy": _interpolate_vector(previous_bbox, following_bbox, ratio),
                    "pitch_m": _interpolate_optional_vector(previous.get("pitch_m"), following.get("pitch_m"), ratio),
                    "confidence": round(min(float(previous.get("confidence") or 0.0), float(following.get("confidence") or 0.0)) * 0.75, 4),
                    "status": status,
                    "source": status,
                    "eligible_for_distance": False,
                    "eligible_for_heatmap": False,
                    "play_area_status": "interpolated",
                    "footpoint_reliable": False,
                    "appearance_reliable": False,
                    "quality_class": None,
                    "tracklet_id": None,
                }
            )
    positions.sort(key=lambda item: (int(item["frame"]), str(item["source"])))
    return {
        "stable_player_id": row["candidate_player_id"],
        "stable_subject_id": row["candidate_subject_id"],
        "candidate_subject_id": row["candidate_subject_id"],
        "display_label": row["candidate_player_id"],
        "team_label": row["team_label"],
        "role": row["role"],
        "tracklet_ids": row["tracklet_ids"],
        "production_subject_ids": row["production_subject_ids"],
        "anchored": bool(row["production_subject_ids"])
        and "production_anchor_team_mismatch" not in row["quality_flags"],
        "requires_review": row["requires_review"],
        "quality_flags": row["quality_flags"],
        "overlay_positions": positions,
        "overlay_positions_count": len(positions),
        "trusted_overlay_positions_count": sum(position["source"] == "detected" for position in positions),
        "statistics_eligible": False,
    }


def _nearest_observation(
    observations: dict[int, dict[str, Any]],
    frame: int,
    *,
    direction: int,
) -> dict[str, Any] | None:
    candidates = [value for key, value in observations.items() if (key <= frame if direction < 0 else key >= frame)]
    if not candidates:
        return None
    return max(candidates, key=lambda row: int(row["frame"])) if direction < 0 else min(
        candidates,
        key=lambda row: int(row["frame"]),
    )


def _review_item(row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "candidate_subject_id": row["candidate_subject_id"],
        "candidate_player_id": row["candidate_player_id"],
        "quality_flags": row["quality_flags"],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "review_key": f"identity-candidate:v1:{digest}",
        **payload,
        "team_label": row["team_label"],
        "production_subject_ids": row["production_subject_ids"],
        "start_frame": row["start_frame"],
        "end_frame": row["end_frame"],
        "status": "pending",
    }


def _active_counts(players: list[dict[str, Any]]) -> dict[str, Any]:
    frame_team_counts: Counter[tuple[int, str]] = Counter()
    for player in players:
        team = str(player.get("team_label") or "U")
        for position in player.get("overlay_positions") or []:
            frame_team_counts[(int(position.get("frame") or 0), team)] += 1
    maxima = {
        team: max((count for (frame, label), count in frame_team_counts.items() if label == team), default=0)
        for team in sorted({label for _, label in frame_team_counts})
    }
    over_cap = [
        {"frame": frame, "team_label": team, "candidate_subjects": count}
        for (frame, team), count in sorted(frame_team_counts.items())
        if team in {"A", "B"} and count > 7
    ]
    return {"max_active_per_team": maxima, "over_seven_frames": over_cap}


def _candidate_report(
    rows: list[dict[str, Any]],
    *,
    active_counts: dict[str, Any],
    status_counts: Counter[str],
    review_items: list[dict[str, Any]],
    fps: float,
    generated_at: str,
) -> dict[str, Any]:
    split_subjects = sum("splits_production_subject" in row["quality_flags"] for row in rows)
    merged_subjects = sum("merges_production_subjects" in row["quality_flags"] for row in rows)
    unanchored_subjects = sum("no_production_anchor" in row["quality_flags"] for row in rows)
    team_mismatched_anchor_subjects = sum(
        "production_anchor_team_mismatch" in row["quality_flags"] for row in rows
    )
    summary = {
        "candidate_subjects": len(rows),
        "anchored_subjects": len(rows) - unanchored_subjects,
        "unanchored_subjects": unanchored_subjects,
        "team_mismatched_anchor_subjects": team_mismatched_anchor_subjects,
        "split_fragments": split_subjects,
        "merged_production_subjects": merged_subjects,
        "subjects_requiring_review": len(review_items),
        "status_frame_counts": dict(sorted(status_counts.items())),
        "status_seconds": {
            status: round(count / fps, 3)
            for status, count in sorted(status_counts.items())
        },
        "max_active_per_team": active_counts["max_active_per_team"],
        "frames_over_seven_candidates": len(active_counts["over_seven_frames"]),
        "product_readiness": "shadow_ready_for_visual_validation",
        "promotion_readiness": "blocked_pending_fragmentation_and_overflow_reduction",
    }
    gates = {
        "production_identity_untouched": True,
        "candidate_positions_excluded_from_statistics": True,
        "candidate_labels_unique": len({row["candidate_player_id"] for row in rows}) == len(rows),
        "no_cross_team_visual_labels": not any(
            str(row.get("team_label") or "U") in {"A", "B"}
            and not str(row.get("candidate_player_id") or "").startswith(str(row["team_label"]))
            for row in rows
        ),
        "no_parallel_candidate_overflow": not active_counts["over_seven_frames"],
        "no_unreviewed_cross_production_merge": not any(
            "merges_production_subjects" in row["quality_flags"] and not row["requires_review"]
            for row in rows
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": "shadow_visual_validation",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "status": "ready_for_visual_validation" if gates["candidate_labels_unique"] else "blocked",
        "summary": summary,
        "gates": gates,
        "over_seven_candidate_frames": active_counts["over_seven_frames"][:200],
        "review_items": review_items,
        "limitations": [
            "Candidate IDs are visual labels, not roster assignments.",
            "Predicted and occluded positions are visualization-only.",
            "The candidate must not feed production statistics before a separate promotion gate.",
        ],
    }


def _valid_vector(value: Any, length: int) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= length


def _interpolate_vector(left: Any, right: Any, ratio: float) -> list[float]:
    return [round(float(a) + (float(b) - float(a)) * ratio, 2) for a, b in zip(left, right, strict=False)]


def _interpolate_optional_vector(left: Any, right: Any, ratio: float) -> list[float] | None:
    if not _valid_vector(left, 2) or not _valid_vector(right, 2):
        return None
    return _interpolate_vector(left, right, ratio)
