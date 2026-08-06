from __future__ import annotations

"""Streaming reviewed MP4 renderer using the canonical reviewed snapshot only."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_minimap import TEAM_COLORS, draw_reviewed_minimap
from app.services.identity_reviewed_effective_observation import (
    effective_observations_by_frame,
    visible_reviewed_overlay,
    visible_reviewed_player,
)
from app.services.video import resolve_match_video_path


RenderProgressCallback = Callable[[dict[str, Any]], None]


def render_reviewed_video(
    match_path: Path,
    snapshot: dict[str, Any],
    match_doc: dict[str, Any],
    *,
    include_minimap: bool = True,
    include_ball: bool = True,
    show_roster_number: bool = False,
    progress_callback: RenderProgressCallback | None = None,
) -> dict[str, Any]:
    import cv2

    emitter = _ProgressEmitter(progress_callback)
    emitter.emit("resolve_source_video")
    source = reviewed_source_video_path(match_path, match_doc)
    output = match_path / "reviewed_video.mp4"; raw = match_path / "reviewed_video.raw.avi"; partial = match_path / "reviewed_video.partial.mp4"
    emitter.emit("load_render_inputs")
    positions = _positions_by_frame(match_path, snapshot); pitch = _load_optional(match_path / "pitch_config.json"); balls = _ball_by_frame(match_path) if include_ball else {}
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened(): raise RuntimeError("Source video could not be opened")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0); width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)); height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)); total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)); writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height))
    if not writer.isOpened(): capture.release(); raise RuntimeError("Could not create temporary reviewed video")
    started=time.monotonic(); count=0; labeled_frames=0; confirmed_labels=0; fallback_labels=0; minimap_frames=0; ball_frames=0; duplicate_stable_labels=0; duplicate_canonical_players=0; max_simultaneous_stable_labels=0
    emitter.emit("render_frames", processed_frames=0, total_frames=total, force=True)
    try:
        while True:
            ok, frame = capture.read()
            if not ok: break
            rows = positions.get(count, [])
            if rows:
                labeled_frames += 1
                confirmed_labels += sum(row.get("identity_status") == "confirmed" for row in rows)
                fallback_labels += sum(row.get("identity_status") != "confirmed" for row in rows)
                stable_labels = _rendered_stable_labels(rows)
                duplicate_stable_labels += sum(value - 1 for value in Counter(stable_labels).values() if value > 1)
                canonical_players = _rendered_canonical_players(rows)
                duplicate_canonical_players += sum(value - 1 for value in Counter(canonical_players).values() if value > 1)
                max_simultaneous_stable_labels = max(max_simultaneous_stable_labels, len(stable_labels))
            _draw_rows(frame, rows, show_roster_number)
            minimap = {"status": "not_available", "reason": "pitch positions unavailable"}
            if include_minimap and pitch:
                minimap = draw_reviewed_minimap(frame, [row for row in rows if visible_reviewed_player(row)], pitch_width=float(pitch.get("width_m") or 40), pitch_length=float(pitch.get("length_m") or 60), include_ball=include_ball, ball=balls.get(count))
                minimap_frames += minimap.get("status") == "available"
                ball_frames += bool(minimap.get("ball_rendered"))
            _hud(frame, count, fps, rows, snapshot)
            writer.write(frame); count += 1
            emitter.emit("render_frames", processed_frames=count, total_frames=total)
    finally: capture.release(); writer.release()
    if count == 0: raw.unlink(missing_ok=True); raise RuntimeError("Renderer produced zero frames")
    emitter.emit("encode_mp4", processed_frames=count, total_frames=total, force=True)
    _encode(raw, partial, total_frames=total, progress_callback=emitter.emit)
    partial.replace(output); raw.unlink(missing_ok=True)
    emitter.emit("validate_output", processed_frames=count, total_frames=total, force=True)
    config={"include_minimap":include_minimap,"include_ball":include_ball,"show_roster_number":show_roster_number}; manifest={"schema_version":"1.3.0","status":"completed","generated_at":datetime.now(timezone.utc).isoformat(),"source_snapshot_digest":snapshot["semantic_digest"],"source_video_digest":_sha(source),"render_config_digest":canonical_digest(config),"renderer_version":"reviewed_video:v5","path":"reviewed_video.mp4","frames":count,"fps":fps,"resolution":[width,height],"duration_sec":round(count/fps,3),"file_size_bytes":output.stat().st_size,"digest":_sha(output),"render_duration_sec":round(time.monotonic()-started,3),"real_time_factor":round((count/fps)/max(time.monotonic()-started,.001),3),"semantic_checks":{"frames_with_player_labels":labeled_frames,"confirmed_labels_rendered":confirmed_labels,"fallback_labels_rendered":fallback_labels,"minimap_frames_rendered":minimap_frames,"ball_frames_rendered":ball_frames,"duplicate_stable_labels_rendered":duplicate_stable_labels,"duplicate_canonical_players_rendered":duplicate_canonical_players,"max_simultaneous_stable_labels":max_simultaneous_stable_labels},"minimap":minimap,"safety":{"reran_yolo":False,"reran_tracking":False,"production_identity_mutated":False}}
    emitter.emit("write_manifests", processed_frames=count, total_frames=total, force=True)
    write_identity_json_atomic(match_path / "reviewed_video_manifest.json",manifest)
    return manifest


def reviewed_source_video_path(match_path: Path, match_doc: dict[str, Any]) -> Path:
    preferred = str(match_doc.get("video_filename") or "") or None
    return resolve_match_video_path(match_path, preferred)


def reviewed_source_video_digest(match_path: Path, match_doc: dict[str, Any]) -> str:
    return _sha(reviewed_source_video_path(match_path, match_doc))


def _positions_by_frame(path: Path, snapshot: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    tracklets={str(row.get("tracklet_id")):row for row in _load_optional(path/"tracklets.json").get("tracklets") or []}
    return {
        frame: [
            row
            for row in rows
            if visible_reviewed_overlay(row)
            and isinstance(row.get("bbox_xyxy"), list)
            and len(row["bbox_xyxy"]) >= 4
        ]
        for frame, rows in effective_observations_by_frame(tracklets, snapshot).items()
    }


def _rendered_stable_labels(rows: list[dict[str, Any]]) -> list[str]:
    return [
        str(row["stable_anonymous_slot_id"])
        for row in rows
        if row.get("stable_anonymous_slot_id")
        and str(row.get("display_label") or "")
        == str(row.get("stable_anonymous_slot_id"))
    ]


def _rendered_canonical_players(rows: list[dict[str, Any]]) -> list[str]:
    return [
        str(row["canonical_player_id"])
        for row in rows
        if row.get("identity_status") == "confirmed"
        and row.get("canonical_player_id")
    ]
def _draw_rows(frame: Any, rows: list[dict[str,Any]], show_number: bool) -> None:
    import cv2
    occupied: list[tuple[int, int, int, int]] = []
    for row in sorted(rows, key=lambda item: (int(float(item["bbox_xyxy"][1])), int(float(item["bbox_xyxy"][0])))):
        x1,y1,x2,y2=(int(float(v)) for v in row["bbox_xyxy"][:4]); color=TEAM_COLORS.get(str(row.get("team_label") or "U"),TEAM_COLORS["U"]); cv2.rectangle(frame,(x1,y1),(x2,y2),color,2); label=str(row.get("display_label") or row.get("fallback_label") or "?")
        if show_number and row.get("identity_status")=="confirmed" and row.get("roster_number"): label=f"{label} #{row['roster_number']}"
        (width, height), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, .5, 1)
        label_box = _choose_label_box(x1, y1, y2, width, height, base, frame.shape[1], frame.shape[0], occupied)
        left, top, right, bottom = label_box
        occupied.append(label_box)
        cv2.rectangle(frame, (left, top), (right, bottom), (10, 14, 20), -1)
        cv2.putText(frame, label, (left + 3, bottom - base - 2), cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1, cv2.LINE_AA)


def _choose_label_box(x1: int, y1: int, y2: int, width: int, height: int, base: int, frame_width: int, frame_height: int, occupied: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    left = max(0, min(x1, frame_width - width - 6))
    candidates = [y1 - height - base - 8, y2 + 3, y1 - (2 * height + base + 12), y2 + height + base + 8]
    for top in candidates:
        top = max(0, min(top, frame_height - height - base - 5))
        box = (left, top, left + width + 6, top + height + base + 5)
        if not any(_rectangles_overlap(box, other) for other in occupied):
            return box
    top = max(0, min(candidates[-1], frame_height - height - base - 5))
    return (left, top, left + width + 6, top + height + base + 5)


def _rectangles_overlap(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]
def _hud(frame: Any, index:int,fps:float,rows:list[dict[str,Any]],snapshot:dict[str,Any])->None:
    import cv2
    summary=snapshot.get("summary") or {}; text=f"{index/fps:05.1f}s  Potwierdzone obserwacje: {summary.get('confirmed_detected_observation_ratio') or 0:.0%}  Nieprzypisani: {sum(row.get('identity_status')=='unresolved' for row in rows)}"; cv2.rectangle(frame,(8,8),(min(frame.shape[1]-8,610),38),(10,14,20),-1); cv2.putText(frame,text,(16,29),cv2.FONT_HERSHEY_SIMPLEX,.52,(240,240,240),1,cv2.LINE_AA)
class _ProgressEmitter:
    """Throttles renderer callbacks so callers never receive per-frame spam."""

    def __init__(self, callback: RenderProgressCallback | None) -> None:
        self.callback = callback
        self.started_at = time.monotonic()
        self.last_at = 0.0
        self.last_stage: str | None = None

    def emit(
        self,
        stage: str,
        *,
        processed_frames: int | None = None,
        total_frames: int | None = None,
        force: bool = False,
    ) -> None:
        if self.callback is None:
            return
        now = time.monotonic()
        stage_changed = stage != self.last_stage
        if not force and not stage_changed and now - self.last_at < 5.0:
            return
        elapsed = max(now - self.started_at, 0.0)
        processed = int(processed_frames or 0)
        total = int(total_frames or 0)
        speed = processed / elapsed if processed and elapsed else None
        eta = (
            (total - processed) / speed
            if total > 0 and speed and processed >= 30 and elapsed >= 2.0
            else None
        )
        self.callback(
            {
                "stage": stage,
                "processed_frames": processed_frames,
                "total_frames": total_frames,
                "progress": processed / total if total > 0 and processed_frames is not None else None,
                "elapsed_sec": elapsed,
                "frames_per_sec": speed,
                "eta_sec": eta,
            }
        )
        self.last_at = now
        self.last_stage = stage


def _parse_ffmpeg_progress(lines: list[str]) -> dict[str, int | str | None]:
    values: dict[str, int | str | None] = {"frame": None, "out_time_us": None, "progress": None}
    for line in lines:
        key, separator, value = line.strip().partition("=")
        if not separator:
            continue
        if key == "frame":
            try:
                values["frame"] = int(value)
            except ValueError:
                pass
        elif key in {"out_time_us", "out_time_ms"}:
            try:
                values["out_time_us"] = int(value)
            except ValueError:
                pass
        elif key == "progress":
            values["progress"] = value
    return values


def _encode(
    raw: Path,
    output: Path,
    *,
    total_frames: int,
    progress_callback: Callable[..., None] | None = None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for browser-playable reviewed video")
    command = [
        ffmpeg, "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
        "-i", str(raw), "-an", "-vf", "format=yuv420p", "-c:v", "libx264",
        "-preset", "veryfast", "-pix_fmt", "yuv420p", "-color_range", "tv",
        "-movflags", "+faststart", str(output),
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        lines.append(line)
        if line.strip() != "progress=continue":
            continue
        progress = _parse_ffmpeg_progress(lines)
        if progress_callback:
            progress_callback(
                "encode_mp4",
                processed_frames=int(progress.get("frame") or 0),
                total_frames=total_frames,
            )
        lines.clear()
    stderr = process.stderr.read() if process.stderr is not None else ""
    if process.wait() != 0:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg encoding failed: {stderr.strip()}")
def _ball_by_frame(path:Path)->dict[int,dict[str,Any]]: return {int(row.get("frame") or 0):row for row in _load_optional(path/"ball_tracks.json").get("positions") or []}
def _load_optional(path:Path)->dict[str,Any]:
    import json
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
