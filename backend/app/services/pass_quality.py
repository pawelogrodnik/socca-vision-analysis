from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PASS_OUTCOMES = {"completed_pass", "failed_pass"}


def load_pass_goldset(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Pass goldset must be a JSON object.")
    events = document.get("events")
    if not isinstance(events, list):
        raise ValueError("Pass goldset must contain an events list.")
    return document


def evaluate_pass_candidates_against_gold(
    pass_candidates_doc: dict[str, Any],
    goldset_doc: dict[str, Any],
    *,
    tolerance_frames: int = 45,
) -> dict[str, Any]:
    expected = [
        event
        for event in goldset_doc.get("events") or []
        if isinstance(event, dict) and str(event.get("expected_outcome") or "") in PASS_OUTCOMES
    ]
    candidates = [
        candidate
        for candidate in pass_candidates_doc.get("candidates") or []
        if isinstance(candidate, dict) and str(candidate.get("outcome") or _legacy_outcome(candidate)) in PASS_OUTCOMES
    ]
    frame_min, frame_max = _gold_frame_range(expected, tolerance_frames)
    scoped_candidates = [
        candidate
        for candidate in candidates
        if frame_min is None or frame_min <= _candidate_match_frame(candidate) <= frame_max
    ]
    unmatched_candidates = set(range(len(scoped_candidates)))
    matches: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []

    for event in expected:
        best_index = _best_candidate_index(event, scoped_candidates, unmatched_candidates, tolerance_frames)
        if best_index is None:
            missed.append(_public_gold_event(event))
            continue
        unmatched_candidates.remove(best_index)
        candidate = scoped_candidates[best_index]
        matches.append(
            {
                "gold_event_id": event.get("id"),
                "candidate_id": candidate.get("candidate_id"),
                "frame_delta": int(_candidate_match_frame(candidate) - int(event.get("frame") or 0)),
                "expected_outcome": event.get("expected_outcome"),
                "actual_outcome": candidate.get("outcome") or _legacy_outcome(candidate),
                "expected_team_label": event.get("team_label"),
                "actual_team_label": candidate.get("count_for_team_label") or candidate.get("from_team_label"),
            }
        )

    false_positives = [_public_candidate(scoped_candidates[index]) for index in sorted(unmatched_candidates)]
    true_positives = len(matches)
    precision = _ratio(true_positives, true_positives + len(false_positives))
    recall = _ratio(true_positives, true_positives + len(missed))
    return {
        "schema_version": "0.1.0",
        "source": "pass_goldset_evaluator_v1",
        "tolerance_frames": int(tolerance_frames),
        "summary": {
            "expected_pass_events": len(expected),
            "candidate_pass_attempts_in_scope": len(scoped_candidates),
            "true_positives": true_positives,
            "missed_passes": len(missed),
            "false_positives": len(false_positives),
            "precision": precision,
            "recall": recall,
        },
        "matches": matches,
        "missed": missed,
        "false_positives": false_positives,
    }


def _best_candidate_index(
    event: dict[str, Any],
    candidates: list[dict[str, Any]],
    unmatched_candidates: set[int],
    tolerance_frames: int,
) -> int | None:
    event_frame = int(event.get("frame") or 0)
    expected_outcome = str(event.get("expected_outcome") or "")
    expected_team = str(event.get("team_label") or "")
    expected_restart = event.get("from_restart")
    scored: list[tuple[int, int]] = []
    for index in unmatched_candidates:
        candidate = candidates[index]
        candidate_frame = _candidate_match_frame(candidate)
        frame_delta = abs(candidate_frame - event_frame)
        if frame_delta > tolerance_frames:
            continue
        if str(candidate.get("outcome") or _legacy_outcome(candidate)) != expected_outcome:
            continue
        candidate_team = str(candidate.get("count_for_team_label") or candidate.get("from_team_label") or "")
        if expected_team and candidate_team != expected_team:
            continue
        if expected_restart is not None and bool(candidate.get("from_restart")) != bool(expected_restart):
            continue
        scored.append((frame_delta, index))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], str(candidates[item[1]].get("candidate_id") or "")))
    return scored[0][1]


def _candidate_match_frame(candidate: dict[str, Any]) -> int:
    if candidate.get("end_frame") is not None:
        return int(candidate.get("end_frame") or 0)
    return int(candidate.get("start_frame") or 0)


def _gold_frame_range(expected: list[dict[str, Any]], tolerance_frames: int) -> tuple[int | None, int | None]:
    frames = [int(event.get("frame") or 0) for event in expected if event.get("frame") is not None]
    if not frames:
        return None, None
    return min(frames) - tolerance_frames, max(frames) + tolerance_frames


def _public_gold_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "frame": event.get("frame"),
        "team_label": event.get("team_label"),
        "expected_outcome": event.get("expected_outcome"),
        "from_player": event.get("from_player"),
        "to_player": event.get("to_player"),
        "notes": event.get("notes"),
    }


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id"),
        "frame": _candidate_match_frame(candidate),
        "outcome": candidate.get("outcome") or _legacy_outcome(candidate),
        "team_label": candidate.get("count_for_team_label") or candidate.get("from_team_label"),
        "from_stable_player_id": candidate.get("from_stable_player_id"),
        "to_stable_player_id": candidate.get("to_stable_player_id"),
        "excluded_reason": candidate.get("excluded_reason"),
        "confidence": candidate.get("confidence"),
    }


def _legacy_outcome(candidate: dict[str, Any]) -> str:
    pass_type = str(candidate.get("pass_type") or "")
    if pass_type == "same_team_pass":
        return "completed_pass"
    if pass_type == "turnover_or_interception":
        return "failed_pass"
    return "unknown_pass_attempt"


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)
