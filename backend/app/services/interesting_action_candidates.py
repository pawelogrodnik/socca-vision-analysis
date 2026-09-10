from __future__ import annotations

"""Deterministic, shadow-only interesting-action candidate generation.

The generator deliberately consumes only observable canonical football signals.
It neither imports nor knows about manual Key Moments/goldsets, and it does not
write any match artifact.  A caller may persist its *shadow* output separately.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Any


POLICY_VERSION = "interesting-action-shadow:v1"
LOOK_FORWARD_SEC = 18.0
MAX_EVIDENCE_GAP_SEC = 3.0
CLUSTER_GAP_SEC = 3.0
MIN_SCORE = 0.55
MIN_CONFIDENCE = 0.35


def build_logical_candidate_signals(sources: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Rebase canonical physical artifacts onto one logical match timeline.

    ``sources`` are intentionally plain mappings so this helper stays usable by
    an offline benchmark and unit tests.  Each source needs its logical offset
    plus optional possession, pass, restart and momentum artifact rows.
    """

    signals: list[dict[str, Any]] = []
    evidence_gaps: list[tuple[float, float]] = []
    for source_index, source in enumerate(sources):
        offset = _number(source.get("logical_offset_sec"), 0.0)
        source_id = str(source.get("source_match_id") or source_index)
        segments = _rows(source.get("possession_segments"))
        team_by_label = _team_ids_by_label(segments, _rows(source.get("pass_candidates")))
        ordered_segments = sorted(segments, key=lambda row: _number(row.get("start_time_sec"), 0.0))
        previous_controlled: Mapping[str, Any] | None = None
        for segment in ordered_segments:
            start, end = _interval(segment)
            if start is None:
                continue
            logical_start, logical_end = offset + start, offset + end
            status = str(segment.get("status") or "unknown")
            if status == "unknown":
                evidence_gaps.append((logical_start, logical_end))
                previous_controlled = None
                continue
            if status != "controlled":
                continue
            team_id = _team_id(segment, team_by_label)
            if not team_id:
                previous_controlled = None
                continue
            confidence = _unit(segment.get("mean_confidence"), 0.0)
            signals.append(_signal("control", logical_start, logical_end, team_id, confidence, source_id))
            if previous_controlled is not None:
                previous_end = _number(previous_controlled.get("end_time_sec"), -1.0)
                previous_team = _team_id(previous_controlled, team_by_label)
                if previous_team and previous_team != team_id and 0 <= start - previous_end <= MAX_EVIDENCE_GAP_SEC:
                    signals.append(_signal("regain", logical_start, logical_start, team_id, confidence, source_id))
            previous_controlled = segment

        for candidate in _rows(source.get("pass_candidates")):
            start, end = _interval(candidate)
            if start is None or str(candidate.get("excluded_reason") or ""):
                continue
            pass_type = str(candidate.get("pass_type") or "")
            if pass_type != "same_team_pass":
                continue
            team_id = _team_id(candidate, team_by_label, prefer=("from_team_id", "count_for_team_id"))
            if not team_id:
                continue
            forward = max(0.0, _number(candidate.get("forward_progress_m"), 0.0))
            signals.append(
                _signal(
                    "pass",
                    offset + start,
                    offset + end,
                    team_id,
                    _unit(candidate.get("confidence"), 0.0),
                    source_id,
                    forward_progress_m=forward,
                    progressive=bool(candidate.get("is_progressive")),
                    attack_direction=str(candidate.get("attack_direction") or ""),
                    start_position_m=candidate.get("start_position_m"),
                    end_position_m=candidate.get("end_position_m"),
                )
            )

        for contact in _rows(source.get("contact_events")):
            start, end = _interval(contact)
            if start is None:
                continue
            team_id = _team_id(contact, team_by_label)
            if team_id:
                signals.append(_signal("contact", offset + start, offset + end, team_id, _unit(contact.get("confidence"), 0.0), source_id))

        for restart in _rows(source.get("restart_candidates")):
            start, end = _interval(restart)
            if start is None:
                continue
            team_id = _team_id(restart, team_by_label, prefer=("actor_team_id", "receiver_team_id"))
            if not team_id:
                continue
            signals.append(_signal("restart", offset + start, offset + end, team_id, _unit(restart.get("confidence"), 0.0), source_id))

        previous_momentum: dict[str, float] = {}
        for point in sorted(_rows(source.get("momentum_points")), key=lambda row: _number(row.get("start_time_sec"), 0.0)):
            start, end = _interval(point)
            if start is None:
                continue
            label = str(point.get("dominant_team_label") or "")
            team_id = team_by_label.get(label)
            if not team_id:
                continue
            value = _number(point.get("intensity"), 0.0)
            delta = max(0.0, value - previous_momentum.get(team_id, 0.0))
            previous_momentum[team_id] = value
            if delta > 0:
                signals.append(_signal("momentum_rise", offset + start, offset + end, team_id, _unit(point.get("confidence"), 0.0), source_id, momentum_delta=delta))

    return {
        "policy_version": POLICY_VERSION,
        "signals": sorted(signals, key=lambda row: (row["start_time_sec"], row["end_time_sec"], row["kind"], row["team_id"])),
        "evidence_gaps": sorted(evidence_gaps),
    }


