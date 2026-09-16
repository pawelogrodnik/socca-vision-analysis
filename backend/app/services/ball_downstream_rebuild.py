from __future__ import annotations

"""Explicit, digest-gated maintenance for ball-derived analytics.

Shot Review edits intentionally stop at ``resolved_ball_tracks.json``.  This
service is the sole opt-in boundary which turns that effective ball trajectory
into possession, contact, pass, restart and momentum candidates.
"""

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app import config
from app.services.analysis import load_pitch_config
from app.services.ball_event_rebuild import build_analytics_readiness
from app.services.ball_possession import build_ball_possession_analysis
from app.services.artifact_lineage import canonical_json_sha256
from app.services.effective_ball_tracks import EffectiveBallTracksError, load_effective_ball_tracks
from app.services.json_publish_store import get_published_match, import_match_package
from app.services.match_groups import MATCH_GROUPS_DIR, get_match_group, list_match_groups
from app.services.merged_public_match import group_id_for_merged_published_id, is_merged_published_id, refresh_merged_match_to_latest
from app.services.player_event_timeline import PlayerEventTimelineError, load_player_event_timeline


GENERATION_FILENAME = "ball_downstream_generation.json"
GENERATION_SCHEMA_VERSION = "ball-downstream-generation:v1"
GROUP_PUBLICATION_STATE_FILENAME = "ball_downstream_publication_state.json"
SOURCE_DOWNSTREAM_INPUT_SCHEMA_VERSION = "ball-downstream-source-input:v1"
DOWNSTREAM_FILENAMES = (
    "possession_candidates.json",
    "possession_segments.json",
    "contact_candidates.json",
    "event_candidates.json",
    "event_review_report.json",
    "restart_candidates.json",
    "pass_candidates.json",
    "pass_review_report.json",
    "attacking_momentum.json",
    "analytics_readiness.json",
    "possession_report.json",
)

PackageBuilder = Callable[[Path], dict[str, Any]]


class BallDownstreamRebuildError(ValueError):
    pass


def source_downstream_input_digest(
    ball_track_input_digest: str,
    player_event_timeline_digest: str,
) -> str:
    """Fingerprint every semantic source input consumed downstream.

    Physical freshness diagnostics and merged-publication snapshots call this
    same helper so a group cannot accidentally treat a player-only correction
    as current merely because its ball trajectory did not move.
    """

    return canonical_json_sha256({
        "schema_version": SOURCE_DOWNSTREAM_INPUT_SCHEMA_VERSION,
        "ball_track_input_digest": ball_track_input_digest,
        "player_event_timeline_digest": player_event_timeline_digest,
    })


def ball_downstream_status(published_match_id: str) -> dict[str, Any]:
    published = _published(published_match_id)
    sources = [_source_status(source) for source in _sources_for_published(published_match_id, published)]
    current = bool(sources) and all(source["status"] == "current" for source in sources)
    return {
        "published_match_id": published_match_id,
        "status": "current" if current else "stale",
        "source_count": len(sources),
        "sources": sources,
    }


