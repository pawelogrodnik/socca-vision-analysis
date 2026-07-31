from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_reviewed_video import render_reviewed_video, reviewed_source_video_digest


JOB_FILENAME = "reviewed_video_job.json"
_locks: dict[str, threading.Lock] = {}
_active_job_keys: set[str] = set()


def generate_reviewed_output(match_path: Path, snapshot: dict[str, Any], match_doc: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    source_video_digest = reviewed_source_video_digest(match_path, match_doc)
    key = canonical_digest({"snapshot": snapshot["semantic_digest"], "source_video": source_video_digest, "options": options, "renderer_version": "reviewed_video:v3"})
    existing = _load(match_path / JOB_FILENAME)
    if existing.get("job_key") == key and existing.get("status") in {"queued", "running", "completed"}:
        return existing
    job = {"schema_version": "1.0.0", "job_key": key, "status": "queued", "created_at": _now(), "options": options, "source_snapshot_digest": snapshot["semantic_digest"], "source_video_digest": source_video_digest, "error": None}
    write_identity_json_atomic(match_path / JOB_FILENAME, job)
    lock = _locks.setdefault(str(match_path), threading.Lock())
    _active_job_keys.add(str(job["job_key"]))
    thread = threading.Thread(target=_run, args=(lock, match_path, snapshot, match_doc, options, job), daemon=True)
    thread.start()
    return job


def reviewed_output_status(match_path: Path, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    job = _load(match_path / JOB_FILENAME)
    if not job:
        return {"status": "missing"}
    if snapshot and snapshot.get("status") == "stale":
        return {**job, "status": "stale", "stale_reason": "reviewed_identity_changed"}
    if snapshot and job.get("source_snapshot_digest") != snapshot.get("semantic_digest") and job.get("status") == "completed":
        return {**job, "status": "stale", "stale_reason": "reviewed_identity_changed"}
    if job.get("status") in {"queued", "running"} and str(job.get("job_key") or "") not in _active_job_keys:
        interrupted = {**job, "status": "failed", "failed_at": _now(), "error": {"message": "Render został przerwany; uruchom go ponownie.", "developer_detail": "No active local renderer owns this persisted job."}}
        write_identity_json_atomic(match_path / JOB_FILENAME, interrupted)
        return interrupted
    return job


def _run(lock: threading.Lock, path: Path, snapshot: dict[str, Any], match_doc: dict[str, Any], options: dict[str, Any], job: dict[str, Any]) -> None:
    if not lock.acquire(blocking=False):
        _active_job_keys.discard(str(job["job_key"]))
        return
    try:
        running={**job,"status":"running","started_at":_now()}; write_identity_json_atomic(path/JOB_FILENAME,running)
        stats=build_reviewed_stats(path,snapshot,match_doc,_load(path/"pitch_config.json"))
        manifest=render_reviewed_video(path,snapshot,match_doc,include_minimap=bool(options.get("include_minimap",True)),include_ball=bool(options.get("include_ball",True)),show_roster_number=bool(options.get("show_roster_number",False)))
        output={**running,"status":"completed","completed_at":_now(),"video_manifest":"reviewed_video_manifest.json","stats_readiness":"reviewed_stats_readiness.json"}; write_identity_json_atomic(path/JOB_FILENAME,output)
        write_identity_json_atomic(path/"reviewed_output_manifest.json",{"schema_version":"1.0.0","match_id":snapshot.get("match_id"),"reviewed_identity":{"status":"fresh","digest":snapshot["semantic_digest"]},"video":{"status":"completed","path":"reviewed_video.mp4","digest":manifest["digest"],"source_snapshot_digest":snapshot["semantic_digest"]},"minimap":manifest["minimap"],"stats":{"status":"completed","source_snapshot_digest":snapshot["semantic_digest"],"players":len(stats["reviewed_player_stats.json"].get("players") or [])},"stale":False,"safety":manifest["safety"]})
    except Exception as exc:
        write_identity_json_atomic(path/JOB_FILENAME,{**job,"status":"failed","failed_at":_now(),"error":{"message":"Nie udało się wygenerować reviewed video.","developer_detail":f"{type(exc).__name__}: {exc}"}})
    finally: lock.release()
    _active_job_keys.discard(str(job["job_key"]))


def _load(path: Path) -> dict[str, Any]:
    import json
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
def _now() -> str: return datetime.now(timezone.utc).isoformat()