def build_interesting_action_candidates(logical_signals: Mapping[str, Any], *, timeline_span_sec: float) -> list[dict[str, Any]]:
    """Return ranked, explainable action windows from rebased canonical signals."""

    if timeline_span_sec <= 0:
        return []
    signals = [row for row in _rows(logical_signals.get("signals")) if _valid_signal(row, timeline_span_sec)]
    gaps = [gap for gap in logical_signals.get("evidence_gaps", []) if _valid_gap(gap, timeline_span_sec)]
    raw: list[dict[str, Any]] = []
    for seed in signals:
        if seed["kind"] not in {"regain", "restart", "pass"}:
            continue
        if seed["kind"] == "pass" and not (seed.get("progressive") or _number(seed.get("forward_progress_m"), 0.0) >= 7.0):
            continue
        candidate = _candidate_from_seed(seed, signals, gaps, timeline_span_sec)
        if candidate is not None and candidate["interestingness_score"] >= MIN_SCORE:
            raw.append(candidate)
    clustered = _cluster(raw)
    return sorted(clustered, key=lambda row: (-row["interestingness_score"], row["start_time_sec"], row["candidate_id"]))


def _candidate_from_seed(seed: Mapping[str, Any], signals: list[dict[str, Any]], gaps: list[tuple[float, float]], span: float) -> dict[str, Any] | None:
    team_id, start = str(seed["team_id"]), float(seed["start_time_sec"])
    horizon = min(span, start + LOOK_FORWARD_SEC)
    relevant = [
        row for row in signals
        if row["team_id"] == team_id and start <= float(row["start_time_sec"]) <= horizon and not _crosses_gap(start, float(row["end_time_sec"]), gaps)
    ]
    if not relevant:
        return None
    passes = [row for row in relevant if row["kind"] == "pass"]
    progressive = [row for row in passes if bool(row.get("progressive"))]
    forward = sum(_number(row.get("forward_progress_m"), 0.0) for row in passes)
    momentum_rise = max((_number(row.get("momentum_delta"), 0.0) for row in relevant if row["kind"] == "momentum_rise"), default=0.0)
    has_restart = seed["kind"] == "restart" or any(row["kind"] == "restart" for row in relevant)
    # A single short control is not a review-worthy action.  A regain needs a
    # subsequent pass/progression/momentum observation; a progression seed has
    # already proven at least one material forward movement.
    developed = bool(passes or momentum_rise >= 0.15 or (seed["kind"] == "pass" and forward >= 7.0))
    if not developed:
        return None
    evidence: list[dict[str, Any]] = [{"kind": str(seed["kind"]), "time_sec": round(start, 3), "confidence": seed["confidence"]}]
    if progressive:
        evidence.append({"kind": "progressive_pass_sequence", "count": len(progressive), "forward_progress_m": round(sum(_number(row.get("forward_progress_m"), 0.0) for row in progressive), 3)})
    elif forward > 0:
        evidence.append({"kind": "forward_progression", "forward_progress_m": round(forward, 3)})
    if momentum_rise > 0:
        evidence.append({"kind": "momentum_rise", "delta": round(momentum_rise, 4)})
    if has_restart:
        evidence.append({"kind": "restart"})
    latest = max(float(row["end_time_sec"]) for row in relevant if row["kind"] in {"pass", "momentum_rise", "restart"})
    end = min(horizon, max(start + 3.0, latest + 3.0))
    progression_score = min(0.35, forward / 20.0 * 0.35)
    score = 0.16 + (0.24 if seed["kind"] == "regain" else 0.08) + progression_score + min(0.15, len(progressive) * 0.075) + min(0.12, momentum_rise * 0.18) + (0.08 if has_restart else 0.0)
    confidence_rows = [float(row["confidence"]) for row in relevant if row["kind"] in {"control", "pass", "regain", "restart"}]
    confidence = sum(confidence_rows) / len(confidence_rows) if confidence_rows else float(seed["confidence"])
    if confidence < MIN_CONFIDENCE:
        return None
    peak = max(relevant, key=lambda row: (_number(row.get("forward_progress_m"), 0.0) + _number(row.get("momentum_delta"), 0.0) * 10, row["end_time_sec"]))
    identity = f"{team_id}:{start:.3f}:{end:.3f}:{seed['kind']}"
    return {
        "candidate_id": _candidate_id(identity),
        "team_id": team_id,
        "start_time_sec": round(start, 3),
        "peak_time_sec": round(float(peak["end_time_sec"]), 3),
        "end_time_sec": round(end, 3),
        "interestingness_score": round(min(1.0, score), 4),
        "confidence": round(_unit(confidence, 0.0), 4),
        "evidence": evidence,
    }


