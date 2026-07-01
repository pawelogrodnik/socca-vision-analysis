from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import ADMIN_IMPORT_TOKEN, APP_MODE, CORS_ORIGINS, MATCHES_DIR, PUBLISH_TARGET
from app.models import AnalyzePayload, BallAnalyzePayload, MatchMetadataPayload, PitchConfigPayload
from app.services.analysis import analyze_match, analyze_match_ball_yolo
from app.services.analysis_jobs import list_analysis_jobs, load_analysis_job, start_analysis_job
from app.services.change_candidates import load_change_candidates_review, save_change_candidate_reviews
from app.services.chunked_analysis import analyze_match_chunked_yolo
from app.services.contact_review import load_contact_candidates_review, save_contact_candidate_reviews
from app.services.database import database_health, delete_published_match, get_published_match, import_match_package, init_db, list_published_matches
from app.services.identity import build_identity_review, save_identity_assignments
from app.services.match_phase_config import load_match_phase_config, save_match_phase_config
from app.services.pass_review import load_pass_candidates_review, save_pass_candidate_reviews
from app.services.player_identity import build_player_identity_review, save_player_identity_assignments
from app.services.player_profiles import build_player_profile_stats
from app.services.publish import PublishError, publish_match_package
from app.services.resolved_player_stats import build_resolved_player_stats_from_files
from app.services.runtime import build_performance_report, collect_runtime_info, normalize_yolo_device
from app.services.stabilization import load_stable_review, load_team_config_review, save_stable_review, save_team_config_review
from app.services.team_profiles import build_team_profile_stats
from app.services.team_registry import create_team as registry_create_team
from app.services.team_registry import delete_team as registry_delete_team
from app.services.team_registry import get_team as registry_get_team
from app.services.team_registry import list_teams as registry_list_teams
from app.services.team_registry import update_team as registry_update_team
from app.services.video import extract_frame, read_video_metadata

app = FastAPI(title="Orlik Vision API", version="0.6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "item"


def require_admin_import_token(authorization: str | None) -> None:
    if not ADMIN_IMPORT_TOKEN:
        return
    expected = f"Bearer {ADMIN_IMPORT_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing admin import token")


def with_generated_ids(metadata: dict[str, Any]) -> dict[str, Any]:
    """Ensure teams and players have stable IDs before writing match metadata."""
    teams = []
    for team_idx, team in enumerate(metadata.get("teams") or []):
        team = dict(team)
        team_id = team.get("id") or f"team-{team_idx + 1}-{slugify(str(team.get('name') or 'team'))}"
        team["id"] = team_id
        players = []
        for player_idx, player in enumerate(team.get("players") or []):
            player = dict(player)
            player["id"] = player.get("id") or f"{team_id}-player-{player_idx + 1}-{slugify(str(player.get('name') or 'player'))}"
            players.append(player)
        team["players"] = players
        teams.append(team)
    metadata["teams"] = teams
    return metadata


