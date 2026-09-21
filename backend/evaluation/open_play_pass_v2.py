"""Evaluation-only comparison for Pass Policy v2 during active open play.

The active-play mask is derived from the manual Contact / Action goldset and
must never be supplied to runtime pass generation.  V1 and V2 candidates are
generated independently, then evaluated against this identical mask.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from evaluation.contact_action_baseline import (
    PASS_OUTCOMES,
    _aggregate_fidelity,
    _classify_pass_false_positives,
    _gold_public,
    _is_scored_pass,
    _match_passes,
    _project_candidates,
    _score_pass_matches,
    _scope_candidates,
    _pass_summary,
    _ratio,
    _round,
)

SCHEMA_VERSION = "open-play-pass-policy-comparison:v2"


def derive_active_play_mask(goldset: Mapping[str, Any]) -> dict[str, Any]:
    """Subtract manually annotated inactive and restart ranges from W1–W6."""

    windows = {
        str(row["window_id"]): dict(row)
        for row in goldset.get("windows") or []
        if isinstance(row, Mapping)
    }
    intervals_by_window: dict[str, list[dict[str, Any]]] = {key: [] for key in windows}
    for row in goldset.get("game_state_intervals") or []:
        if not isinstance(row, Mapping) or row.get("state") not in {"NOT_IN_PLAY", "GK_HOLD"}:
            continue
        window_id = str(row.get("window_id") or "")
        if window_id in windows:
            intervals_by_window[window_id].append({
                "start_time_sec": float(row.get("start_time_sec") or 0.0),
                "end_time_sec": float(row.get("end_time_sec") or 0.0),
                "state": str(row.get("state")),
                "reason": row.get("reason"),
            })
    restarts_by_window: dict[str, list[dict[str, Any]]] = {key: [] for key in windows}
    for row in goldset.get("events") or []:
        if not isinstance(row, Mapping) or row.get("action") != "RESTART" or row.get("context_only"):
            continue
        window_id = str(row.get("window_id") or "")
        if window_id not in windows:
            continue
        center = float(row.get("approx_time_sec") or 0.0)
        tolerance = float(row.get("timing_tolerance_sec") or 1.0)
        restarts_by_window[window_id].append({
            "event_id": row.get("event_id"),
            "start_time_sec": center - tolerance,
            "end_time_sec": center + tolerance,
        })

    windows_result: dict[str, dict[str, Any]] = {}
    total_selected = total_active = 0.0
    for window_id, window in sorted(windows.items()):
        start, end = float(window["merged_start_time_sec"]), float(window["merged_end_time_sec"])
        states = _clip_intervals(intervals_by_window[window_id], start, end)
        restarts = _clip_intervals(restarts_by_window[window_id], start, end)
        excluded = _merge_intervals([*states, *restarts])
        active = _subtract_intervals(start, end, excluded)
        selected = end - start
        active_duration = sum(row["end_time_sec"] - row["start_time_sec"] for row in active)
        total_selected += selected
        total_active += active_duration
        windows_result[window_id] = {
            "selected_window": {"start_time_sec": start, "end_time_sec": end},
            "active_play_intervals": active,
            "excluded_game_state_intervals": states,
            "excluded_restart_ranges": restarts,
            "selected_duration_sec": _round(selected),
            "active_play_scored_duration_sec": _round(active_duration),
            "excluded_duration_sec": _round(selected - active_duration),
        }
    return {
        "source": "contact-action-goldset:v1 evaluation-only human game-state annotations",
        "windows": windows_result,
        "total_selected_duration_sec": _round(total_selected),
        "total_active_play_scored_duration_sec": _round(total_active),
        "total_excluded_duration_sec": _round(total_selected - total_active),
    }


def evaluate_open_play_pass_policy_comparison(
    goldset: Mapping[str, Any],
    v1_source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
    v2_source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Compare independently generated policies using one immutable mask."""

    mask = derive_active_play_mask(goldset)
    windows = {str(row["window_id"]): dict(row) for row in goldset.get("windows") or [] if isinstance(row, Mapping)}
    events = [dict(row) for row in goldset.get("events") or [] if isinstance(row, Mapping)]
    active_gold = [
        row for row in events
        if _is_scored_pass(row)
        and row.get("action") == "PASS"
        and _in_active_play(float(row.get("approx_time_sec") or 0.0), str(row.get("window_id") or ""), mask)
    ]
    v1 = _evaluate_policy("v1", windows, events, active_gold, v1_source_documents, mask)
    v2 = _evaluate_policy("v2", windows, events, active_gold, v2_source_documents, mask)
    comparison = _comparison(v1, v2, active_gold)
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_layer": {
            "active_play_mask": "evaluation-only; never supplied to production pass generation",
            "manual_review_status_used": False,
            "production_artifacts_written": False,
            "product_policy": "Pass statistics remain fully automatic; manual gold annotations are evaluation-only.",
        },
        "active_play_mask": mask,
        "primary": {
            "gold": _aggregate_fidelity(active_gold, []) ["gold"]["combined"],
            "v1": v1,
            "v2": v2,
        },
        "comparison": comparison,
        "secondary_non_blocking": {
            "v1": _secondary_summary(windows, v1_source_documents, goldset.get("game_state_intervals") or []),
            "v2": _secondary_summary(windows, v2_source_documents, goldset.get("game_state_intervals") or []),
        },
    }


