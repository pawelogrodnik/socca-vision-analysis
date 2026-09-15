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
from app.services.effective_ball_tracks import EffectiveBallTracksError, load_effective_ball_tracks
from app.services.json_publish_store import get_published_match, import_match_package
from app.services.match_groups import get_match_group
from app.services.merged_public_match import group_id_for_merged_published_id, is_merged_published_id, refresh_merged_match_to_latest


GENERATION_FILENAME = "ball_downstream_generation.json"
GENERATION_SCHEMA_VERSION = "ball-downstream-generation:v1"
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

    published = _published(published_match_id)
    initial = ball_downstream_status(published_match_id)
    stale_sources = [row for row in initial["sources"] if row["status"] == "stale"]
    if not stale_sources:
        return {**initial, "result": "already_current", "rebuilt_sources": []}

    rebuilt: list[dict[str, Any]] = []
    try:
        for source in stale_sources:
            match_path = config.MATCHES_DIR / source["source_match_id"]
            result = rebuild_ball_downstream_for_match(match_path)
            package = package_builder(match_path)
            imported = import_match_package(package, replace=True)
            if str(imported.get("id") or "") != source["published_id"]:
                raise BallDownstreamRebuildError("The physical publication ID changed during ball analytics rebuild.")
            rebuilt.append({**source, "result": "rebuilt", "ball_track_input_digest": result["ball_track_input_digest"]})
    except Exception as error:
        # The generation marker is written in the same atomic family as its
        # artifacts. A failure therefore leaves the failed source visibly stale
        # instead of claiming that its old output used the new effective track.
        raise BallDownstreamRebuildError(str(error)) from error

    if is_merged_published_id(published_match_id):
        group_id = group_id_for_merged_published_id(published_match_id)
        if not group_id:
            raise BallDownstreamRebuildError("Merged publication has no backing group.")
        refresh_merged_match_to_latest(group_id)
    return {**ball_downstream_status(published_match_id), "result": "rebuilt", "rebuilt_sources": rebuilt}


def rebuild_ball_downstream_for_match(match_path: Path) -> dict[str, Any]:
    """Build the complete downstream family from the current effective track."""

    metadata = _read_object(match_path / "match.json", "match metadata")
    stable_players = _read_optional_object(match_path / "stable_players.json") or {"players": []}
    effective = load_effective_ball_tracks(match_path)
    pitch = load_pitch_config(match_path)
    result = build_ball_possession_analysis(
        match_path,
        match_path / "video.mp4",
        pitch,
        metadata.get("video") if isinstance(metadata.get("video"), dict) else {},
        effective.document,
        stable_players,
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


def _source_status(source: dict[str, str]) -> dict[str, Any]:
    match_path = config.MATCHES_DIR / source["source_match_id"]
    try:
        effective = load_effective_ball_tracks(match_path)
    except EffectiveBallTracksError as error:
        raise BallDownstreamRebuildError(f"{source['source_match_id']}: {error}") from error
    generation = _read_optional_object(match_path / GENERATION_FILENAME)
    recorded_digest = generation.get("ball_track_input_digest") if generation else None
    return {
        **source,
        "status": "current" if recorded_digest == effective.input_digest else "stale",
        "current_effective_ball_track_digest": effective.input_digest,
        "ball_track_input_digest": recorded_digest,
        "provenance": effective.provenance,
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