def rebuild_ball_downstream_analytics(
    published_match_id: str,
    *,
    package_builder: PackageBuilder,
) -> dict[str, Any]:
    """Rebuild each physical source once and refresh its public projections.

    This intentionally does not run candidate generation, YOLO, automatic ball
    tracking, or any overlay rendering.  If every effective digest is already
    recorded, it is a complete no-op.
    """

    initial = ball_downstream_status(published_match_id)
    pending_sources = [row for row in initial["sources"] if not row["analytics_generation_current"] or not row["physical_publication_current"]]
    requested_group_id = group_id_for_merged_published_id(published_match_id) if is_merged_published_id(published_match_id) else None
    affected_groups = _affected_groups(initial["sources"], explicit_group_id=requested_group_id)
    pending_groups = [group for group in affected_groups if group["status"] != "current"]
    if not pending_sources and not pending_groups:
        return {**initial, "result": "already_current", "rebuilt_sources": []}

    rebuilt: list[dict[str, Any]] = []
    try:
        for source in pending_sources:
            match_path = config.MATCHES_DIR / source["source_match_id"]
            result: dict[str, Any] | None = None
            if not source["analytics_generation_current"]:
                result = rebuild_ball_downstream_for_match(match_path)
            if not source["physical_publication_current"]:
                package = package_builder(match_path)
                imported = import_match_package(package, replace=True)
                if str(imported.get("id") or "") != source["published_id"]:
                    raise BallDownstreamRebuildError("The physical publication ID changed during ball analytics rebuild.")
                _mark_physical_publication_current(match_path, source["published_id"])
            rebuilt.append({
                **source,
                "result": "rebuilt" if result is not None else "republished",
                "ball_track_input_digest": (result or source)["ball_track_input_digest"],
            })
    except Exception as error:
        # The generation marker is written in the same atomic family as its
        # artifacts. A failure therefore leaves the failed source visibly stale
        # instead of claiming that its old output used the new effective track.
        raise BallDownstreamRebuildError(str(error)) from error

    # A physical page may be a member of one or more merged reports.  Refresh
    # by dependency, never by the page that happened to start the operation.
    for group in affected_groups:
        # A merged read model depends on every physical member, not just the
        # source whose page started this maintenance request.  Snapshot the
        # complete effective input set before it is refreshed.
        _mark_group_publication_pending(group["group_id"])
    for group in affected_groups:
        refresh_merged_match_to_latest(group["group_id"])
        _mark_group_publication_current(group["group_id"])
    return {**ball_downstream_status(published_match_id), "result": "rebuilt", "rebuilt_sources": rebuilt}


def rebuild_ball_downstream_for_match(match_path: Path) -> dict[str, Any]:
    """Build the complete downstream family from the current effective track."""

    metadata = _read_object(match_path / "match.json", "match metadata")
    effective = load_effective_ball_tracks(match_path)
    players = load_player_event_timeline(match_path)
    pitch = load_pitch_config(match_path)
    result = build_ball_possession_analysis(
        match_path,
        match_path / "video.mp4",
        pitch,
        metadata.get("video") if isinstance(metadata.get("video"), dict) else {},
        effective.document,
        players.document,
        write_overlay_video=False,
        persist_artifacts=False,
    )
    documents = _downstream_documents(result)
    readiness = build_analytics_readiness(
        possession_doc=documents["possession_candidates.json"],
        pass_doc=documents["pass_candidates.json"],
        momentum_doc=documents["attacking_momentum.json"],
        trigger="operator_ball_analysis_rebuild",
    )
    documents["analytics_readiness.json"] = readiness
    if set(documents) != set(DOWNSTREAM_FILENAMES):
        missing = sorted(set(DOWNSTREAM_FILENAMES) - set(documents))
        unexpected = sorted(set(documents) - set(DOWNSTREAM_FILENAMES))
        raise BallDownstreamRebuildError(
            "Ball analytics rebuild output family mismatch: "
            f"missing={missing or 'none'}, unexpected={unexpected or 'none'}"
        )
    generation = {
        "schema_version": GENERATION_SCHEMA_VERSION,
        "generation_id": f"ball-downstream-{uuid.uuid4().hex}",
        "generated_at": _now_iso(),
        "trigger": "operator_ball_analysis_rebuild",
        "ball_track_input_digest": effective.input_digest,
        "ball_track_input_provenance": effective.provenance,
        "ball_track_input_artifact": effective.artifact,
        "player_event_timeline_digest": players.input_digest,
        "player_event_timeline_provenance": players.provenance,
        "player_event_timeline_artifact": players.artifact,
        "analytics_generation": {"status": "current"},
        "physical_publication": {"status": "pending", "published_id": None},
        "artifacts": sorted(documents),
        "write_overlay_video": False,
    }
    _atomic_write(match_path, documents, generation)
    return {"ball_track_input_digest": effective.input_digest, "provenance": effective.provenance, "artifacts": sorted(documents)}