def _cluster(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_team[candidate["team_id"]].append(candidate)
    result: list[dict[str, Any]] = []
    for team_id, rows in sorted(by_team.items()):
        cluster: list[dict[str, Any]] = []
        end = -1.0
        for row in sorted(rows, key=lambda item: (item["start_time_sec"], item["end_time_sec"], -item["interestingness_score"])):
            if cluster and row["start_time_sec"] > end + CLUSTER_GAP_SEC:
                result.append(_merge_cluster(cluster))
                cluster = []
            cluster.append(row)
            end = max(end, row["end_time_sec"])
        if cluster:
            result.append(_merge_cluster(cluster))
    return result


def _merge_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    primary = max(cluster, key=lambda row: (row["interestingness_score"], row["confidence"], -row["start_time_sec"]))
    start, end = min(row["start_time_sec"] for row in cluster), max(row["end_time_sec"] for row in cluster)
    merged = {**primary, "start_time_sec": start, "end_time_sec": end}
    merged["candidate_id"] = _candidate_id(f"{primary['team_id']}:{start:.3f}:{end:.3f}")
    merged["evidence"] = [item for row in cluster for item in row["evidence"]]
    return merged


def _signal(kind: str, start: float, end: float, team_id: str, confidence: float, source_id: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "start_time_sec": round(start, 3), "end_time_sec": round(end, 3), "team_id": team_id, "confidence": round(confidence, 4), "source_match_id": source_id, **extra}


def _team_ids_by_label(*collections: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for rows in collections:
        for row in rows:
            label, team_id = str(row.get("team_label") or ""), str(row.get("team_id") or "")
            if label and team_id:
                result[label] = team_id
    return result


def _team_id(row: Mapping[str, Any], labels: Mapping[str, str], prefer: tuple[str, ...] = ("team_id",)) -> str:
    for field in prefer:
        value = row.get(field)
        if isinstance(value, str) and value:
            return value
    for label_field in ("team_label", "from_team_label", "actor_team_label", "receiver_team_label"):
        label = row.get(label_field)
        if isinstance(label, str) and label in labels:
            return labels[label]
    return ""


def _interval(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    start, end = _number(row.get("start_time_sec"), -1.0), _number(row.get("end_time_sec"), -1.0)
    return (start, end) if start >= 0 and end >= start else (None, None)


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value] if isinstance(value, list) and all(isinstance(row, Mapping) for row in value) else []


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _unit(value: Any, default: float) -> float:
    return max(0.0, min(1.0, _number(value, default)))


def _valid_gap(value: Any, span: float) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 2 and _number(value[0], -1) >= 0 and _number(value[1], -1) >= _number(value[0], -1) and _number(value[1], -1) <= span


def _valid_signal(row: Mapping[str, Any], span: float) -> bool:
    return bool(row.get("team_id")) and _number(row.get("start_time_sec"), -1) >= 0 and _number(row.get("end_time_sec"), -1) >= _number(row.get("start_time_sec"), -1) and _number(row.get("end_time_sec"), -1) <= span


def _crosses_gap(start: float, end: float, gaps: list[tuple[float, float]]) -> bool:
    return any(gap_start < end and gap_end > start for gap_start, gap_end in gaps)


def _candidate_id(identity: str) -> str:
    return f"iac-{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
