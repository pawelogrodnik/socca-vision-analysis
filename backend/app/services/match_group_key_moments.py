from __future__ import annotations

"""Pure deterministic Key Moments derived from rebased aggregate timelines."""

import copy
import math
from collections.abc import Mapping
from typing import Any

from app.services.artifact_lineage import canonical_json_sha256


KEY_MOMENTS_SCHEMA_VERSION = "1.0.0"
KEY_MOMENTS_POLICY_VERSION = "logical-key-moments:v1"
MAX_KEY_MOMENTS = 8
KEY_MOMENT_CLUSTER_GAP_SEC = 10.0
MOMENTUM_MIN_INTENSITY = 0.60
POSSESSION_MIN_SHARE_PERCENT = 70.0
POSSESSION_MIN_COVERAGE = 0.50


def build_logical_match_key_moments(report: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact presentation projection from canonical logical time only."""

    domain = _input_domain(report)
    digest = canonical_json_sha256(domain)
    span = _finite_number(_record(report.get("timing")).get("timeline_span_sec"))
    if span is None or span < 0:
        return _empty(digest, "logical_timeline_unavailable")
    candidate_builders = (_momentum_candidates, _possession_candidates)
    candidates = [
        candidate
        for builder in candidate_builders
        for candidate in builder(report, span)
    ]
    clustered = _cluster(candidates)
    moments = [_moment(candidate, digest) for candidate in clustered]
    moments.sort(
        key=lambda row: (
            -float(row["importance_score"]),
            float(row["time_sec"]),
            str(row["type"]),
            str(row["team_id"]),
            str(row["moment_id"]),
        )
    )
    return {
        "schema_version": KEY_MOMENTS_SCHEMA_VERSION,
        "policy_version": KEY_MOMENTS_POLICY_VERSION,
        "timeline_semantics": "logical_match_video",
        "status": "ready" if moments else "not_available",
        "reason": None if moments else "no_reliable_key_moment_signals",
        "source_timeline_semantic_digest": digest,
        "moments": moments[:MAX_KEY_MOMENTS],
    }


def _input_domain(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_version": KEY_MOMENTS_POLICY_VERSION,
        "timing": copy.deepcopy(_record(report.get("timing"))),
        "team_ids": sorted(str(_record(team).get("team_id") or "") for team in _list(report.get("teams"))),
        "possession": _canonical_timeline(_record(_record(report.get("timelines")).get("possession")), "windows"),
        "attacking_momentum": _canonical_timeline(
            _record(_record(report.get("timelines")).get("attacking_momentum")), "points"
        ),
    }


def _canonical_timeline(timeline: Mapping[str, Any], row_key: str) -> dict[str, Any]:
    """Canonicalize semantically unordered candidate rows before digesting."""

    result = copy.deepcopy(dict(timeline))
    rows = result.get(row_key)
    if not isinstance(rows, list):
        return result
    result[row_key] = sorted(
        (copy.deepcopy(_record(row)) for row in rows),
        key=lambda row: (
            _finite_number(row.get("start_time_sec")) or 0.0,
            _finite_number(row.get("end_time_sec")) or 0.0,
            canonical_json_sha256(row),
        ),
    )
    return result


def _momentum_candidates(report: Mapping[str, Any], span: float) -> list[dict[str, Any]]:
    timeline = _record(_record(report.get("timelines")).get("attacking_momentum"))
    if (
        timeline.get("status") not in {"ready", "completed", "available", "fresh"}
        or timeline.get("product_readiness") != "experimental"
        or timeline.get("signal_quality") not in {"medium", "high"}
        or timeline.get("quality") not in {"medium", "high"}
    ):
        return []
    candidates = []
    for point in _list(timeline.get("points")):
        interval = _interval(_record(point), span)
        values = {
            str(key): _finite_number(value)
            for key, value in _record(_record(point).get("team_values_by_team_id")).items()
        }
        dominant_team_id = point.get("dominant_team_id")
        if not isinstance(dominant_team_id, str) or not dominant_team_id:
            continue
        value = values.get(dominant_team_id)
        if interval is None or value is None or value <= 0:
            continue
        intensity = _finite_number(_record(point).get("intensity"))
        confidence = _finite_number(_record(point).get("confidence"))
        if intensity is None or confidence is None:
            continue
        score = max(0.0, min(1.0, intensity)) * max(0.0, min(1.0, confidence))
        if score < MOMENTUM_MIN_INTENSITY:
            continue
        candidates.append(
            _candidate(
                "momentum_peak",
                dominant_team_id,
                interval,
                score,
                "attacking_momentum",
                {
                    "intensity": round(intensity, 6),
                    "confidence": round(confidence, 6),
                    "importance": round(score, 6),
                    "experimental": True,
                },
            )
        )
    return candidates


def _possession_candidates(report: Mapping[str, Any], span: float) -> list[dict[str, Any]]:
    timeline = _record(_record(report.get("timelines")).get("possession"))
    if timeline.get("status") not in {"ready", "completed", "available", "fresh"}:
        return []
    candidates = []
    for window in _list(timeline.get("windows")):
        row = _record(window)
        interval = _interval(row, span)
        shares = {
            str(key): _finite_number(value)
            for key, value in _record(row.get("possession_share_percent_by_team_id")).items()
        }
        usable = [(team, value) for team, value in shares.items() if team and value is not None]
        known = _finite_number(row.get("known_team_frames"))
        total = _finite_number(row.get("total_frames"))
        if interval is None or not usable or known is None or known < 0:
            continue
        if total is None:
            decomposed_counts = (
                _finite_number(row.get("contested_frames")),
                _finite_number(row.get("free_frames")),
                _finite_number(row.get("unknown_frames")),
            )
            if any(value is None or value < 0 for value in decomposed_counts):
                continue
            contested, free, unknown = (
                float(value) for value in decomposed_counts if value is not None
            )
            total = known + contested + free + unknown
        if total <= 0 or known > total:
            continue
        coverage = known / total
        team_id, share = sorted(usable, key=lambda item: (-float(item[1]), item[0]))[0]
        if float(share) < POSSESSION_MIN_SHARE_PERCENT or coverage < POSSESSION_MIN_COVERAGE:
            continue
        score = min(1.0, ((float(share) - 50.0) / 50.0) * coverage)
        candidates.append(
            _candidate(
                "possession_dominance",
                team_id,
                interval,
                score,
                "possession",
                {"share_percent": round(float(share), 3), "coverage": round(coverage, 6)},
            )
        )
    return candidates


def _candidate(
    kind: str,
    team_id: str,
    interval: tuple[float, float],
    importance: float,
    signal: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    start, end = interval
    return {
        "type": kind,
        "team_id": team_id,
        "window_start_sec": start,
        "window_end_sec": end,
        "time_sec": (start + end) / 2,
        "importance": round(importance, 6),
        "signal": {"source": signal, **detail},
    }


def _cluster(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join connected same-team intervals without depending on other teams.

    Different teams are deliberately never merged: overlapping evidence must
    not be turned into a fabricated team call.  Grouping each team before the
    interval sweep also makes an A-B-A sequence deterministic regardless of
    the original candidate order.
    """

    by_team: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_team.setdefault(str(candidate["team_id"]), []).append(candidate)

    clusters: list[list[dict[str, Any]]] = []
    for team_id in sorted(by_team):
        current: list[dict[str, Any]] = []
        current_end: float | None = None
        ordered = sorted(
            by_team[team_id],
            key=lambda item: (
                float(item["window_start_sec"]),
                float(item["window_end_sec"]),
                float(item["time_sec"]),
                str(item["type"]),
                canonical_json_sha256(item["signal"]),
            ),
        )
        for candidate in ordered:
            start = float(candidate["window_start_sec"])
            end = float(candidate["window_end_sec"])
            if current and current_end is not None and start <= current_end + KEY_MOMENT_CLUSTER_GAP_SEC:
                current.append(candidate)
                current_end = max(current_end, end)
                continue
            if current:
                clusters.append(current)
            current = [candidate]
            current_end = end
        if current:
            clusters.append(current)
    result = []
    for cluster in clusters:
        primary = sorted(
            cluster,
            key=lambda item: (
                -float(item["importance"]),
                float(item["time_sec"]),
                str(item["type"]),
                str(item["team_id"]),
            ),
        )[0]
        result.append(
            {
                **primary,
                "window_start_sec": min(float(item["window_start_sec"]) for item in cluster),
                "window_end_sec": max(float(item["window_end_sec"]) for item in cluster),
                "signals": [
                    item["signal"]
                    for item in sorted(
                        cluster,
                        key=lambda item: (
                            str(item["signal"]["source"]),
                            float(item["time_sec"]),
                            canonical_json_sha256(item["signal"]),
                        ),
                    )
                ],
            }
        )
    return result


def _moment(candidate: dict[str, Any], digest: str) -> dict[str, Any]:
    identity = {
        "policy_version": KEY_MOMENTS_POLICY_VERSION,
        "type": candidate["type"],
        "team_id": candidate["team_id"],
        "start": round(float(candidate["window_start_sec"]), 3),
        "end": round(float(candidate["window_end_sec"]), 3),
        "anchor": round(float(candidate["time_sec"]), 3),
        "signals": candidate["signals"],
    }
    headline = (
        "Mocny okres przewagi"
        if candidate["type"] == "momentum_peak"
        else "Wyraźna przewaga w rozpoznanym posiadaniu"
    )
    return {
        "moment_id": f"km-{canonical_json_sha256(identity)[:16]}",
        "time_sec": candidate["time_sec"],
        "window_start_sec": candidate["window_start_sec"],
        "window_end_sec": candidate["window_end_sec"],
        "type": candidate["type"],
        "team_id": candidate["team_id"],
        "importance_score": candidate["importance"],
        "headline": headline,
        "evidence": {
            "primary_signal": candidate["signal"]["source"],
            "signals": candidate["signals"],
            "source_timeline_semantic_digest": digest,
        },
    }


def _interval(row: Mapping[str, Any], span: float) -> tuple[float, float] | None:
    start, end = _finite_number(row.get("start_time_sec")), _finite_number(row.get("end_time_sec"))
    if start is None or end is None or start < 0 or end < start or end > span:
        return None
    return start, end


def _empty(digest: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": KEY_MOMENTS_SCHEMA_VERSION,
        "policy_version": KEY_MOMENTS_POLICY_VERSION,
        "timeline_semantics": "logical_match_video",
        "status": "not_available",
        "reason": reason,
        "source_timeline_semantic_digest": digest,
        "moments": [],
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
