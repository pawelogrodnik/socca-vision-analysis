from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import cv2

from app.services.identity_initial_audit_frame_selection import (
    build_initial_identity_audit_frame_selection,
    collect_candidate_frame_numbers,
)
from app.services.identity_jersey_number_common import canonical_digest


SCHEMA_VERSION = "0.2.0"
MODE = "initial_identity_audit_operator_seed"
AUDIT_DIRECTORY = "identity_initial_audit"
SELECTION_FILENAME = "identity_initial_audit_frame_selection.json"
FRAME_DIRECTORY = f"{AUDIT_DIRECTORY}/frames"


def prepare_initial_identity_audit(
    match_path: Path,
    video_path: Path,
    match_document: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    audit_path = match_path / AUDIT_DIRECTORY
    selection_path = audit_path / SELECTION_FILENAME
    selection = None
    if not force and selection_path.exists():
        candidate = _load_json(selection_path)
        if _selection_artifacts_exist(match_path, candidate):
            selection = candidate

    if selection is None:
        analysis_report = _load_required_artifact(
            match_path,
            match_document,
            "analysis_report.json",
        )
        global_identity = _load_required_artifact(
            match_path,
            match_document,
            "global_identity.json",
        )
        tracklets = _load_required_artifact(
            match_path,
            match_document,
            "tracklets.json",
        )
        camera_motion = _load_optional_artifact(
            match_path,
            match_document,
            "camera_motion_report.json",
        )
        candidate_frames = collect_candidate_frame_numbers(
            global_identity,
            stride_frames=15,
        )
        visual_metrics = _read_visual_metrics(video_path, candidate_frames)
        selection = build_initial_identity_audit_frame_selection(
            global_identity,
            tracklets,
            analysis_report,
            camera_motion_report=camera_motion,
            frame_visual_metrics=visual_metrics,
            generated_at=datetime.now(timezone.utc).isoformat(),
            artifact_directory=FRAME_DIRECTORY,
        )
        frames = [int(row["frame"]) for row in selection["selected_frames"]]
        frames_path = audit_path / "frames"
        frames_path.mkdir(parents=True, exist_ok=True)
        _export_selected_frames(video_path, frames, frames_path)
        selection_path.write_text(
            json.dumps(selection, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return build_initial_identity_audit_document(selection, match_document)


def build_initial_identity_audit_document(
    selection: dict[str, Any],
    match_document: dict[str, Any],
) -> dict[str, Any]:
    video = selection.get("video") or {}
    frames: list[dict[str, Any]] = []
    selected_rows = (selection.get("selected_frames") or [])[:10]
    for selection_index, row in enumerate(selected_rows, start=1):
        frame_number = int(row.get("frame") or 0)
        observations = []
        for detection_index, detection in enumerate(
            row.get("visible_detections") or [],
            start=1,
        ):
            bbox = _valid_bbox(detection.get("bbox_xyxy"))
            if bbox is None:
                continue
            observation_key = (
                "observation:v1:"
                + canonical_digest(
                    {
                        "frame": frame_number,
                        "stable_subject_id": detection.get("stable_subject_id"),
                        "tracklet_id": detection.get("tracklet_id"),
                        "bbox_xyxy": bbox,
                    }
                )
            )
            observations.append(
                {
                    "observation_key": observation_key,
                    "bbox_xyxy": bbox,
                    "team_label": _team_label(detection.get("team_label")),
                    "role": str(detection.get("role") or "unknown"),
                    "provenance": {
                        "stable_subject_id": detection.get("stable_subject_id"),
                        "stable_player_id": detection.get("stable_player_id"),
                        "slot_id": detection.get("slot_id"),
                        "tracklet_id": detection.get("tracklet_id"),
                        "raw_track_id": detection.get("raw_track_id"),
                        "stint_id": detection.get("stint_id"),
                        "source": detection.get("source"),
                    },
                    "display_order": detection_index,
                }
            )
        frames.append(
            {
                "audit_frame_key": f"audit-frame-{selection_index:02d}",
                "frame_number": frame_number,
                "time_sec": float(row.get("time_sec") or 0.0),
                "full_frame_artifact": str(row.get("full_frame_artifact") or ""),
                "thumbnail_artifact": str(row.get("thumbnail_artifact") or ""),
                "observations": observations,
            }
        )

    roster = _operator_roster(match_document)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "read_only": False,
        "selection_digest": selection.get("selection_digest"),
        "video": {
            "fps": float(video.get("fps") or 30.0),
            "frame_count": int(video.get("frame_count") or 0),
            "duration_sec": float(video.get("duration_sec") or 0.0),
            "width": int(video.get("width") or 1),
            "height": int(video.get("height") or 1),
        },
        "summary": {
            "selected_frames": len(frames),
            "visible_observations": sum(
                len(frame["observations"])
                for frame in frames
            ),
            "maximum_frames": 10,
            "target_actions": "8-12 certain assignments",
        },
        "roster": roster,
        "frames": frames,
        "actions": [
            "assign_roster_player",
            "team_a_unknown",
            "team_b_unknown",
            "referee",
            "false_detection",
            "skip",
        ],
        "operator_contract": {
            "certainty": "certain_assignment_or_skip",
            "finish_before_full_coverage": True,
            "raw_coordinates_required": False,
            "technical_ids_visible": False,
            "decisions_persisted": True,
        },
        "safety": {
            "production_identity_untouched": True,
            "candidate_identity_untouched": True,
            "yolo_not_required": True,
            "downstream_rebuild_triggered": False,
        },
    }


def _operator_roster(match_document: dict[str, Any]) -> list[dict[str, Any]]:
    roster = []
    for index, team in enumerate(match_document.get("teams") or []):
        label = "A" if index == 0 else "B" if index == 1 else "U"
        players = []
        for player in team.get("players") or []:
            player_id = str(player.get("id") or "")
            if not player_id:
                continue
            number = str(player.get("number") or "").strip()
            if number.lower() in {"", "player", "goalkeeper", "unknown", "none"}:
                number = ""
            players.append(
                {
                    "player_id": player_id,
                    "player_name": str(player.get("name") or "Unknown"),
                    "player_number": number or None,
                    "player_role": str(player.get("role") or "unknown"),
                }
            )
        roster.append(
            {
                "team_label": label,
                "team_id": team.get("id"),
                "team_name": str(team.get("name") or f"Team {label}"),
                "players": players,
            }
        )
    return roster


def _selection_artifacts_exist(
    match_path: Path,
    selection: dict[str, Any],
) -> bool:
    frames = selection.get("selected_frames")
    if not isinstance(frames, list) or not frames:
        return False
    for row in frames:
        if not isinstance(row, dict):
            return False
        for key in ("full_frame_artifact", "thumbnail_artifact"):
            artifact = str(row.get(key) or "")
            artifact_path = (match_path / artifact).resolve()
            if match_path.resolve() not in artifact_path.parents:
                return False
            if not artifact_path.exists() or artifact_path.stat().st_size == 0:
                return False
    return True


def _load_required_artifact(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> dict[str, Any]:
    document = _load_optional_artifact(match_path, match_document, filename)
    if document is None:
        raise FileNotFoundError(f"{filename} not found. Run analysis first.")
    return document


def _load_optional_artifact(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> dict[str, Any] | None:
    for candidate in _artifact_candidates(match_path, match_document, filename):
        if candidate.exists():
            return _load_json(candidate)
    return None


def _artifact_candidates(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> list[Path]:
    candidates = [match_path / filename]
    runs = match_document.get("analysis_runs") or []
    latest_run_id = str(match_document.get("latest_analysis_run_id") or "")
    ordered_runs = sorted(
        (row for row in runs if isinstance(row, dict)),
        key=lambda row: (
            str(row.get("run_id") or "") != latest_run_id,
            str(row.get("generated_at") or ""),
        ),
    )
    for row in ordered_runs:
        run_directory = str(row.get("run_directory") or "")
        if run_directory:
            candidates.append(match_path / run_directory / filename)
    return candidates


def _read_visual_metrics(
    video_path: Path,
    frames: list[int],
) -> dict[int, dict[str, float]]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    metrics: dict[int, dict[str, float]] = {}
    try:
        for frame_number in frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            metrics[frame_number] = {
                "blur_variance": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
            }
    finally:
        capture.release()
    return metrics


def _export_selected_frames(
    video_path: Path,
    frames: list[int],
    output_directory: Path,
) -> None:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        for frame_number in frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"Could not read frame {frame_number}")
            full_path = output_directory / f"frame-{frame_number:06d}.jpg"
            thumbnail_path = (
                output_directory / f"frame-{frame_number:06d}-thumb.jpg"
            )
            if not cv2.imwrite(
                str(full_path),
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, 93],
            ):
                raise RuntimeError(f"Could not write {full_path}")
            thumbnail = _fit_width(frame, 640)
            if not cv2.imwrite(
                str(thumbnail_path),
                thumbnail,
                [cv2.IMWRITE_JPEG_QUALITY, 88],
            ):
                raise RuntimeError(f"Could not write {thumbnail_path}")
    finally:
        capture.release()


def _fit_width(frame: Any, width: int) -> Any:
    if frame.shape[1] <= width:
        return frame
    scale = width / frame.shape[1]
    return cv2.resize(
        frame,
        (width, max(1, round(frame.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _team_label(value: Any) -> str:
    normalized = str(value or "U").upper()
    return normalized if normalized in {"A", "B"} else "U"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
