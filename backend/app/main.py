from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import CORS_ORIGINS, MATCHES_DIR
from app.models import AnalyzePayload, MatchMetadataPayload, PitchConfigPayload
from app.services.analysis import analyze_match
from app.services.video import extract_frame, read_video_metadata

app = FastAPI(title="Orlik Vision API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    for optional in ["pitch_config.json", "analysis_report.json", "match_package.json"]:
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


def build_match_package(path: Path) -> dict[str, Any]:
    meta = read_match_meta(path)
    package = {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "contains_video": False,
        "match": meta,
        "pitch_config": None,
        "analysis_report": None,
        "team_count": len(meta.get("teams") or []),
        "player_count": sum(len(team.get("players") or []) for team in meta.get("teams") or []),
        "assets": {},
        "publish_status": "draft-package",
    }
    for key, filename in [("pitch_config", "pitch_config.json"), ("analysis_report", "analysis_report.json")]:
        file_path = path / filename
        if file_path.exists():
            package[key] = json.loads(file_path.read_text(encoding="utf-8"))
    if (path / "heatmap_all_tracks.png").exists():
        package["assets"]["heatmap_all_tracks"] = "heatmap_all_tracks.png"
    if (path / "tracks.json").exists():
        package["assets"]["tracks_json"] = "tracks.json"
    (path / "match_package.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
    return package


@app.post("/api/matches/{match_id}/package")
def create_match_package(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    return build_match_package(path)


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
    }
    if artifact_name not in allowed:
        raise HTTPException(status_code=404, detail="Artifact not available")
    artifact_path = path / artifact_name
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not generated yet")
    if artifact_path.stat().st_size == 0:
        raise HTTPException(status_code=410, detail=f"Artifact {artifact_name} exists but is empty. Rerun analysis and check backend logs.")
    return FileResponse(artifact_path, media_type=allowed[artifact_name])