def _sources_for_published(published_match_id: str, published: dict[str, Any]) -> list[dict[str, str]]:
    if not is_merged_published_id(published_match_id):
        source_id = str(published.get("source_match_id") or "")
        if not source_id:
            raise BallDownstreamRebuildError("Published physical match has no source match binding.")
        return [{"published_id": published_match_id, "source_match_id": source_id}]
    group_id = group_id_for_merged_published_id(published_match_id)
    if not group_id:
        raise BallDownstreamRebuildError("Merged publication has no backing group.")
    group = get_match_group(group_id)
    sources = [
        {"published_id": str(member.get("published_id") or ""), "source_match_id": str(member.get("source_match_id") or "")}
        for member in group.get("members") or [] if isinstance(member, dict)
    ]
    if not sources or any(not source["published_id"] or not source["source_match_id"] for source in sources):
        raise BallDownstreamRebuildError("Merged group has invalid physical source bindings.")
    return sources


def _affected_groups(
    sources: list[dict[str, Any]],
    *,
    explicit_group_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return deduplicated merged dependencies for the supplied physical rows."""

    by_group: dict[str, dict[str, Any]] = {}
    for source in sources:
        for group in _groups_for_source(source):
            by_group[str(group["group_id"])] = group
    if explicit_group_id:
        by_group[str(explicit_group_id)] = {"group_id": str(explicit_group_id)}
    for group_id, group in by_group.items():
        group["status"] = _group_publication_status(group_id)["status"]
    return [by_group[group_id] for group_id in sorted(by_group)]


def _groups_for_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    published_id = str(source["published_id"])
    source_match_id = str(source["source_match_id"])
    groups: list[dict[str, Any]] = []
    for group in list_match_groups():
        if not isinstance(group, dict):
            continue
        members = group.get("members") if isinstance(group.get("members"), list) else []
        if any(
            isinstance(member, dict)
            and str(member.get("published_id") or "") == published_id
            and str(member.get("source_match_id") or "") == source_match_id
            for member in members
        ):
            groups.append({"group_id": str(group.get("group_id") or "")})
    return [group for group in groups if group["group_id"]]


def _group_statuses_for_source(source: dict[str, str]) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for group in _groups_for_source(source):
        group_id = group["group_id"]
        # Do not infer freshness from the initiating source alone: another
        # group member can have changed while this source stayed untouched.
        statuses.append({"group_id": group_id, "status": _group_publication_status(group_id)["status"]})
    return statuses


def _mark_physical_publication_current(match_path: Path, published_id: str) -> None:
    generation = _read_object(match_path / GENERATION_FILENAME, "ball analytics generation")
    generation["physical_publication"] = {
        "status": "current",
        "published_id": published_id,
        "completed_at": _now_iso(),
    }
    _write_json_atomic(match_path / GENERATION_FILENAME, generation)


def acknowledge_ball_downstream_physical_publication(source_match_id: str, published_id: str) -> bool:
    """Acknowledge a successful normal physical publish when it is fresh.

    Normal analysis has already generated the downstream family.  Publishing
    that same physical package is the final lifecycle step; it must not force
    an operator to run the explicit maintenance action again.  Legacy or
    incomplete markers deliberately remain unacknowledged and therefore stale.
    """

    match_path = config.MATCHES_DIR / source_match_id
    generation = _read_optional_object(match_path / GENERATION_FILENAME)
    if not isinstance(generation, dict):
        return False
    try:
        effective = load_effective_ball_tracks(match_path)
        players = load_player_event_timeline(match_path)
    except (EffectiveBallTracksError, PlayerEventTimelineError):
        return False
    analytics = generation.get("analytics_generation")
    if (
        generation.get("ball_track_input_digest") != effective.input_digest
        or generation.get("player_event_timeline_digest") != players.input_digest
        or not isinstance(analytics, dict)
        or analytics.get("status") != "current"
    ):
        return False
    _mark_physical_publication_current(match_path, published_id)
    return True


def _current_group_source_input_digests(group_id: str) -> dict[str, str]:
    group = get_match_group(group_id)
    members = group.get("members") if isinstance(group.get("members"), list) else []
    digests: dict[str, str] = {}
    for member in members:
        if not isinstance(member, dict):
            continue
        published_id = str(member.get("published_id") or "")
        source_match_id = str(member.get("source_match_id") or "")
        if not published_id or not source_match_id or published_id in digests:
            raise BallDownstreamRebuildError(f"Merged group {group_id} has invalid physical source bindings.")
        try:
            match_path = config.MATCHES_DIR / source_match_id
            effective = load_effective_ball_tracks(match_path)
            players = load_player_event_timeline(match_path)
            digests[published_id] = source_downstream_input_digest(effective.input_digest, players.input_digest)
        except (EffectiveBallTracksError, PlayerEventTimelineError) as error:
            raise BallDownstreamRebuildError(f"{source_match_id}: {error}") from error
    if not digests:
        raise BallDownstreamRebuildError(f"Merged group {group_id} has no physical source bindings.")
    return digests


def _group_publication_status(group_id: str) -> dict[str, Any]:
    state = _read_optional_object(_group_publication_state_path(group_id))
    current_digests = _current_group_source_input_digests(group_id)
    stored_digests = state.get("source_input_digests") if isinstance(state, dict) else None
    current = (
        isinstance(state, dict)
        and state.get("status") == "current"
        and isinstance(stored_digests, dict)
        and {str(key): str(value) for key, value in stored_digests.items()} == current_digests
    )
    return {"status": "current" if current else "pending", "source_input_digests": current_digests}


def _mark_group_publication_pending(group_id: str) -> None:
    digests = _current_group_source_input_digests(group_id)
    _write_json_atomic(_group_publication_state_path(group_id), {
        "schema_version": "ball-downstream-publication-state:v1",
        "group_id": group_id,
        "status": "pending",
        "source_input_digests": digests,
        "updated_at": _now_iso(),
    })


def _mark_group_publication_current(group_id: str) -> None:
    path = _group_publication_state_path(group_id)
    current = _read_object(path, "ball analytics group publication state")
    # Capture every member again after a successful refresh.  The state is a
    # content snapshot, never merely a success flag for the initiating source.
    current["source_input_digests"] = _current_group_source_input_digests(group_id)
    current["status"] = "current"
    current["completed_at"] = _now_iso()
    _write_json_atomic(path, current)


def _group_publication_state_path(group_id: str) -> Path:
    return MATCH_GROUPS_DIR / group_id / GROUP_PUBLICATION_STATE_FILENAME


def _source_status(source: dict[str, str]) -> dict[str, Any]:
    match_path = config.MATCHES_DIR / source["source_match_id"]
    try:
        effective = load_effective_ball_tracks(match_path)
        players = load_player_event_timeline(match_path)
    except (EffectiveBallTracksError, PlayerEventTimelineError) as error:
        raise BallDownstreamRebuildError(f"{source['source_match_id']}: {error}") from error
    generation = _read_optional_object(match_path / GENERATION_FILENAME)
    recorded_digest = generation.get("ball_track_input_digest") if generation else None
    recorded_player_digest = generation.get("player_event_timeline_digest") if generation else None
    current_source_input_digest = source_downstream_input_digest(effective.input_digest, players.input_digest)
    recorded_source_input_digest = (
        source_downstream_input_digest(str(recorded_digest), str(recorded_player_digest))
        if isinstance(recorded_digest, str) and isinstance(recorded_player_digest, str)
        else None
    )
    analytics_current = (
        recorded_source_input_digest == current_source_input_digest
        and isinstance((generation or {}).get("analytics_generation"), dict)
        and (generation or {})["analytics_generation"].get("status") == "current"
    )
    physical = (generation or {}).get("physical_publication")
    physical_current = (
        analytics_current
        and isinstance(physical, dict)
        and physical.get("status") == "current"
        and str(physical.get("published_id") or "") == source["published_id"]
    )
    groups = _group_statuses_for_source(source)
    groups_current = all(group["status"] == "current" for group in groups)
    return {
        **source,
        "status": "current" if analytics_current and physical_current and groups_current else "stale",
        "current_effective_ball_track_digest": effective.input_digest,
        "ball_track_input_digest": recorded_digest,
        "current_source_downstream_input_digest": current_source_input_digest,
        "recorded_source_downstream_input_digest": recorded_source_input_digest,
        "provenance": effective.provenance,
        "player_event_timeline_digest": players.input_digest,
        "recorded_player_event_timeline_digest": recorded_player_digest,
        "player_event_timeline_provenance": players.provenance,
        "analytics_generation_current": analytics_current,
        "physical_publication_current": physical_current,
        "merged_publications": groups,
    }


def _downstream_documents(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    keys = {
        "possession_candidates": "possession_candidates.json",
        "possession_segments": "possession_segments.json",
        "contact_candidates": "contact_candidates.json",
        "event_candidates": "event_candidates.json",
        "event_review_report": "event_review_report.json",
        "restart_candidates": "restart_candidates.json",
        "pass_candidates": "pass_candidates.json",
        "pass_review_report": "pass_review_report.json",
        "attacking_momentum": "attacking_momentum.json",
        "possession_report": "possession_report.json",
    }
    documents = {filename: dict(result[key]) for key, filename in keys.items() if isinstance(result.get(key), dict)}
    if len(documents) != len(keys):
        missing = sorted(set(keys.values()) - set(documents))
        raise BallDownstreamRebuildError(f"Ball analytics rebuild did not produce: {', '.join(missing)}")
    return documents


def _atomic_write(match_path: Path, documents: dict[str, dict[str, Any]], generation: dict[str, Any]) -> None:
    # Keep the generation marker in the same replacement transaction as every
    # downstream artifact. It is the freshness authority, never a best-effort
    # follow-up write.
    stage = Path(tempfile.mkdtemp(prefix=".ball-downstream-stage-", dir=match_path))
    backup = Path(tempfile.mkdtemp(prefix=".ball-downstream-backup-", dir=match_path))
    replaced: list[str] = []
    backed_up: list[str] = []
    try:
        for filename, document in documents.items():
            (stage / filename).write_text(json.dumps(document, indent=2), encoding="utf-8")
        (stage / GENERATION_FILENAME).write_text(json.dumps(generation, indent=2), encoding="utf-8")
        for filename in [*sorted(documents), GENERATION_FILENAME]:
            target = match_path / filename
            if target.exists():
                os.replace(target, backup / filename)
                backed_up.append(filename)
            os.replace(stage / filename, target)
            replaced.append(filename)
    except Exception:
        for filename in reversed(replaced):
            (match_path / filename).unlink(missing_ok=True)
        for filename in reversed(backed_up):
            backup_file = backup / filename
            if backup_file.exists():
                os.replace(backup_file, match_path / filename)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def _published(published_match_id: str) -> dict[str, Any]:
    try:
        return get_published_match(published_match_id)
    except KeyError as error:
        raise BallDownstreamRebuildError("Published match not found.") from error


def _read_object(path: Path, label: str) -> dict[str, Any]:
    document = _read_optional_object(path)
    if document is None:
        raise BallDownstreamRebuildError(f"Missing or invalid {label}.")
    return document


def _read_optional_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
    temporary.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
