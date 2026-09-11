from __future__ import annotations

"""Durable stats-only maintenance for canonical merged publications.

The service deliberately owns source ordering, preflight and publication
refresh.  A browser never has to chain physical rebuild endpoints.  It keeps
the data generation separate from the reviewed-video generation: changing a
current Reviewed Identity may make an existing render historical, but it does
not rewrite that render's provenance or request a new MP4.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import MATCHES_DIR
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_review_scope import identity_review_scope_digest
from app.services.identity_reviewed_output_jobs import JOB_FILENAME as REVIEWED_VIDEO_JOB_FILENAME
from app.services.identity_reviewed_snapshot import get_reviewed_identity_status
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_reviewed_video import reviewed_source_video_path
from app.services.json_publish_store import get_published_match, import_match_package
from app.services.match_group_video import reserve_match_group_video_idle
from app.services.match_groups import MATCH_GROUPS_DIR, MatchGroupError, get_match_group
from app.services.merged_public_match import (
    group_id_for_merged_published_id,
    is_merged_published_id,
    refresh_merged_match_to_latest,
)
from app.services.review_workflow_state import reviewed_stats_artifact_is_current
from app.services.reviewed_sprint_policy import reviewed_sprint_policy_matches_artifact
from app.services.published_video import sha256_file


JOB_FILENAME = "source_data_rebuild_job.json"
_submission_lock = threading.Lock()
_active_jobs: set[str] = set()
PackageBuilder = Callable[[Path], dict[str, Any]]


class SourceDataRebuildError(MatchGroupError):
    pass


def preview_merged_source_data_rebuild(merged_id: str) -> dict[str, Any]:
    group_id = _group_id_for_merged(merged_id)
    return _preflight(group_id, merged_id)


def get_merged_source_data_rebuild_status(merged_id: str) -> dict[str, Any]:
    group_id = _group_id_for_merged(merged_id)
    job = _load(_job_path(group_id))
    if not job:
        return {"status": "idle", "merged_published_match_id": merged_id, "group_id": group_id, "preflight": _preflight(group_id, merged_id)}
    if job.get("status") in {"queued", "running"} and not _job_worker_is_alive(job):
        job = _failed(
            job,
            code="job_worker_unavailable",
            detail="The previous source-data rebuild worker is no longer running. No merged refresh was completed.",
        )
        _write_job(group_id, job)
    return job


def submit_merged_source_data_rebuild(
    merged_id: str,
    *,
    package_builder: PackageBuilder,
) -> dict[str, Any]:
    """Persist a job and return immediately; the worker owns the group lock."""

    group_id = _group_id_for_merged(merged_id)
    with _submission_lock:
        existing = _load(_job_path(group_id))
        if existing.get("status") in {"queued", "running"}:
            if _job_worker_is_alive(existing):
                return existing
            _write_job(
                group_id,
                _failed(
                    existing,
                    code="job_worker_unavailable",
                    detail="The previous source-data rebuild worker is no longer running. No merged refresh was completed.",
                ),
            )
        preflight = _preflight(group_id, merged_id)
        if preflight["status"] == "blocked":
            return {"status": "blocked", "merged_published_match_id": merged_id, "group_id": group_id, "preflight": preflight}
        job = {
            "schema_version": "1.0.0",
            "job_id": f"source-data-rebuild-{uuid.uuid4().hex}",
            "status": "queued",
            "created_at": _now(),
            "worker_pid": os.getpid(),
            "merged_published_match_id": merged_id,
            "group_id": group_id,
            "preflight": preflight,
            "progress": _progress(phase="preflight", source_index=0, source_total=len(preflight["sources"])),
            "sources": preflight["sources"],
        }
        _write_job(group_id, job)
        _active_jobs.add(str(job["job_id"]))
        thread = threading.Thread(target=_run_job, args=(job, package_builder), daemon=True)
        try:
            thread.start()
        except Exception as error:  # pragma: no cover - defensive start failure
            failed = _failed(job, code="job_start_failed", detail=str(error))
            _write_job(group_id, failed)
            _active_jobs.discard(str(job["job_id"]))
            raise SourceDataRebuildError("job_start_failed", "Could not start the source-data rebuild worker.") from error
        return job


def run_merged_source_data_rebuild(
    merged_id: str,
    *,
    package_builder: PackageBuilder,
) -> dict[str, Any]:
    """Synchronous entry point for focused lifecycle tests and maintenance."""

    group_id = _group_id_for_merged(merged_id)
    preflight = _preflight(group_id, merged_id)
    if preflight["status"] == "blocked":
        return {"status": "blocked", "merged_published_match_id": merged_id, "group_id": group_id, "preflight": preflight}
    job = {
        "schema_version": "1.0.0",
        "job_id": f"source-data-rebuild-{uuid.uuid4().hex}",
        "status": "queued",
        "created_at": _now(),
        "worker_pid": os.getpid(),
        "merged_published_match_id": merged_id,
        "group_id": group_id,
        "preflight": preflight,
        "progress": _progress(phase="preflight", source_index=0, source_total=len(preflight["sources"])),
        "sources": preflight["sources"],
    }
    _write_job(group_id, job)
    return _run_job(job, package_builder)


def _run_job(job: dict[str, Any], package_builder: PackageBuilder) -> dict[str, Any]:
    group_id = str(job["group_id"])
    merged_id = str(job["merged_published_match_id"])
    try:
        with reserve_match_group_video_idle(group_id, operation="source-data-rebuild"):
            # Re-read under the same durable lock that excludes combined-video
            # generation and normal merged refresh.  A preview is advisory;
            # this pass is the authority before any mutation.
            preflight = _preflight(group_id, merged_id)
            if preflight["status"] == "blocked":
                blocked = {**job, "status": "blocked", "finished_at": _now(), "preflight": preflight, "sources": preflight["sources"]}
                _write_job(group_id, blocked)
                return blocked
            running = {
                **job,
                "status": "running",
                "started_at": _now(),
                "preflight": preflight,
                "sources": preflight["sources"],
                "progress": _progress(phase="preflight", source_index=0, source_total=len(preflight["sources"])),
            }
            _write_job(group_id, running)
            results: list[dict[str, Any]] = []
            for index, source in enumerate(preflight["sources"], start=1):
                running["progress"] = _progress(
                    phase="building_stats" if source["classification"] == "safe_stats_only" else "already_current",
                    source_index=index,
                    source_total=len(preflight["sources"]),
                    source_match_id=source["source_match_id"],
                    published_id=source["published_id"],
                )
                _write_job(group_id, running)
                try:
                    result = _rebuild_one_source(
                        source,
                        package_builder,
                        progress=lambda phase: _update_phase(group_id, running, phase, index, len(preflight["sources"]), source),
                    )
                except SourceDataRebuildError:
                    raise
                except Exception as error:
                    raise SourceDataRebuildError(
                        "source_rebuild_failed",
                        f"Could not rebuild physical source {source['source_match_id']}: {error}",
                        member=str(source["published_id"]),
                    ) from error
                results.append(result)
                running["sources"] = [*results, *preflight["sources"][index:]]
                _write_job(group_id, running)
            _update_phase(group_id, running, "refreshing_merged", len(preflight["sources"]), len(preflight["sources"]), None)
            refreshed = refresh_merged_match_to_latest(group_id)
            completed = {
                **running,
                "status": "completed",
                "completed_at": _now(),
                "sources": results,
                "merged": {
                    "status": "refreshed" if refreshed.get("status") == "refreshed" else "current",
                    "merged_published_match_id": merged_id,
                    "group_id": group_id,
                },
                "progress": _progress(phase="completed", source_index=len(preflight["sources"]), source_total=len(preflight["sources"])),
            }
            _write_job(group_id, completed)
            return completed
    except Exception as error:  # Preserve the prior publication and a durable failure state.
        code = error.code if isinstance(error, MatchGroupError) else "source_data_rebuild_failed"
        failed = _failed(
            job,
            code=code,
            detail=str(error),
            member=error.member if isinstance(error, MatchGroupError) else None,
        )
        _write_job(group_id, failed)
        return failed
    finally:
        _active_jobs.discard(str(job.get("job_id") or ""))


def _rebuild_one_source(source: dict[str, Any], package_builder: PackageBuilder, *, progress: Callable[[str], None]) -> dict[str, Any]:
    if source["classification"] == "already_current":
        return {**source, "result": "already_current"}
    match_path = MATCHES_DIR / str(source["source_match_id"])
    snapshot = get_reviewed_identity_status(match_path)
    meta = _load_required(match_path / "match.json")
    pitch = _load(match_path / "pitch_config.json")
    progress("building_stats")
    documents = build_reviewed_stats(match_path, snapshot, meta, pitch)
    _refresh_data_provenance(match_path, snapshot, meta, documents)
    progress("building_report")
    package = package_builder(match_path)
    validation = package.get("package_validation") if isinstance(package.get("package_validation"), dict) else {}
    if validation.get("status") == "blocked":
        missing = ", ".join(str(item) for item in validation.get("missing_required") or [])
        raise SourceDataRebuildError(
            "package_not_publishable",
            f"Rebuilt source package is not publishable: {missing or 'unknown prerequisite'}.",
            member=source["published_id"],
        )
    package_source = str((package.get("match") or {}).get("id") or "")
    if package_source != source["source_match_id"]:
        raise SourceDataRebuildError("package_source_mismatch", "Rebuilt package source identity does not match the physical publication.", member=source["published_id"])
    progress("publishing_source")
    published = import_match_package(package, replace=True)
    if str(published.get("id") or "") != source["published_id"]:
        raise SourceDataRebuildError("published_id_changed", "Stats-only rebuild changed a stable physical publication ID.", member=source["published_id"])
    return {**source, "result": "rebuilt"}


def _preflight(group_id: str, merged_id: str) -> dict[str, Any]:
    group = get_match_group(group_id)
    sources = [_preflight_source(member) for member in group.get("members") or [] if isinstance(member, dict)]
    blocked = [source for source in sources if source["classification"] == "blocked"]
    if not sources:
        return {
            "status": "blocked",
            "merged_published_match_id": merged_id,
            "group_id": group_id,
            "source_count": 0,
            "sources": [],
            "blocking_reasons": [{"code": "group_sources_missing", "detail": "The merged group has no ordered physical sources."}],
        }
    return {
        "status": "blocked" if blocked else "ready",
        "merged_published_match_id": merged_id,
        "group_id": group_id,
        "source_count": len(sources),
        "sources": sources,
        "blocking_reasons": [
            {"published_id": source["published_id"], "source_match_id": source["source_match_id"], "code": source.get("blocking_code"), "detail": source.get("blocking_reason")}
            for source in blocked
        ],
    }


def _preflight_source(member: dict[str, Any]) -> dict[str, Any]:
    published_id = str(member.get("published_id") or "")
    source_match_id = str(member.get("source_match_id") or "")
    result: dict[str, Any] = {
        "published_id": published_id,
        "source_match_id": source_match_id,
        "classification": "blocked",
        "derived_data_status": "unknown",
        "video_disposition": "unavailable",
        "qa_disposition": "unavailable",
    }
    if not published_id or not source_match_id or published_id != f"published-{source_match_id}":
        return _blocked(result, "physical_publication_binding_unproven", "Physical publication cannot be reliably linked to its local source.")
    try:
        published = get_published_match(published_id)
    except KeyError:
        return _blocked(result, "physical_publication_missing", "Physical publication is missing.")
    if str(published.get("source_kind") or "physical") != "physical":
        return _blocked(result, "physical_publication_required", "Merged publications cannot be a source of stats-only maintenance.")
    if str(published.get("source_match_id") or "") != source_match_id:
        return _blocked(result, "physical_publication_binding_unproven", "Published source identity does not match the group member.")
    match_path = MATCHES_DIR / source_match_id
    if not match_path.is_dir():
        return _blocked(result, "local_source_missing", "Local source match is missing.")
    meta = _load(match_path / "match.json")
    snapshot = get_reviewed_identity_status(match_path)
    result["reviewed_identity_status"] = snapshot.get("status")
    result["reviewed_identity_digest"] = snapshot.get("semantic_digest")
    if snapshot.get("status") in {"missing", "stale", "blocked"} or not snapshot.get("semantic_digest"):
        return _blocked(result, "reviewed_identity_not_current", "Current Reviewed Identity is missing, stale, or blocked.")
    progress = _load(match_path / "reviewed_identity_progress.json")
    if not progress:
        return _blocked(result, "review_progress_missing", "Required Reviewed Identity progress is missing or invalid.")
    readiness = progress.get("coverage_readiness") if isinstance(progress.get("coverage_readiness"), dict) else {}
    if progress.get("source_snapshot_digest") != snapshot.get("semantic_digest"):
        return _blocked(result, "review_progress_stale", "Required Reviewed Identity progress is not from the current canonical snapshot.")
    if progress.get("status") != "ready":
        return _blocked(result, "review_progress_not_ready", "Required Reviewed Identity progress is not ready for a stats-only rebuild.")
    progress_summary = progress.get("summary") if isinstance(progress.get("summary"), dict) else {}
    required_review_debt = sum(
        int(progress_summary.get(key) or 0)
        for key in (
            "important_decisions_remaining",
            "semantic_decisions_remaining",
            "coverage_decisions_remaining",
            "material_continuity_decisions_remaining",
            "structural_blockers",
        )
    )
    if required_review_debt > 0:
        return _blocked(result, "review_identity_incomplete", "Required Reviewed Identity work remains unresolved.")
    if readiness.get("allows_finalize") is not True:
        return _blocked(result, "review_identity_incomplete", "Required Reviewed Identity work remains unresolved.")
    job = _load(match_path / REVIEWED_VIDEO_JOB_FILENAME)
    try:
        # The cache-filling digest helper belongs to rendering. Maintenance
        # preflight remains fully read-only against operator-owned match state.
        raw_video_digest = sha256_file(reviewed_source_video_path(match_path, meta))
    except (FileNotFoundError, OSError, ValueError):
        return _blocked(result, "source_video_binding_unproven", "Current source-video binding cannot be proven.")
    if not job or str(job.get("source_video_digest") or "") != raw_video_digest:
        return _blocked(result, "source_video_binding_unproven", "Existing Review-video provenance does not prove the current source video.")
    video_digest = str(job.get("source_snapshot_digest") or "")
    if job.get("status") == "completed" and video_digest == snapshot.get("semantic_digest"):
        result["video_disposition"] = "current_preserved"
        result["qa_disposition"] = "current_preserved"
    elif job.get("status") == "completed" and video_digest:
        result["video_disposition"] = "historical_preserved"
        result["qa_disposition"] = "historical_preserved"
        result["review_video_identity_digest"] = video_digest
    stats = _load(match_path / "reviewed_player_stats.json")
    stats_readiness = _load(match_path / "reviewed_stats_readiness.json")
    local_current = reviewed_stats_artifact_is_current(
        stats,
        stats_readiness,
        snapshot_digest=str(snapshot["semantic_digest"]),
        match_doc=meta,
    )
    packaged_stats = ((published.get("package") or {}).get("reviewed_player_stats") or {})
    packaged_current = (
        local_current
        and packaged_stats.get("source_snapshot_digest") == snapshot.get("semantic_digest")
        and reviewed_sprint_policy_matches_artifact(packaged_stats)
    )
    result["derived_data_status"] = "current" if packaged_current else "stale"
    result["classification"] = "already_current" if packaged_current else "safe_stats_only"
    return result


def _refresh_data_provenance(
    match_path: Path,
    snapshot: dict[str, Any],
    meta: dict[str, Any],
    documents: dict[str, dict[str, Any]],
) -> None:
    """Advance data lineage only; visual/QA provenance remains immutable."""

    output_path = match_path / "reviewed_output_manifest.json"
    output = _load_required(output_path)
    snapshot_digest = str(snapshot["semantic_digest"])
    stats = documents["reviewed_player_stats.json"]
    readiness = documents["reviewed_stats_readiness.json"]
    output["reviewed_identity"] = {"status": "fresh", "digest": snapshot_digest}
    output["stats"] = {
        "status": str(readiness.get("status") or "completed"),
        "source_snapshot_digest": snapshot_digest,
        "source_review_scope_digest": identity_review_scope_digest(meta),
        "players": len(stats.get("players") or []),
    }
    output["data_generation"] = {
        "status": "current",
        "source_identity_digest": snapshot_digest,
        "maintenance": "stats_only",
    }
    visual = output.get("video") if isinstance(output.get("video"), dict) else {}
    visual_digest = str(visual.get("source_snapshot_digest") or "")
    output["review_video_generation"] = {
        "status": "current" if visual_digest == snapshot_digest else "historical",
        "source_identity_digest": visual_digest or None,
        "current_identity_digest": snapshot_digest,
    }
    write_identity_json_atomic(output_path, output)


def _update_phase(group_id: str, job: dict[str, Any], phase: str, index: int, total: int, source: dict[str, Any] | None) -> None:
    job["progress"] = _progress(
        phase=phase,
        source_index=index,
        source_total=total,
        source_match_id=str(source.get("source_match_id") or "") if source else None,
        published_id=str(source.get("published_id") or "") if source else None,
    )
    _write_job(group_id, job)


def _progress(*, phase: str, source_index: int, source_total: int, source_match_id: str | None = None, published_id: str | None = None) -> dict[str, Any]:
    return {
        "phase": phase,
        "source_index": source_index,
        "source_total": source_total,
        "source_match_id": source_match_id,
        "published_id": published_id,
        # No percentage: build_reviewed_stats has no honest unit callback.
    }


def _group_id_for_merged(merged_id: str) -> str:
    if not is_merged_published_id(merged_id):
        raise SourceDataRebuildError("not_a_merged_match", "Only merged published matches support source-data rebuild.")
    group_id = group_id_for_merged_published_id(merged_id)
    if not group_id:
        raise SourceDataRebuildError("backing_group_missing", "Merged publication has no authoritative backing group.")
    return group_id


def _job_path(group_id: str) -> Path:
    return MATCH_GROUPS_DIR / group_id / JOB_FILENAME


def _write_job(group_id: str, job: dict[str, Any]) -> None:
    write_identity_json_atomic(_job_path(group_id), job)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_required(path: Path) -> dict[str, Any]:
    value = _load(path)
    if not value:
        raise SourceDataRebuildError("required_artifact_missing", f"Required artifact {path.name} is missing or invalid.")
    return value


def _blocked(result: dict[str, Any], code: str, detail: str) -> dict[str, Any]:
    return {**result, "classification": "blocked", "blocking_code": code, "blocking_reason": detail}


def _failed(job: dict[str, Any], *, code: str, detail: str, member: str | None = None) -> dict[str, Any]:
    failure = {"code": code, "detail": detail}
    if member:
        failure["published_id"] = member
    return {**job, "status": "failed", "failed_at": _now(), "failure": failure, "progress": {**dict(job.get("progress") or {}), "phase": "failed"}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_worker_is_alive(job: dict[str, Any]) -> bool:
    """Avoid leaving a persisted running job forever after an app restart."""

    job_id = str(job.get("job_id") or "")
    if job_id and job_id in _active_jobs:
        return True
    worker_pid = job.get("worker_pid")
    if not isinstance(worker_pid, int) or worker_pid <= 0:
        return False
    try:
        os.kill(worker_pid, 0)
    except OSError:
        return False
    # A different process can reuse a PID; it cannot own this in-memory job.
    return worker_pid == os.getpid() and job_id in _active_jobs
