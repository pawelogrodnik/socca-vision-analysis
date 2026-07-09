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

PROGRESS_PLAN_VERSION = "0.1.0"
PROGRESS_STEPS: list[tuple[str, str, float, float]] = [
    ("queued", "Queued", 0.0, 5.0),
    ("starting", "Starting analysis", 5.0, 8.0),
    ("camera_motion", "Camera motion model", 8.0, 12.0),
    ("chunk_analyzing", "YOLO chunk analysis", 15.0, 80.0),
    ("chunk_merging", "Merge player tracks", 82.0, 86.0),
    ("chunk_ball_merge", "Merge ball observations", 86.0, 88.0),
    ("ball_tracking", "Build ball tracks", 88.0, 90.0),
    ("stabilization", "Player identity and stats", 90.0, 94.0),
    ("stable_overlay_render", "Render stable overlay", 94.0, 96.0),
    ("possession_pass_candidates", "Possession and pass candidates", 96.0, 98.0),
    ("stable_overlay_possession_render", "Render possession overlay", 98.0, 99.0),
    ("final_reports", "Final reports", 99.0, 100.0),
    ("completed", "Completed", 100.0, 100.0),
]
STAGE_TO_STEP_ID = {
    "chunk_resume": "chunk_analyzing",
    "chunk_analyzing": "chunk_analyzing",
    "chunk_merging": "chunk_merging",
    "chunk_ball_merge": "chunk_ball_merge",
    "ball_tracking": "ball_tracking",
    "stabilization": "stabilization",
    "stable_overlay_render": "stable_overlay_render",
    "possession_pass_candidates": "possession_pass_candidates",
    "stable_overlay_possession_render": "stable_overlay_possession_render",
    "final_reports": "final_reports",
}

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
    created_at = now_iso()
    document = {
        "schema_version": "0.1.0",
        "job_id": job_id,
        "match_id": match_id,
        "status": "queued",
        "stage": "queued",
        "progress_percent": 0.0,
        "message": "Analysis job queued.",
        "created_at": created_at,
        "updated_at": created_at,
        "started_at": None,
        "finished_at": None,
        "payload": payload,
        "result": None,
        "error": None,
        "progress_plan": _build_progress_plan("queued", created_at, message="Analysis job queued."),
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
        interrupted_at = now_iso()
        interrupted_stage = str(current.get("stage") or "starting")
        current.update(
            {
                "status": "failed",
                "stage": interrupted_stage,
                "message": "Analysis job was interrupted by backend restart.",
                "finished_at": interrupted_at,
                "updated_at": interrupted_at,
                "interrupted_stage": interrupted_stage,
                "error": {
                    "type": "BackendRestart",
                    "message": "Analysis job was interrupted by backend restart. Start analysis again to resume from completed chunks.",
                },
            }
        )
        _sync_progress_plan(current, timestamp=interrupted_at)
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
    updated_at = now_iso()
    current.update({key: value for key, value in updates.items() if value is not None})
    if extra:
        current.update(extra)
    current["updated_at"] = updated_at
    _sync_progress_plan(current, timestamp=updated_at, extra=extra if isinstance(extra, dict) else None)
    _write_job(match_path, current)
    return current


def _build_progress_plan(active_step_id: str, timestamp: str, *, message: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": PROGRESS_PLAN_VERSION,
        "active_step_id": active_step_id,
        "last_heartbeat_at": timestamp,
        "last_artifact_at": None,
        "active_step_elapsed_sec": 0.0,
        "current": None,
        "steps": [
            {
                "id": step_id,
                "label": label,
                "status": "running" if step_id == active_step_id else "pending",
                "progress_start": start,
                "progress_end": end,
                "started_at": timestamp if step_id == active_step_id else None,
                "finished_at": None,
                "message": message if step_id == active_step_id else None,
            }
            for step_id, label, start, end in PROGRESS_STEPS
        ],
    }


def _sync_progress_plan(document: dict[str, Any], *, timestamp: str, extra: dict[str, Any] | None = None) -> None:
    status = str(document.get("status") or "queued")
    stage = str(document.get("stage") or "queued")
    active_step_id = _step_id_for_stage(stage)
    if status == "completed":
        active_step_id = "completed"
    plan = document.get("progress_plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        plan = _build_progress_plan(active_step_id, timestamp, message=str(document.get("message") or ""))
        document["progress_plan"] = plan
    steps = [step for step in plan.get("steps", []) if isinstance(step, dict)]
    step_ids = [str(step.get("id")) for step in steps]
    if active_step_id not in step_ids:
        active_step_id = "starting" if status in {"queued", "running"} else "completed"
    active_index = step_ids.index(active_step_id) if active_step_id in step_ids else 0
    for index, step in enumerate(steps):
        step_id = str(step.get("id"))
        if status == "completed":
            step["status"] = "completed"
            step["started_at"] = step.get("started_at") or timestamp
            step["finished_at"] = step.get("finished_at") or timestamp
            continue
        if status == "failed":
            if index < active_index:
                step["status"] = "completed"
                step["finished_at"] = step.get("finished_at") or timestamp
            elif index == active_index:
                step["status"] = "failed"
                step["started_at"] = step.get("started_at") or timestamp
                step["finished_at"] = step.get("finished_at") or timestamp
            else:
                step["status"] = "pending"
            continue
        if index < active_index:
            step["status"] = "completed"
            step["finished_at"] = step.get("finished_at") or timestamp
        elif index == active_index:
            previous_status = step.get("status")
            step["status"] = "running"
            step["started_at"] = step.get("started_at") or timestamp
            step["finished_at"] = None
            step["message"] = document.get("message")
            if previous_status != "running":
                step["started_at"] = timestamp
        else:
            step["status"] = "pending"
    plan["active_step_id"] = active_step_id
    plan["last_heartbeat_at"] = timestamp
    plan["active_step_elapsed_sec"] = _elapsed_sec(_active_step(steps, active_step_id), timestamp)
    plan["current"] = _progress_current(document, extra)
    if extra and extra.get("artifact"):
        plan["last_artifact_at"] = timestamp


def _step_id_for_stage(stage: str) -> str:
    if stage in STAGE_TO_STEP_ID:
        return STAGE_TO_STEP_ID[stage]
    if stage in {step_id for step_id, _, _, _ in PROGRESS_STEPS}:
        return stage
    if stage == "failed":
        return "final_reports"
    return "starting"


def _active_step(steps: list[dict[str, Any]], active_step_id: str) -> dict[str, Any] | None:
    for step in steps:
        if step.get("id") == active_step_id:
            return step
    return None


def _elapsed_sec(step: dict[str, Any] | None, timestamp: str) -> float:
    if not step or not step.get("started_at"):
        return 0.0
    try:
        start = datetime.fromisoformat(str(step["started_at"]))
        end = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0.0
    return round(max(0.0, (end - start).total_seconds()), 1)


def _progress_current(document: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any] | None:
    source = extra or document
    keys = ("current", "total", "unit", "label")
    current = {key: source.get(key) for key in keys if source.get(key) is not None}
    if current:
        return current
    if document.get("chunk_count") is not None:
        return {"total": document.get("chunk_count"), "unit": "chunks"}
    return None


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
