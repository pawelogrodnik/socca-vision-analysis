from __future__ import annotations

"""Canonical merged published matches: one normal public report from fragments.

Product invariant (authoritative):

    A merged match is not a report about several matches.
    A merged match is one new match assembled from several physical fragments.

The match-group remains the INTERNAL durable provenance / aggregation
definition (manifest, pins, digests, refresh lifecycle, video lifecycle).
Its user-facing output is a normal canonical published match whose report
satisfies the SAME ``public_match_report`` contract as a physical published
match, rendered by the SAME ``PublishedMatchReportPage`` /
``PublicMatchReportContent``.

Pipeline::

    physical published matches
            ↓
    match-group manifest (internal provenance)
            ↓
    aggregation engine (in-memory candidate, pins validated)
            ↓
    canonical merged PublicMatchReport  (this module)
            ↓
    merged published-match projection:
        published/matches/published-merged-{uuid}/
            summary.json / public_report.json / provenance.json / heatmaps/
            (NO package.json — a merged match is not a physical package)
            ↓
    /published/matches/{mergedPublishedId}/report
            ↓
    PublishedMatchReportPage → PublicMatchReportContent

Aggregation contract matrix (every canonical field):

    match.duration_sec ............ SUM of logical source durations
    match.title/date/season/venue .. logical metadata, fallback first source
    team distance/HI/sprints ...... SUM
    team peak speed ............... MAX
    team avg speed ................ RECOMPUTE from merged distance/duration
    team possession ............... RECOMPUTE from summed controlled frames
    team pass counts .............. SUM
    team completion rate .......... RECOMPUTE from summed completed/attempts
    player times .................. SUM
    player distance/HI/sprints .... SUM
    player peak/max speed ......... MAX
    player avg speed .............. RECOMPUTE from merged distance/time
    player coverage ............... RECOMPUTE from merged playing time
    player workload ............... RECOMPUTE rates + rebased windows
    player quality flags .......... UNION
    possession/momentum timelines .. REBASED to logical time, canonical A/B
    heatmaps ...................... merged pitch-m samples → shared renderer
    average position .............. RECOMPUTE from merged samples (pitch m)
    team_shape .................... duration-weighted summary + mean grid
                                    + rebased timeline (when compatible)
    identity coverage ............. SUM counts, RECOMPUTE rates
    key moments ................... deterministic logical generation

Percentages are never averaged from fragments; they are recomputed from
summed primitives.
"""

import copy
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_group_aggregation import (
    AGGREGATE_ENGINE_POLICY_VERSION,
    build_match_group_report_candidate,
)
from app.services.match_group_key_moments import build_logical_match_key_moments
from app.services.match_groups import (
    MATCH_GROUPS_DIR,
    PUBLISHED_MATCHES_DIR,
    MatchGroupError,
    get_match_group,
)
from app.services.public_match_report import (
    CLIENT_PUBLIC_MATCHES_DIR,
    PUBLIC_MATCH_REPORT_SCHEMA_VERSION,
    PUBLIC_MATCH_REPORT_TYPE,
)
from app.services.stabilization import (
    _heatmap_quality,
    _safe_artifact_id,
    _write_player_heatmap_png,
)


MERGED_PUBLISHED_ID_PREFIX = "published-merged-"
MERGED_PROJECTION_SCHEMA_VERSION = "1.0.0"
MERGED_REPORT_POLICY_VERSION = "1.0.0"

# Canonical labels are assigned by sorted stable team_id so every fragment
# maps deterministically to the same merged A/B presentation.
_CANONICAL_LABELS = ("A", "B")