def render_open_play_pass_policy_comparison_markdown(report: Mapping[str, Any]) -> str:
    primary = _mapping(report.get("primary"))
    gold, v1, v2 = (_mapping(primary.get(key)) for key in ("gold", "v1", "v2"))
    comparison = _mapping(report.get("comparison"))
    mask = _mapping(report.get("active_play_mask"))
    lines = [
        "# Open-play Pass Policy v2 — evaluation-only comparison", "",
        "## Active-play mask", "",
        f"- Selected window duration: **{mask.get('total_selected_duration_sec', 0):.1f}s**",
        f"- Active-play scored duration: **{mask.get('total_active_play_scored_duration_sec', 0):.1f}s**",
        f"- Excluded duration: **{mask.get('total_excluded_duration_sec', 0):.1f}s**", "",
        "| Window | Active-play duration | Excluded game-state ranges | Restart ranges |",
        "| --- | ---: | --- | --- |",
    ]
    for window_id, row in _mapping(mask.get("windows")).items():
        data = _mapping(row)
        lines.append(
            f"| {window_id} | {data.get('active_play_scored_duration_sec', 0):.1f}s | "
            f"{_ranges(data.get('excluded_game_state_intervals'))} | {_ranges(data.get('excluded_restart_ranges'))} |"
        )
    lines.extend(["", "## Primary active open-play metrics", "", "| Metric | Gold | V1 | V2 |", "| --- | ---: | ---: | ---: |"])
    for label, path in (
        ("Attempts", ("attempts",)), ("Corgi attempts", ("teams", "Corgi", "attempts")),
        ("Verisk attempts", ("teams", "Verisk", "attempts")), ("Unknown attempts", ("teams", "unknown", "attempts")),
        ("Corgi share", ("teams", "Corgi", "share")), ("Verisk share", ("teams", "Verisk", "share")),
        ("Completion rate", ("completion_rate",)), ("Corgi completion", ("teams", "Corgi", "completion_rate")),
        ("Verisk completion", ("teams", "Verisk", "completion_rate")), ("Completed", ("completed",)), ("Failed", ("failed",)),
    ):
        lines.append(f"| {label} | {_format_metric(_nested(gold, path), path)} | {_format_metric(_nested(v1.get('aggregate'), path), path)} | {_format_metric(_nested(v2.get('aggregate'), path), path)} |")
    lines.extend(["", "## Event-level diagnostics (secondary)", "", "| Metric | V1 | V2 |", "| --- | ---: | ---: |"])
    for key, label in (("precision", "Precision"), ("recall", "Recall"), ("f1", "F1"), ("matched_pass_attempts", "Matched"), ("missed_pass_attempts", "Missed"), ("unmatched_pass_candidates", "False positives"), ("outcome_accuracy", "Outcome accuracy"), ("actor_team_accuracy", "Actor-team accuracy"), ("receiver_team_accuracy", "Receiver-team accuracy")):
        lines.append(f"| {label} | {_format_event(v1, key)} | {_format_event(v2, key)} |")
    lines.extend(["", "## Aggregate error versus gold", "", "| Metric | V1 | V2 |", "| --- | ---: | ---: |"])
    for team in ("Corgi", "Verisk"):
        v1_team = _mapping(_mapping(v1.get("aggregate_error")).get("teams")).get(team)
        v2_team = _mapping(_mapping(v2.get("aggregate_error")).get("teams")).get(team)
        lines.append(f"| {team} count delta / error | {_signed(_mapping(v1_team).get('pass_count_delta'))} / {_percent(_mapping(v1_team).get('pass_count_error_percent'))} | {_signed(_mapping(v2_team).get('pass_count_delta'))} / {_percent(_mapping(v2_team).get('pass_count_error_percent'))} |")
        lines.append(f"| {team} share delta | {_pp(_mapping(v1_team).get('team_share_delta_pp'))} | {_pp(_mapping(v2_team).get('team_share_delta_pp'))} |")
        lines.append(f"| {team} completion delta | {_pp(_mapping(v1_team).get('completion_rate_delta_pp'))} | {_pp(_mapping(v2_team).get('completion_rate_delta_pp'))} |")
    lines.append(f"| Overall completion delta | {_pp(_mapping(v1.get('aggregate_error')).get('completion_rate_delta_pp'))} | {_pp(_mapping(v2.get('aggregate_error')).get('completion_rate_delta_pp'))} |")
    lines.extend(["", "## Active-play false positives", "", "| Category | V1 | V2 |", "| --- | ---: | ---: |"])
    for category in ("SHOT_AS_PASS", "INTERVENTION_AS_PASS", "CONTROL_AS_PASS", "CONTEST_AS_PASS", "AMBIGUOUS_ACTION_AS_PASS", "UNMATCHED_PASS_CANDIDATE"):
        lines.append(f"| {category} | {_mapping(v1.get('false_positive_categories')).get(category, 0)} | {_mapping(v2.get('false_positive_categories')).get(category, 0)} |")
    lines.extend(["", "## V2 policy effect", "", f"- Decision: **{comparison.get('decision')}**", f"- Rejection reasons: `{comparison.get('rejection_reason_counts')}`", "", "### Newly lost gold passes", ""])
    lines.extend(_listed_rows(comparison.get("newly_lost_gold_passes"), "- None.", _loss_line))
    lines.extend(["", "### Newly matched gold passes", ""])
    lines.extend(_listed_rows(comparison.get("newly_matched_gold_passes"), "- None.", _match_line))
    lines.extend(["", "### Removed false positives", ""])
    lines.extend(_listed_rows(comparison.get("removed_false_positives"), "- None.", _removed_line))
    secondary = _mapping(report.get("secondary_non_blocking"))
    lines.extend(["", "## Secondary / non-blocking", "", "Restart and dead-ball behavior are deliberately outside the v2 decision.", "", "| Metric | V1 | V2 |", "| --- | ---: | ---: |"])
    for key, label in (("whole_window_pass_attempts", "Whole-window pass attempts"), ("restart_pass_attempts", "Restart pass attempts"), ("dead_ball_pass_candidates", "Dead-ball pass candidates")):
        lines.append(f"| {label} | {_mapping(secondary.get('v1')).get(key, 0)} | {_mapping(secondary.get('v2')).get(key, 0)} |")
    lines.append("")
    return "\n".join(lines)