def read_match_meta(path: Path) -> dict[str, Any]:
    meta_path = path / "match.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="match.json not found")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def write_match_meta(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    meta["updated_at"] = now_iso()
    (path / "match.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def match_dir(match_id: str) -> Path:
    path = MATCHES_DIR / match_id
    if not path.exists():
        raise HTTPException(status_code=404, detail="Match not found")
    return path


def match_video_path(path: Path) -> Path:
    for candidate in path.glob("video.*"):
        return candidate
    raise HTTPException(status_code=404, detail="Video file not found")


def parse_metadata_form(
    *,
    title: str,
    match_date: str | None,
    season: str | None,
    venue: str | None,
    format: str,
    teams_json: str | None,
) -> dict[str, Any]:
    teams: list[dict[str, Any]] = []
    if teams_json:
        try:
            loaded = json.loads(teams_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid teams_json: {exc}") from exc
        if not isinstance(loaded, list):
            raise HTTPException(status_code=400, detail="teams_json must be a JSON array")
        teams = loaded
    payload = MatchMetadataPayload(
        title=title,
        match_date=match_date or None,
        season=season or None,
        venue=venue or None,
        format=format or "7v7",
        status="uploaded",
        teams=teams,
    )
    return with_generated_ids(payload.model_dump())


def load_tracks(path: Path) -> list[dict[str, Any]]:
    tracks_path = path / "tracks.json"
    if not tracks_path.exists():
        raise HTTPException(status_code=404, detail="tracks.json not found. Run analysis first.")
    tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
    if not isinstance(tracks, list):
        raise HTTPException(status_code=400, detail="tracks.json must contain a list")
    return tracks


def summarize_track(track: dict[str, Any]) -> dict[str, Any]:
    positions = track.get("positions") if isinstance(track.get("positions"), list) else []
    confidences = [float(pos.get("confidence")) for pos in positions if isinstance(pos, dict) and pos.get("confidence") is not None]
    first = positions[0] if positions else {}
    last = positions[-1] if positions else {}
    return {
        "tracklet_id": int(track.get("track_id")),
        "start_time_sec": track.get("start_time_sec"),
        "end_time_sec": track.get("end_time_sec"),
        "duration_sec": track.get("duration_sec"),
        "positions_count": track.get("positions_count", len(positions)),
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "first_pitch_m": first.get("pitch_m") if isinstance(first, dict) else None,
        "last_pitch_m": last.get("pitch_m") if isinstance(last, dict) else None,
        "first_bbox_xyxy": first.get("bbox_xyxy") if isinstance(first, dict) else None,
        "last_bbox_xyxy": last.get("bbox_xyxy") if isinstance(last, dict) else None,
    }


def summarize_analysis_run(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": report.get("run_id"),
        "status": report.get("status"),
        "analysis_type": report.get("analysis_type"),
        "generated_at": report.get("generated_at"),
        "frames_processed": report.get("frames_processed"),
        "tracks_count": report.get("tracks_count"),
        "stable_players_count": report.get("stable_players_count"),
        "parameters": report.get("parameters") or {},
        "run_directory": report.get("run_directory"),
        "run_manifest": report.get("run_manifest"),
    }


def run_match_analysis_and_update_meta(
    *,
    match_id: str,
    path: Path,
    video_path: Path,
    payload: AnalyzePayload,
    job_id: str | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    if progress:
        progress("preparing", 8.0, "Preparing analysis inputs.", None)
    started_at = time.perf_counter()
    if payload.chunked:
        report = analyze_match_chunked_yolo(
            path,
            video_path,
            payload=payload.model_dump(),
            job_id=job_id,
            progress=progress,
        )
    else:
        if progress:
            progress("analyzing", 20.0, "Running video analysis.", None)
        report = analyze_match(
            path,
            video_path,
            adapter=payload.adapter,  # type: ignore[arg-type]
            max_seconds=payload.max_seconds,
            frame_stride=max(1, payload.frame_stride),
            yolo_model=payload.yolo_model,
            yolo_conf=payload.yolo_conf,
            yolo_imgsz=payload.yolo_imgsz,
            yolo_tracker=payload.yolo_tracker,
            yolo_device=payload.yolo_device,
        )
    elapsed_wall_sec = time.perf_counter() - started_at
    report_runtime = report.get("runtime") if isinstance(report.get("runtime"), dict) else collect_runtime_info()
    report_parameters = report.get("parameters") if isinstance(report.get("parameters"), dict) else {}
    normalized_device = str(report_parameters.get("yolo_device") or "")
    if normalized_device == "auto":
        normalized_device = normalize_yolo_device(payload.yolo_device) or "auto"
    performance_report = build_performance_report(
        label=f"{match_id}-{payload.adapter}",
        requested_device=payload.yolo_device,
        normalized_device=normalized_device,
        elapsed_wall_sec=elapsed_wall_sec,
        analysis_report=report,
        runtime_info=report_runtime,
    )
    report["performance_report"] = performance_report
    (path / "performance_report.json").write_text(json.dumps(performance_report, indent=2), encoding="utf-8")
    report = attach_analysis_artifact_to_report(path, report, key="performance_report", filename="performance_report.json")
    if progress:
        progress("finalizing", 95.0, "Updating match metadata.", None)
    meta = read_match_meta(path)
    if report.get("status") == "completed":
        meta["status"] = "analyzed"
    run_summary = summarize_analysis_run(report)
    if run_summary.get("run_id"):
        existing_runs = [item for item in meta.get("analysis_runs", []) if isinstance(item, dict)]
        existing_runs = [item for item in existing_runs if item.get("run_id") != run_summary["run_id"]]
        meta["analysis_runs"] = [run_summary, *existing_runs][:30]
        meta["latest_analysis_run_id"] = run_summary["run_id"]
    meta["latest_analysis_job_id"] = job_id or meta.get("latest_analysis_job_id")
    if job_id:
        meta["analysis_job_status"] = "completed" if report.get("status") == "completed" else str(report.get("status") or "finished")
    meta["updated_at"] = now_iso()
    write_match_meta(path, meta)
    return report


def attach_analysis_artifact_to_report(path: Path, report: dict[str, Any], *, key: str, filename: str) -> dict[str, Any]:
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), dict) else {}
    artifacts[key] = filename
    report["artifacts"] = artifacts
    run_directory = report.get("run_directory")
    if run_directory:
        run_dir = path / str(run_directory)
        run_dir.mkdir(parents=True, exist_ok=True)
        source = path / filename
        if source.exists() and source.is_file():
            shutil.copy2(source, run_dir / Path(filename).name)
        run_artifacts = report.get("run_artifacts") if isinstance(report.get("run_artifacts"), dict) else {}
        run_artifacts[key] = f"{run_directory}/{Path(filename).name}"
        report["run_artifacts"] = run_artifacts
        manifest_path = path / str(report.get("run_manifest") or "")
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                manifest["artifacts"] = artifacts
                manifest["run_artifacts"] = run_artifacts
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (run_dir / "analysis_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (path / "analysis_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def default_assignments_for_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tracklet_id": int(track.get("track_id")),
            "status": "unassigned",
            "team_id": None,
            "player_id": None,
            "notes": "",
        }
        for track in tracks
        if track.get("track_id") is not None
    ]


