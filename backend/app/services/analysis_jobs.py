from __future__ import annotations

import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


JobUpdater = Callable[[str, float, str, dict[str, Any] | None], None]
JobRunner = Callable[[str, JobUpdater], dict[str, Any]]

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis-job")
_LOCK = threading.Lock()
_JOB_MATCH_PATHS: dict[str, Path] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_analysis_job(
    *,
    match_id: str,
    match_path: Path,
    payload: dict[str, Any],
    runner: JobRunner,
) -> dict[str, Any]:
    job_id = f"analysis-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    document = {
        "schema_version": "0.1.0",
        "job_id": job_id,
        "match_id": match_id,
        "status": "queued",
        "stage": "queued",
        "progress_percent": 0.0,
        "message": "Analysis job queued.",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "payload": payload,
        "result": None,
        "error": None,
    }
    with _LOCK:
        _JOB_MATCH_PATHS[job_id] = match_path
    _write_job(match_path, document)
    _EXECUTOR.submit(_run_job, match_path, job_id, runner)
    return document


def list_analysis_jobs(match_path: Path) -> list[dict[str, Any]]:
    jobs_dir = _jobs_dir(match_path)
    if not jobs_dir.exists():
        return []
    rows = [_read_json(path) for path in sorted(jobs_dir.glob("*.json"), reverse=True)]
    return [row for row in rows if row is not None]


def load_analysis_job(matches_dir: Path, job_id: str) -> dict[str, Any]:
    with _LOCK:
        match_path = _JOB_MATCH_PATHS.get(job_id)
    if match_path:
        loaded = _read_json(_job_path(match_path, job_id))
        if loaded:
            return loaded
    for candidate in matches_dir.glob(f"*/analysis_jobs/{job_id}.json"):
        loaded = _read_json(candidate)
        if loaded:
            return loaded
    raise FileNotFoundError(f"Analysis job not found: {job_id}")


def mark_interrupted_analysis_jobs(matches_dir: Path) -> int:
    count = 0
    for job_path in matches_dir.glob("*/analysis_jobs/*.json"):
        current = _read_json(job_path)
        if not current or current.get("status") not in {"queued", "running"}:
            continue
        current.update(
            {
                "status": "failed",
                "stage": "interrupted",
                "progress_percent": 100.0,
                "message": "Analysis job was interrupted by backend restart.",
                "finished_at": now_iso(),
                "updated_at": now_iso(),
                "error": {
                    "type": "BackendRestart",
                    "message": "Analysis job was interrupted by backend restart. Start analysis again to resume from completed chunks.",
                },
            }
        )
        tmp_path = job_path.with_suffix(f"{job_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        tmp_path.replace(job_path)
        count += 1
    _sync_latest_analysis_job_statuses(matches_dir)
    return count


def _sync_latest_analysis_job_statuses(matches_dir: Path) -> None:
    for meta_path in matches_dir.glob("*/match.json"):
        meta = _read_json(meta_path)
        if not meta:
            continue
        job_id = meta.get("latest_analysis_job_id")
        if not job_id:
            continue
        job = _read_json(meta_path.parent / "analysis_jobs" / f"{job_id}.json")
        if not job:
            continue
        status = job.get("status")
        if not status or meta.get("analysis_job_status") == status:
            continue
        meta["analysis_job_status"] = status
        meta["updated_at"] = now_iso()
        tmp_path = meta_path.with_suffix(f"{meta_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        tmp_path.replace(meta_path)


def _run_job(match_path: Path, job_id: str, runner: JobRunner) -> None:
    _update_job(
        match_path,
        job_id,
        status="running",
        stage="starting",
        progress_percent=5.0,
        message="Analysis job started.",
        started_at=now_iso(),
    )

    def update(stage: str, progress_percent: float, message: str, extra: dict[str, Any] | None = None) -> None:
        _update_job(
            match_path,
            job_id,
            status="running",
            stage=stage,
            progress_percent=progress_percent,
            message=message,
            extra=extra,
        )

    try:
        result = runner(job_id, update)
        _update_job(
            match_path,
            job_id,
            status="completed",
            stage="completed",
            progress_percent=100.0,
            message="Analysis completed.",
            finished_at=now_iso(),
            result=_summarize_result(result),
            error=None,
        )
    except Exception as exc:  # pragma: no cover - tested through public behavior in service-level tests.
        _update_job(
            match_path,
            job_id,
            status="failed",
            stage="failed",
            progress_percent=100.0,
            message=str(exc),
            finished_at=now_iso(),
            error={
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(limit=8),
            },
        )


def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "analysis_type": result.get("analysis_type"),
        "run_id": result.get("run_id"),
        "generated_at": result.get("generated_at"),
        "frames_processed": result.get("frames_processed"),
        "tracks_count": result.get("tracks_count"),
        "stable_players_count": result.get("stable_players_count"),
        "run_directory": result.get("run_directory"),
        "run_manifest": result.get("run_manifest"),
        "artifacts": result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {},
    }


def _update_job(match_path: Path, job_id: str, **updates: Any) -> dict[str, Any]:
    path = _job_path(match_path, job_id)
    current = _read_json(path) or {}
    extra = updates.pop("extra", None)
    current.update({key: value for key, value in updates.items() if value is not None})
    if extra:
        current.update(extra)
    current["updated_at"] = now_iso()
    _write_job(match_path, current)
    return current


def _write_job(match_path: Path, document: dict[str, Any]) -> None:
    job_id = str(document["job_id"])
    path = _job_path(match_path, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _job_path(match_path: Path, job_id: str) -> Path:
    return _jobs_dir(match_path) / f"{job_id}.json"


def _jobs_dir(match_path: Path) -> Path:
    return match_path / "analysis_jobs"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None
