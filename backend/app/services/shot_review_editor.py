from __future__ import annotations

"""Durable, operator-owned canonical Shot Review state.

Detector candidates are deliberately read-only suggestions here.  This module
never runs inference and never lets suggestions become canonical without an
explicit operator mutation.
"""

import copy
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app import config
from app.services.artifact_lineage import canonical_json_sha256
from app.services.json_publish_store import MERGED_SOURCE_KIND, get_published_match
from app.services.match_groups import get_match_group
from app.services.merged_public_match import group_id_for_merged_published_id
from app.services.resolved_player_timeline import build_resolved_player_timeline_from_files
from app.services.shot_candidates import MIN_BALL_CONFIDENCE, build_logical_shot_candidates_document


SHOT_REVIEW_SCHEMA_VERSION = "shot-review-editorial:v1"
EDITORIAL_DIRECTORY = config.STORAGE_DIR / "editorial" / "shots"
OUTCOMES = frozenset({"goal", "on_target", "off_target", "blocked"})
LOCATION_SOURCES = frozenset({"ball", "player", "manual", "unavailable"})
LOCATION_TOLERANCE_SEC = 0.5
CLUSTER_MAX_NEIGHBOR_GAP_SEC = 2.5
CLUSTER_MAX_SPAN_SEC = 5.0
CLUSTER_DISPLAY_BUFFER_SEC = 0.5