def load_player_assignments(path: Path, tracks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    assignment_path = path / "player_assignments.json"
    if assignment_path.exists():
        data = json.loads(assignment_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    if tracks is None:
        tracks = load_tracks(path)
    return {
        "schema_version": "0.1.0",
        "updated_at": now_iso(),
        "assignments": default_assignments_for_tracks(tracks),
        "summary": {},
    }


PACKAGE_REQUIRED_KEYS = [
    "analysis_report",
    "stable_players",
    "player_identity_assignments",
    "resolved_player_stats",
    "team_config",
    "team_stats",
]

PACKAGE_OPTIONAL_KEYS = [
    "pitch_config",
    "performance_report",
    "analysis_chunk_manifest",
    "global_identity",
    "global_identity_report",
    "analysis_quality_report",
    "stabilization_report",
    "team_clusters",
    "frame_detection_counts",
    "movement_stats",
    "player_stats",
    "player_heatmaps",
    "change_candidates",
    "change_review_report",
    "ball_tracks",
    "ball_analysis_report",
    "ball_tracking_report",
    "ball_quality_report",
    "possession_candidates",
    "possession_segments",
    "contact_candidates",
    "match_phase_config",
    "event_candidates",
    "event_review_report",
    "pass_candidates",
    "pass_review_report",
    "possession_report",
]

PACKAGE_DEBUG_KEYS = [
    "player_assignments",
    "identity_candidates",
    "identity_assignments",
    "tracklets",
    "tracking_quality_report",
]


def _has_assigned_real_player(identity_doc: dict[str, Any] | None) -> bool:
    if not isinstance(identity_doc, dict):
        return False
    for assignment in identity_doc.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        if assignment.get("status") == "assigned" and assignment.get("player_id"):
            return True
    return False


def build_package_validation(package: dict[str, Any]) -> dict[str, Any]:
    missing_required = [key for key in PACKAGE_REQUIRED_KEYS if package.get(key) is None]
    warnings: list[str] = []
    analysis_report = package.get("analysis_report") if isinstance(package.get("analysis_report"), dict) else None
    if analysis_report and analysis_report.get("status") != "completed":
        missing_required.append("analysis_report.status_completed")
    identity_doc = package.get("player_identity_assignments") if isinstance(package.get("player_identity_assignments"), dict) else None
    if identity_doc and not _has_assigned_real_player(identity_doc):
        warnings.append("No real roster player is assigned. This is allowed, but player profile aggregation will be empty.")
    summary = identity_doc.get("summary") if isinstance(identity_doc, dict) and isinstance(identity_doc.get("summary"), dict) else {}
    conflicts_total = int(summary.get("conflicts_total") or 0) if isinstance(summary, dict) else 0
    if conflicts_total > 0:
        warnings.append(f"Player identity review contains {conflicts_total} conflict(s).")
    status = "blocked" if missing_required else ("warnings" if warnings else "ready")
    return {
        "status": status,
        "missing_required": missing_required,
        "warnings": warnings,
        "optional_available": [key for key in PACKAGE_OPTIONAL_KEYS if package.get(key) is not None],
        "debug_available": [key for key in PACKAGE_DEBUG_KEYS if package.get(key) is not None],
    }


def ensure_package_publishable(package: dict[str, Any]) -> None:
    validation = package.get("package_validation") if isinstance(package.get("package_validation"), dict) else build_package_validation(package)
    if validation.get("status") == "blocked":
        missing = ", ".join(str(item) for item in validation.get("missing_required") or [])
        raise ValueError(f"Match package is not publishable. Missing required data: {missing or 'unknown'}")


def build_assignment_summary(meta: dict[str, Any], tracks: list[dict[str, Any]], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    track_ids = {int(track.get("track_id")) for track in tracks if track.get("track_id") is not None}
    valid_assignments = [a for a in assignments if int(a.get("tracklet_id", -1)) in track_ids]
    assigned_tracklets = [a for a in valid_assignments if a.get("status") == "assigned" and a.get("player_id")]
    ignored_tracklets = [a for a in valid_assignments if a.get("status") in {"false_positive", "referee", "opponent"}]
    unassigned_tracklets = [a for a in valid_assignments if a.get("status") in {None, "", "unassigned", "unknown"}]

    unique_players_by_team: dict[str, set[str]] = {}
    assigned_tracklets_by_team: dict[str, int] = {}
    for assignment in assigned_tracklets:
        team_id = assignment.get("team_id") or "unknown-team"
        player_id = assignment.get("player_id")
        if player_id:
            unique_players_by_team.setdefault(team_id, set()).add(str(player_id))
        assigned_tracklets_by_team[team_id] = assigned_tracklets_by_team.get(team_id, 0) + 1

    roster_by_team = {
        str(team.get("id")): len(team.get("players") or [])
        for team in meta.get("teams") or []
        if isinstance(team, dict) and team.get("id")
    }

    return {
        "raw_tracklets": len(tracks),
        "assignments_total": len(valid_assignments),
        "assigned_tracklets": len(assigned_tracklets),
        "ignored_tracklets": len(ignored_tracklets),
        "unassigned_tracklets": len(unassigned_tracklets),
        "unique_players_total": len({str(a.get("player_id")) for a in assigned_tracklets if a.get("player_id")}),
        "unique_players_by_team": {team_id: len(players) for team_id, players in unique_players_by_team.items()},
        "assigned_tracklets_by_team": assigned_tracklets_by_team,
        "roster_players_by_team": roster_by_team,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app_mode": APP_MODE,
        "publish_target": PUBLISH_TARGET,
        "database": database_health(),
    }


@app.get("/api/runtime")
def runtime_info() -> dict[str, Any]:
    return collect_runtime_info()


@app.get("/api/teams")
def api_list_teams() -> list[dict[str, Any]]:
    return registry_list_teams()


@app.post("/api/teams")
def api_create_team(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return registry_create_team(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/teams/{team_id}")
def api_get_team(team_id: str) -> dict[str, Any]:
    try:
        return registry_get_team(team_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc


@app.get("/api/teams/{team_id}/stats")
def api_get_team_stats(team_id: str, season: str | None = Query(default=None)) -> dict[str, Any]:
    try:
        return build_team_profile_stats(MATCHES_DIR, team_id, season=season or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Team not found: {team_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/teams/{team_id}")
def api_update_team(team_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return registry_update_team(team_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/teams/{team_id}")
def api_delete_team(team_id: str) -> dict[str, Any]:
    try:
        return registry_delete_team(team_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc


@app.post("/api/matches")
def create_match(
    video: UploadFile = File(...),
    title: str = Form("Untitled match"),
    match_date: str | None = Form(None),
    season: str | None = Form(None),
    venue: str | None = Form(None),
    format: str = Form("7v7"),
    teams_json: str | None = Form(None),
) -> dict[str, Any]:
    if APP_MODE == "production-viewer":
        raise HTTPException(status_code=403, detail="Video upload is disabled in production-viewer mode")
    if not video.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    suffix = Path(video.filename).suffix.lower() or ".mp4"
    match_id = str(uuid.uuid4())[:8]
    path = MATCHES_DIR / match_id
    path.mkdir(parents=True, exist_ok=True)
    video_path = path / f"video{suffix}"
    with video_path.open("wb") as f:
        shutil.copyfileobj(video.file, f)

    metadata = read_video_metadata(video_path)
    meta = parse_metadata_form(
        title=title,
        match_date=match_date,
        season=season,
        venue=venue,
        format=format,
        teams_json=teams_json,
    )
    meta.update(
        {
            "id": match_id,
            "video_filename": video.filename,
            "video": metadata,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    write_match_meta(path, meta)
    return meta


@app.get("/api/matches")
def list_matches() -> list[dict[str, Any]]:
    matches = []
    for path in sorted(MATCHES_DIR.iterdir(), reverse=True):
        meta_path = path / "match.json"
        if meta_path.exists():
            matches.append(json.loads(meta_path.read_text(encoding="utf-8")))
    return matches


@app.get("/api/matches/{match_id}")
def get_match(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    for optional in [
        "pitch_config.json",
        "analysis_report.json",
        "performance_report.json",
        "analysis_chunk_manifest.json",
        "match_package.json",
        "player_assignments.json",
        "identity_candidates.json",
        "identity_assignments.json",
        "player_identity_assignments.json",
        "stable_players.json",
        "global_identity_report.json",
        "analysis_quality_report.json",
        "stabilization_report.json",
        "team_clusters.json",
        "frame_detection_counts.json",
        "movement_stats.json",
        "player_stats.json",
        "resolved_player_stats.json",
        "team_config.json",
        "team_stats.json",
        "change_candidates.json",
        "change_review_report.json",
        "tracklets.json",
        "tracking_quality_report.json",
        "ball_analysis_report.json",
        "ball_tracking_report.json",
        "ball_quality_report.json",
        "possession_candidates.json",
        "possession_segments.json",
        "contact_candidates.json",
        "match_phase_config.json",
        "event_candidates.json",
        "event_review_report.json",
        "pass_candidates.json",
        "pass_review_report.json",
        "possession_report.json",
    ]:
        optional_path = path / optional
        if optional_path.exists():
            meta[optional.removesuffix(".json")] = json.loads(optional_path.read_text(encoding="utf-8"))
    return meta


@app.put("/api/matches/{match_id}/metadata")
def update_match_metadata(match_id: str, payload: MatchMetadataPayload) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    next_metadata = with_generated_ids(payload.model_dump())
    meta.update(next_metadata)
    write_match_meta(path, meta)
    return meta


@app.get("/api/matches/{match_id}/frame")
def get_frame(match_id: str, second: float = 0.0) -> FileResponse:
    path = match_dir(match_id)
    video_path = match_video_path(path)
    frame_path = path / f"frame_{second:.2f}.jpg"
    if not frame_path.exists():
        extract_frame(video_path, second, frame_path)
    return FileResponse(frame_path, media_type="image/jpeg")


@app.get("/api/matches/{match_id}/video")
def get_video(match_id: str) -> FileResponse:
    path = match_dir(match_id)
    return FileResponse(match_video_path(path))


@app.post("/api/matches/{match_id}/pitch")
def save_pitch(match_id: str, payload: PitchConfigPayload) -> dict[str, Any]:
    path = match_dir(match_id)
    data = payload.model_dump()
    existing_path = path / "pitch_config.json"
    existing = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.exists() else {}
    data["pitch_dimensions_m"] = {
        "width_m": float(data.get("width_m") or 30.0),
        "length_m": float(data.get("length_m") or 47.4),
    }
    if data.get("calibration_frame_time_sec") is None and existing.get("calibration_frame_time_sec") is not None:
        data["calibration_frame_time_sec"] = existing.get("calibration_frame_time_sec")
    data["created_at"] = data.get("created_at") or existing.get("created_at") or now_iso()
    data["updated_at"] = now_iso()
    (path / "pitch_config.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    meta = read_match_meta(path)
    if meta.get("status") in {"draft", "uploaded"}:
        meta["status"] = "calibrated"
    meta["updated_at"] = now_iso()
    write_match_meta(path, meta)
    return {"status": "saved", "pitch_config": data}


@app.post("/api/matches/{match_id}/analyze")
def analyze(match_id: str, payload: AnalyzePayload) -> dict[str, Any]:
    if APP_MODE == "production-viewer":
        raise HTTPException(status_code=403, detail="Video analysis is disabled in production-viewer mode")
    path = match_dir(match_id)
    video_path = match_video_path(path)
    try:
        return run_match_analysis_and_update_meta(
            match_id=match_id,
            path=path,
            video_path=video_path,
            payload=payload,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected analysis error: {exc}") from exc


@app.post("/api/matches/{match_id}/analyze/background")
def analyze_background(match_id: str, payload: AnalyzePayload) -> dict[str, Any]:
    if APP_MODE == "production-viewer":
        raise HTTPException(status_code=403, detail="Video analysis is disabled in production-viewer mode")
    path = match_dir(match_id)
    video_path = match_video_path(path)

    def runner(job_id: str, update: Any) -> dict[str, Any]:
        try:
            return run_match_analysis_and_update_meta(
                match_id=match_id,
                path=path,
                video_path=video_path,
                payload=payload,
                job_id=job_id,
                progress=update,
            )
        except Exception:
            meta = read_match_meta(path)
            meta["latest_analysis_job_id"] = job_id
            meta["analysis_job_status"] = "failed"
            write_match_meta(path, meta)
            raise

    job = start_analysis_job(
        match_id=match_id,
        match_path=path,
        payload=payload.model_dump(),
        runner=runner,
    )
    meta = read_match_meta(path)
    meta["latest_analysis_job_id"] = job["job_id"]
    meta["analysis_job_status"] = job["status"]
    write_match_meta(path, meta)
    return job


@app.get("/api/matches/{match_id}/analysis-jobs")
def get_match_analysis_jobs(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    jobs = list_analysis_jobs(path)
    return {
        "schema_version": "0.1.0",
        "match_id": match_id,
        "jobs": jobs,
        "latest_job": jobs[0] if jobs else None,
    }


@app.get("/api/analysis-jobs/{job_id}")
def get_analysis_job(job_id: str) -> dict[str, Any]:
    try:
        return load_analysis_job(MATCHES_DIR, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/matches/{match_id}/analyze-ball")
def analyze_ball(match_id: str, payload: BallAnalyzePayload) -> dict[str, Any]:
    if APP_MODE == "production-viewer":
        raise HTTPException(status_code=403, detail="Video analysis is disabled in production-viewer mode")
    path = match_dir(match_id)
    video_path = match_video_path(path)
    try:
        report = analyze_match_ball_yolo(
            path,
            video_path,
            max_seconds=payload.max_seconds,
            frame_stride=max(1, payload.frame_stride),
            yolo_model=payload.yolo_model,
            yolo_conf=payload.yolo_conf,
            yolo_imgsz=payload.yolo_imgsz,
            yolo_device=payload.yolo_device,
        )
        meta = read_match_meta(path)
        run_summary = {
            "run_id": report.get("run_id"),
            "status": report.get("status"),
            "analysis_type": report.get("analysis_type"),
            "generated_at": report.get("generated_at"),
            "frames_processed": report.get("frames_processed"),
            "parameters": report.get("parameters") or {},
            "run_directory": report.get("run_directory"),
            "run_manifest": report.get("run_manifest"),
        }
        existing_runs = [item for item in meta.get("ball_analysis_runs", []) if isinstance(item, dict)]
        existing_runs = [item for item in existing_runs if item.get("run_id") != run_summary["run_id"]]
        meta["ball_analysis_runs"] = [run_summary, *existing_runs][:30]
        meta["latest_ball_analysis_run_id"] = run_summary["run_id"]
        meta["updated_at"] = now_iso()
        write_match_meta(path, meta)
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected ball analysis error: {exc}") from exc


@app.get("/api/matches/{match_id}/tracklets")
def get_match_tracklets(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    tracks = load_tracks(path)
    assignment_doc = load_player_assignments(path, tracks)
    assignments = assignment_doc.get("assignments") if isinstance(assignment_doc.get("assignments"), list) else []
    summary = build_assignment_summary(meta, tracks, assignments)
    assignment_doc["summary"] = summary
    return {
        "tracklets": sorted([summarize_track(track) for track in tracks], key=lambda item: float(item.get("duration_sec") or 0), reverse=True),
        "assignments": assignments,
        "summary": summary,
    }


@app.get("/api/matches/{match_id}/identity-candidates")
def get_identity_candidates(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    try:
        return build_identity_review(path, meta)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/identity-assignments")
def save_candidate_assignments(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        raise HTTPException(status_code=400, detail="assignments must be a list")
    try:
        doc = save_identity_assignments(path, meta, assignments)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/player-identity")
def get_player_identity(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    try:
        doc = build_player_identity_review(path, meta)
        if (path / "player_identity_assignments.json").exists() and (path / "player_stats.json").exists():
            doc["resolved_player_stats"] = build_resolved_player_stats_from_files(path, persist=True)
        return doc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/player-identity")
def review_player_identity(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        raise HTTPException(status_code=400, detail="assignments must be a list")
    try:
        doc = save_player_identity_assignments(path, meta, assignments)
        try:
            doc["resolved_player_stats"] = build_resolved_player_stats_from_files(path, persist=True)
        except FileNotFoundError:
            doc["resolved_player_stats"] = None
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/resolved-player-stats")
def get_resolved_player_stats(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return build_resolved_player_stats_from_files(path, persist=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/players/{player_id}/stats")
def get_player_profile_stats(player_id: str) -> dict[str, Any]:
    try:
        return build_player_profile_stats(MATCHES_DIR, player_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/stable-players")
def get_stable_players(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_stable_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/stable-players/review")
def review_stable_players(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        doc = save_stable_review(path, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/change-candidates")
def get_change_candidates(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_change_candidates_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/change-candidates/review")
def review_change_candidates(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    try:
        doc = save_change_candidate_reviews(path, updates)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/team-config")
def get_team_config(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_team_config_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/team-config")
def review_team_config(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        doc = save_team_config_review(path, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/contact-candidates")
def get_contact_candidates(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_contact_candidates_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/contact-candidates/review")
def review_contact_candidates(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    try:
        doc = save_contact_candidate_reviews(path, updates)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/match-phase-config")
def get_match_phase_config(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    try:
        return load_match_phase_config(path, meta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/match-phase-config")
def update_match_phase_config(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    try:
        document = save_match_phase_config(path, meta, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return document


@app.get("/api/matches/{match_id}/pass-candidates")
def get_pass_candidates(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_pass_candidates_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/pass-candidates/review")
def review_pass_candidates(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    try:
        doc = save_pass_candidate_reviews(path, updates)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.put("/api/matches/{match_id}/player-assignments")
def save_player_assignments(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    tracks = load_tracks(path)
    track_ids = {int(track.get("track_id")) for track in tracks if track.get("track_id") is not None}
    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        raise HTTPException(status_code=400, detail="assignments must be a list")

    normalized: list[dict[str, Any]] = []
    allowed_statuses = {"unassigned", "assigned", "unknown", "false_positive", "referee", "opponent"}
    for item in assignments:
        if not isinstance(item, dict):
            continue
        try:
            tracklet_id = int(item.get("tracklet_id"))
        except (TypeError, ValueError):
            continue
        if tracklet_id not in track_ids:
            continue
        status = str(item.get("status") or "unassigned")
        if status not in allowed_statuses:
            status = "unassigned"
        normalized.append(
            {
                "tracklet_id": tracklet_id,
                "status": status,
                "team_id": item.get("team_id") or None,
                "player_id": item.get("player_id") or None,
                "notes": item.get("notes") or "",
            }
        )

    existing_ids = {int(item["tracklet_id"]) for item in normalized}
    for track_id in sorted(track_ids - existing_ids):
        normalized.append({"tracklet_id": track_id, "status": "unassigned", "team_id": None, "player_id": None, "notes": ""})

    summary = build_assignment_summary(meta, tracks, normalized)
    doc = {
        "schema_version": "0.1.0",
        "updated_at": now_iso(),
        "assignments": sorted(normalized, key=lambda item: int(item["tracklet_id"])),
        "summary": summary,
    }
    (path / "player_assignments.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


def build_match_package(path: Path) -> dict[str, Any]:
    meta = read_match_meta(path)
    package = {
        "schema_version": "0.2.0",
        "generated_at": now_iso(),
        "contains_video": False,
        "match": meta,
        "pitch_config": None,
        "analysis_report": None,
        "performance_report": None,
        "analysis_chunk_manifest": None,
        "player_assignments": None,
        "identity_candidates": None,
        "identity_assignments": None,
        "player_identity_assignments": None,
        "stable_players": None,
        "global_identity": None,
        "global_identity_report": None,
        "analysis_quality_report": None,
        "stabilization_report": None,
        "team_clusters": None,
        "frame_detection_counts": None,
        "movement_stats": None,
        "player_stats": None,
        "resolved_player_stats": None,
        "player_heatmaps": None,
        "team_config": None,
        "team_stats": None,
        "change_candidates": None,
        "change_review_report": None,
        "tracklets": None,
        "tracking_quality_report": None,
        "ball_tracks": None,
        "ball_analysis_report": None,
        "ball_tracking_report": None,
        "ball_quality_report": None,
        "possession_candidates": None,
        "possession_segments": None,
        "contact_candidates": None,
        "match_phase_config": None,
        "event_candidates": None,
        "event_review_report": None,
        "pass_candidates": None,
        "pass_review_report": None,
        "possession_report": None,
        "team_count": len(meta.get("teams") or []),
        "player_count": sum(len(team.get("players") or []) for team in meta.get("teams") or []),
        "assets": {},
        "publish_status": "draft-package",
    }
    for key, filename in [
        ("pitch_config", "pitch_config.json"),
        ("analysis_report", "analysis_report.json"),
        ("performance_report", "performance_report.json"),
        ("analysis_chunk_manifest", "analysis_chunk_manifest.json"),
        ("player_assignments", "player_assignments.json"),
        ("identity_candidates", "identity_candidates.json"),
        ("identity_assignments", "identity_assignments.json"),
        ("player_identity_assignments", "player_identity_assignments.json"),
        ("stable_players", "stable_players.json"),
        ("global_identity", "global_identity.json"),
        ("global_identity_report", "global_identity_report.json"),
        ("analysis_quality_report", "analysis_quality_report.json"),
        ("stabilization_report", "stabilization_report.json"),
        ("team_clusters", "team_clusters.json"),
        ("frame_detection_counts", "frame_detection_counts.json"),
        ("movement_stats", "movement_stats.json"),
        ("player_stats", "player_stats.json"),
        ("resolved_player_stats", "resolved_player_stats.json"),
        ("player_heatmaps", "player_heatmaps.json"),
        ("team_config", "team_config.json"),
        ("team_stats", "team_stats.json"),
        ("change_candidates", "change_candidates.json"),
        ("change_review_report", "change_review_report.json"),
        ("tracklets", "tracklets.json"),
        ("tracking_quality_report", "tracking_quality_report.json"),
        ("ball_tracks", "ball_tracks.json"),
        ("ball_analysis_report", "ball_analysis_report.json"),
        ("ball_tracking_report", "ball_tracking_report.json"),
        ("ball_quality_report", "ball_quality_report.json"),
        ("possession_candidates", "possession_candidates.json"),
        ("possession_segments", "possession_segments.json"),
        ("contact_candidates", "contact_candidates.json"),
        ("match_phase_config", "match_phase_config.json"),
        ("event_candidates", "event_candidates.json"),
        ("event_review_report", "event_review_report.json"),
        ("pass_candidates", "pass_candidates.json"),
        ("pass_review_report", "pass_review_report.json"),
        ("possession_report", "possession_report.json"),
    ]:
        file_path = path / filename
        if file_path.exists():
            package[key] = json.loads(file_path.read_text(encoding="utf-8"))
    if (path / "heatmap_all_tracks.png").exists():
        package["assets"]["heatmap_all_tracks"] = "heatmap_all_tracks.png"
    if (path / "tracks.json").exists():
        package["assets"]["tracks_json"] = "tracks.json"
    if (path / "overlay_preview.mp4").exists():
        package["assets"]["overlay_preview"] = "overlay_preview.mp4"
    if (path / "analysis_chunk_manifest.json").exists():
        package["assets"]["analysis_chunk_manifest_json"] = "analysis_chunk_manifest.json"
    if (path / "performance_report.json").exists():
        package["assets"]["performance_report_json"] = "performance_report.json"
    if (path / "player_assignments.json").exists():
        package["assets"]["player_assignments_json"] = "player_assignments.json"
    if (path / "identity_candidates.json").exists():
        package["assets"]["identity_candidates_json"] = "identity_candidates.json"
    if (path / "identity_assignments.json").exists():
        package["assets"]["identity_assignments_json"] = "identity_assignments.json"
    if (path / "player_identity_assignments.json").exists():
        package["assets"]["player_identity_assignments_json"] = "player_identity_assignments.json"
    if (path / "stable_players.json").exists():
        package["assets"]["stable_players_json"] = "stable_players.json"
    if (path / "global_identity.json").exists():
        package["assets"]["global_identity_json"] = "global_identity.json"
    if (path / "global_identity_report.json").exists():
        package["assets"]["global_identity_report_json"] = "global_identity_report.json"
    if (path / "analysis_quality_report.json").exists():
        package["assets"]["analysis_quality_report_json"] = "analysis_quality_report.json"
    if (path / "stabilization_report.json").exists():
        package["assets"]["stabilization_report_json"] = "stabilization_report.json"
    if (path / "stable_overlay_preview.mp4").exists():
        package["assets"]["stable_overlay_preview"] = "stable_overlay_preview.mp4"
    if (path / "debug_identity_overlay.mp4").exists():
        package["assets"]["debug_identity_overlay"] = "debug_identity_overlay.mp4"
    if (path / "team_clusters.json").exists():
        package["assets"]["team_clusters_json"] = "team_clusters.json"
    if (path / "frame_detection_counts.json").exists():
        package["assets"]["frame_detection_counts_json"] = "frame_detection_counts.json"
    if (path / "movement_stats.json").exists():
        package["assets"]["movement_stats_json"] = "movement_stats.json"
    if (path / "player_stats.json").exists():
        package["assets"]["player_stats_json"] = "player_stats.json"
    if (path / "resolved_player_stats.json").exists():
        package["assets"]["resolved_player_stats_json"] = "resolved_player_stats.json"
    if (path / "player_heatmaps.json").exists():
        package["assets"]["player_heatmaps_json"] = "player_heatmaps.json"
    if (path / "team_config.json").exists():
        package["assets"]["team_config_json"] = "team_config.json"
    if (path / "team_stats.json").exists():
        package["assets"]["team_stats_json"] = "team_stats.json"
    if (path / "change_candidates.json").exists():
        package["assets"]["change_candidates_json"] = "change_candidates.json"
    if (path / "change_review_report.json").exists():
        package["assets"]["change_review_report_json"] = "change_review_report.json"
    if (path / "tracklets.json").exists():
        package["assets"]["tracklets_json"] = "tracklets.json"
    if (path / "tracking_quality_report.json").exists():
        package["assets"]["tracking_quality_report_json"] = "tracking_quality_report.json"
    if (path / "ball_candidates.json").exists():
        package["assets"]["ball_candidates_json"] = "ball_candidates.json"
    if (path / "ball_tracks.json").exists():
        package["assets"]["ball_tracks_json"] = "ball_tracks.json"
    if (path / "ball_analysis_report.json").exists():
        package["assets"]["ball_analysis_report_json"] = "ball_analysis_report.json"
    if (path / "ball_tracking_report.json").exists():
        package["assets"]["ball_tracking_report_json"] = "ball_tracking_report.json"
    if (path / "ball_quality_report.json").exists():
        package["assets"]["ball_quality_report_json"] = "ball_quality_report.json"
    if (path / "ball_overlay_preview.mp4").exists():
        package["assets"]["ball_overlay_preview"] = "ball_overlay_preview.mp4"
    if (path / "possession_candidates.json").exists():
        package["assets"]["possession_candidates_json"] = "possession_candidates.json"
    if (path / "possession_segments.json").exists():
        package["assets"]["possession_segments_json"] = "possession_segments.json"
    if (path / "contact_candidates.json").exists():
        package["assets"]["contact_candidates_json"] = "contact_candidates.json"
    if (path / "match_phase_config.json").exists():
        package["assets"]["match_phase_config_json"] = "match_phase_config.json"
    if (path / "event_candidates.json").exists():
        package["assets"]["event_candidates_json"] = "event_candidates.json"
    if (path / "event_review_report.json").exists():
        package["assets"]["event_review_report_json"] = "event_review_report.json"
    if (path / "pass_candidates.json").exists():
        package["assets"]["pass_candidates_json"] = "pass_candidates.json"
    if (path / "pass_review_report.json").exists():
        package["assets"]["pass_review_report_json"] = "pass_review_report.json"
    if (path / "possession_report.json").exists():
        package["assets"]["possession_report_json"] = "possession_report.json"
    if (path / "possession_overlay_preview.mp4").exists():
        package["assets"]["possession_overlay_preview"] = "possession_overlay_preview.mp4"
    package["required"] = {key: package.get(key) for key in PACKAGE_REQUIRED_KEYS}
    package["optional"] = {key: package.get(key) for key in PACKAGE_OPTIONAL_KEYS}
    package["debug"] = {
        **{key: package.get(key) for key in PACKAGE_DEBUG_KEYS},
        "assets": {
            key: value
            for key, value in package["assets"].items()
            if key
            in {
                "tracks_json",
                "overlay_preview",
                "debug_identity_overlay",
                "identity_candidates_json",
                "identity_assignments_json",
                "tracklets_json",
                "tracking_quality_report_json",
            }
        },
    }
    package["package_validation"] = build_package_validation(package)
    (path / "match_package.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
    return package


@app.post("/api/matches/{match_id}/package")
def create_match_package(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    return build_match_package(path)


@app.post("/api/matches/{match_id}/publish")
def publish_match(match_id: str, replace: bool = Query(False)) -> dict[str, Any]:
    path = match_dir(match_id)
    package_path = path / "match_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8")) if package_path.exists() else build_match_package(path)
    ensure_package_publishable(package)
    try:
        published = publish_match_package(package, replace=replace)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (PublishError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meta = read_match_meta(path)
    meta["status"] = "published"
    meta["publish_target"] = PUBLISH_TARGET
    meta["published_match_id"] = published.get("id")
    write_match_meta(path, meta)
    return published


@app.post("/api/matches/{match_id}/publish-local")
def publish_local_match(match_id: str, replace: bool = Query(False)) -> dict[str, Any]:
    path = match_dir(match_id)
    package_path = path / "match_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8")) if package_path.exists() else build_match_package(path)
    ensure_package_publishable(package)
    try:
        published = import_match_package(package, replace=replace)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meta = read_match_meta(path)
    meta["status"] = "published"
    meta["publish_target"] = "local-db"
    meta["published_match_id"] = published["id"]
    write_match_meta(path, meta)
    return published


@app.get("/api/published/matches")
def api_list_published_matches() -> list[dict[str, Any]]:
    return list_published_matches()


@app.get("/api/published/matches/{published_match_id}")
def api_get_published_match(published_match_id: str) -> dict[str, Any]:
    try:
        return get_published_match(published_match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Published match not found") from exc


@app.delete("/api/published/matches/{published_match_id}")
def api_delete_published_match(published_match_id: str) -> dict[str, Any]:
    try:
        deleted = delete_published_match(published_match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Published match not found") from exc
    return {"status": "deleted", "match": deleted}


@app.post("/api/admin/import-match")
def api_import_match_package(
    package: dict[str, Any] = Body(...),
    replace: bool = Query(False),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_import_token(authorization)
    try:
        return import_match_package(package, replace=replace)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/artifact/{artifact_name:path}")
def get_artifact(match_id: str, artifact_name: str) -> FileResponse:
    path = match_dir(match_id)
    allowed = {
        "tracks.json": "application/json",
        "analysis_report.json": "application/json",
        "performance_report.json": "application/json",
        "analysis_chunk_manifest.json": "application/json",
        "overlay_preview.mp4": "video/mp4",
        "heatmap_all_tracks.png": "image/png",
        "pitch_config.json": "application/json",
        "match_package.json": "application/json",
        "player_assignments.json": "application/json",
        "identity_candidates.json": "application/json",
        "identity_assignments.json": "application/json",
        "player_identity_assignments.json": "application/json",
        "stable_players.json": "application/json",
        "global_identity.json": "application/json",
        "global_identity_report.json": "application/json",
        "analysis_quality_report.json": "application/json",
        "stabilization_report.json": "application/json",
        "team_clusters.json": "application/json",
        "frame_detection_counts.json": "application/json",
        "movement_stats.json": "application/json",
        "player_stats.json": "application/json",
        "resolved_player_stats.json": "application/json",
        "player_heatmaps.json": "application/json",
        "team_config.json": "application/json",
        "team_stats.json": "application/json",
        "change_candidates.json": "application/json",
        "change_review_report.json": "application/json",
        "tracklets.json": "application/json",
        "tracking_quality_report.json": "application/json",
        "ball_candidates.json": "application/json",
        "ball_tracks.json": "application/json",
        "ball_analysis_report.json": "application/json",
        "ball_tracking_report.json": "application/json",
        "ball_quality_report.json": "application/json",
        "possession_candidates.json": "application/json",
        "possession_segments.json": "application/json",
        "contact_candidates.json": "application/json",
        "match_phase_config.json": "application/json",
        "event_candidates.json": "application/json",
        "event_review_report.json": "application/json",
        "pass_candidates.json": "application/json",
        "pass_review_report.json": "application/json",
        "possession_report.json": "application/json",
        "run_metadata.json": "application/json",
        "stable_overlay_preview.mp4": "video/mp4",
        "debug_identity_overlay.mp4": "video/mp4",
        "ball_overlay_preview.mp4": "video/mp4",
        "possession_overlay_preview.mp4": "video/mp4",
    }
    artifact_rel = Path(artifact_name)
    if artifact_rel.is_absolute() or any(part == ".." for part in artifact_rel.parts):
        raise HTTPException(status_code=404, detail="Artifact not available")
    artifact_basename = artifact_rel.name
    if (
        artifact_rel.parts
        and artifact_rel.parts[0] == "player_heatmaps"
        and artifact_basename.lower().endswith(".png")
    ):
        allowed[artifact_basename] = "image/png"
    if artifact_basename not in allowed:
        raise HTTPException(status_code=404, detail="Artifact not available")
    artifact_path = (path / artifact_rel).resolve()
    match_root = path.resolve()
    if artifact_path != match_root and match_root not in artifact_path.parents:
        raise HTTPException(status_code=404, detail="Artifact not available")
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not generated yet")
    if artifact_path.stat().st_size == 0:
        raise HTTPException(status_code=410, detail=f"Artifact {artifact_name} exists but is empty. Rerun analysis and check backend logs.")
    return FileResponse(artifact_path, media_type=allowed[artifact_basename])
