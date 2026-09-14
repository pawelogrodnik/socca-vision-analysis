from __future__ import annotations

"""Evaluation-only candidate matching for canonical pre-event shot anchors."""

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping


SCHEMA_VERSION = "shot-anchor-aware-benchmark:v1"
DEFAULT_ACCEPTED_TOLERANCE_SEC = 1.5
DEFAULT_MANUAL_FORWARD_HORIZON_SEC = 2.0
DEFAULT_MANUAL_BACKWARD_SLACK_SEC = 0.25
DEFAULT_MANUAL_FORWARD_HORIZONS_SEC = (1.5, 2.0, 2.5)

MANUAL_TIMESTAMP_SEMANTICS = "pre_event_playback_anchor"
ACCEPTED_TIMESTAMP_SEMANTICS = "candidate_event_anchor"


def benchmark_anchor_aware_shot_candidates(
    candidates_document: Mapping[str, Any],
    goldset_document: Mapping[str, Any],
    *,
    accepted_tolerance_sec: float = DEFAULT_ACCEPTED_TOLERANCE_SEC,
    manual_forward_horizon_sec: float = DEFAULT_MANUAL_FORWARD_HORIZON_SEC,
    manual_backward_slack_sec: float = DEFAULT_MANUAL_BACKWARD_SLACK_SEC,
) -> dict[str, Any]:
    """Find directional, evaluation-only eligibility around canonical v3 anchors.

    A manual canonical time is a convenient point to start playback before a
    strike. Its forward horizon is deliberately an explicit *evaluation
    parameter*, not a claim about a universal annotation bound. A small,
    separately named backward slack only protects against detector timing
    granularity; it is not a symmetric matching window.
    """

    _validate_parameters(accepted_tolerance_sec, manual_forward_horizon_sec, manual_backward_slack_sec)
    if str(goldset_document.get("schema_version") or "") != "shot-goldset:v3":
        raise ValueError("Anchor-aware matching requires shot-goldset:v3 timestamp semantics")
    gold = sorted((_validated_shot(row) for row in goldset_document.get("shots") or [] if isinstance(row, Mapping)), key=lambda row: (_time(row), _text(row.get("id"))))
    candidates = sorted((dict(row) for row in candidates_document.get("candidates") or [] if isinstance(row, Mapping)), key=lambda row: (_candidate_time(row), _candidate_key(row)))
    assignments = _maximum_cardinality_chronological_matching(
        gold,
        candidates,
        accepted_tolerance_sec=accepted_tolerance_sec,
        manual_forward_horizon_sec=manual_forward_horizon_sec,
        manual_backward_slack_sec=manual_backward_slack_sec,
    )
    matches: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    for gold_index, shot in enumerate(gold):
        candidate_index = assignments.get(gold_index)
        if candidate_index is None:
            missed.append(_gold_summary(shot))
            continue
        matches.append(_match_row(shot, candidates[candidate_index]))

    detector_derived_offsets = [float(row["candidate_event_anchor_offset_sec"]) for row in matches if row["candidate_event_anchor_offset_sec"] is not None]
    manual_offsets = [float(row["candidate_after_anchor_sec"]) for row in matches if row["candidate_after_anchor_sec"] is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "goldset_schema_version": goldset_document.get("schema_version"),
        "candidate_policy_version": candidates_document.get("policy_version"),
        "parameters": {
            "accepted_suggestion_symmetric_tolerance_sec": accepted_tolerance_sec,
            "manual_pre_event_forward_horizon_sec": manual_forward_horizon_sec,
            "manual_pre_event_backward_slack_sec": manual_backward_slack_sec,
        },
        "summary": {
            "gold_shots": len(gold),
            "anchor_aware_eligible_gold_shots": len(matches),
            "missed_gold_shots": len(missed),
            "eligibility_rate": _ratio(len(matches), len(gold)),
            "timestamp_semantics": _semantic_breakdown(gold, matches),
            "detector_derived_timestamp_offsets": _offset_summary(
                detector_derived_offsets,
                timestamp_semantics=[ACCEPTED_TIMESTAMP_SEMANTICS],
                label="candidate_event_anchor_offset_sec",
            ),
            "manual_playback_anchor_offsets": _offset_summary(
                manual_offsets,
                timestamp_semantics=[MANUAL_TIMESTAMP_SEMANTICS],
                label="candidate_after_manual_anchor_sec",
            ),
        },
        "matches": matches,
        "missed_gold_shots": missed,
        "limitations": [
            "Anchor-aware eligibility identifies locally plausible candidates, not proven football-action identity.",
            "Manual pre-event playback anchors are excluded from detector-derived timestamp-offset aggregates.",
            "The manual forward horizon is an explicit sensitivity parameter, not a documented universal bound.",
        ],
    }