def _evaluate_policy(
    version: str,
    windows: Mapping[str, Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    gold: Sequence[Mapping[str, Any]],
    source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
    mask: Mapping[str, Any],
) -> dict[str, Any]:
    scoped = _scope_candidates(windows, _project_candidates(windows, source_documents))
    eligible = [
        row for row in scoped["pass"]
        if not row.get("from_restart")
        and _in_active_play(float(row.get("merged_release_time_sec") or 0.0), _window_for_time(windows, float(row.get("merged_release_time_sec") or 0.0)), mask)
    ]
    candidates = [row for row in eligible if str(row.get("outcome")) in PASS_OUTCOMES]
    matches, unmatched_gold, unmatched_candidates = _match_passes(gold, candidates)
    rows = _score_pass_matches(gold, candidates, matches, [])
    false_positives = _classify_pass_false_positives([candidates[index] for index in unmatched_candidates], events, [])
    misses = [_gold_public(gold[index]) for index in unmatched_gold]
    summary = _pass_summary(gold, candidates, rows, misses, false_positives)
    aggregates = _aggregate_fidelity(gold, candidates)
    return {
        "policy_version": version,
        "aggregate": aggregates["automatic"]["combined"],
        "aggregate_error": aggregates["error"]["combined"],
        "event_metrics": summary,
        "matches": rows,
        "misses": misses,
        "false_positives": false_positives,
        "false_positive_categories": dict(sorted(Counter(row["classification"] for row in false_positives).items())),
        "candidate_refs": [_candidate_ref(row) for row in candidates],
        "candidate_rejection_reasons": {
            _candidate_ref(row): list(row.get("rejection_reasons") or [])
            for row in eligible
        },
    }


def _comparison(v1: Mapping[str, Any], v2: Mapping[str, Any], gold: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    v1_matches = {str(row.get("gold_event_id")): row for row in v1.get("matches") or []}
    v2_matches = {str(row.get("gold_event_id")): row for row in v2.get("matches") or []}
    gold_by_id = {str(row.get("event_id")): row for row in gold}
    v1_reasons = _mapping(v1.get("candidate_rejection_reasons"))
    v2_reasons = _mapping(v2.get("candidate_rejection_reasons"))
    v2_refs = set(v2.get("candidate_refs") or [])
    losses = [
        {
            **_gold_public(gold_by_id[event_id]),
            "v1_candidate_id": v1_matches[event_id].get("candidate_id"),
            "v1_candidate_source_match_id": v1_matches[event_id].get("candidate_source_match_id"),
            "v2_rejection_reason": _lost_match_reason(v1_matches[event_id], v2_refs, v2_reasons),
        }
        for event_id in sorted(v1_matches.keys() - v2_matches.keys())
    ]
    gains = [
        {**_gold_public(gold_by_id[event_id]), "v2_candidate_id": v2_matches[event_id].get("candidate_id")}
        for event_id in sorted(v2_matches.keys() - v1_matches.keys())
    ]
    removed = [
        {**row, "v2_rejection_reason": v2_reasons.get(_candidate_ref(row), [])}
        for row in v1.get("false_positives") or []
        if _candidate_ref(row) not in v2_refs
    ]
    reasons = Counter(
        reason
        for reference, current in v2_reasons.items()
        if isinstance(current, list)
        if not (v1_reasons.get(reference) or [])
        for reason in current
    )
    decision = _decision(v1, v2, losses, removed)
    return {
        "decision": decision,
        "newly_lost_gold_passes": losses,
        "newly_matched_gold_passes": gains,
        "removed_false_positives": removed,
        "rejection_reason_counts": dict(sorted(reasons.items())),
    }


def _secondary_summary(
    windows: Mapping[str, Mapping[str, Any]],
    source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
    game_state_intervals: Sequence[Any],
) -> dict[str, int]:
    scoped = _scope_candidates(windows, _project_candidates(windows, source_documents))
    passes = [row for row in scoped["pass"] if str(row.get("outcome")) in PASS_OUTCOMES]
    inactive = [row for row in game_state_intervals if isinstance(row, Mapping)]
    return {
        "whole_window_pass_attempts": len(passes),
        "restart_pass_attempts": sum(bool(row.get("from_restart")) for row in passes),
        "dead_ball_pass_candidates": sum(
            any(
                row.get("source_match_id") == windows.get(str(interval.get("window_id")), {}).get("source_match_id")
                and float(interval.get("start_time_sec") or 0.0) < float(row.get("merged_release_time_sec") or 0.0) < float(interval.get("end_time_sec") or 0.0)
                for interval in inactive
            )
            for row in passes
        ),
    }


def _decision(v1: Mapping[str, Any], v2: Mapping[str, Any], losses: Sequence[Mapping[str, Any]], removed: Sequence[Mapping[str, Any]]) -> str:
    """Use direction of primary product metrics, never a fitted acceptance threshold."""

    v1_error, v2_error = _mapping(v1.get("aggregate_error")), _mapping(v2.get("aggregate_error"))
    v1_volume = abs(int(v1_error.get("attempt_count_delta") or 0))
    v2_volume = abs(int(v2_error.get("attempt_count_delta") or 0))
    v1_completion = abs(float(v1_error.get("completion_rate_delta_pp") or 0.0))
    v2_completion = abs(float(v2_error.get("completion_rate_delta_pp") or 0.0))
    if v2_volume < v1_volume and v2_completion <= v1_completion and removed and not losses:
        return "V2_CLEAR_IMPROVEMENT"
    if v2_volume < v1_volume and removed:
        return "V2_MIXED"
    return "V2_NO_IMPROVEMENT"


def _candidate_ref(row: Mapping[str, Any]) -> str:
    return f"{row.get('source_match_id') or row.get('candidate_source_match_id') or 'unknown'}:{row.get('candidate_id') or ''}"


def _lost_match_reason(
    v1_match: Mapping[str, Any],
    v2_refs: set[str],
    v2_reasons: Mapping[str, Any],
) -> list[str]:
    reference = _candidate_ref(v1_match)
    reasons = v2_reasons.get(reference)
    if isinstance(reasons, list) and reasons:
        return [str(reason) for reason in reasons]
    if reference in v2_refs:
        return ["temporal_reassignment_after_v2_suppression"]
    return ["candidate_not_retained_by_v2"]


def _clip_intervals(rows: Sequence[Mapping[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        left, right = max(start, float(row.get("start_time_sec") or 0.0)), min(end, float(row.get("end_time_sec") or 0.0))
        if right > left:
            result.append({**dict(row), "start_time_sec": _round(left), "end_time_sec": _round(right)})
    return sorted(result, key=lambda row: (row["start_time_sec"], row["end_time_sec"], str(row.get("event_id") or "")))


def _merge_intervals(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    merged: list[dict[str, float]] = []
    for row in sorted(rows, key=lambda item: (float(item["start_time_sec"]), float(item["end_time_sec"]))):
        start, end = float(row["start_time_sec"]), float(row["end_time_sec"])
        if merged and start <= merged[-1]["end_time_sec"]:
            merged[-1]["end_time_sec"] = max(merged[-1]["end_time_sec"], end)
        else:
            merged.append({"start_time_sec": start, "end_time_sec": end})
    return merged


def _subtract_intervals(start: float, end: float, excluded: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    active: list[dict[str, float]] = []
    cursor = start
    for row in excluded:
        left, right = float(row["start_time_sec"]), float(row["end_time_sec"])
        if left > cursor:
            active.append({"start_time_sec": _round(cursor), "end_time_sec": _round(left)})
        cursor = max(cursor, right)
    if cursor < end:
        active.append({"start_time_sec": _round(cursor), "end_time_sec": _round(end)})
    return active


def _in_active_play(time_sec: float, window_id: str, mask: Mapping[str, Any]) -> bool:
    window = _mapping(_mapping(mask.get("windows")).get(window_id))
    return any(
        float(row.get("start_time_sec") or 0.0) < time_sec < float(row.get("end_time_sec") or 0.0)
        for row in window.get("active_play_intervals") or []
        if isinstance(row, Mapping)
    )


def _window_for_time(windows: Mapping[str, Mapping[str, Any]], time_sec: float) -> str:
    for window_id, row in windows.items():
        if float(row["merged_start_time_sec"]) <= time_sec <= float(row["merged_end_time_sec"]):
            return window_id
    return ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nested(value: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for key in path:
        current = _mapping(current).get(key)
    return current


def _format_metric(value: Any, path: Sequence[str]) -> str:
    return f"{float(value or 0.0) * 100:.2f}%" if path[-1] in {"share", "completion_rate"} else str(value or 0)


def _format_event(policy: Mapping[str, Any], key: str) -> str:
    value = _mapping(policy.get("event_metrics")).get(key)
    return f"{float(value or 0.0) * 100:.2f}%" if key.endswith("accuracy") or key in {"precision", "recall", "f1"} else str(value or 0)


def _ranges(rows: Any) -> str:
    values = [row for row in rows or [] if isinstance(row, Mapping)]
    return ", ".join(f"{float(row['start_time_sec']):.1f}–{float(row['end_time_sec']):.1f}" for row in values) or "—"


def _listed_rows(rows: Any, empty: str, format_row: Any) -> list[str]:
    values = [row for row in rows or [] if isinstance(row, Mapping)]
    return [format_row(row) for row in values] or [empty]


def _loss_line(row: Mapping[str, Any]) -> str:
    actor, target = _mapping(row.get("actor")), _mapping(row.get("target"))
    return f"- {row.get('gold_event_id')} @ {float(row.get('gold_time_sec') or 0):.2f}s · {actor.get('display_name') or actor.get('team')} → {target.get('display_name') or target.get('team')} · {row.get('outcome')} · v1 `{row.get('v1_candidate_id')}` · v2 `{row.get('v2_rejection_reason')}`"


def _match_line(row: Mapping[str, Any]) -> str:
    return f"- {row.get('gold_event_id')} @ {float(row.get('gold_time_sec') or 0):.2f}s · v2 `{row.get('v2_candidate_id')}`"


def _removed_line(row: Mapping[str, Any]) -> str:
    return f"- `{row.get('candidate_id')}` @ {float(row.get('merged_release_time_sec') or 0):.2f}s · {row.get('classification')} · `{row.get('v2_rejection_reason')}`"


def _signed(value: Any) -> str:
    return f"{float(value or 0.0):+.0f}"


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):+.2f}%"


def _pp(value: Any) -> str:
    return f"{float(value or 0.0):+.2f} pp"
