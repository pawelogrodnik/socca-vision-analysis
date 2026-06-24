from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import CORS_ORIGINS, MATCHES_DIR
from app.models import AnalyzePayload, PitchConfigPayload
from app.services.analysis import analyze_match
from app.services.video import extract_frame, read_video_metadata

app = FastAPI(title="Orlik Vision API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def match_dir(match_id: str) -> Path:
    path = MATCHES_DIR / match_id
    if not path.exists():
        raise HTTPException(status_code=404, detail="Match not found")
    return path


def match_video_path(path: Path) -> Path:
    for candidate in path.glob("video.*"):
        return candidate
    raise HTTPException(status_code=404, detail="Video file not found")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/matches")
def create_match(video: UploadFile = File(...), title: str = Form("Untitled match")) -> dict[str, Any]:
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
    meta = {"id": match_id, "title": title, "video_filename": video.filename, "video": metadata}
    (path / "match.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
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
    meta_path = path / "match.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for optional in ["pitch_config.json", "analysis_report.json"]:
        optional_path = path / optional
        if optional_path.exists():
            meta[optional.removesuffix(".json")] = json.loads(optional_path.read_text(encoding="utf-8"))
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
    return {"status": "saved", "pitch_config": data}


@app.post("/api/matches/{match_id}/analyze")
def analyze(match_id: str, payload: AnalyzePayload) -> dict[str, Any]:
    path = match_dir(match_id)
    video_path = match_video_path(path)
    try:
        return analyze_match(
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
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected analysis error: {exc}") from exc


@app.get("/api/matches/{match_id}/artifact/{artifact_name}")
def get_artifact(match_id: str, artifact_name: str) -> FileResponse:
    path = match_dir(match_id)
    allowed = {
        "tracks.json": "application/json",
        "analysis_report.json": "application/json",
        "overlay_preview.mp4": "video/mp4",
        "heatmap_all_tracks.png": "image/png",
        "pitch_config.json": "application/json",
    }
    if artifact_name not in allowed:
        raise HTTPException(status_code=404, detail="Artifact not available")
    artifact_path = path / artifact_name
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not generated yet")
    if artifact_path.stat().st_size == 0:
        raise HTTPException(status_code=410, detail=f"Artifact {artifact_name} exists but is empty. Rerun analysis and check backend logs.")
    return FileResponse(artifact_path, media_type=allowed[artifact_name])
