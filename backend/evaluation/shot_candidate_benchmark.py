from __future__ import annotations

"""Evaluation-only matching for frozen manual shot goldsets."""

from collections import Counter
from statistics import median
from typing import Any, Mapping


BENCHMARK_SCHEMA_VERSION = "shot-candidate-benchmark:v1"
DEFAULT_TOLERANCE_SEC = 1.5


def benchmark_shot_candidates(
    candidates_doc: Mapping[str, Any],
    goldset_doc: Mapping[str, Any],
    *,
    tolerance_sec: float = DEFAULT_TOLERANCE_SEC,
) -> dict[str, Any]:
    """Match suggestions to manual contact anchors without influencing generation."""

    if str(goldset_doc.get("schema_version") or "") != "shot-goldset:v1":
        raise ValueError("Expected shot-goldset:v1 evaluation fixture")
    if tolerance_sec <= 0:
        raise ValueError("tolerance_sec must be positive")
    gold = sorted((dict(row) for row in goldset_doc.get("shots") or [] if isinstance(row, Mapping)), key=lambda row: (_time(row), str(row.get("id") or "")))
    candidates = sorted((dict(row) for row in candidates_doc.get("candidates") or [] if isinstance(row, Mapping)), key=lambda row: (_candidate_time(row), str(row.get("candidate_key") or "")))
    available = set(range(len(candidates)))
    matches: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    duplicate_counts: dict[str, int] = {}
    for shot in gold:
        compatible = [index for index in available if abs(_candidate_time(candidates[index]) - _time(shot)) <= tolerance_sec]
        all_compatible = [index for index, candidate in enumerate(candidates) if abs(_candidate_time(candidate) - _time(shot)) <= tolerance_sec]
        duplicate_counts[str(shot.get("id") or "")] = len(all_compatible)
        if not compatible:
            missed.append(_gold_summary(shot))
            continue
        chosen_index = min(
            compatible,
            key=lambda index: (
                abs(_candidate_time(candidates[index]) - _time(shot)),
                -_number(candidates[index].get("confidence"), 0.0),
                str(candidates[index].get("candidate_key") or ""),
            ),
        )
        available.remove(chosen_index)
        candidate = candidates[chosen_index]
        matches.append({
            "gold_shot_id": shot.get("id"),
            "candidate_id": candidate.get("candidate_id"),
            "candidate_key": candidate.get("candidate_key"),
            "gold_timestamp_sec": _time(shot),
            "candidate_timestamp_sec": _candidate_time(candidate),
            "signed_timing_error_sec": round(_candidate_time(candidate) - _time(shot), 3),
            "gold_team": shot.get("team"),
            "suggested_team": candidate.get("suggested_team_name"),
            "gold_player": shot.get("player"),
            "suggested_player": candidate.get("suggested_player_id"),
            "gold_outcome": shot.get("outcome"),
            "candidate_reasons": list(candidate.get("reasons") or []),
            "candidate_confidence": _number(candidate.get("confidence"), 0.0),
        })
    hard_negative_hits = [
        {
            "gold_example_id": row.get("id"),
            "timestamp_sec": _time(row),
            "negative_type": row.get("negative_type"),
            "candidate_keys": [candidate.get("candidate_key") for candidate in candidates if abs(_candidate_time(candidate) - _time(row)) <= tolerance_sec],
        }
        for row in goldset_doc.get("hard_negatives") or []
        if isinstance(row, Mapping) and any(abs(_candidate_time(candidate) - _time(row)) <= tolerance_sec for candidate in candidates)
    ]
    errors = [float(row["signed_timing_error_sec"]) for row in matches]
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "evaluation_only": True,
        "goldset_schema_version": goldset_doc.get("schema_version"),
        "candidate_policy_version": candidates_doc.get("policy_version"),
        "match_tolerance_sec": tolerance_sec,
        "summary": {
            "gold_shots": len(gold),
            "matched_gold_shots": len(matches),
            "missed_gold_shots": len(missed),
            "recall": _ratio(len(matches), len(gold)),
            "total_generated_candidates": len(candidates),
            "candidates_per_10_minutes": _candidates_per_ten(candidates_doc, candidates),
            "hard_negative_hits": len(hard_negative_hits),
            "timing_error": {
                "median_signed_sec": _round(median(errors)) if errors else None,
                "median_absolute_sec": _round(median(abs(value) for value in errors)) if errors else None,
                "max_absolute_sec": _round(max((abs(value) for value in errors), default=0.0)) if errors else None,
            },
            "candidate_reason_distribution": dict(sorted(Counter(reason for candidate in candidates for reason in candidate.get("reasons") or []).items())),
            "confidence_distribution": _confidence_distribution(candidates),
        },
        "outcome_recall": _breakdown(gold, matches, "outcome", "gold_outcome"),
        "team_recall": _breakdown(gold, matches, "team", "gold_team"),
        "player_attribution_diagnostics": _player_diagnostics(matches),
        "matches": matches,
        "missed_gold_shots": missed,
        "hard_negative_hits": hard_negative_hits,
        "duplicate_candidates_around_gold": [
            {"gold_shot_id": shot_id, "candidate_count_within_tolerance": count}
            for shot_id, count in sorted(duplicate_counts.items())
            if count > 1
        ],
        "candidate_review_table": {
            "chronological": [_review_row(candidate) for candidate in candidates],
            "by_confidence": [_review_row(candidate) for candidate in sorted(candidates, key=lambda row: (-_number(row.get("confidence"), 0.0), _candidate_time(row), str(row.get("candidate_key") or "")))],
        },
        "limitations": [
            "Curated hard negatives are regression examples, not a representative sample for global precision.",
            "Manual timestamps are approximate anchors and are matched with the explicit tolerance above.",
        ],
    }


