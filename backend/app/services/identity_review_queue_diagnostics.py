from __future__ import annotations

"""Read-only diagnostics for the operator-facing Reviewed Identity queue."""

from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import fmean, median
from typing import Any


def diagnose_review_queue(match_path: Path) -> dict[str, Any]:
    match_doc = _load(match_path / "match.json")
    progress = _load(match_path / "reviewed_identity_progress.json")
    segment_review = _load(match_path / "reviewed_identity_segment_review.json")
    cards_doc = _load(match_path / "identity_roster_subject_review_shadow.json")
    return summarize_review_queue(match_doc, progress, segment_review, cards_doc)


def summarize_review_queue(
    match_doc: dict[str, Any],
    progress: dict[str, Any],
    segment_review: dict[str, Any],
    cards_doc: dict[str, Any],
) -> dict[str, Any]:
    fps = _fps(match_doc)
    units = [row for row in progress.get("review_units") or [] if isinstance(row, dict)]
    high = [row for row in units if row.get("priority") == "high"]
    optional = [row for row in units if row.get("priority") == "optional"]
    segment_high = [row for row in high if row.get("scope_kind") == "canonical_segment"]
    whole_high = [row for row in high if row.get("scope_kind") != "canonical_segment"]
    cards = {
        str(row.get("candidate_subject_id") or ""): row
        for row in cards_doc.get("cards") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    targets = {
        str(row.get("review_target_id") or ""): row
        for row in segment_review.get("targets") or []
        if isinstance(row, dict) and row.get("review_target_id")
    }
    return {
        "schema_version": "1.0.0",
        "match_id": str(match_doc.get("id") or progress.get("match_id") or ""),
        "fps": fps,
        "queue": {
            "total_review_units": len(units),
            "high_priority_units": len(high),
            "optional_units": len(optional),
        },
        "high_priority_segments": _segment_diagnostics(
            segment_high,
            targets,
            fps,
        ),
        "high_priority_whole_subjects": _whole_subject_diagnostics(
            whole_high,
            cards,
        ),
    }


def _segment_diagnostics(
    units: list[dict[str, Any]],
    targets: dict[str, dict[str, Any]],
    fps: float,
) -> dict[str, Any]:
    enriched = [
        {
            **unit,
            "target": targets.get(str(unit.get("review_target_id") or ""), {}),
        }
        for unit in units
    ]
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    tracklet_counts: Counter[tuple[str, str]] = Counter()
    frame_counts: list[int] = []
    durations: list[float] = []
    for row in enriched:
        target = row["target"]
        tracklet_id = str(((target.get("tracklet_ids") or row.get("tracklet_ids") or [""])[0]))
        group = (
            str(row.get("candidate_subject_id") or ""),
            tracklet_id,
            str(target.get("stable_slot_id") or row.get("stable_slot_id") or ""),
            str(target.get("source_team_label") or row.get("source_team_label") or "U"),
        )
        groups[group].append(row)
        tracklet_counts[group[:2]] += 1
        frame_count = len(target.get("owned_frames") or []) or int(
            row.get("detected_frame_count") or 0
        )
        frame_counts.append(frame_count)
        durations.append(frame_count / fps)

    adjacent_gaps: Counter[int] = Counter()
    for rows in groups.values():
        ordered = sorted(rows, key=lambda row: int(row.get("frame_start") or 0))
        for previous, current in zip(ordered, ordered[1:]):
            gap = int(current.get("frame_start") or 0) - int(
                previous.get("frame_end") or 0
            ) - 1
            if gap >= 0:
                adjacent_gaps[gap] += 1

    duration_stats = _distribution(durations)
    return {
        "total": len(units),
        "unique_candidate_subjects": len(
            {str(row.get("candidate_subject_id") or "") for row in units}
        ),
        "unique_tracklets": len(
            {
                str(tracklet_id)
                for row in units
                for tracklet_id in row.get("tracklet_ids") or []
            }
        ),
        "unique_subject_tracklet_slot_team_groups": len(groups),
        "duration_sec": duration_stats,
        "case_size_counts": {
            "one_frame": sum(value == 1 for value in frame_counts),
            "lte_3_frames": sum(value <= 3 for value in frame_counts),
            "lte_5_frames": sum(value <= 5 for value in frame_counts),
            "lte_10_frames": sum(value <= 10 for value in frame_counts),
            "lte_15_frames": sum(value <= 15 for value in frame_counts),
            "lte_30_frames": sum(value <= 30 for value in frame_counts),
            "lte_1_sec": sum(value <= 1.0 for value in durations),
        },
        "duration_buckets": {
            "lte_0_1_sec": sum(value <= 0.1 for value in durations),
            "lte_0_25_sec": sum(value <= 0.25 for value in durations),
            "lte_0_5_sec": sum(value <= 0.5 for value in durations),
            "lte_1_sec": sum(value <= 1.0 for value in durations),
            "gt_1_sec": sum(value > 1.0 for value in durations),
        },
        "adjacent_gap_frames": {
            str(gap): count for gap, count in sorted(adjacent_gaps.items())
        },
        "top_fragmented_tracklets": [
            {
                "candidate_subject_id": subject_id,
                "tracklet_id": tracklet_id,
                "case_count": count,
            }
            for (subject_id, tracklet_id), count in sorted(
                tracklet_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:10]
        ],
    }


def _whole_subject_diagnostics(
    units: list[dict[str, Any]],
    cards: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    reason_codes: Counter[str] = Counter()
    blockers: Counter[str] = Counter()
    quality_flags: Counter[str] = Counter()
    team_conflicts = 0
    review_card_conflicts = 0
    for unit in units:
        subject_id = str(unit.get("candidate_subject_id") or "")
        reasons = {str(value) for value in unit.get("reason_codes") or []}
        reason_codes.update(reasons)
        card = cards.get(subject_id) or {}
        blockers.update(str(value) for value in card.get("blockers") or [])
        quality_flags.update(str(value) for value in card.get("quality_flags") or [])
        team_conflicts += "conflicting_detected_team_labels" in reasons
        review_card_conflicts += "review_card_conflict" in reasons
    return {
        "total": len(units),
        "reason_code_counts": dict(sorted(reason_codes.items())),
        "blocker_counts": dict(sorted(blockers.items())),
        "quality_flag_counts": dict(sorted(quality_flags.items())),
        "team_conflict_cases": team_conflicts,
        "review_card_conflict_cases": review_card_conflicts,
    }


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {key: None for key in ("min", "median", "mean", "p75", "p90", "max")}
    ordered = sorted(values)
    return {
        "min": round(ordered[0], 3),
        "median": round(median(ordered), 3),
        "mean": round(fmean(ordered), 3),
        "p75": round(_percentile(ordered, 0.75), 3),
        "p90": round(_percentile(ordered, 0.9), 3),
        "max": round(ordered[-1], 3),
    }


def _percentile(ordered: list[float], fraction: float) -> float:
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _fps(match_doc: dict[str, Any]) -> float:
    value = float((match_doc.get("video") or {}).get("fps") or match_doc.get("fps") or 30.0)
    return value if value > 0 else 30.0


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