def anchor_aware_sensitivity(
    candidates_document: Mapping[str, Any],
    goldset_document: Mapping[str, Any],
    *,
    manual_forward_horizons_sec: tuple[float, ...] = DEFAULT_MANUAL_FORWARD_HORIZONS_SEC,
    accepted_tolerance_sec: float = DEFAULT_ACCEPTED_TOLERANCE_SEC,
    manual_backward_slack_sec: float = DEFAULT_MANUAL_BACKWARD_SLACK_SEC,
) -> dict[str, dict[str, Any]]:
    """Expose the bounded sensitivity of manual playback-anchor eligibility."""

    if not manual_forward_horizons_sec:
        raise ValueError("manual_forward_horizons_sec must not be empty")
    return {
        _horizon_key(horizon): benchmark_anchor_aware_shot_candidates(
            candidates_document,
            goldset_document,
            accepted_tolerance_sec=accepted_tolerance_sec,
            manual_forward_horizon_sec=horizon,
            manual_backward_slack_sec=manual_backward_slack_sec,
        )
        for horizon in manual_forward_horizons_sec
    }


@dataclass(frozen=True)
class _MatchState:
    count: int
    cost: int
    signature: tuple[str, ...]
    pairs: tuple[tuple[int, int], ...]


def _maximum_cardinality_chronological_matching(
    gold: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    accepted_tolerance_sec: float,
    manual_forward_horizon_sec: float,
    manual_backward_slack_sec: float,
) -> dict[int, int]:
    """Use chronological one-to-one matching with forward preference for manual rows."""

    states = [[_MatchState(0, 0, (), ()) for _ in range(len(candidates) + 1)] for _ in range(len(gold) + 1)]
    for gold_index in range(1, len(gold) + 1):
        for candidate_index in range(1, len(candidates) + 1):
            options = [states[gold_index - 1][candidate_index], states[gold_index][candidate_index - 1]]
            shot, candidate = gold[gold_index - 1], candidates[candidate_index - 1]
            match_cost = _match_cost(
                shot,
                candidate,
                accepted_tolerance_sec=accepted_tolerance_sec,
                manual_forward_horizon_sec=manual_forward_horizon_sec,
                manual_backward_slack_sec=manual_backward_slack_sec,
            )
            if match_cost is not None:
                prior = states[gold_index - 1][candidate_index - 1]
                options.append(_MatchState(
                    prior.count + 1,
                    prior.cost + match_cost,
                    prior.signature + (_candidate_key(candidate),),
                    prior.pairs + ((gold_index - 1, candidate_index - 1),),
                ))
            states[gold_index][candidate_index] = _best(options)
    return dict(states[-1][-1].pairs)


def _best(options: list[_MatchState]) -> _MatchState:
    return min(options, key=lambda state: (-state.count, state.cost, state.signature))


def _match_cost(
    shot: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    accepted_tolerance_sec: float,
    manual_forward_horizon_sec: float,
    manual_backward_slack_sec: float,
) -> int | None:
    offset = _candidate_time(candidate) - _time(shot)
    semantics = _text(shot.get("timestamp_semantics"))
    if semantics == MANUAL_TIMESTAMP_SEMANTICS:
        if offset < -manual_backward_slack_sec or offset > manual_forward_horizon_sec:
            return None
        # Every forward candidate ranks ahead of a backward-slack candidate.
        directional_cost = offset if offset >= 0 else manual_forward_horizon_sec + abs(offset)
        return int(round(directional_cost * 1_000_000))
    if semantics == ACCEPTED_TIMESTAMP_SEMANTICS:
        if abs(offset) > accepted_tolerance_sec:
            return None
        return int(round(abs(offset) * 1_000_000))
    raise ValueError(f"Unsupported timestamp semantics: {semantics or '<missing>'}")