def _breakdown(gold: list[dict[str, Any]], matches: list[dict[str, Any]], gold_field: str, match_field: str) -> dict[str, dict[str, float | int | None]]:
    expected = Counter(str(row.get(gold_field) or "unknown") for row in gold)
    recovered = Counter(str(row.get(match_field) or "unknown") for row in matches)
    return {
        key: {"gold": count, "matched": recovered.get(key, 0), "recall": _ratio(recovered.get(key, 0), count)}
        for key, count in sorted(expected.items())
    }


def _player_diagnostics(matches: list[dict[str, Any]]) -> dict[str, int | float | None]:
    with_gold_player = [row for row in matches if row.get("gold_player")]
    with_suggested_player = [row for row in matches if row.get("suggested_player")]
    exact = [row for row in with_gold_player if row.get("suggested_player") == row.get("gold_player")]
    return {
        "matched_shots": len(matches),
        "suggested_player_available": len(with_suggested_player),
        "gold_player_known_on_matched": len(with_gold_player),
        "exact_player_match_when_gold_known": len(exact),
        "exact_player_match_rate_when_gold_known": _ratio(len(exact), len(with_gold_player)),
    }


def _confidence_distribution(candidates: list[dict[str, Any]]) -> dict[str, int]:
    bins = {"0.00-0.39": 0, "0.40-0.59": 0, "0.60-0.79": 0, "0.80-1.00": 0}
    for candidate in candidates:
        value = _number(candidate.get("confidence"), 0.0)
        if value < 0.4:
            bins["0.00-0.39"] += 1
        elif value < 0.6:
            bins["0.40-0.59"] += 1
        elif value < 0.8:
            bins["0.60-0.79"] += 1
        else:
            bins["0.80-1.00"] += 1
    return bins


def _review_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    trajectory = candidate.get("trajectory_evidence") if isinstance(candidate.get("trajectory_evidence"), Mapping) else {}
    return {
        "logical_timestamp_sec": _candidate_time(candidate),
        "source_match_id": candidate.get("source_match_id"),
        "source_timestamp_sec": candidate.get("source_timestamp_sec"),
        "suggested_team": candidate.get("suggested_team_name"),
        "suggested_player": candidate.get("suggested_player_id"),
        "confidence": candidate.get("confidence"),
        "top_reasons": list(candidate.get("reasons") or [])[:4],
        "start_position_m": candidate.get("start_position_m"),
        "trajectory_summary": {key: trajectory.get(key) for key in ("distance_m", "mean_speed_mps", "goalward_progress_m", "endpoint_goal_distance_m", "cross_like")},
        "candidate_key": candidate.get("candidate_key"),
    }


def _gold_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("id", "timestamp_sec", "timestamp_display", "team", "player", "outcome")}


def _candidate_time(row: Mapping[str, Any]) -> float:
    return _number(row.get("logical_timestamp_sec"), _number(row.get("candidate_timestamp_sec"), 0.0))


def _time(row: Mapping[str, Any]) -> float:
    return _number(row.get("timestamp_sec"), 0.0)


def _candidates_per_ten(document: Mapping[str, Any], candidates: list[dict[str, Any]]) -> float | None:
    span = _number(document.get("timeline_span_sec"), 0.0)
    return _round(len(candidates) / (span / 600.0)) if span > 0 else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return _round(numerator / denominator) if denominator else None


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _round(value: float) -> float:
    return round(float(value), 4)