class ShotReviewError(ValueError):
    def __init__(self, code: str, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sidecar_path(published_id: str) -> Path:
    if not published_id or published_id != Path(published_id).name:
        raise ShotReviewError("shot_review_publication_invalid", "Nieprawidłowy identyfikator publikacji.")
    return EDITORIAL_DIRECTORY / f"{published_id}.json"


def _authority_path(published_id: str) -> Path:
    return EDITORIAL_DIRECTORY / f"{published_id}.authority.json"


def _content(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SHOT_REVIEW_SCHEMA_VERSION,
        "published_id": str(document.get("published_id") or ""),
        "canonical_shots": copy.deepcopy(document.get("canonical_shots") or []),
        "suggested_candidate_reviews": copy.deepcopy(document.get("suggested_candidate_reviews") or []),
    }


def _revision(document: Mapping[str, Any]) -> str:
    return canonical_json_sha256(_content(document))


def _empty_document(published_id: str) -> dict[str, Any]:
    document = {"schema_version": SHOT_REVIEW_SCHEMA_VERSION, "published_id": published_id, "canonical_shots": [], "suggested_candidate_reviews": [], "_exists": False}
    document["revision"] = _revision(document)
    return document


def load_shot_review_document(published_id: str) -> dict[str, Any]:
    sidecar, authority = _sidecar_path(published_id), _authority_path(published_id)
    if not sidecar.exists():
        if authority.exists():
            raise ShotReviewError("shot_review_recovery_required", "Trwały stan Shot Review jest niedostępny; odzyskanie wymaga naprawy danych redakcyjnych.", 409)
        return _empty_document(published_id)
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ShotReviewError("shot_review_editorial_store_invalid", "Nie można odczytać danych Shot Review.", 409) from error
    if not isinstance(value, dict) or value.get("published_id") != published_id or not isinstance(value.get("canonical_shots"), list):
        raise ShotReviewError("shot_review_editorial_store_invalid", "Dane Shot Review nie pasują do publikacji.", 409)
    value["_exists"] = True
    value["revision"] = _revision(value)
    return value


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _source_context(published_id: str, report: Mapping[str, Any], time_sec: float) -> tuple[str, float] | None:
    match = get_published_match(published_id)
    if str(match.get("source_kind") or "physical") != MERGED_SOURCE_KIND:
        source_id = str(match.get("source_match_id") or "")
        return (source_id, time_sec) if source_id else None
    group_id = group_id_for_merged_published_id(published_id)
    if not group_id:
        return None
    for member in get_match_group(group_id).get("members") or []:
        if not isinstance(member, Mapping):
            continue
        start, end = _number(member.get("logical_start_sec")), _number(member.get("logical_end_sec"))
        source_id = str(member.get("source_match_id") or "")
        if source_id and start is not None and end is not None and start <= time_sec <= end:
            return source_id, time_sec - start
    return None


def _suggestion_projection(published_id: str) -> dict[str, Any]:
    """Read existing shot-candidate artifacts; never produce detector output."""

    match = get_published_match(published_id)
    report = _record(match.get("public_report"))
    source_kind = str(match.get("source_kind") or "physical")
    if source_kind != MERGED_SOURCE_KIND:
        source_id = str(match.get("source_match_id") or "")
        document = _read_object(config.MATCHES_DIR / source_id / "shot_candidates.json")
        candidates = [copy.deepcopy(row) for row in document.get("candidates") or [] if isinstance(row, dict)]
        if not document:
            return {"status": "not_available", "reason": "shot_candidates_unavailable", "candidate_count": 0, "candidates": []}
        normalized = [{**row, "time_sec": _number(row.get("candidate_timestamp_sec"))} for row in candidates]
        digest_input: Mapping[str, Any] = {key: value for key, value in document.items() if key != "generated_at"}
    else:
        group_id = group_id_for_merged_published_id(published_id)
        if not group_id:
            return {"status": "not_available", "reason": "merged_source_unavailable", "candidate_count": 0, "candidates": []}
        manifest = get_match_group(group_id)
        sources = []
        for member in manifest.get("members") or []:
            if not isinstance(member, Mapping):
                continue
            source_id = str(member.get("source_match_id") or "")
            document = _read_object(config.MATCHES_DIR / source_id / "shot_candidates.json")
            if document:
                sources.append({"source_match_id": source_id, "logical_offset_sec": _number(member.get("logical_start_sec")) or 0.0, "shot_candidates": document})
        source_policy_versions = {
            str(_record(source.get("shot_candidates")).get("policy_version") or "")
            for source in sources
        }
        if len(source_policy_versions) != 1 or not next(iter(source_policy_versions), ""):
            return {
                "status": "not_available",
                "reason": "shot_candidate_policy_mismatch",
                "candidate_count": 0,
                "candidates": [],
            }
        # Read the policy that produced the persisted source artifacts.  A
        # group is intentionally not projected from a mixture of policies;
        # regeneration applies the promoted default consistently to all
        # members, while old artifacts remain reproducible and reviewable.
        logical = build_logical_shot_candidates_document(
            sources,
            timeline_span_sec=_number(_record(report.get("match")).get("duration_sec")) or 0.0,
            policy_version=next(iter(source_policy_versions)),
        )
        normalized = [{**copy.deepcopy(row), "time_sec": _number(row.get("logical_timestamp_sec"))} for row in logical.get("candidates") or [] if isinstance(row, dict)]
        digest_input = {"manifest_digest": manifest.get("aggregate_semantic_digest"), "logical_candidates": logical}
    digest = canonical_json_sha256({"schema_version": SHOT_REVIEW_SCHEMA_VERSION, "published_id": published_id, "candidate_source": digest_input})
    return {"status": "ready", "candidate_generation_digest": digest, "candidate_count": len(normalized), "candidates": normalized}


def _reviews_for_generation(document: Mapping[str, Any], generation_digest: str) -> dict[str, dict[str, Any]]:
    current_generation = {
        str(row.get("candidate_id")): row
        for row in document.get("suggested_candidate_reviews") or []
        if isinstance(row, dict) and str(row.get("candidate_generation_digest") or "") == generation_digest and row.get("candidate_id")
    }
    if current_generation:
        return current_generation
    return {}


def _reviews_for_candidates(
    document: Mapping[str, Any],
    generation_digest: str,
    candidates: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Apply durable decisions to stable candidate identities after rebuild.

    A candidate ID is derived from the reviewed contact and source evidence,
    not its policy version.  A regenerated document therefore must not make a
    previously rejected same candidate reviewable again merely because its
    aggregate generation digest changed.  Same-generation reviews always win;
    historical rows are used only for still-present candidate IDs.
    """

    current = _reviews_for_generation(document, generation_digest)
    candidate_ids = {str(candidate.get("candidate_id") or "") for candidate in candidates}
    historical = {
        str(row.get("candidate_id") or ""): row
        for row in document.get("suggested_candidate_reviews") or []
        if isinstance(row, dict)
        and str(row.get("candidate_generation_digest") or "") != generation_digest
        and str(row.get("candidate_id") or "") in candidate_ids
    }
    return {**historical, **current}


def _candidate_time(candidate: Mapping[str, Any]) -> float:
    for key in ("time_sec", "logical_timestamp_sec", "candidate_timestamp_sec", "source_timestamp_sec"):
        value = _number(candidate.get(key))
        if value is not None:
            return value
    return 0.0


def _cluster_member(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Keep compact, already-generated evidence available to the review UI."""

    keys = (
        "candidate_id", "time_sec", "logical_timestamp_sec", "candidate_timestamp_sec", "source_timestamp_sec",
        "source_match_id", "suggested_team_label", "suggested_team_name", "suggested_player_id",
        "confidence", "reasons", "source_event_id", "source_context_start_sec", "source_context_end_sec",
        "logical_context_start_sec", "logical_context_end_sec",
    )
    return {key: copy.deepcopy(candidate.get(key)) for key in keys if key in candidate}


def _preferred_candidate(candidates: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Choose the deterministic default signal for a reviewable cluster."""

    return min(
        candidates,
        key=lambda row: (-(_number(row.get("confidence")) or 0.0), _candidate_time(row), str(row.get("candidate_id") or "")),
    )


def build_review_clusters(
    candidates: list[Mapping[str, Any]],
    *,
    candidate_generation_digest: str,
    timeline_span_sec: float | None,
) -> list[dict[str, Any]]:
    """Group adjacent raw candidates into bounded, source-local review units.

    Clustering is deliberately presentation/read-model logic. Raw candidates
    remain intact and the stable ID is derived solely from their immutable
    generation lineage and member IDs.
    """

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        source_id = str(candidate.get("source_match_id") or "")
        grouped.setdefault(source_id, []).append(candidate)

    clusters: list[dict[str, Any]] = []
    for source_id, source_candidates in sorted(grouped.items()):
        current: list[Mapping[str, Any]] = []

        def flush() -> None:
            if not current:
                return
            ordered = sorted(current, key=lambda row: (_candidate_time(row), str(row.get("candidate_id") or "")))
            first_time, last_time = _candidate_time(ordered[0]), _candidate_time(ordered[-1])
            members = [_cluster_member(row) for row in ordered]
            preferred = _preferred_candidate(members)
            cluster_key = canonical_json_sha256({
                "schema_version": "shot-review-cluster:v1",
                "candidate_generation_digest": candidate_generation_digest,
                "source_match_id": source_id,
                "member_candidate_ids": [str(row.get("candidate_id") or "") for row in members],
            })
            review_start = max(0.0, first_time - CLUSTER_DISPLAY_BUFFER_SEC)
            review_end = last_time + CLUSTER_DISPLAY_BUFFER_SEC
            if timeline_span_sec is not None and timeline_span_sec > 0:
                review_end = min(timeline_span_sec, review_end)
            clusters.append({
                "cluster_id": f"shot-cluster-{cluster_key.split(':', 1)[-1][:16]}",
                "source_match_id": source_id,
                "review_start_time_sec": round(review_start, 6),
                "review_end_time_sec": round(review_end, 6),
                "member_count": len(members),
                "member_candidate_ids": [str(row.get("candidate_id") or "") for row in members],
                "member_candidates": members,
                "preferred_candidate_id": str(preferred.get("candidate_id") or ""),
                "preferred_candidate": preferred,
            })

        for candidate in sorted(source_candidates, key=lambda row: (_candidate_time(row), str(row.get("candidate_id") or ""))):
            if not current:
                current.append(candidate)
                continue
            first_time, prior_time, candidate_time = _candidate_time(current[0]), _candidate_time(current[-1]), _candidate_time(candidate)
            if candidate_time - prior_time <= CLUSTER_MAX_NEIGHBOR_GAP_SEC and candidate_time - first_time <= CLUSTER_MAX_SPAN_SEC:
                current.append(candidate)
            else:
                flush()
                current = [candidate]
        flush()
    return sorted(clusters, key=lambda row: (float(row["review_start_time_sec"]), str(row["source_match_id"]), str(row["cluster_id"])))


def _cluster_status(cluster: Mapping[str, Any], reviews: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str]]:
    statuses = [str((reviews.get(candidate_id) or {}).get("review_status") or "unreviewed") for candidate_id in cluster.get("member_candidate_ids") or []]
    canonical_ids = sorted({
        str((reviews.get(candidate_id) or {}).get("canonical_shot_id") or "")
        for candidate_id in cluster.get("member_candidate_ids") or []
        if (reviews.get(candidate_id) or {}).get("review_status") == "accepted"
    } - {""})
    if "accepted" in statuses:
        return "accepted", canonical_ids
    if statuses and all(status == "rejected" for status in statuses):
        return "rejected", canonical_ids
    if "rejected" in statuses:
        return "partially_rejected", canonical_ids
    return "unreviewed", canonical_ids


def _cluster_projection(
    candidates: list[Mapping[str, Any]],
    *,
    candidate_generation_digest: str,
    reviews: Mapping[str, Mapping[str, Any]],
    timeline_span_sec: float | None,
) -> list[dict[str, Any]]:
    rows = build_review_clusters(
        candidates,
        candidate_generation_digest=candidate_generation_digest,
        timeline_span_sec=timeline_span_sec,
    )
    for row in rows:
        row["status"], row["canonical_shot_ids"] = _cluster_status(row, reviews)
        if row["status"] in {"unreviewed", "partially_rejected"}:
            unresolved_members = [
                member
                for member in row["member_candidates"]
                if str((reviews.get(str(member.get("candidate_id") or "")) or {}).get("review_status") or "unreviewed") == "unreviewed"
            ]
            # Rejected signals remain durable raw evidence, but a new cluster
            # action must default to the signal the operator can still accept.
            if unresolved_members:
                preferred = _preferred_candidate(unresolved_members)
                row["preferred_candidate_id"] = str(preferred.get("candidate_id") or "")
                row["preferred_candidate"] = preferred
    return rows


def _canonical_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(row.get(key)) for key in ("shot_id", "time_sec", "team_id", "outcome", "player_id", "origin", "location_m", "location_source")}


def editor_state(published_id: str) -> dict[str, Any]:
    document = load_shot_review_document(published_id)
    projection = _suggestion_projection(published_id)
    if projection.get("status") != "ready":
        suggestions = {
            **projection,
            "accepted_count": 0,
            "rejected_count": 0,
            "unreviewed_count": 0,
            "unreviewed_suggestions": [],
            "cluster_count": 0,
            "unreviewed_cluster_count": 0,
            "unreviewed_clusters": [],
        }
    else:
        reviews = _reviews_for_candidates(
            document,
            str(projection["candidate_generation_digest"]),
            projection["candidates"],
        )
        unreviewed, accepted, rejected = [], 0, 0
        for candidate in projection["candidates"]:
            status = str((reviews.get(str(candidate.get("candidate_id") or "")) or {}).get("review_status") or "unreviewed")
            accepted += status == "accepted"
            rejected += status == "rejected"
            if status == "unreviewed":
                unreviewed.append(candidate)
        report = _record(get_published_match(published_id).get("public_report"))
        duration = _number(_record(report.get("match")).get("duration_sec"))
        clusters = _cluster_projection(
            projection["candidates"],
            candidate_generation_digest=str(projection["candidate_generation_digest"]),
            reviews=reviews,
            timeline_span_sec=duration,
        )
        unreviewed_clusters = [
            row for row in clusters
            if row.get("status") in {"unreviewed", "partially_rejected"}
        ]
        suggestions = {
            **projection,
            "accepted_count": accepted,
            "rejected_count": rejected,
            "unreviewed_count": len(unreviewed),
            "unreviewed_suggestions": unreviewed,
            "cluster_count": len(clusters),
            "unreviewed_cluster_count": len(unreviewed_clusters),
            "unreviewed_clusters": unreviewed_clusters,
        }
    return {
        "published_id": published_id,
        "revision": document["revision"],
        "has_editorial_sidecar": bool(document["_exists"]),
        "canonical_shots": sorted([_canonical_dto(row) for row in document["canonical_shots"] if isinstance(row, dict)], key=lambda row: (row["time_sec"], row["shot_id"])),
        # Flat fields keep the future UI contract small; the nested object
        # additionally carries the read-only suggestion provenance.
        "candidate_generation_digest": suggestions.get("candidate_generation_digest"),
        "candidate_count": int(suggestions.get("candidate_count") or 0),
        "accepted_count": int(suggestions.get("accepted_count") or 0),
        "rejected_count": int(suggestions.get("rejected_count") or 0),
        "unreviewed_count": int(suggestions.get("unreviewed_count") or 0),
        "unreviewed_suggestions": copy.deepcopy(suggestions.get("unreviewed_suggestions") or []),
        "cluster_count": int(suggestions.get("cluster_count") or 0),
        "unreviewed_cluster_count": int(suggestions.get("unreviewed_cluster_count") or 0),
        "unreviewed_suggestion_clusters": copy.deepcopy(suggestions.get("unreviewed_clusters") or []),
        "suggestions": suggestions,
    }


def _teams_players(report: Mapping[str, Any]) -> tuple[set[str], dict[str, str]]:
    teams = {str(row.get("team_id") or "") for row in report.get("teams") or [] if isinstance(row, dict)} - {""}
    players = {str(row.get("player_id") or ""): str(row.get("team_id") or "") for row in report.get("players") or [] if isinstance(row, dict) and row.get("player_id")}
    return teams, players


def _pitch_bounds(source_id: str | None) -> tuple[float, float]:
    pitch = _read_object(config.MATCHES_DIR / str(source_id or "") / "pitch_config.json")
    return _number(pitch.get("width_m")) or 30.0, _number(pitch.get("length_m")) or 47.4


def _valid_location(value: Any, *, width: float, length: float) -> dict[str, float] | None:
    value = _record(value)
    x, y = _number(value.get("x")), _number(value.get("y"))
    if x is None or y is None or x < 0 or y < 0 or x > width or y > length:
        return None
    return {"x": round(x, 6), "y": round(y, 6)}


def _trusted_ball_location(source_id: str, source_time: float) -> dict[str, float] | None:
    rows = _read_object(config.MATCHES_DIR / source_id / "ball_tracks.json").get("positions") or []
    trusted = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("source") or "") not in {"detected", "interpolated"} or (_number(row.get("confidence")) or 0) < MIN_BALL_CONFIDENCE:
            continue
        position = row.get("position_m")
        if not isinstance(position, list) or len(position) < 2:
            continue
        time = _number(row.get("time_sec"))
        if time is not None and abs(time - source_time) <= LOCATION_TOLERANCE_SEC:
            trusted.append((abs(time - source_time), time, position))
    if not trusted:
        return None
    _, selected_time, position = min(trusted, key=lambda item: (item[0], item[1]))
    # A canonical unknown/untrusted row is an explicit continuity boundary.
    # Never reach over it merely to make a shot mappable.
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_time = _number(row.get("time_sec"))
        if row_time is None or not min(selected_time, source_time) <= row_time <= max(selected_time, source_time):
            continue
        if str(row.get("source") or "") not in {"detected", "interpolated"} or (_number(row.get("confidence")) or 0) < MIN_BALL_CONFIDENCE:
            return None
    return {"x": round(float(position[0]), 6), "y": round(float(position[1]), 6)}


def _trusted_player_location(source_id: str, player_id: str | None, source_time: float) -> dict[str, float] | None:
    if not player_id:
        return None
    # This timeline is the canonical Reviewed Identity resolution.  In
    # particular it starts with the public roster player_id and only then
    # follows its assigned stable subject/slot over the exact stint interval.
    # Comparing roster IDs to stable_player_id would invent a namespace alias.
    try:
        timeline = build_resolved_player_timeline_from_files(config.MATCHES_DIR / source_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    player = _record(_record(timeline.get("players")).get(player_id))
    if not player:
        return None
    choices = []
    for row in player.get("rows") or []:
        if not isinstance(row, dict) or str(row.get("source") or row.get("status") or "") not in {"detected", "interpolated"}:
            continue
        time, position = _number(row.get("time_sec")), row.get("pitch_m")
        if time is not None and isinstance(position, list) and len(position) >= 2 and abs(time - source_time) <= LOCATION_TOLERANCE_SEC:
            choices.append((abs(time - source_time), time, position))
    if not choices:
        return None
    _, _, position = min(choices, key=lambda item: (item[0], item[1]))
    return {"x": round(float(position[0]), 6), "y": round(float(position[1]), 6)}


def _location_for_shot(published_id: str, report: Mapping[str, Any], time_sec: float, player_id: str | None, supplied: Any) -> tuple[dict[str, float] | None, str]:
    context = _source_context(published_id, report, time_sec)
    source_id = context[0] if context else None
    width, length = _pitch_bounds(source_id)
    if supplied is not None:
        manual = _valid_location(supplied, width=width, length=length)
        if manual is None:
            raise ShotReviewError("shot_review_location_invalid", "Punkt strzału musi znajdować się na boisku.")
        return manual, "manual"
    if context:
        ball = _trusted_ball_location(*context)
        if ball is not None:
            return ball, "ball"
        player = _trusted_player_location(context[0], player_id, context[1])
        if player is not None:
            return player, "player"
    return None, "unavailable"


def _validate_shot(
    published_id: str,
    report: Mapping[str, Any],
    raw: Mapping[str, Any],
    *,
    origin: str,
    fallback_time: float | None = None,
    existing: Mapping[str, Any] | None = None,
    location_mode: str = "derive_or_manual",
    manual_location_override: Any = None,
) -> dict[str, Any]:
    duration = _number(_record(report.get("match")).get("duration_sec"))
    time_sec = _number(raw.get("time_sec")) if raw.get("time_sec") is not None else fallback_time
    if time_sec is None or time_sec < 0 or duration is None or time_sec > duration:
        raise ShotReviewError("shot_review_timestamp_invalid", "Czas strzału jest poza zakresem meczu.")
    team_id, outcome = str(raw.get("team_id") or ""), str(raw.get("outcome") or "")
    teams, players = _teams_players(report)
    if team_id not in teams:
        raise ShotReviewError("shot_review_team_invalid", "Wybrana drużyna nie występuje w raporcie.")
    if outcome not in OUTCOMES:
        raise ShotReviewError("shot_review_outcome_invalid", "Wynik strzału jest nieprawidłowy.")
    player_id = str(raw.get("player_id") or "") or None
    if player_id is not None and player_id not in players:
        raise ShotReviewError("shot_review_player_invalid", "Wybrany zawodnik nie występuje w raporcie.")
    if player_id is not None and players[player_id] != team_id:
        raise ShotReviewError("shot_review_player_invalid", "Zawodnik nie należy do wybranej drużyny.")
    if location_mode == "preserve" and existing is not None:
        location, location_source = copy.deepcopy(existing.get("location_m")), str(existing.get("location_source") or "unavailable")
        if location_source not in LOCATION_SOURCES:
            raise ShotReviewError("shot_review_location_invalid", "Istniejąca lokalizacja strzału jest nieprawidłowa.", 409)
    elif location_mode == "manual_override":
        location, location_source = _location_for_shot(published_id, report, time_sec, player_id, manual_location_override)
    elif location_mode == "derive":
        # Canonical location_m is a read-model field during edits.  A changed
        # timestamp/player must resolve fresh evidence rather than treating an
        # echoed prior value as a new manual point.
        location, location_source = _location_for_shot(published_id, report, time_sec, player_id, None)
    else:
        location, location_source = _location_for_shot(published_id, report, time_sec, player_id, raw.get("location_m"))
    return {**copy.deepcopy(existing or {}), "shot_id": str((existing or {}).get("shot_id") or f"shot-review-{uuid.uuid4()}"), "time_sec": round(time_sec, 6), "team_id": team_id, "outcome": outcome, "player_id": player_id, "origin": origin, "location_m": location, "location_source": location_source}


def _ensure_revision(current: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    if str(payload.get("expected_revision") or "") != str(current.get("revision") or ""):
        raise ShotReviewError("shot_review_revision_conflict", "Stan Shot Review zmienił się na serwerze. Odśwież edytor.", 409)


def _replace_review(rows: Any, review: Mapping[str, Any]) -> list[dict[str, Any]]:
    previous = [copy.deepcopy(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return [row for row in previous if not (row.get("candidate_id") == review["candidate_id"] and row.get("candidate_generation_digest") == review["candidate_generation_digest"])] + [dict(review)]


def _next_document(current: Mapping[str, Any], published_id: str, *, shots: list[dict[str, Any]] | None = None, reviews: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    document = {"schema_version": SHOT_REVIEW_SCHEMA_VERSION, "published_id": published_id, "canonical_shots": copy.deepcopy(shots if shots is not None else current.get("canonical_shots") or []), "suggested_candidate_reviews": copy.deepcopy(reviews if reviews is not None else current.get("suggested_candidate_reviews") or [])}
    document["revision"] = _revision(document)
    return document


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _persist(published_id: str, document: Mapping[str, Any]) -> None:
    _write_json_atomic(_sidecar_path(published_id), _content(document))
    _write_json_atomic(_authority_path(published_id), {"schema_version": SHOT_REVIEW_SCHEMA_VERSION, "published_id": published_id, "operator_owned": True})


def _current_suggestion(published_id: str, current: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    state = editor_state(published_id)
    suggestions = _record(state.get("suggestions"))
    digest = str(payload.get("candidate_generation_digest") or "")
    if suggestions.get("status") != "ready" or digest != str(suggestions.get("candidate_generation_digest") or ""):
        raise ShotReviewError("shot_review_candidate_stale", "Sugestia pochodzi z nieaktualnej generacji. Odśwież edytor.", 409)
    candidate_id = str(payload.get("candidate_id") or "")
    candidate = next((row for row in suggestions.get("unreviewed_suggestions") or [] if isinstance(row, dict) and str(row.get("candidate_id") or "") == candidate_id), None)
    if not isinstance(candidate, dict):
        raise ShotReviewError("shot_review_candidate_unavailable", "Sugestia nie jest dostępna do przeglądu.", 409)
    return candidate


def _current_cluster(published_id: str, current: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    projection = _suggestion_projection(published_id)
    digest = str(payload.get("candidate_generation_digest") or "")
    if projection.get("status") != "ready" or digest != str(projection.get("candidate_generation_digest") or ""):
        raise ShotReviewError("shot_review_candidate_stale", "Sugestia pochodzi z nieaktualnej generacji. Odśwież edytor.", 409)
    report = _record(get_published_match(published_id).get("public_report"))
    clusters = _cluster_projection(
        projection["candidates"],
        candidate_generation_digest=digest,
        reviews=_reviews_for_candidates(current, digest, projection["candidates"]),
        timeline_span_sec=_number(_record(report.get("match")).get("duration_sec")),
    )
    cluster_id = str(payload.get("cluster_id") or "")
    cluster = next((row for row in clusters if str(row.get("cluster_id") or "") == cluster_id), None)
    if not isinstance(cluster, dict) or cluster.get("status") not in {"unreviewed", "partially_rejected"}:
        raise ShotReviewError("shot_review_cluster_unavailable", "Klaster sugestii nie jest dostępny do przeglądu.", 409)
    return cluster


def accept_suggestion(published_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    match, current = get_published_match(published_id), load_shot_review_document(published_id)
    _ensure_revision(current, payload)
    candidate = _current_suggestion(published_id, current, payload)
    report = _record(match.get("public_report"))
    shot = _validate_shot(published_id, report, _record(payload.get("shot")), origin="accepted_suggestion", fallback_time=_number(candidate.get("time_sec")))
    shot["suggested_candidate_id"] = str(candidate.get("candidate_id") or "")
    shot["suggested_candidate_generation_digest"] = str(payload.get("candidate_generation_digest") or "")
    review = {"candidate_id": shot["suggested_candidate_id"], "candidate_generation_digest": shot["suggested_candidate_generation_digest"], "review_status": "accepted", "canonical_shot_id": shot["shot_id"], "reviewed_at": _now()}
    document = _next_document(current, published_id, shots=[*current["canonical_shots"], shot], reviews=_replace_review(current.get("suggested_candidate_reviews"), review))
    _persist(published_id, document)
    return {**editor_state(published_id), "accepted_shot": _canonical_dto(shot)}


def reject_suggestion(published_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    current = load_shot_review_document(published_id)
    _ensure_revision(current, payload)
    candidate = _current_suggestion(published_id, current, payload)
    review = {"candidate_id": str(candidate.get("candidate_id") or ""), "candidate_generation_digest": str(payload.get("candidate_generation_digest") or ""), "review_status": "rejected", "reviewed_at": _now()}
    _persist(published_id, _next_document(current, published_id, reviews=_replace_review(current.get("suggested_candidate_reviews"), review)))
    return editor_state(published_id)


def accept_cluster(published_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Accept one bounded review action and atomically resolve all its signals."""

    match, current = get_published_match(published_id), load_shot_review_document(published_id)
    _ensure_revision(current, payload)
    cluster = _current_cluster(published_id, current, payload)
    preferred = _record(cluster.get("preferred_candidate"))
    digest = str(payload.get("candidate_generation_digest") or "")
    shot = _validate_shot(
        published_id,
        _record(match.get("public_report")),
        _record(payload.get("shot")),
        origin="accepted_suggestion",
        fallback_time=_number(preferred.get("time_sec")),
    )
    shot["suggested_candidate_id"] = str(preferred.get("candidate_id") or "")
    shot["suggested_candidate_generation_digest"] = digest
    shot["suggested_cluster_id"] = str(cluster.get("cluster_id") or "")
    shot["suggested_cluster_member_candidate_ids"] = list(cluster.get("member_candidate_ids") or [])
    reviews: list[dict[str, Any]] | Any = current.get("suggested_candidate_reviews") or []
    existing_reviews = _reviews_for_candidates(current, digest, cluster["member_candidates"])
    reviewed_at = _now()
    for candidate_id in cluster.get("member_candidate_ids") or []:
        # A prior candidate-level rejection is durable operator truth.  A
        # later cluster can resolve its remaining signals, but must not turn a
        # known rejection into acceptance merely because both signals are near
        # each other in time.
        if (existing_reviews.get(str(candidate_id)) or {}).get("review_status") == "rejected":
            continue
        reviews = _replace_review(reviews, {
            "candidate_id": str(candidate_id),
            "candidate_generation_digest": digest,
            "review_status": "accepted",
            "canonical_shot_id": shot["shot_id"],
            "cluster_id": shot["suggested_cluster_id"],
            "reviewed_at": reviewed_at,
        })
    _persist(published_id, _next_document(current, published_id, shots=[*current["canonical_shots"], shot], reviews=reviews))
    return {**editor_state(published_id), "accepted_shot": _canonical_dto(shot)}


def reject_cluster(published_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reject all raw signals in one bounded review action atomically."""

    current = load_shot_review_document(published_id)
    _ensure_revision(current, payload)
    cluster = _current_cluster(published_id, current, payload)
    digest = str(payload.get("candidate_generation_digest") or "")
    reviews: list[dict[str, Any]] | Any = current.get("suggested_candidate_reviews") or []
    existing_reviews = _reviews_for_candidates(current, digest, cluster["member_candidates"])
    reviewed_at = _now()
    for candidate_id in cluster.get("member_candidate_ids") or []:
        if (existing_reviews.get(str(candidate_id)) or {}).get("review_status") == "rejected":
            continue
        reviews = _replace_review(reviews, {
            "candidate_id": str(candidate_id),
            "candidate_generation_digest": digest,
            "review_status": "rejected",
            "cluster_id": str(cluster.get("cluster_id") or ""),
            "reviewed_at": reviewed_at,
        })
    _persist(published_id, _next_document(current, published_id, reviews=reviews))
    return editor_state(published_id)


def create_manual_shot(published_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    match, current = get_published_match(published_id), load_shot_review_document(published_id)
    _ensure_revision(current, payload)
    shot = _validate_shot(published_id, _record(match.get("public_report")), _record(payload.get("shot")), origin="manual")
    _persist(published_id, _next_document(current, published_id, shots=[*current["canonical_shots"], shot]))
    return {**editor_state(published_id), "saved_shot": _canonical_dto(shot)}


def edit_canonical_shot(published_id: str, shot_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    match, current = get_published_match(published_id), load_shot_review_document(published_id)
    _ensure_revision(current, payload)
    existing = next((row for row in current["canonical_shots"] if isinstance(row, dict) and str(row.get("shot_id") or "") == shot_id), None)
    if not isinstance(existing, dict):
        raise ShotReviewError("shot_review_shot_not_found", "Nie znaleziono zapisanego strzału.", 404)
    raw = _record(payload.get("shot"))
    # location_m is a canonical read model field.  It is intentionally ignored
    # for ordinary edits; #145 must send manual_location_override to express a
    # new operator point.  This makes a full-form round trip provenance-safe.
    next_time = raw.get("time_sec", existing.get("time_sec"))
    next_player = raw.get("player_id") if "player_id" in raw else existing.get("player_id")
    normalized = {**raw, "time_sec": next_time, "player_id": next_player}
    time_changed = _number(next_time) != _number(existing.get("time_sec"))
    player_changed = (str(next_player or "") or None) != (str(existing.get("player_id") or "") or None)
    if "manual_location_override" in raw:
        location_mode, override = "manual_override", raw.get("manual_location_override")
    elif not time_changed and not player_changed:
        location_mode, override = "preserve", None
    else:
        location_mode, override = "derive", None
    shot = _validate_shot(
        published_id,
        _record(match.get("public_report")),
        normalized,
        origin=str(existing.get("origin") or "manual"),
        existing=existing,
        location_mode=location_mode,
        manual_location_override=override,
    )
    shots = [shot if str(row.get("shot_id") or "") == shot_id else row for row in current["canonical_shots"]]
    _persist(published_id, _next_document(current, published_id, shots=shots))
    return {**editor_state(published_id), "saved_shot": _canonical_dto(shot)}


def delete_canonical_shot(published_id: str, shot_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    current = load_shot_review_document(published_id)
    _ensure_revision(current, payload)
    existing = next((row for row in current["canonical_shots"] if isinstance(row, dict) and str(row.get("shot_id") or "") == shot_id), None)
    shots = [row for row in current["canonical_shots"] if isinstance(row, dict) and str(row.get("shot_id") or "") != shot_id]
    if len(shots) == len(current["canonical_shots"]):
        raise ShotReviewError("shot_review_shot_not_found", "Nie znaleziono zapisanego strzału.", 404)
    reviews = current.get("suggested_candidate_reviews") or []
    if isinstance(existing, dict) and existing.get("origin") == "accepted_suggestion":
        # A cluster acceptance has one canonical shot but several accepted raw
        # candidate reviews.  Deleting that shot must resolve every linked row
        # together, otherwise a sibling would retain a dangling reference.
        linked_rows = [
            row for row in reviews
            if isinstance(row, dict)
            and row.get("review_status") == "accepted"
            and row.get("canonical_shot_id") == shot_id
        ]
        reviewed_at = _now()
        for linked in linked_rows:
            reviews = _replace_review(reviews, {
                "candidate_id": linked.get("candidate_id"),
                "candidate_generation_digest": linked.get("candidate_generation_digest"),
                "review_status": "rejected",
                "cluster_id": linked.get("cluster_id"),
                "reviewed_at": reviewed_at,
            })
    _persist(published_id, _next_document(current, published_id, shots=shots, reviews=reviews))
    return editor_state(published_id)
