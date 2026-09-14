from __future__ import annotations

"""Deterministic, evaluation-only comparison of frozen shot goldsets."""

from functools import lru_cache
from typing import Any, Mapping


COMPARISON_SCHEMA_VERSION = "shot-goldset-comparison:v1"
# v1 anchors were intentionally recorded as approximate clocks.  This is a
# reconciliation bound between two manual truth sets, not the tighter
# candidate-to-gold benchmark tolerance.
DEFAULT_RECONCILIATION_TOLERANCE_SEC = 3.0
MATERIAL_TIMESTAMP_CHANGE_SEC = 0.25


def compare_shot_goldsets(
    v1_doc: Mapping[str, Any],
    v2_doc: Mapping[str, Any],
    *,
    tolerance_sec: float = DEFAULT_RECONCILIATION_TOLERANCE_SEC,
) -> dict[str, Any]:
    """Compare v1's approximate anchors with v2's canonical shot-review state.

    Pairing is chronological, one-to-one and bounded by ``tolerance_sec``.
    It maximizes paired actions, then prefers team/outcome/player consistency,
    then minimizes timing error. Semantic fields remain soft evidence: a
    canonical correction is still paired rather than represented as two rows.
    """

    if str(v1_doc.get("schema_version") or "") != "shot-goldset:v1":
        raise ValueError("Expected shot-goldset:v1 as the baseline")
    if str(v2_doc.get("schema_version") or "") != "shot-goldset:v2":
        raise ValueError("Expected shot-goldset:v2 as the canonical comparison")
    if tolerance_sec <= 0:
        raise ValueError("tolerance_sec must be positive")

    v1 = _ordered_shots(v1_doc)
    v2 = _ordered_shots(v2_doc)
    pairs = _ordered_maximum_match(v1, v2, tolerance_sec)
    paired_v1 = {left for left, _ in pairs}
    paired_v2 = {right for _, right in pairs}
    matches = [_match_row(v1[left], v2[right]) for left, right in pairs]
    v1_only = [_summary(row) for index, row in enumerate(v1) if index not in paired_v1]
    v2_only = [_summary(row) for index, row in enumerate(v2) if index not in paired_v2]
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "evaluation_only": True,
        "baseline_schema_version": "shot-goldset:v1",
        "canonical_schema_version": "shot-goldset:v2",
        "match_tolerance_sec": tolerance_sec,
        "matching_strategy": "maximum_pairs_then_semantic_consistency_then_timing_error",
        "material_timestamp_change_sec": MATERIAL_TIMESTAMP_CHANGE_SEC,
        "summary": {
            "v1_shots": len(v1),
            "v2_shots": len(v2),
            "same_action_pairs": len(matches),
            "timestamp_materially_corrected": sum(row["timestamp_materially_corrected"] for row in matches),
            "team_corrected": sum(row["team_changed"] for row in matches),
            "outcome_corrected": sum(row["outcome_changed"] for row in matches),
            "player_attribution_changed": sum(row["player_changed"] for row in matches),
            "v1_only": len(v1_only),
            "v2_only": len(v2_only),
        },
        "same_action_matches": matches,
        "v1_only": v1_only,
        "v2_only": v2_only,
    }


def _ordered_shots(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in document.get("shots") or [] if isinstance(row, Mapping)),
        key=lambda row: (_time(row), str(row.get("id") or "")),
    )


def _ordered_maximum_match(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    tolerance_sec: float,
) -> tuple[tuple[int, int], ...]:
    """Match chronological actions by count, soft semantics, then clock error."""

    @lru_cache(maxsize=None)
    def solve(left_index: int, right_index: int) -> tuple[int, int, int, tuple[tuple[int, int], ...]]:
        if left_index == len(left) or right_index == len(right):
            return 0, 0, 0, ()
        choices = [solve(left_index + 1, right_index), solve(left_index, right_index + 1)]
        delta = abs(_time(left[left_index]) - _time(right[right_index]))
        if delta <= tolerance_sec:
            count, semantic_penalty, error_micros, pairs = solve(left_index + 1, right_index + 1)
            choices.append((
                count + 1,
                semantic_penalty + _semantic_penalty(left[left_index], right[right_index]),
                error_micros + int(round(delta * 1_000_000)),
                ((left_index, right_index), *pairs),
            ))
        return min(choices, key=lambda value: (-value[0], value[1], value[2], value[3]))

    return solve(0, 0)[3]


def _match_row(v1: Mapping[str, Any], v2: Mapping[str, Any]) -> dict[str, Any]:
    signed_error = round(_time(v2) - _time(v1), 3)
    return {
        "v1_id": v1.get("id"),
        "v2_id": v2.get("id"),
        "v1_timestamp_sec": _time(v1),
        "v2_timestamp_sec": _time(v2),
        "signed_timing_error_sec": signed_error,
        "timestamp_materially_corrected": abs(signed_error) > MATERIAL_TIMESTAMP_CHANGE_SEC,
        "v1_team": v1.get("team"),
        "v2_team": v2.get("team"),
        "team_changed": v1.get("team") != v2.get("team"),
        "v1_outcome": v1.get("outcome"),
        "v2_outcome": v2.get("outcome"),
        "outcome_changed": v1.get("outcome") != v2.get("outcome"),
        "v1_player": v1.get("player"),
        "v2_player": v2.get("player"),
        "player_changed": v1.get("player") != v2.get("player"),
        "v2_origin": v2.get("origin"),
        "semantic_consistency": _semantic_consistency(v1, v2),
    }


def _summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in ("id", "timestamp_sec", "timestamp_display", "team", "player", "outcome", "origin")
    }


def _time(row: Mapping[str, Any]) -> float:
    value = row.get("timestamp_sec")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _semantic_penalty(v1: Mapping[str, Any], v2: Mapping[str, Any]) -> int:
    consistency = _semantic_consistency(v1, v2)
    # Team and outcome are strong action discriminators. Player evidence is
    # only considered when both goldsets actually know a player.
    return (
        (4 if consistency["team"] is False else 0)
        + (3 if consistency["outcome"] is False else 0)
        + (1 if consistency["player"] is False else 0)
    )


def _semantic_consistency(v1: Mapping[str, Any], v2: Mapping[str, Any]) -> dict[str, bool | None]:
    return {
        "team": _field_consistency(v1.get("team"), v2.get("team")),
        "outcome": _field_consistency(v1.get("outcome"), v2.get("outcome")),
        "player": _field_consistency(v1.get("player"), v2.get("player")),
    }


def _field_consistency(left: Any, right: Any) -> bool | None:
    if left in (None, "") or right in (None, ""):
        return None
    return left == right