def _match_row(shot: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    offset = round(_candidate_time(candidate) - _time(shot), 3)
    semantics = _text(shot.get("timestamp_semantics"))
    manual = semantics == MANUAL_TIMESTAMP_SEMANTICS
    return {
        "gold_shot_id": shot.get("id"),
        "candidate_id": candidate.get("candidate_id"),
        "candidate_key": candidate.get("candidate_key"),
        "gold_timestamp_sec": _time(shot),
        "candidate_timestamp_sec": _candidate_time(candidate),
        "timestamp_semantics": semantics,
        "timestamp_precision": shot.get("timestamp_precision"),
        "candidate_timestamp_offset_sec": offset,
        "candidate_after_anchor_sec": offset if manual else None,
        "candidate_event_anchor_offset_sec": offset if not manual else None,
        "gold_team": shot.get("team"),
        "suggested_team": candidate.get("suggested_team_name"),
        "gold_player": shot.get("player"),
        "suggested_player": candidate.get("suggested_player_id"),
        "gold_outcome": shot.get("outcome"),
        "gold_origin": shot.get("origin"),
        "candidate_reasons": list(candidate.get("reasons") or []),
        "candidate_confidence": _number(candidate.get("confidence")),
    }


def _semantic_breakdown(gold: list[Mapping[str, Any]], matches: list[Mapping[str, Any]]) -> dict[str, dict[str, int | float | None]]:
    total: dict[str, int] = {}
    matched: dict[str, int] = {}
    for row in gold:
        semantics = _text(row.get("timestamp_semantics"))
        total[semantics] = total.get(semantics, 0) + 1
    for row in matches:
        semantics = _text(row.get("timestamp_semantics"))
        matched[semantics] = matched.get(semantics, 0) + 1
    return {
        semantics: {"gold_shots": count, "eligible_gold_shots": matched.get(semantics, 0), "eligibility_rate": _ratio(matched.get(semantics, 0), count)}
        for semantics, count in sorted(total.items())
    }


def _offset_summary(offsets: list[float], *, timestamp_semantics: list[str], label: str) -> dict[str, Any]:
    return {
        "timestamp_semantics_included": timestamp_semantics,
        "matched_shots": len(offsets),
        f"median_{label}": _round(median(offsets)) if offsets else None,
        f"median_absolute_{label}": _round(median(abs(value) for value in offsets)) if offsets else None,
        f"max_absolute_{label}": _round(max((abs(value) for value in offsets), default=0.0)) if offsets else None,
    }


def _validated_shot(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    semantics = _text(result.get("timestamp_semantics"))
    precision = _text(result.get("timestamp_precision"))
    valid = {
        MANUAL_TIMESTAMP_SEMANTICS: "approximate",
        ACCEPTED_TIMESTAMP_SEMANTICS: "detector_derived",
    }
    if valid.get(semantics) != precision:
        raise ValueError("Goldset v3 timestamp semantics and precision are inconsistent")
    return result


def _validate_parameters(accepted_tolerance_sec: float, manual_forward_horizon_sec: float, manual_backward_slack_sec: float) -> None:
    if accepted_tolerance_sec <= 0 or manual_forward_horizon_sec <= 0 or manual_backward_slack_sec < 0:
        raise ValueError("Anchor-aware matching parameters must be positive (or zero only for backward slack)")


def _gold_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("id", "timestamp_sec", "timestamp_display", "timestamp_semantics", "timestamp_precision", "team", "player", "outcome", "origin")}


def _candidate_time(row: Mapping[str, Any]) -> float:
    return _number(row.get("logical_timestamp_sec"), _number(row.get("candidate_timestamp_sec")))


def _candidate_key(row: Mapping[str, Any]) -> str:
    return _text(row.get("candidate_key") or row.get("candidate_id"))


def _time(row: Mapping[str, Any]) -> float:
    return _number(row.get("timestamp_sec"))


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _ratio(numerator: int, denominator: int) -> float | None:
    return _round(numerator / denominator) if denominator else None


def _round(value: float) -> float:
    return round(float(value), 4)


def _horizon_key(value: float) -> str:
    return f"forward_horizon_{value:.1f}s"