_QUALITY_ORDER = {"not_available": 0, "low": 1, "medium": 2, "high": 3}
_READY_STATUSES = frozenset({"ready", "available", "completed", "fresh"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_merged_published_id() -> str:
    """Return a stable, clearly distinguishable merged published-match ID."""

    return f"{MERGED_PUBLISHED_ID_PREFIX}{uuid.uuid4()}"


def is_merged_published_id(published_id: str) -> bool:
    value = str(published_id or "")
    if not value.startswith(MERGED_PUBLISHED_ID_PREFIX):
        return False
    try:
        uuid.UUID(value[len(MERGED_PUBLISHED_ID_PREFIX):])
    except (ValueError, AttributeError):
        return False
    return True


def merged_projection_path(group_id: str) -> Path:
    return MATCH_GROUPS_DIR / group_id / "merged_projection.json"


def get_merged_projection(group_id: str) -> dict[str, Any] | None:
    """Return the persisted group ↔ merged-published-match relationship."""

    path = merged_projection_path(group_id)
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    merged_id = str(document.get("merged_published_match_id") or "")
    if not is_merged_published_id(merged_id):
        return None
    if str(document.get("group_id") or "") != group_id:
        return None
    return document


def merged_published_id_for_group(group_id: str) -> str | None:
    projection = get_merged_projection(group_id)
    return str(projection["merged_published_match_id"]) if projection else None


def group_id_for_merged_published_id(merged_id: str) -> str | None:
    """Resolve a merged published ID to its backing group (server-side)."""

    if not is_merged_published_id(merged_id):
        return None
    summary_path = PUBLISHED_MATCHES_DIR / merged_id / "summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        backing = str(summary.get("backing_group_id") or "") if isinstance(summary, dict) else ""
        if backing:
            return backing
    # Fallback for projections whose summary was lost: scan sidecars.
    if MATCH_GROUPS_DIR.is_dir():
        for sidecar in MATCH_GROUPS_DIR.glob("match-group-*/merged_projection.json"):
            try:
                document = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(document, dict)
                and str(document.get("merged_published_match_id") or "") == merged_id
            ):
                return str(document.get("group_id") or sidecar.parent.name)
    return None


# ---------------------------------------------------------------------------
# Canonical report construction
# ---------------------------------------------------------------------------


def build_canonical_merged_report(
    manifest: Mapping[str, Any],
    sources: list[dict[str, Any]],
    *,
    merged_published_id: str,
) -> dict[str, Any]:
    """Build a normal ``public_match_report`` from pinned member fragments.

    ``sources`` rows are ``{"member", "aggregate", "public", "package"}``
    mappings loaded and digest-validated by :func:`load_pinned_merge_sources`.
    The returned document satisfies the physical ``PublicMatchReport``
    contract, plus two explicitly merged-only extras that physical UI
    ignores: ``key_moments`` (optional section) and ``merged_provenance``
    (admin/debug lineage).
    """

    members = _list(manifest.get("members"))
    if len(members) != len(sources) or not members:
        raise MatchGroupError("manifest_members_invalid", "Manifest member sources are unavailable.")
    metadata = _record(manifest.get("metadata"))
    group_id = str(manifest.get("group_id") or "")

    team_ids = sorted({str(team.get("team_id") or "") for source in sources for team in _list(_record(source["aggregate"]).get("teams"))} - {""})
    if len(team_ids) != 2:
        raise MatchGroupError(
            "merged_team_cardinality_unsupported",
            "A canonical merged match requires exactly two stable teams.",
        )
    canonical_of_stable = {team_id: label for team_id, label in zip(team_ids, _CANONICAL_LABELS, strict=True)}

    aggregate_report = build_match_group_report_candidate(manifest)
    key_moments = aggregate_report.get("key_moments")
    # Key Moments are generated from the rebased logical timeline; the
    # physical source rows must never leak into the merged output.
    if isinstance(key_moments, dict):
        key_moments = copy.deepcopy(key_moments)

    duration_sec = _round(sum(_number(member.get("analyzed_duration_sec")) for member in members), 2)
    public_reports = [_record(source["public"]) for source in sources]
    first_public_match = _record(public_reports[0].get("match")) if public_reports else {}

    match = {
        "id": merged_published_id,
        "title": metadata.get("title") or first_public_match.get("title") or "Scalony mecz",
        "match_date": metadata.get("match_date") if metadata.get("match_date") is not None else first_public_match.get("match_date"),
        "season": metadata.get("season") if metadata.get("season") is not None else first_public_match.get("season"),
        "venue": metadata.get("venue") if metadata.get("venue") is not None else first_public_match.get("venue"),
        "format": metadata.get("format") if metadata.get("format") is not None else first_public_match.get("format"),
        "duration_sec": duration_sec,
    }

    teams = _merge_teams(sources, canonical_of_stable, duration_sec=duration_sec)
    players, heatmap_jobs = _merge_players(sources, canonical_of_stable, duration_sec=duration_sec)
    ball = _merge_ball(sources, public_reports, aggregate_report, canonical_of_stable)

    report: dict[str, Any] = {
        "schema_version": PUBLIC_MATCH_REPORT_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "id": merged_published_id,
        "source_match_id": group_id,
        "report_type": PUBLIC_MATCH_REPORT_TYPE,
        "stats_semantics": {
            "identity": "human_reviewed_named_players_only",
            "player_time": "confirmed_detected_observations",
            "team_time": "source_video_duration",
            "team_tracking": "named_and_anonymous_team_observations",
            "team_distance": "reviewed_safe_team_observations",
            "ball": "experimental_candidates",
            "technical_debug": "excluded",
        },
        "match": match,
        "teams": teams,
        "players": players,
        "ball": ball,
    }
    identity_coverage = _record(aggregate_report.get("identity_coverage"))
    if identity_coverage.get("status") == "ready":
        report["identity_coverage"] = {
            key: identity_coverage[key]
            for key in (
                "status",
                "coverage_unit",
                "confirmed_observations",
                "reliable_observations",
                "unresolved_observations",
                "conflicted_observations",
                "confirmed_coverage_percent",
            )
            if key in identity_coverage
        }
    team_shape = _merge_team_shape(sources, canonical_of_stable, duration_sec=duration_sec)
    if team_shape is not None:
        report["team_shape"] = team_shape
    if isinstance(key_moments, dict) and key_moments.get("status") == "ready":
        report["key_moments"] = key_moments
    report["merged_provenance"] = {
        "group_id": group_id,
        "merged_published_match_id": merged_published_id,
        "policy_version": MERGED_REPORT_POLICY_VERSION,
        "aggregate_engine_policy_version": AGGREGATE_ENGINE_POLICY_VERSION,
        "sources": [
            {
                "published_id": str(member.get("published_id") or ""),
                "source_match_id": str(member.get("source_match_id") or ""),
                "sequence_index": int(member.get("sequence_index") or 0),
                "logical_start_sec": _number(member.get("logical_start_sec")),
                "logical_end_sec": _number(member.get("logical_end_sec")),
                "aggregation_input_semantic_digest": str(member.get("aggregation_input_semantic_digest") or ""),
                "public_report_semantic_digest": str(member.get("public_report_semantic_digest") or ""),
                "reviewed_identity_digest": str(member.get("reviewed_identity_digest") or ""),
            }
            for member in members
        ],
        "aggregate_semantic_digest": str(aggregate_report.get("aggregate_semantic_digest") or ""),
    }
    report["_heatmap_jobs"] = heatmap_jobs
    return report


def load_pinned_merge_sources(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Load and digest-validate pinned member publications incl. packages.

    Extends the aggregate loader trust chain with the published
    ``package.json`` (heatmap/position primitives).  Fails closed on any
    pin mismatch.
    """

    rows = []
    for member in _list(manifest.get("members")):
        item = _record(member)
        published_id = str(item.get("published_id") or "")
        if not published_id:
            raise MatchGroupError("manifest_members_invalid", "Manifest member published_id is required.")
        source_dir = PUBLISHED_MATCHES_DIR / published_id
        aggregate = _load_json(source_dir / "aggregate_inputs.json", published_id)
        public = _load_json(source_dir / "public_report.json", published_id)
        package = _load_json(source_dir / "package.json", published_id)
        source = _record(aggregate.get("source"))
        _assert_equal(source.get("aggregation_input_semantic_digest"), item.get("aggregation_input_semantic_digest"), "source_generation_changed", published_id)
        _assert_equal(source.get("public_report_semantic_digest"), item.get("public_report_semantic_digest"), "source_generation_changed", published_id)
        _assert_equal(canonical_json_sha256(public), source.get("public_report_semantic_digest"), "public_report_digest_mismatch", published_id)
        digest_document = copy.deepcopy(aggregate)
        _record(digest_document.get("source")).pop("aggregation_input_semantic_digest", None)
        _assert_equal(canonical_json_sha256(digest_document), source.get("aggregation_input_semantic_digest"), "aggregation_input_digest_mismatch", published_id)
        _assert_equal(source.get("source_match_id"), item.get("source_match_id"), "source_generation_changed", published_id)
        rows.append({"member": dict(item), "aggregate": aggregate, "public": public, "package": package})
    return rows


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


def _merge_teams(
    sources: list[dict[str, Any]],
    canonical_of_stable: dict[str, str],
    *,
    duration_sec: float,
) -> list[dict[str, Any]]:
    # Stable-team rows keyed by canonical label.
    grouped: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    presentation: dict[str, dict[str, Any]] = {}
    for source in sources:
        local_of_stable = {
            str(team.get("team_id") or ""): str(team.get("source_team_label") or "")
            for team in _list(_record(source["aggregate"]).get("teams"))
        }
        stable_of_local = {local: stable for stable, local in local_of_stable.items() if local}
        for team in _list(_record(source["public"]).get("teams")):
            row = _record(team)
            local_label = str(row.get("team_label") or "")
            stable_id = stable_of_local.get(local_label) or str(row.get("team_id") or "")
            canonical = canonical_of_stable.get(stable_id)
            if canonical is None:
                raise MatchGroupError("player_team_mismatch", "A source team row cannot be mapped to a stable merged team.")
            grouped[canonical].append(row)
            if stable_id not in presentation:
                presentation[stable_id] = {
                    "team_id": stable_id,
                    "team_name": row.get("team_name") or stable_id,
                    "display_color": row.get("display_color"),
                }

    merged_controlled = _merged_controlled_frames(sources)
    known_total = sum(merged_controlled.values())
    rows = []
    for stable_id, canonical in sorted(canonical_of_stable.items(), key=lambda item: item[1]):
        parts = grouped[canonical]
        if not parts:
            raise MatchGroupError("merged_team_primitives_missing", "A merged team has no source rows.")
        present = presentation[stable_id]
        distance = sum(_number(part.get("total_distance_m")) for part in parts)
        observed = _summed_or_none(parts, "observed_distance_m")
        estimated_gap = _summed_or_none(parts, "estimated_short_gap_distance_m")
        authorities = {str(part.get("movement_authority") or "") for part in parts}
        possession_share = (
            round(merged_controlled.get(stable_id, 0.0) / known_total * 100.0, 1) if known_total > 0 else None
        )
        pass_attempts = sum(_int(part.get("pass_attempts")) for part in parts)
        completed = sum(_int(part.get("completed_passes")) for part in parts)
        rows.append({
            "team_label": canonical,
            "team_id": stable_id,
            "team_name": str(present.get("team_name") or stable_id),
            "display_color": present.get("display_color"),
            "playing_time_sec": _round(duration_sec, 2),
            "total_distance_m": _round(distance, 2),
            "observed_distance_m": _round(observed, 2) if observed is not None else None,
            "estimated_short_gap_distance_m": _round(estimated_gap, 2) if estimated_gap is not None else None,
            "movement_authority": "merged_reviewed_safe" if authorities == {"reviewed_safe"} or authorities <= {"reviewed_safe", "merged_reviewed_safe"} else "merged_legacy_team_stats",
            "high_intensity_distance_m": _round(sum(_number(part.get("high_intensity_distance_m")) for part in parts), 2),
            "sprint_count": sum(_int(part.get("sprint_count")) for part in parts),
            "avg_speed_kmh": _round(distance / duration_sec * 3.6, 2) if duration_sec > 0 else 0.0,
            "peak_speed_kmh": _round(max(_number(part.get("peak_speed_kmh")) for part in parts), 2),
            "possession_share_percent": possession_share,
            "pass_candidates": sum(_int(part.get("pass_candidates")) for part in parts),
            "pass_attempts": pass_attempts,
            "completed_passes": completed,
            "failed_passes": sum(_int(part.get("failed_passes")) for part in parts),
            "completion_rate": _round(completed / pass_attempts * 100.0, 1) if pass_attempts else 0.0,
            "restart_passes": sum(_int(part.get("restart_passes")) for part in parts),
            "same_team_pass_candidates": sum(_int(part.get("same_team_pass_candidates")) for part in parts),
            "turnover_or_interception_candidates": sum(_int(part.get("turnover_or_interception_candidates")) for part in parts),
            "progressive_pass_candidates": sum(_int(part.get("progressive_pass_candidates")) for part in parts),
            "accepted_passes": sum(_int(part.get("accepted_passes")) for part in parts),
        })
    return rows


def _merged_controlled_frames(sources: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for source in sources:
        possession = _record(_record(source["aggregate"]).get("ball")).get("possession")
        controlled = _record(_record(possession).get("controlled_frames_by_team_id"))
        for team_id, value in controlled.items():
            result[str(team_id)] = result.get(str(team_id), 0.0) + _number(value)
    return result


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


def _merge_players(
    sources: list[dict[str, Any]],
    canonical_of_stable: dict[str, str],
    *,
    duration_sec: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for source in sources:
        offset = float(_record(source["member"]).get("logical_start_sec") or 0.0)
        aggregate_players = {
            str(row.get("player_id") or ""): _record(row)
            for row in _list(_record(source["aggregate"]).get("players"))
        }
        for player in _list(_record(source["public"]).get("players")):
            row = _record(player)
            player_id = str(row.get("player_id") or "")
            if not player_id:
                continue
            aggregate_row = aggregate_players.get(player_id, {})
            stable_team = str(aggregate_row.get("team_id") or row.get("team_id") or "")
            if not stable_team or stable_team not in canonical_of_stable:
                raise MatchGroupError("player_team_mismatch", f"A merged player {player_id!r} cannot be mapped to a stable team.")
            entry = grouped.get(player_id)
            if entry is None:
                entry = {"team_id": stable_team, "rows": []}
                grouped[player_id] = entry
                order.append(player_id)
            elif entry["team_id"] != stable_team:
                raise MatchGroupError("player_team_mismatch", f"Stable player_id {player_id!r} maps to different stable team_ids across sources.")
            entry["rows"].append((offset, row))

    players: list[dict[str, Any]] = []
    heatmap_jobs: list[dict[str, Any]] = []
    for player_id in sorted(order):
        entry = grouped[player_id]
        ranked = sorted(entry["rows"], key=lambda item: float(item[0]))
        offsets = [float(offset) for offset, _ in ranked]
        rows = [row for _, row in ranked if isinstance(row, dict)]
        first = rows[0]
        canonical_label = canonical_of_stable[entry["team_id"]]
        playing = sum(_number(row.get("playing_time_sec")) for row in rows)
        detected = sum(_number(row.get("detected_time_sec")) for row in rows)
        distance = sum(_number(row.get("total_distance_m")) for row in rows)
        peak = max((_number(row.get("peak_speed_kmh")) for row in rows), default=0.0)
        max_sprint = max((_number(row.get("max_sprint_speed_kmh")) for row in rows), default=0.0)
        flags: set[str] = set()
        for row in rows:
            for flag in row.get("quality_flags") or []:
                flags.add(str(flag))
        methods = {str(row.get("playing_time_method") or "") for row in rows} - {""}
        calculations = {str(row.get("calculation_method") or "") for row in rows} - {""}
        workload = _merge_workload(player_id, rows, workload_offsets=offsets, detected_time_sec=detected)
        heatmap_jobs.append({"player_id": player_id, "team_id": entry["team_id"]})
        players.append({
            "player_id": player_id,
            "player_name": first.get("player_name") or player_id,
            "player_number": first.get("player_number"),
            "player_role": first.get("player_role"),
            "team_id": entry["team_id"],
            "team_name": first.get("team_name"),
            "team_label": canonical_label,
            "playing_time_sec": _round(playing, 2),
            "detected_time_sec": _round(detected, 2),
            "certain_playing_time_sec": _round(sum(_number(row.get("certain_playing_time_sec", row.get("detected_time_sec"))) for row in rows), 2),
            "possible_playing_time_sec": _round(sum(_number(row.get("possible_playing_time_sec")) for row in rows), 2),
            "ambiguous_playing_time_sec": _round(sum(_number(row.get("ambiguous_playing_time_sec")) for row in rows), 2),
            "continuity_gap_time_sec": _round(sum(_number(row.get("continuity_gap_time_sec")) for row in rows), 2),
            "playing_time_method": "merged_exact_detected_only" if methods else None,
            "total_distance_m": _round(distance, 2),
            "avg_speed_kmh": _round(distance / detected * 3.6, 2) if detected > 0 else 0.0,
            "peak_speed_kmh": _round(peak, 2),
            "high_intensity_distance_m": _round(sum(_number(row.get("high_intensity_distance_m")) for row in rows), 2),
            "high_intensity_time_sec": _round(sum(_number(row.get("high_intensity_time_sec")) for row in rows), 2),
            "sprint_count": sum(_int(row.get("sprint_count")) for row in rows),
            "sprint_time_sec": _round(sum(_number(row.get("sprint_time_sec")) for row in rows), 2),
            "sprint_distance_m": _round(sum(_number(row.get("sprint_distance_m")) for row in rows), 2),
            "max_sprint_speed_kmh": _round(max_sprint, 2),
            "workload": workload,
            "calculation_method": "merged_exact_identity_coverage" if calculations else None,
            "coverage_ratio": _round(min(1.0, playing / duration_sec), 4) if duration_sec > 0 else 0.0,
            "quality_flags": sorted(flags),
            "heatmap": None,  # filled by render_merged_heatmaps()
        })
    players.sort(key=lambda item: str(item.get("player_name") or item.get("player_id") or ""))
    return players, heatmap_jobs


def _merge_workload(
    player_id: str,
    rows: list[dict[str, Any]],
    *,
    workload_offsets: list[float] | None = None,
    detected_time_sec: float,
) -> dict[str, Any] | None:
    workloads = [_record(row.get("workload")) for row in rows]
    workloads = [item for item in workloads if item]
    if not workloads:
        return None
    total_distance = sum(_number(row.get("total_distance_m")) for row in rows)
    high_distance = sum(_number(row.get("high_intensity_distance_m")) for row in rows)
    high_time = sum(_number(row.get("high_intensity_time_sec")) for row in rows)
    sprint_count = sum(_int(row.get("sprint_count")) for row in rows)
    sprint_time = sum(_number(row.get("sprint_time_sec")) for row in rows)
    sprint_distance = sum(_number(row.get("sprint_distance_m")) for row in rows)
    max_sprint = max((_number(row.get("max_sprint_speed_kmh")) for row in rows), default=0.0)

    windows: list[dict[str, Any]] = []
    offsets = list(workload_offsets) if workload_offsets else [0.0 for _ in rows]
    for offset, row in zip(offsets, rows, strict=False):
        for window in _list(_record(row.get("workload")).get("activity_windows")):
            item = dict(_record(window))
            item["start_time_sec"] = _round(_number(item.get("start_time_sec")) + offset, 3)
            item["end_time_sec"] = _round(_number(item.get("end_time_sec")) + offset, 3)
            windows.append(item)
    windows.sort(key=lambda item: (_number(item.get("start_time_sec")), _number(item.get("end_time_sec"))))
    for index, window in enumerate(windows):
        window["window_index"] = index
        window["display_label"] = _workload_window_label(_number(window.get("start_time_sec")), _number(window.get("end_time_sec")))
    best = max(
        (window for window in windows if window.get("distance_per_5min_m") is not None and _number(window.get("detected_time_sec")) >= 180.0),
        key=lambda window: float(window.get("distance_per_5min_m") or 0.0),
        default=None,
    )
    return {
        "semantics": "merged_reviewed_confirmed_detected_in_play",
        "rate_window_sec": 300.0,
        "minimum_rate_sample_sec": 120.0,
        "minimum_best_window_sample_sec": 180.0,
        "detected_time_sec": _round(detected_time_sec, 3),
        "distance_per_5min_m": _rate(total_distance, detected_time_sec),
        "high_intensity_distance_per_5min_m": _rate(high_distance, detected_time_sec),
        "sprints_per_5min": _rate(sprint_count, detected_time_sec),
        "high_intensity_distance_ratio": round(high_distance / total_distance, 4) if total_distance > 0 else None,
        "high_intensity_time_sec": _round(high_time, 3),
        "high_intensity_distance_m": _round(high_distance, 2),
        "sprint_count": sprint_count,
        "sprint_time_sec": _round(sprint_time, 3),
        "sprint_distance_m": _round(sprint_distance, 2),
        "max_sprint_speed_kmh": _round(max_sprint, 2),
        "activity_windows": windows,
        "best_activity_window": (
            {key: best[key] for key in ("window_index", "display_label", "start_time_sec", "end_time_sec", "detected_time_sec", "total_distance_m", "distance_per_5min_m", "high_intensity_distance_m", "sprint_count") if key in best}
            if best is not None
            else None
        ),
    }


def _workload_window_label(start_sec: float, end_sec: float) -> str:
    start_minute = int(start_sec // 60)
    end_minute = max(start_minute + 1, int(end_sec // 60))
    return f"{start_minute}–{end_minute}"


def _rate(value: float, detected_time_sec: float) -> float | None:
    if detected_time_sec < 120.0 or detected_time_sec <= 0:
        return None
    return round(float(value) / detected_time_sec * 300.0, 2)


# ---------------------------------------------------------------------------
# Ball: possession, passes, momentum
# ---------------------------------------------------------------------------


def _merge_ball(
    sources: list[dict[str, Any]],
    public_reports: list[dict[str, Any]],
    aggregate_report: dict[str, Any],
    canonical_of_stable: dict[str, str],
) -> dict[str, Any]:
    stable_of_canonical = {label: stable for stable, label in canonical_of_stable.items()}
    possession_windows = _merge_possession_windows(aggregate_report, stable_of_canonical)
    timeline_rows = _format_possession_timeline(possession_windows)
    known = sum(window["known_team_frames"] for window in possession_windows)
    free = sum(window["free_frames"] for window in possession_windows)
    unknown = sum(window["unknown_frames"] for window in possession_windows)
    denominator = known + free + unknown
    controlled_coverage = round(known / denominator, 4) if denominator > 0 else 0.0

    pass_attempts = sum(_int(_record(report.get("ball")).get("pass_attempts")) for report in public_reports)
    completed = sum(_int(_record(report.get("ball")).get("completed_passes")) for report in public_reports)
    momentum = _merge_momentum(aggregate_report, stable_of_canonical)
    return {
        "known_possession_coverage": controlled_coverage,
        "controlled_coverage": controlled_coverage,
        "pass_candidates": sum(_int(_record(report.get("ball")).get("pass_candidates")) for report in public_reports),
        "pass_attempts": pass_attempts,
        "completed_passes": completed,
        "failed_passes": sum(_int(_record(report.get("ball")).get("failed_passes")) for report in public_reports),
        "completion_rate": _round(completed / pass_attempts * 100.0, 1) if pass_attempts else 0.0,
        "restart_passes": sum(_int(_record(report.get("ball")).get("restart_passes")) for report in public_reports),
        "same_team_pass_candidates": sum(_int(_record(report.get("ball")).get("same_team_pass_candidates")) for report in public_reports),
        "progressive_pass_candidates": sum(_int(_record(report.get("ball")).get("progressive_pass_candidates")) for report in public_reports),
        "accepted_passes": sum(_int(_record(report.get("ball")).get("accepted_passes")) for report in public_reports),
        "possession_timeline": timeline_rows,
        "attacking_momentum": momentum,
    }


def _merge_possession_windows(
    aggregate_report: dict[str, Any],
    stable_of_canonical: dict[str, str],
) -> list[dict[str, Any]]:
    timelines = _record(aggregate_report.get("timelines"))
    possession = _record(timelines.get("possession"))
    if str(possession.get("status") or "") != "ready":
        return []
    windows = []
    for raw in _list(possession.get("windows")):
        row = _record(raw)
        controlled = _record(row.get("controlled_frames_by_team_id"))
        team_a = _number(controlled.get(stable_of_canonical.get("A", "")))
        team_b = _number(controlled.get(stable_of_canonical.get("B", "")))
        windows.append({
            "start_time_sec": _number(row.get("start_time_sec")),
            "end_time_sec": _number(row.get("end_time_sec")),
            "team_a_frames": team_a,
            "team_b_frames": team_b,
            "known_team_frames": team_a + team_b,
            "free_frames": _number(row.get("free_frames")),
            "unknown_frames": _number(row.get("unknown_frames")),
        })
    return sorted(windows, key=lambda item: (item["start_time_sec"], item["end_time_sec"]))


def _format_possession_timeline(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from math import ceil

    rows = []
    cumulative_a = 0.0
    cumulative_b = 0.0
    for index, window in enumerate(windows):
        start = _round(window["start_time_sec"], 2)
        end = _round(window["end_time_sec"], 2)
        start_minute = int(start // 60)
        end_minute = max(start_minute + 1, int(ceil(end / 60)))
        team_a = int(window["team_a_frames"])
        team_b = int(window["team_b_frames"])
        known = team_a + team_b
        team_a_percent = team_a / known * 100.0 if known > 0 else 0.0
        team_b_percent = 100.0 - team_a_percent if known > 0 else 0.0
        cumulative_a += team_a
        cumulative_b += team_b
        cumulative_known = cumulative_a + cumulative_b
        cumulative_a_percent = cumulative_a / cumulative_known * 100.0 if cumulative_known > 0 else 0.0
        cumulative_b_percent = 100.0 - cumulative_a_percent if cumulative_known > 0 else 0.0
        free = int(window["free_frames"])
        unknown = int(window["unknown_frames"])
        total = known + free + unknown
        controlled_coverage = known / total if total > 0 else 0.0
        rows.append({
            "index": index,
            "minute": end_minute,
            "label": str(end_minute),
            "window_label": f"{start_minute}-{end_minute}m",
            "start_time_sec": start,
            "end_time_sec": end,
            "team_a_frames": team_a,
            "team_b_frames": team_b,
            "known_team_frames": known,
            "team_a_percent": _round(team_a_percent, 1),
            "team_b_percent": _round(team_b_percent, 1),
            "cumulative_team_a_frames": int(cumulative_a),
            "cumulative_team_b_frames": int(cumulative_b),
            "cumulative_known_team_frames": int(cumulative_known),
            "cumulative_team_a_percent": _round(cumulative_a_percent, 1),
            "cumulative_team_b_percent": _round(cumulative_b_percent, 1),
            "free_frames": free,
            "unknown_frames": unknown,
            "team_a_share": _round(team_a_percent / 100.0, 4),
            "team_b_share": _round(team_b_percent / 100.0, 4),
            "controlled_coverage": _round(controlled_coverage, 4),
            "controlled_coverage_percent": _round(controlled_coverage * 100.0, 1),
            "unknown_coverage": _round(unknown / total, 4) if total > 0 else 0.0,
        })
    return rows


def _merge_momentum(
    aggregate_report: dict[str, Any],
    stable_of_canonical: dict[str, str],
) -> dict[str, Any]:
    from math import ceil

    timelines = _record(aggregate_report.get("timelines"))
    momentum = _record(timelines.get("attacking_momentum"))
    status = str(momentum.get("status") or "not_available")
    if status not in _READY_STATUSES:
        return {
            "experimental": True,
            "status": "not_available",
            "signal_quality": "unavailable",
            "product_readiness": "not_available",
            "quality": "unavailable",
            "warnings": [],
            "timeline": [],
        }
    stable_a = stable_of_canonical.get("A", "")
    stable_b = stable_of_canonical.get("B", "")
    points = []
    for index, raw in enumerate(_list(momentum.get("points"))):
        row = _record(raw)
        values = _record(row.get("team_values_by_team_id"))
        value_a = _number(values.get(stable_a))
        value_b = _number(values.get(stable_b))
        signed = value_a + value_b
        dominant_stable = str(row.get("dominant_team_id") or "")
        dominant = (
            "A" if dominant_stable == stable_a else "B" if dominant_stable == stable_b else None
        )
        end = _round(row.get("end_time_sec"), 3)
        points.append({
            "index": index,
            "minute": max(1, int(ceil(end / 60.0))),
            "label": _clock_label(end),
            "time_sec": end,
            "start_time_sec": _round(row.get("start_time_sec"), 3),
            "end_time_sec": end,
            "signed_score": _round(signed, 3),
            "team_a_value": _round(value_a, 3),
            "team_b_value": _round(value_b, 3),
            "dominant_team_label": dominant,
            "confidence": _round(row.get("confidence"), 4),
            "positional_confidence": _round(row.get("positional_confidence"), 4),
            "event_confidence": _round(row.get("event_confidence"), 4),
            "controlled_coverage": _round(row.get("controlled_coverage"), 4),
            "intensity": _round(row.get("intensity"), 4),
            "evidence": {},
        })
    signal_quality = str(momentum.get("signal_quality") or momentum.get("quality") or "unavailable")
    return {
        "experimental": True,
        "status": "completed" if status == "ready" else "partial",
        "signal_quality": signal_quality,
        "product_readiness": str(momentum.get("product_readiness") or "not_available"),
        "quality": str(momentum.get("quality") or signal_quality),
        "warnings": [],
        "timeline": points,
    }


def _clock_label(time_sec: float) -> str:
    total_seconds = max(0, int(round(time_sec)))
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Team shape
# ---------------------------------------------------------------------------


def _merge_team_shape(
    sources: list[dict[str, Any]],
    canonical_of_stable: dict[str, str],
    *,
    duration_sec: float,
) -> dict[str, Any] | None:
    """Merge member team-shape docs into the canonical physical shape.

    Requires every fragment to expose a ready team shape with identical
    pitch dimensions and grid geometry (explicit orientation contract v1).
    Summaries are duration-weighted; grid cells are averaged; timelines are
    concatenated with logical minute offsets.
    """

    documents: list[dict[str, Any]] = []
    for source in sources:
        document = _record(source["package"].get("team_shape"))
        if not document or document.get("available") is not True or document.get("readiness") != "ready":
            return None
        documents.append(document)
    pitch_sets = {
        (_number(_record(doc.get("pitch_dimensions_m")).get("width_m")), _number(_record(doc.get("pitch_dimensions_m")).get("length_m")))
        for doc in documents
    }
    if len(pitch_sets) != 1:
        return None
    width_m, length_m = next(iter(pitch_sets))
    if width_m <= 0 or length_m <= 0:
        return None

    stable_of_canonical = {label: stable for stable, label in canonical_of_stable.items()}
    durations = [float(_record(source["member"]).get("analyzed_duration_sec") or 0.0) for source in sources]
    total = sum(durations) or 1.0
    offsets = [float(_record(source["member"]).get("logical_start_sec") or 0.0) for source in sources]

    merged_teams = []
    grids: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    # Local label → stable team per fragment (packages predate stable IDs in
    # team_shape docs, so rows are matched through the aggregate mapping).
    stable_of_local_by_source: list[dict[str, str]] = []
    for source in sources:
        mapping: dict[str, str] = {}
        for team in _list(_record(source["aggregate"]).get("teams")):
            label = str(_record(team).get("source_team_label") or "")
            stable = str(_record(team).get("team_id") or "")
            if label and stable:
                mapping[label] = stable
        stable_of_local_by_source.append(mapping)
    for canonical in ("A", "B"):
        stable = stable_of_canonical[canonical]
        summaries = []
        timeline: list[dict[str, Any]] = []
        team_name: str | None = None
        for source_index, (document, duration, offset) in enumerate(zip(documents, durations, offsets, strict=True)):
            source_team = _shape_team_for_stable(
                document,
                stable,
                stable_of_local=stable_of_local_by_source[source_index],
            )
            if source_team is None:
                return None
            summary = _record(source_team.get("summary"))
            summaries.append((duration, summary))
            if team_name is None:
                team_name = str(source_team.get("team_name") or stable)
            minute_offset = int(offset // 60)
            for point in source_team.get("timeline") or []:
                if not isinstance(point, dict):
                    continue
                minute = int(point.get("minute") or 0) + minute_offset
                timeline.append({
                    "minute": minute,
                    "label": str(minute),
                    "width_m": None if point.get("width_m") is None else _round(point.get("width_m"), 2),
                    "depth_m": None if point.get("depth_m") is None else _round(point.get("depth_m"), 2),
                    "compactness_m": None if point.get("compactness_m") is None else _round(point.get("compactness_m"), 2),
                    "block_height_percent": None if point.get("block_height_percent") is None else _round(point.get("block_height_percent"), 2),
                })
            grid_key = _shape_grid_key(source_team)
            if grid_key is None:
                return None
            grids.setdefault(canonical, []).append((duration, source_team))
        merged_teams.append({
            "team_label": canonical,
            "team_id": stable,
            "team_name": team_name or stable,
            "summary": {
                key: _round(sum(duration * _number(summary.get(key)) for duration, summary in summaries) / total, 2)
                for key in ("average_width_m", "average_depth_m", "average_compactness_m", "average_block_height_percent")
            },
            "average_shape": _merge_shape_grid([item for item in grids[canonical]]),
            "timeline": sorted(timeline, key=lambda point: point["minute"]),
        })
        if merged_teams[-1]["average_shape"] is None:
            return None
    takeaways: list[str] = []
    for document in documents:
        for value in document.get("takeaways") or []:
            text = str(value)
            if text and text not in takeaways:
                takeaways.append(text)
    return {
        "available": True,
        "scope": "all_in_play",
        "pitch_dimensions_m": {"width_m": _round(width_m, 2), "length_m": _round(length_m, 2)},
        "teams": merged_teams,
        "takeaways": takeaways[:3],
    }


def _shape_team_for_stable(
    document: dict[str, Any],
    stable_id: str,
    *,
    stable_of_local: dict[str, str],
) -> dict[str, Any] | None:
    rows = [row for row in document.get("teams") or [] if isinstance(row, dict)]
    for row in rows:
        if str(row.get("team_id") or "") == stable_id:
            return row
    # Fallback for packages that predate stable team_id propagation in
    # team_shape docs: match through the source-local team label.
    for row in rows:
        local_label = str(row.get("team_label") or "")
        if local_label and stable_of_local.get(local_label) == stable_id:
            return row
    return None


def _shape_grid_key(team: dict[str, Any]) -> tuple[int, int] | None:
    average_shape = _record(team.get("average_shape"))
    grid = _record(average_shape.get("grid"))
    columns = int(grid.get("columns") or 0)
    rows = int(grid.get("rows") or 0)
    if columns <= 0 or rows <= 0:
        return None
    cells = average_shape.get("cells") if isinstance(average_shape.get("cells"), list) else []
    if not cells:
        return None
    return (columns, rows)


def _merge_shape_grid(items: list[tuple[float, dict[str, Any]]]) -> dict[str, Any] | None:
    keys = {_shape_grid_key(team) for _, team in items}
    if len(keys) != 1 or next(iter(keys)) is None:
        return None
    columns, rows = next(iter(keys))  # type: ignore[misc]
    total = sum(duration for duration, _ in items) or 1.0
    acc: dict[tuple[int, int], float] = {}
    for duration, team in items:
        cells = _record(team.get("average_shape")).get("cells") or []
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            key = (int(cell.get("column") or 0), int(cell.get("row") or 0))
            acc[key] = acc.get(key, 0.0) + duration * _number(cell.get("value"))
    return {
        "grid": {"columns": columns, "rows": rows},
        "cells": [
            {"column": column, "row": row, "value": _round(value / total, 6)}
            for (column, row), value in sorted(acc.items())
        ],
    }


# ---------------------------------------------------------------------------
# Heatmaps (merged spatial samples → shared renderer)
# ---------------------------------------------------------------------------


def collect_merged_heatmap_rows(
    sources: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, float] | None]:
    """Collect per-player pitch-m samples and common pitch dimensions.

    Returns ``(rows_by_player_id, pitch_dimensions_or_None)``.  Dimensions
    must be identical across fragments (explicit orientation contract v1);
    otherwise ``None`` is returned and callers render empty canvases.
    """

    rows: dict[str, list[dict[str, Any]]] = {}
    dimensions: list[tuple[float, float]] = []
    for source in sources:
        heatmaps_doc = _record(source["package"].get("reviewed_player_heatmaps"))
        pitch = _record(heatmaps_doc.get("pitch_dimensions_m"))
        width = _number_or_none(pitch.get("width_m"))
        length = _number_or_none(pitch.get("length_m"))
        if width and length:
            dimensions.append((width, length))
        for row in heatmaps_doc.get("heatmaps") or []:
            if not isinstance(row, dict):
                continue
            player_id = str(row.get("player_id") or "")
            if not player_id:
                continue
            bucket = rows.setdefault(player_id, [])
            for point in row.get("positions_m") or []:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    bucket.append({"pitch_m": [float(point[0]), float(point[1])], "source": "detected"})
    if dimensions and len(set(dimensions)) == 1:
        width, length = dimensions[0]
        return rows, {"width_m": width, "length_m": length}
    if not dimensions:
        return rows, {"width_m": 30.0, "length_m": 47.4}
    return rows, None


def render_merged_heatmaps(
    report: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    target_dir: Path,
    merged_published_id: str,
) -> dict[str, Any]:
    """Render merged heatmaps with the shared physical renderer.

    Writes ``heatmaps/*.png`` under ``target_dir`` and fills
    ``players[].heatmap`` + ``average_position`` in canonical form.
    """

    rows_by_player, pitch = collect_merged_heatmap_rows(sources)
    pitch_width = float((pitch or {}).get("width_m") or 30.0)
    pitch_length = float((pitch or {}).get("length_m") or 47.4)
    heatmap_dir = target_dir / "heatmaps"
    heatmap_dir.mkdir(parents=True, exist_ok=True)
    public_base = f"published/matches/{merged_published_id}/heatmaps"
    width_px, length_px = 360, 720
    for player in report.get("players") or []:
        if not isinstance(player, dict):
            continue
        rows = rows_by_player.get(str(player.get("player_id") or ""), [])
        filename = f"player_{_safe_artifact_id(str(player.get('player_id') or 'player'))}.png"
        _write_player_heatmap_png(
            heatmap_dir / filename,
            rows,
            pitch_width_m=pitch_width,
            pitch_length_m=pitch_length,
            width_px=width_px,
            length_px=length_px,
        )
        detected_samples = sum(1 for row in rows if row.get("source") == "detected")
        quality = _heatmap_quality(
            samples=len(rows),
            detected_samples=detected_samples,
            detected_frames=len(rows),
            ambiguous_frames=0,
        )
        player["heatmap"] = {
            "path": f"{public_base}/{filename}",
            "samples": len(rows),
            "detected_samples": detected_samples,
            "quality": quality,
            "interactive": _heatmap_points(rows, pitch_width_m=pitch_width, pitch_length_m=pitch_length, width_px=width_px, length_px=length_px),
            "average_position": _average_position(rows, pitch_width_m=pitch_width, pitch_length_m=pitch_length, width_px=width_px, length_px=length_px),
        }
    report.pop("_heatmap_jobs", None)
    return report


def _heatmap_points(
    rows: list[dict[str, Any]],
    *,
    pitch_width_m: float,
    pitch_length_m: float,
    width_px: int,
    length_px: int,
    grid_width: int = 48,
    grid_length: int = 96,
) -> dict[str, Any]:
    import numpy as np

    bins: dict[tuple[int, int], int] = {}
    cell_width = width_px / max(1, grid_width)
    cell_height = length_px / max(1, grid_length)
    for row in rows:
        pitch_m = row.get("pitch_m")
        if not pitch_m or len(pitch_m) < 2:
            continue
        x_m, y_m = float(pitch_m[0]), float(pitch_m[1])
        x = int(np.clip(x_m / max(pitch_width_m, 0.001) * (width_px - 1), 0, width_px - 1))
        y = int(np.clip(y_m / max(pitch_length_m, 0.001) * (length_px - 1), 0, length_px - 1))
        bin_x = min(grid_width - 1, max(0, int(x / cell_width)))
        bin_y = min(grid_length - 1, max(0, int(y / cell_height)))
        bins[(bin_x, bin_y)] = bins.get((bin_x, bin_y), 0) + 1
    max_value = max(bins.values(), default=0)
    return {
        "method": "pitch_meter_binned_canvas_heatmap_v1",
        "width": width_px,
        "height": length_px,
        "grid_width": grid_width,
        "grid_length": grid_length,
        "radius": max(14, int(round(max(cell_width, cell_height) * 2.2))),
        "max_value": max_value,
        "points": [
            {"x": int(round((bin_x + 0.5) * cell_width)), "y": int(round((bin_y + 0.5) * cell_height)), "value": value}
            for (bin_x, bin_y), value in sorted(bins.items(), key=lambda item: (item[0][1], item[0][0]))
        ],
    }


def _average_position(
    rows: list[dict[str, Any]],
    *,
    pitch_width_m: float,
    pitch_length_m: float,
    width_px: int,
    length_px: int,
) -> dict[str, Any] | None:
    # Recomputed from merged pitch-meter samples — never an average of
    # fragment-level screen coordinates.
    points = [
        (float(row["pitch_m"][0]), float(row["pitch_m"][1]))
        for row in rows
        if isinstance(row.get("pitch_m"), (list, tuple)) and len(row["pitch_m"]) >= 2
    ]
    if not points:
        return None
    x_m = sum(point[0] for point in points) / len(points)
    y_m = sum(point[1] for point in points) / len(points)
    if not (0.0 <= x_m <= pitch_width_m and 0.0 <= y_m <= pitch_length_m):
        return None
    return {
        "pitch_m": [_round(x_m, 3), _round(y_m, 3)],
        "x": int(round(x_m / max(pitch_width_m, 0.001) * (width_px - 1))),
        "y": int(round(y_m / max(pitch_length_m, 0.001) * (length_px - 1))),
    }


# ---------------------------------------------------------------------------
# Projection: merged published-match storage
# ---------------------------------------------------------------------------


def ensure_merged_published_match(group_id: str) -> dict[str, Any]:
    """Create or rebuild the canonical merged published-match projection.

    The merged published ID is stable: it is allocated once per group and
    persisted in ``merged_projection.json``.  Regeneration and refresh reuse
    it — they never allocate a new one.
    """

    manifest = get_match_group(group_id)
    projection = get_merged_projection(group_id)
    if projection is None:
        merged_id = new_merged_published_id()
        _write_merged_projection(group_id, merged_id)
    else:
        merged_id = str(projection["merged_published_match_id"])
    sources = load_pinned_merge_sources(manifest)
    report = build_canonical_merged_report(manifest, sources, merged_published_id=merged_id)
    target_dir = PUBLISHED_MATCHES_DIR / merged_id
    target_dir.mkdir(parents=True, exist_ok=True)
    render_merged_heatmaps(report, sources, target_dir=target_dir, merged_published_id=merged_id)
    _write_merged_projection_files(group_id, merged_id, manifest, sources, report, target_dir=target_dir)
    return {"merged_published_match_id": merged_id, "report": report}


def delete_merged_published_match(group_id: str) -> None:
    """Remove the user-facing projection; the group provenance stays intact."""

    projection = get_merged_projection(group_id)
    if projection is not None:
        merged_id = str(projection["merged_published_match_id"])
        shutil.rmtree(PUBLISHED_MATCHES_DIR / merged_id, ignore_errors=True)
        shutil.rmtree(CLIENT_PUBLIC_MATCHES_DIR / merged_id, ignore_errors=True)
    merged_projection_path(group_id).unlink(missing_ok=True)


def _write_merged_projection(
    group_id: str,
    merged_id: str,
) -> None:
    path = merged_projection_path(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": MERGED_PROJECTION_SCHEMA_VERSION,
        "group_id": group_id,
        "merged_published_match_id": merged_id,
        "updated_at": now_iso(),
    }
    _atomic_write_json(path, document)


def _write_merged_projection_files(
    group_id: str,
    merged_id: str,
    manifest: Mapping[str, Any],
    sources: list[dict[str, Any]],
    report: dict[str, Any],
    *,
    target_dir: Path,
) -> None:
    members = _list(manifest.get("members"))
    metadata = _record(manifest.get("metadata"))
    member_ids = [str(member.get("published_id") or "") for member in members if isinstance(member, dict)]
    title = str(report.get("match", {}).get("title") or metadata.get("title") or "Scalony mecz")
    existing_summary = _read_json_or_none(target_dir / "summary.json")
    generated = now_iso()
    created_at = str((existing_summary or {}).get("created_at") or generated)
    summary = {
        "id": merged_id,
        "source_match_id": group_id,
        "source_kind": "merged",
        "backing_group_id": group_id,
        "merged_published_match_id": merged_id,
        "member_published_ids": member_ids,
        "member_count": len(member_ids),
        "title": title,
        "match_date": report.get("match", {}).get("match_date"),
        "season": report.get("match", {}).get("season"),
        "venue": report.get("match", {}).get("venue"),
        "format": report.get("match", {}).get("format"),
        "duration_sec": report.get("match", {}).get("duration_sec"),
        "teams": [{"id": str(team.get("team_id") or ""), "name": str(team.get("team_name") or "")} for team in report.get("teams") or []],
        "status": "published",
        "schema_version": str(report.get("schema_version") or PUBLIC_MATCH_REPORT_SCHEMA_VERSION),
        "team_count": len(report.get("teams") or []),
        "player_count": len(report.get("players") or []),
        "tracks_count": None,
        "frames_processed": None,
        "detections_kept": None,
        "warnings_count": 0,
        "report_type": PUBLIC_MATCH_REPORT_TYPE,
        "created_at": created_at,
        "updated_at": generated,
        "storage": "json",
    }
    provenance = {
        "schema_version": MERGED_PROJECTION_SCHEMA_VERSION,
        "group_id": group_id,
        "merged_published_match_id": merged_id,
        "manifest_digest": str(manifest.get("aggregate_semantic_digest") or ""),
        "report_digest": canonical_json_sha256(report),
        "sources": (_record(report.get("merged_provenance")).get("sources") if isinstance(report.get("merged_provenance"), dict) else []),
        "updated_at": generated,
    }
    _atomic_write_json(target_dir / "summary.json", summary)
    _atomic_write_json(target_dir / "public_report.json", report)
    _atomic_write_json(target_dir / "provenance.json", provenance)

    # Static public mirror so the merged report renders without dynamic APIs.
    mirror_dir = CLIENT_PUBLIC_MATCHES_DIR / merged_id
    mirror_heatmaps = mirror_dir / "heatmaps"
    if mirror_dir.exists():
        shutil.rmtree(mirror_dir)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    (mirror_dir / "public_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    source_heatmaps = target_dir / "heatmaps"
    if source_heatmaps.is_dir():
        shutil.copytree(source_heatmaps, mirror_heatmaps)

    _write_merged_projection(group_id, merged_id)


def rebuild_canonical_report_for_group(group_id: str) -> dict[str, Any]:
    """Regenerate the canonical report from current pins (no repinning)."""

    return ensure_merged_published_match(group_id)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _round(value: Any, digits: int = 2) -> float:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return 0.0


def _number(value: Any) -> float:
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result and abs(result) != float("inf") else 0.0


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result == result and abs(result) != float("inf") else None


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _summed_or_none(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [_number_or_none(row.get(field)) for row in rows]
    if any(value is None for value in values):
        return None
    return sum(values)  # type: ignore[arg-type]


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _load_json(path: Path, member: str) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MatchGroupError("published_source_missing", f"Could not read {path.name}.", member=member) from error
    if not isinstance(result, dict):
        raise MatchGroupError("source_json_invalid", f"{path.name} must be an object.", member=member)
    return result


def _read_json_or_none(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _assert_equal(current: Any, expected: Any, code: str, member: str) -> None:
    if current != expected:
        raise MatchGroupError(code, "Current published source no longer matches the pinned group generation.", member=member)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


__all__ = [
    "MERGED_PUBLISHED_ID_PREFIX",
    "MERGED_PROJECTION_SCHEMA_VERSION",
    "MERGED_REPORT_POLICY_VERSION",
    "build_canonical_merged_report",
    "build_logical_match_key_moments",
    "collect_merged_heatmap_rows",
    "delete_merged_published_match",
    "ensure_merged_published_match",
    "get_merged_projection",
    "group_id_for_merged_published_id",
    "is_merged_published_id",
    "load_pinned_merge_sources",
    "merged_projection_path",
    "merged_published_id_for_group",
    "new_merged_published_id",
    "rebuild_canonical_report_for_group",
    "render_merged_heatmaps",
]
