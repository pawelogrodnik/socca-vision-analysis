from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import CORS_ORIGINS, MATCHES_DIR
from app.models import AnalyzePayload, MatchMetadataPayload, PitchConfigPayload
from app.services.analysis import analyze_match
from app.services.database import database_health, delete_published_match, get_published_match, import_match_package, init_db, list_published_matches
from app.services.video import extract_frame, read_video_metadata

app = FastAPI(title="Orlik Vision API", version="0.5.0")
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


def read_json_if_exists(path: Path, filename: str) -> dict[str, Any] | list[Any] | None:
    file_path = path / filename
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))


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


def build_assignment_summary(meta: dict[str, Any], tracks: list[dict[str, Any]], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    track_ids = {int(track.get("track_id")) for track in tracks if track.get("track_id") is not None}
    valid_assignments = [a for a in assignments if int(a.get("tracklet_id", -1)) in track_ids]
    resolved_statuses = {"assigned"}
    ignored_statuses = {"false_positive", "referee", "opponent"}
    assigned_tracklets = [a for a in valid_assignments if a.get("status") in resolved_statuses and a.get("player_id")]
    ignored_tracklets = [a for a in valid_assignments if a.get("status") in ignored_statuses]
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
    return {"status": "ok", "database": database_health()}


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
    for optional in ["pitch_config.json", "analysis_report.json", "match_package.json", "player_assignments.json"]:
        optional_path = path / optional
        if optional_path.exists():
            meta[optional.removesuffix(".json")] = json.loads(optional_path.read_text(encoding="utf-8"))
    return meta


@app.put("/api/matches/{match_id}/metadata")
def update_match_metadata(match_id: str, payload: MatchMetadataPayload) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    next_metadata = with_generated_ids(payload.model_dump())
    # Preserve immutable/imported technical metadata.
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
    (path / "pitch_config.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    meta = read_match_meta(path)
    if meta.get("status") in {"draft", "uploaded"}:
        meta["status"] = "calibrated"
        write_match_meta(path, meta)
    return {"status": "saved", "pitch_config": data}


@app.post("/api/matches/{match_id}/analyze")
def analyze(match_id: str, payload: AnalyzePayload) -> dict[str, Any]:
    path = match_dir(match_id)
    video_path = match_video_path(path)
    try:
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
        meta = read_match_meta(path)
        if report.get("status") == "completed":
            meta["status"] = "analyzed"
            write_match_meta(path, meta)
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected analysis error: {exc}") from exc


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

    # Preserve unmentioned tracklets as unassigned so the review UI always has a complete checklist.
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
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "contains_video": False,
        "match": meta,
        "pitch_config": None,
        "analysis_report": None,
        "player_assignments": None,
        "team_count": len(meta.get("teams") or []),
        "player_count": sum(len(team.get("players") or []) for team in meta.get("teams") or []),
        "assets": {},
        "publish_status": "draft-package",
    }
    for key, filename in [
        ("pitch_config", "pitch_config.json"),
        ("analysis_report", "analysis_report.json"),
        ("player_assignments", "player_assignments.json"),
    ]:
        file_path = path / filename
        if file_path.exists():
            package[key] = json.loads(file_path.read_text(encoding="utf-8"))
    if (path / "heatmap_all_tracks.png").exists():
        package["assets"]["heatmap_all_tracks"] = "heatmap_all_tracks.png"
    if (path / "tracks.json").exists():
        package["assets"]["tracks_json"] = "tracks.json"
    if (path / "player_assignments.json").exists():
        package["assets"]["player_assignments_json"] = "player_assignments.json"
    (path / "match_package.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
    return package


@app.post("/api/matches/{match_id}/package")
def create_match_package(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    return build_match_package(path)


@app.post("/api/matches/{match_id}/publish-local")
def publish_local_match(match_id: str, replace: bool = Query(False)) -> dict[str, Any]:
    path = match_dir(match_id)
    package_path = path / "match_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8")) if package_path.exists() else build_match_package(path)
    try:
        published = import_match_package(package, replace=replace)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meta = read_match_meta(path)
    meta["status"] = "published"
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
def api_import_match_package(package: dict[str, Any] = Body(...), replace: bool = Query(False)) -> dict[str, Any]:
    try:
        return import_match_package(package, replace=replace)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/artifact/{artifact_name}")
def get_artifact(match_id: str, artifact_name: str) -> FileResponse:
    path = match_dir(match_id)
    allowed = {
        "tracks.json": "application/json",
        "analysis_report.json": "application/json",
        "overlay_preview.mp4": "video/mp4",
        "heatmap_all_tracks.png": "image/png",
        "pitch_config.json": "application/json",
        "match_package.json": "application/json",
        "player_assignments.json": "application/json",
    }
    if artifact_name not in allowed:
        raise HTTPException(status_code=404, detail="Artifact not available")
    artifact_path = path / artifact_name
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not generated yet")
    if artifact_path.stat().st_size == 0:
        raise HTTPException(status_code=410, detail=f"Artifact {artifact_name} exists but is empty. Rerun analysis and check backend logs.")
    return FileResponse(artifact_path, media_type=allowed[artifact_name])
