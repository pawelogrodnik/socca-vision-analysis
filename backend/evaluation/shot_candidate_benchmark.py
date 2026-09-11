from __future__ import annotations

"""Evaluation-only matching for frozen manual shot goldsets."""

from collections import Counter
from dataclasses import dataclass
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
    assignments = _maximum_cardinality_minimum_error_matching(gold, candidates, tolerance_sec)
    matches: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    duplicate_counts: dict[str, int] = {}
    for gold_index, shot in enumerate(gold):
        all_compatible = [index for index, candidate in enumerate(candidates) if abs(_candidate_time(candidate) - _time(shot)) <= tolerance_sec]
        duplicate_counts[str(shot.get("id") or "")] = len(all_compatible)
        chosen_index = assignments.get(gold_index)
        if chosen_index is None:
            missed.append(_gold_summary(shot))
            continue
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
        "review_budget": _review_budget_metrics(candidates_doc, goldset_doc, tolerance_sec),
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


def _review_budget_metrics(
    candidates_doc: Mapping[str, Any],
    goldset_doc: Mapping[str, Any],
    tolerance_sec: float,
) -> dict[str, dict[str, float | int | None]]:
    """Measure recall at fixed operator-review budgets, ranked by confidence."""

    ranked = sorted(
        (dict(row) for row in candidates_doc.get("candidates") or [] if isinstance(row, Mapping)),
        key=lambda row: (-_number(row.get("confidence"), 0.0), _candidate_time(row), str(row.get("candidate_key") or "")),
    )
    gold = [dict(row) for row in goldset_doc.get("shots") or [] if isinstance(row, Mapping)]
    gold_count = len(gold)
    result: dict[str, dict[str, float | int | None]] = {}
    for requested in (25, 50, 75, 100):
        subset = ranked[:requested]
        assignments = _maximum_cardinality_minimum_error_matching(
            gold,
            subset,
            tolerance_sec,
        )
        hard_negative_hits = sum(
            1
            for row in goldset_doc.get("hard_negatives") or []
            if isinstance(row, Mapping)
            and any(abs(_candidate_time(candidate) - _time(row)) <= tolerance_sec for candidate in subset)
        )
        result[f"at_{requested}"] = {
            "requested_candidate_count": requested,
            "available_candidate_count": len(subset),
            "matched_gold_shots": len(assignments),
            "recall": _ratio(len(assignments), gold_count),
            "hard_negative_hits": hard_negative_hits,
        }
    all_assignments = _maximum_cardinality_minimum_error_matching(gold, ranked, tolerance_sec)
    result["all"] = {
        "requested_candidate_count": None,
        "available_candidate_count": len(ranked),
        "matched_gold_shots": len(all_assignments),
        "recall": _ratio(len(all_assignments), gold_count),
        "hard_negative_hits": sum(
            1
            for row in goldset_doc.get("hard_negatives") or []
            if isinstance(row, Mapping)
            and any(abs(_candidate_time(candidate) - _time(row)) <= tolerance_sec for candidate in ranked)
        ),
    }
    return result


@dataclass
class _FlowEdge:
    target: int
    reverse_index: int
    capacity: int
    cost: int


def _maximum_cardinality_minimum_error_matching(
    gold: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    tolerance_sec: float,
) -> dict[int, int]:
    """Find a deterministic maximum-cardinality, minimum-error bipartite match.

    Successive shortest augmenting paths run over a small unit-capacity
    min-cost-flow graph. Continuing until no source-to-sink path remains makes
    cardinality primary; timing cost is secondary. Stable sorted gold and
    candidate keys form the final tie cost and traversal order.
    """

    gold_count, candidate_count = len(gold), len(candidates)
    source, gold_start = 0, 1
    candidate_start = gold_start + gold_count
    sink = candidate_start + candidate_count
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]

    def add_edge(start: int, target: int, capacity: int, cost: int) -> _FlowEdge:
        forward = _FlowEdge(target, len(graph[target]), capacity, cost)
        reverse = _FlowEdge(start, len(graph[start]), 0, -cost)
        graph[start].append(forward)
        graph[target].append(reverse)
        return forward

    for index in range(gold_count):
        add_edge(source, gold_start + index, 1, 0)
    for index in range(candidate_count):
        add_edge(candidate_start + index, sink, 1, 0)

    # The multiplier is larger than the aggregate deterministic tie cost of
    # any complete matching, so it cannot alter the minimum timing-error goal.
    # Positional weights make the tie breaker lexicographic by stable gold and
    # candidate keys instead of merely minimizing an ambiguous sum of indexes.
    maximum_matches = min(gold_count, candidate_count)
    stable_base = candidate_count + 2
    tie_cost_bound = stable_base ** (gold_count + 1)
    gold_stable_rank = {
        index: rank
        for rank, index in enumerate(sorted(range(gold_count), key=lambda index: (str(gold[index].get("id") or ""), index)))
    }
    candidate_stable_rank = {
        index: rank
        for rank, index in enumerate(sorted(range(candidate_count), key=lambda index: (str(candidates[index].get("candidate_key") or ""), index)))
    }
    match_edges: dict[tuple[int, int], _FlowEdge] = {}
    for gold_index, shot in enumerate(gold):
        for candidate_index, candidate in enumerate(candidates):
            timing_error = abs(_candidate_time(candidate) - _time(shot))
            if timing_error > tolerance_sec:
                continue
            timing_cost = int(round(timing_error * 1_000_000))
            stable_tie_cost = (candidate_stable_rank[candidate_index] + 1) * (stable_base ** (gold_count - gold_stable_rank[gold_index]))
            match_edges[(gold_index, candidate_index)] = add_edge(
                gold_start + gold_index,
                candidate_start + candidate_index,
                1,
                timing_cost * tie_cost_bound + stable_tie_cost,
            )

    while True:
        distance: list[int | None] = [None] * len(graph)
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = 0
        for _ in range(len(graph) - 1):
            changed = False
            for node, edges in enumerate(graph):
                if distance[node] is None:
                    continue
                for edge_index, edge in enumerate(edges):
                    if edge.capacity <= 0:
                        continue
                    candidate_cost = int(distance[node]) + edge.cost
                    prior = distance[edge.target]
                    tie = (node, edge_index)
                    if prior is None or candidate_cost < prior or (candidate_cost == prior and (previous[edge.target] is None or tie < previous[edge.target])):
                        distance[edge.target] = candidate_cost
                        previous[edge.target] = tie
                        changed = True
            if not changed:
                break
        if distance[sink] is None:
            break
        node = sink
        while node != source:
            previous_edge = previous[node]
            if previous_edge is None:
                raise RuntimeError("Incomplete min-cost-flow path")
            start, edge_index = previous_edge
            edge = graph[start][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse_index].capacity += 1
            node = start

    return {
        gold_index: candidate_index
        for (gold_index, candidate_index), edge in match_edges.items()
        if edge.capacity == 0
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
