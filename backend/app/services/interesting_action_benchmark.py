from __future__ import annotations

"""Evaluation-only metrics for shadow interesting-action candidates.

This module accepts manual windows from a caller.  It intentionally contains no
benchmark fixture and is never used by candidate generation.
"""

from collections.abc import Mapping
from statistics import median
from typing import Any


def benchmark_candidates(
    candidates: list[Mapping[str, Any]],
    manual_windows: list[Mapping[str, Any]],
    *,
    start_tolerance_sec: float = 4.0,
    minimum_manual_coverage: float = 0.25,
) -> dict[str, Any]:
    """Match candidates to known positives while retaining unlabeled rows."""

    normalized_candidates = [_candidate(row) for row in candidates if _candidate(row) is not None]
    normalized_windows = [_window(row) for row in manual_windows if _window(row) is not None]
    matches: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    for window in normalized_windows:
        compatible = [candidate for candidate in normalized_candidates if _matches(candidate, window, start_tolerance_sec, minimum_manual_coverage)]
        chosen = max(compatible, key=lambda candidate: (_coverage(candidate, window), -abs(candidate["start_time_sec"] - window["start_time_sec"]), candidate["interestingness_score"])) if compatible else None
        if chosen is None:
            matches.append({"manual_window": window, "matched": False, "candidate": None, "signed_start_error_sec": None, "manual_action_coverage": 0.0, "peak_inside_manual_window": None})
            continue
        matched_ids.add(chosen["candidate_id"])
        matches.append({
            "manual_window": window,
            "matched": True,
            "candidate": chosen,
            "signed_start_error_sec": round(chosen["start_time_sec"] - window["start_time_sec"], 3),
            "manual_action_coverage": round(_coverage(chosen, window), 4),
            "peak_inside_manual_window": window["start_time_sec"] <= chosen["peak_time_sec"] <= window["end_time_sec"] if chosen["peak_time_sec"] is not None else None,
        })
    unmatched = [
        {**candidate, "classification": "unlabeled_candidate", "nearest_known_window_distance_sec": round(_nearest_distance(candidate, normalized_windows), 3)}
        for candidate in normalized_candidates if candidate["candidate_id"] not in matched_ids
    ]
    matched = [row for row in matches if row["matched"]]
    signed = [float(row["signed_start_error_sec"]) for row in matched]
    coverage = [float(row["manual_action_coverage"]) for row in matched]
    return {
        "match_rule": {"start_tolerance_sec": start_tolerance_sec, "minimum_manual_coverage": minimum_manual_coverage, "requires_window_overlap": True},
        "manual_matches": matches,
        "unlabeled_candidates": sorted(unmatched, key=lambda row: (-row["interestingness_score"], row["start_time_sec"])),
        "summary": {
            "known_positive_total": len(normalized_windows),
            "known_positive_recovered": len(matched),
            "candidate_count": len(normalized_candidates),
            "unlabeled_candidate_count": len(unmatched),
            "median_signed_start_error_sec": round(float(median(signed)), 3) if signed else None,
            "median_absolute_start_error_sec": round(float(median([abs(value) for value in signed])), 3) if signed else None,
            "median_manual_action_coverage": round(float(median(coverage)), 4) if coverage else None,
            "candidate_review_duration_sec": round(sum(row["end_time_sec"] - row["start_time_sec"] for row in normalized_candidates), 3),
        },
    }


def _matches(candidate: Mapping[str, Any], window: Mapping[str, Any], tolerance: float, minimum_coverage: float) -> bool:
    # A late candidate may still overlap a known action but it cannot start so
    # late that it misses the build-up entirely.  The signed error remains a
    # visible diagnostic rather than being hidden inside this tolerance.
    return candidate["start_time_sec"] <= window["start_time_sec"] + tolerance and _coverage(candidate, window) >= minimum_coverage


def _coverage(candidate: Mapping[str, Any], window: Mapping[str, Any]) -> float:
    duration = window["end_time_sec"] - window["start_time_sec"]
    return _overlap(candidate, window) / duration if duration > 0 else 0.0


def _overlap(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    return max(0.0, min(first["end_time_sec"], second["end_time_sec"]) - max(first["start_time_sec"], second["start_time_sec"]))


def _nearest_distance(candidate: Mapping[str, Any], windows: list[dict[str, Any]]) -> float:
    if not windows:
        return float("inf")
    return min(max(window["start_time_sec"] - candidate["end_time_sec"], candidate["start_time_sec"] - window["end_time_sec"], 0.0) for window in windows)


def _candidate(row: Mapping[str, Any]) -> dict[str, Any] | None:
    start, end = _number(row.get("start_time_sec")), _number(row.get("end_time_sec"))
    if start is None or end is None or end <= start:
        return None
    return {
        "candidate_id": str(row.get("candidate_id") or ""),
        "start_time_sec": start,
        "end_time_sec": end,
        "peak_time_sec": _number(row.get("peak_time_sec")),
        "interestingness_score": _number(row.get("interestingness_score")) or 0.0,
        "confidence": _number(row.get("confidence")),
        "evidence": row.get("evidence") if isinstance(row.get("evidence"), list) else [],
    }


def _window(row: Mapping[str, Any]) -> dict[str, Any] | None:
    start, end = _number(row.get("start_time_sec")), _number(row.get("end_time_sec"))
    if start is None or end is None or end <= start:
        return None
    return {
        "window_id": str(row.get("window_id") or ""),
        "start_time_sec": start,
        "end_time_sec": end,
        "category": row.get("category"),
        "secondary_diagnostics": row.get("secondary_diagnostics") if isinstance(row.get("secondary_diagnostics"), Mapping) else None,
    }


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
