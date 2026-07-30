from __future__ import annotations

"""Offline QA package for downstream player-observation coverage.

The package is deliberately a static directory: no audit action calls the API.
The reviewer opens ``review.html`` locally and downloads one reviewed JSON file.
"""

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

import cv2
import numpy as np

from app.services.global_identity import (
    build_stable_players_from_global_identity,
)
from app.services.identity_initial_audit_frame_selection import (
    filter_identity_audit_observations,
)
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_unresolved_overlay import (
    build_unrepresented_tracklet_observations,
    build_visible_player_observations,
    identity_observation_rows_by_frame,
)
from app.services.player_observation_qa_html import (
    render_player_observation_qa_html,
)
from app.services.stabilization import _stable_overlay_frame_rows


SCHEMA_VERSION = "0.2.0"
AUDIT_KIND = "player_observation_coverage_qa"


def build_player_detection_quality_audit(
    *,
    match_path: Path,
    output_dir: Path,
    maximum_frames: int = 24,
    minimum_gap_seconds: float = 5.0,
) -> dict[str, Any]:
    """Create a small offline observation-coverage QA package from frozen data."""
    match_document = _load_json(match_path / "match.json")
    analysis_report = _load_json(match_path / "analysis_report.json")
    global_identity = _load_json(match_path / "global_identity.json")
    tracklets_document = _load_json(match_path / "tracklets.json")
    video = _resolve_video_path(match_path, match_document)
    video_info = analysis_report.get("video") or {}
    fps = max(1.0, float(video_info.get("fps") or 30.0))
    width = max(1, int(video_info.get("width") or 1))
    height = max(1, int(video_info.get("height") or 1))

    pitch_config = (
        _load_json(match_path / "pitch_config.json")
        if (match_path / "pitch_config.json").exists()
        else None
    )
    observations_by_frame = build_renderer_visible_observations(
        global_identity=global_identity,
        tracklets_document=tracklets_document,
        fps=fps,
        width=width,
        height=height,
        pitch_config=pitch_config,
    )
    raw_tracks_path = _resolve_raw_tracks_path(match_path, analysis_report)
    raw_tracks_document = (
        _load_json_value(raw_tracks_path)
        if raw_tracks_path is not None
        else None
    )
    known_false = _known_false_detections(match_path)
    selected = select_player_detection_qa_frames(
        observations_by_frame,
        fps=fps,
        known_false_frames={frame for frame, _ in known_false},
        maximum_frames=maximum_frames,
        minimum_gap_seconds=minimum_gap_seconds,
    )
    manifest = _build_manifest(
        match_document=match_document,
        analysis_report=analysis_report,
        global_identity=global_identity,
        tracklets_document=tracklets_document,
        selected_frames=selected,
        known_false=known_false,
        fps=fps,
        width=width,
        height=height,
        source_lineage=build_player_observation_source_lineage(
            match_document=match_document,
            analysis_report=analysis_report,
            global_identity=global_identity,
            tracklets_document=tracklets_document,
            raw_tracks_document=raw_tracks_document,
            visible_observations_by_frame=observations_by_frame,
        ),
    )
    _render_frames(manifest, video_path=video, output_dir=output_dir)
    _write_json(output_dir / "audit_manifest.json", manifest)
    (output_dir / "review.html").write_text(
        _render_html(manifest), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Player observation coverage QA\n\n"
        "Open `review.html` directly in a browser. Select an existing bbox and "
        "mark it as a player or a shadow/false detection. To report a missed "
        "player, choose Team A or Team B and draw a box directly on the frame. "
        "Click **Finish and download** to export one JSON file. No audit action "
        "is sent to the backend.\n",
        encoding="utf-8",
    )
    return manifest


def build_player_observation_source_lineage(
    *,
    match_document: dict[str, Any],
    analysis_report: dict[str, Any],
    global_identity: dict[str, Any],
    tracklets_document: dict[str, Any],
    raw_tracks_document: Any,
    visible_observations_by_frame: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "global_identity": canonical_digest(global_identity),
        "tracklets": canonical_digest(
            tracklets_document.get("tracklets") or []
        ),
        "rejected_tracklets": canonical_digest(
            tracklets_document.get("rejected_tracklets") or []
        ),
        "raw_tracks": (
            canonical_digest(raw_tracks_document)
            if raw_tracks_document is not None
            else None
        ),
        "match_metadata": canonical_digest(match_document),
        "analysis_metadata": canonical_digest(analysis_report),
        "video_metadata": canonical_digest(
            analysis_report.get("video") or {}
        ),
        "visible_observation_projection": canonical_digest(
            visible_observations_by_frame
        ),
    }


def build_renderer_visible_observations(
    *,
    global_identity: dict[str, Any],
    tracklets_document: dict[str, Any],
    fps: float,
    width: int,
    height: int,
    pitch_config: dict[str, Any] | None,
) -> dict[int, list[dict[str, Any]]]:
    """Return exactly the rows consumed by the visual-diagnostics renderer."""

    try:
        stable_document = build_stable_players_from_global_identity(
            global_identity
        )
    except (KeyError, TypeError, ValueError):
        # A malformed or legacy identity document still needs a deterministic
        # projection so freshness validation can reject it explicitly.
        return _observations_by_frame(
            tracklets_document,
            global_identity=global_identity,
        )
    stable_document["unmatched_observations"] = list(
        global_identity.get("unmatched_observations") or []
    )
    stable_document["unrepresented_tracklet_observations"] = (
        build_unrepresented_tracklet_observations(
            tracklets_document.get("tracklets") or [],
            global_identity,
        )
    )
    image_points = (
        (pitch_config or {}).get("image_points")
        if isinstance(pitch_config, dict)
        else None
    )
    polygon = np.asarray(
        image_points
        if isinstance(image_points, list) and len(image_points) >= 3
        else [[0, 0], [width, 0], [width, height], [0, height]],
        dtype=np.float32,
    )
    rows_by_frame = _stable_overlay_frame_rows(
        stable_document,
        polygon,
        fps=fps,
        include_untrusted=False,
        include_unmatched_raw=True,
    )
    return {
        frame: [
            {
                "stable_subject_id": row.get("stable_subject_id"),
                "tracklet_id": row.get("tracklet_id"),
                "raw_track_id": row.get("raw_track_id"),
                "team_label": str(row.get("team_label") or "U"),
                "bbox_xyxy": _valid_bbox(row.get("bbox_xyxy")),
                "confidence": row.get("confidence"),
                "observation_provenance": row.get(
                    "observation_provenance"
                ),
                "visual_trusted": bool(row.get("visual_trusted")),
                "stats_eligible": bool(row.get("stats_eligible")),
                "identity_eligible": bool(row.get("identity_eligible")),
            }
            for row in rows
            if _valid_bbox(row.get("bbox_xyxy")) is not None
        ]
        for frame, rows in rows_by_frame.items()
    }


def select_player_detection_qa_frames(
    observations_by_frame: dict[int, list[dict[str, Any]]],
    *,
    fps: float,
    known_false_frames: set[int],
    maximum_frames: int,
    minimum_gap_seconds: float,
) -> list[dict[str, Any]]:
    """Prefer known false positives and unusually sparse unique-player frames."""
    if maximum_frames < 1:
        return []
    filtered_counts = []
    details: list[dict[str, Any]] = []
    for frame, observations in observations_by_frame.items():
        filtered, filter_summary = filter_identity_audit_observations(
            observations,
            minimum_confidence=0.15,
            duplicate_containment_threshold=0.80,
        )
        filtered_counts.append(len(filtered))
        details.append(
            {
                "frame": frame,
                "observations": observations,
                "filtered_count": len(filtered),
                "filter_summary": filter_summary,
            }
        )
    expected_visible = int(round(median(filtered_counts))) if filtered_counts else 0
    for row in details:
        row["known_false"] = row["frame"] in known_false_frames
        row["missing_from_typical"] = max(
            0, expected_visible - int(row["filtered_count"])
        )
        row["priority"] = (
            100.0 * float(row["known_false"])
            + 12.0 * int(row["filter_summary"]["excluded"])
            + float(row["missing_from_typical"])
        )

    minimum_gap_frames = max(1, round(minimum_gap_seconds * fps))
    selected: list[dict[str, Any]] = []
    for row in sorted(
        details,
        key=lambda item: (-float(item["priority"]), int(item["frame"])),
    ):
        if not row["known_false"] and row["priority"] <= 0:
            continue
        if any(
            abs(int(row["frame"]) - int(previous["frame"])) < minimum_gap_frames
            for previous in selected
        ):
            continue
        selected.append(row)
        if len(selected) >= maximum_frames:
            break
    if len(selected) < maximum_frames:
        for row in sorted(details, key=lambda item: (int(item["frame"]))):
            if any(int(row["frame"]) == int(previous["frame"]) for previous in selected):
                continue
            if any(
                abs(int(row["frame"]) - int(previous["frame"])) < minimum_gap_frames
                for previous in selected
            ):
                continue
            selected.append(row)
            if len(selected) >= maximum_frames:
                break
    return sorted(selected, key=lambda row: int(row["frame"]))


def _build_manifest(
    *,
    match_document: dict[str, Any],
    analysis_report: dict[str, Any],
    global_identity: dict[str, Any],
    tracklets_document: dict[str, Any],
    selected_frames: list[dict[str, Any]],
    known_false: set[tuple[int, tuple[float, float, float, float]]],
    fps: float,
    width: int,
    height: int,
    source_lineage: dict[str, Any],
) -> dict[str, Any]:
    items = []
    for index, row in enumerate(selected_frames, start=1):
        frame = int(row["frame"])
        detections = []
        for detection_index, detection in enumerate(row["observations"], start=1):
            bbox = [round(float(value), 3) for value in detection["bbox_xyxy"]]
            false_key = (frame, tuple(bbox))
            detection_key = "player-detection:v1:" + canonical_digest(
                {
                    "frame": frame,
                    "subject": detection.get("stable_subject_id"),
                    "tracklet": detection.get("tracklet_id"),
                    "bbox": bbox,
                }
            )
            detections.append(
                {
                    "detection_key": detection_key,
                    "display_order": detection_index,
                    "bbox_xyxy": bbox,
                    "team_label": detection.get("team_label"),
                    "confidence": detection.get("confidence"),
                    "provenance": {
                        "stable_subject_id": detection.get("stable_subject_id"),
                        "tracklet_id": detection.get("tracklet_id"),
                        "raw_track_id": detection.get("raw_track_id"),
                        "observation_provenance": detection.get(
                            "observation_provenance"
                        ),
                    },
                    "visual_trusted": bool(detection.get("visual_trusted")),
                    "stats_eligible": bool(detection.get("stats_eligible")),
                    "identity_eligible": bool(
                        detection.get("identity_eligible")
                    ),
                    "initial_review_status": (
                        "false_detection" if false_key in known_false else "pending"
                    ),
                }
            )
        items.append(
            {
                "frame_key": f"frame-{frame:06d}",
                "audit_index": index,
                "frame_number": frame,
                "time_sec": round(frame / fps, 3),
                "frame_filename": f"frames/frame-{frame:06d}.jpg",
                "selection": {
                    "known_false_feedback": bool(row["known_false"]),
                    "typical_visible_players": max(
                        int(row["filtered_count"]),
                        int(row["filtered_count"]) + int(row["missing_from_typical"]),
                    ),
                    "kept_unique_observations": int(row["filtered_count"]),
                    "excluded_before_selection": int(
                        row["filter_summary"]["excluded"]
                    ),
                },
                "detections": detections,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_kind": AUDIT_KIND,
        "mode": "offline_export_only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "match_id": match_document.get("id"),
            "analysis_run_id": analysis_report.get("run_id"),
            "global_identity_digest": canonical_digest(global_identity),
            "tracklets_digest": canonical_digest(tracklets_document),
            "observation_layer": "shared_visible_observation_projection",
            "artifact_digests": source_lineage,
        },
        "video": {"fps": fps, "width": width, "height": height},
        "operator_contract": {
            "backend_calls_during_review": 0,
            "mark_existing_bbox": True,
            "draw_missing_player_bbox": True,
            "team_labels_are_not_a_review_target": True,
            "raw_coordinates_required": False,
        },
        "ui": {
            "title": "QA pokrycia obserwacji zawodnikow",
            "download_filename": "player_observation_qa_reviewed.json",
        },
        "summary": {
            "frames": len(items),
            "existing_detections": sum(len(item["detections"]) for item in items),
            "prefilled_false_detections": sum(
                detection["initial_review_status"] == "false_detection"
                for item in items
                for detection in item["detections"]
            ),
        },
        "items": items,
    }


def _observations_by_frame(
    tracklets_document: dict[str, Any],
    *,
    global_identity: dict[str, Any] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    if global_identity is not None:
        unrepresented = build_unrepresented_tracklet_observations(
            tracklets_document.get("tracklets") or [],
            global_identity,
        )
        visible = build_visible_player_observations(
            identity_rows_by_frame=identity_observation_rows_by_frame(
                global_identity
            ),
            unmatched_observations=global_identity.get(
                "unmatched_observations"
            )
            or [],
            unrepresented_tracklet_observations=unrepresented,
        )
        return {
            frame: [
                {
                    "stable_subject_id": row.get("stable_subject_id"),
                    "tracklet_id": row.get("tracklet_id"),
                    "raw_track_id": row.get("raw_track_id"),
                    "team_label": str(row.get("team_label") or "U"),
                    "bbox_xyxy": _valid_bbox(row.get("bbox_xyxy")),
                    "confidence": row.get("confidence"),
                    "observation_provenance": row.get(
                        "observation_provenance"
                    ),
                    "visual_trusted": bool(row.get("visual_trusted")),
                    "stats_eligible": bool(row.get("stats_eligible")),
                    "identity_eligible": bool(row.get("identity_eligible")),
                }
                for row in rows
                if _valid_bbox(row.get("bbox_xyxy")) is not None
            ]
            for frame, rows in visible.items()
        }
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for tracklet in tracklets_document.get("tracklets") or []:
        tracklet_id = str(tracklet.get("tracklet_id") or "")
        if not tracklet_id:
            continue
        team_label = str(
            tracklet.get("team_label")
            or tracklet.get("team_candidate")
            or "U"
        )
        for position in tracklet.get("positions_m") or []:
            bbox = _valid_bbox(position.get("bbox_xyxy"))
            if (
                bbox is None
                or str(position.get("play_area_status") or "inside")
                not in {"inside", "inside_play", "inside_pitch"}
            ):
                continue
            result[int(position.get("frame") or 0)].append(
                {
                    "stable_subject_id": None,
                    "tracklet_id": tracklet_id,
                    "team_label": team_label,
                    "bbox_xyxy": bbox,
                    "confidence": position.get("confidence")
                    or tracklet.get("mean_confidence"),
                    "observation_provenance": (
                        "unrepresented_clean_tracklet"
                    ),
                    "visual_trusted": False,
                    "stats_eligible": False,
                    "identity_eligible": False,
                }
            )
    for rows in result.values():
        rows.sort(key=lambda row: (str(row["team_label"]), str(row["stable_subject_id"])))
    return dict(result)


def _known_false_detections(
    match_path: Path,
) -> set[tuple[int, tuple[float, float, float, float]]]:
    paths = [
        match_path / "identity_operator_seeds.json",
        match_path / "identity_second_half_reanchor" / "identity_second_half_reanchor_seeds.json",
    ]
    result = set()
    for path in paths:
        if not path.exists():
            continue
        for decision in (_load_json(path).get("decisions") or []):
            if str(decision.get("action") or "") != "false_detection":
                continue
            bbox = _valid_bbox(decision.get("bbox_xyxy"))
            if bbox is not None:
                result.add((int(decision.get("frame_number") or 0), tuple(bbox)))
    return result


def _resolve_video_path(match_path: Path, match_document: dict[str, Any]) -> Path:
    local_video = match_path / "video.mp4"
    if local_video.exists():
        return local_video
    configured = Path(str((match_document.get("video") or {}).get("path") or ""))
    if configured.exists():
        return configured
    raise FileNotFoundError("Video for player-observation QA is missing")


def _resolve_raw_tracks_path(
    match_path: Path,
    analysis_report: dict[str, Any],
) -> Path | None:
    local = match_path / "tracks.json"
    if local.exists():
        return local
    source_dir = Path(
        str((analysis_report.get("parameters") or {}).get("source_dir") or "")
    )
    artifact = str(
        (analysis_report.get("artifacts") or {}).get("tracks_json")
        or "tracks.json"
    )
    candidate = source_dir / artifact
    return candidate if source_dir and candidate.exists() else None


def _render_frames(
    manifest: dict[str, Any],
    *,
    video_path: Path,
    output_dir: Path,
) -> None:
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    try:
        for item in manifest.get("items") or []:
            frame_number = int(item["frame_number"])
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, image = capture.read()
            if not ok or image is None:
                raise RuntimeError(f"Could not read frame {frame_number}")
            target = output_dir / str(item["frame_filename"])
            if not cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, 93]):
                raise RuntimeError(f"Could not write QA frame: {target}")
    finally:
        capture.release()


def _render_html(manifest: dict[str, Any]) -> str:
    return render_player_observation_qa_html(manifest)

    embedded = json.dumps(manifest, ensure_ascii=True).replace("</", "<\\/")
    title = escape(str((manifest.get("ui") or {}).get("title") or "Player detection QA"))
    filename = json.dumps(str((manifest.get("ui") or {}).get("download_filename") or "player_detection_qa_reviewed.json"))
    return f'''<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0b1220;color:#eef2f8}}body{{margin:0}}header{{position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;gap:20px;padding:16px 24px;background:#0b1220ee;border-bottom:1px solid #273650}}h1{{font-size:22px;margin:0 0 5px}}p{{margin:0;color:#9aacbf}}main{{display:grid;grid-template-columns:minmax(0,1fr) 330px;align-items:start;gap:18px;padding:20px;max-width:1800px;margin:auto}}#frame{{position:relative;align-self:start;line-height:0;background:#05080f;border:1px solid #273650}}#frame img{{display:block;width:100%;height:auto}}#overlay{{position:absolute;top:0;left:0;display:block;width:100%;height:auto;cursor:pointer;touch-action:none}}#overlay.drawing{{cursor:crosshair}}aside{{display:grid;align-content:start;gap:12px;padding:16px;border:1px solid #273650;border-radius:8px;background:#101a2b}}button,textarea{{border:1px solid #3a4b67;border-radius:6px;background:#172338;color:#eef2f8;font:inherit}}button{{padding:10px 12px;cursor:pointer}}textarea{{box-sizing:border-box;width:100%;min-height:88px;padding:10px;resize:vertical;line-height:1.4}}textarea:focus{{outline:2px solid #38bdf8;border-color:#38bdf8}}button.primary{{background:#16a34a;border-color:#16a34a;font-weight:700}}button.active{{outline:3px solid #38bdf8}}.actions{{display:grid;gap:8px}}.muted{{color:#9aacbf}}.selection{{min-height:42px;color:#cbd5e1}}.comment-label{{display:grid;gap:6px;color:#cbd5e1;font-size:14px}}.nav{{display:flex;justify-content:space-between;gap:8px}}.crop{{position:relative;overflow:hidden;display:none;background:#05080f;border:1px solid #3a4b67}}.crop img{{position:absolute;max-width:none}}.crop-target{{position:absolute;border:3px solid #22d3ee;background:#22d3ee22;pointer-events:none}}@media(max-width:900px){{main{{grid-template-columns:1fr}}}}
</style></head><body><header><div><h1>{title}</h1><p><span id="progress"></span> · zaznacz bbox lub narysuj brakującego zawodnika. Nic nie jest wysyłane do serwera.</p></div><button id="download" class="primary">Zakoncz i pobierz JSON</button></header><main><section id="frame"><img id="image" alt="Klatka QA"><canvas id="overlay"></canvas></section><aside><strong id="frameTitle"></strong><p class="muted" id="frameInfo"></p><div class="crop" id="crop"><img id="cropImage" alt="Powiekszenie wybranego bboxa"><div class="crop-target" id="cropTarget"></div></div><div class="selection" id="selection">Kliknij bbox.</div><div class="actions"><button id="player">To zawodnik</button><button id="false">Cien / falszywa detekcja</button><button id="missingA">Dodaj brakujacego Team A</button><button id="missingB">Dodaj brakujacego Team B</button></div><p class="muted">Po wybraniu Teamu A/B przeciagnij ramke bezposrednio na brakujacym zawodniku.</p><label class="comment-label" for="frameComment">Komentarz do tej klatki (opcjonalnie)<textarea id="frameComment" placeholder="Opisz dodatkowy blad lub nietypowa sytuacje..."></textarea></label><div class="nav"><button id="previous">Poprzednia</button><button id="next">Nastepna</button></div></aside></main><script>
const audit={embedded};let index=0,selectedKey=null,drawTeam=null,start=null,draftEnd=null,notice=null;const decisions={{}},missing=[],frameComments={{}};const image=document.getElementById('image'),overlay=document.getElementById('overlay'),context=overlay.getContext('2d'),frameComment=document.getElementById('frameComment');
function current(){{return audit.items[index]}}function status(d){{return decisions[d.detection_key]||d.initial_review_status||'pending'}}function drawManualBox(bbox,team,label,dashed=false){{const [x1,y1,x2,y2]=bbox,color=team==='A'?'#38bdf8':'#a78bfa';context.save();context.strokeStyle=color;context.fillStyle=color+'33';context.lineWidth=5;if(dashed)context.setLineDash([12,8]);context.strokeRect(x1,y1,x2-x1,y2-y1);context.fillRect(x1,y1,x2-x1,y2-y1);context.setLineDash([]);context.fillStyle=color;context.fillRect(x1,y1,46,22);context.fillStyle='#06101d';context.font='bold 14px system-ui';context.fillText(label,x1+5,y1+16);context.restore()}}function drawOverlay(){{if(!overlay.width)return;context.clearRect(0,0,overlay.width,overlay.height);for(const d of current().detections){{const [x1,y1,x2,y2]=d.bbox_xyxy,review=status(d),selected=d.detection_key===selectedKey;const color=selected?'#22d3ee':review==='false_detection'?'#ef4444':review==='player'?'#22c55e':'#facc15';context.strokeStyle=color;context.fillStyle=color+'33';context.lineWidth=4;context.strokeRect(x1,y1,x2-x1,y2-y1);context.fillRect(x1,y1,x2-x1,y2-y1);context.fillStyle=color;context.fillRect(x1,y1,20,18);context.fillStyle='#06101d';context.font='bold 12px system-ui';context.fillText(String(d.display_order),x1+5,y1+13)}}for(const d of missing.filter(row=>row.frame_number===current().frame_number))drawManualBox(d.bbox_xyxy,d.team_label,`+ ${{d.team_label}}`);if(start&&draftEnd&&drawTeam)drawManualBox([Math.min(start[0],draftEnd[0]),Math.min(start[1],draftEnd[1]),Math.max(start[0],draftEnd[0]),Math.max(start[1],draftEnd[1])],drawTeam,`+ ${{drawTeam}}`,true)}}
function frameMissingCount(){{return missing.filter(row=>row.frame_number===current().frame_number).length}}function render(){{const item=current();selectedKey=null;drawTeam=null;start=null;draftEnd=null;notice=null;image.src=item.frame_filename;image.onload=()=>{{overlay.width=image.naturalWidth||audit.video.width;overlay.height=image.naturalHeight||audit.video.height;drawOverlay()}};document.getElementById('frameTitle').textContent=`Klatka ${{index+1}}/${{audit.items.length}} · ${{item.time_sec}} s`;frameComment.value=frameComments[item.frame_number]||'';updateFrameInfo();renderSelection();document.getElementById('progress').textContent=`Klatka ${{index+1}} z ${{audit.items.length}}`;}}
function updateFrameInfo(){{document.getElementById('frameInfo').textContent=`Wykrycia: ${{current().detections.length}} · dorysowane: ${{frameMissingCount()}}. Wybieramy tylko bledy detektora, nie Team A/B.`}}function selected(){{return current().detections.find(d=>d.detection_key===selectedKey)}}function renderSelection(){{const d=selected(),crop=document.getElementById('crop'),cropImage=document.getElementById('cropImage'),cropTarget=document.getElementById('cropTarget');document.getElementById('selection').textContent=d?`BBox ${{d.display_order}} · Team ${{d.team_label}}`:drawTeam?`Tryb rysowania Team ${{drawTeam}}: przeciagnij ramke na zawodniku.`:notice||'Kliknij bbox.';document.getElementById('missingA').classList.toggle('active',drawTeam==='A');document.getElementById('missingB').classList.toggle('active',drawTeam==='B');overlay.classList.toggle('drawing',Boolean(drawTeam));if(!d){{crop.style.display='none';drawOverlay();return}}const [x1,y1,x2,y2]=d.bbox_xyxy,bw=x2-x1,bh=y2-y1,padx=bw*.8,pady=bh*.45,cx=Math.max(0,x1-padx),cy=Math.max(0,y1-pady),cr=Math.min(audit.video.width,x2+padx),cb=Math.min(audit.video.height,y2+pady),cw=cr-cx,ch=cb-cy;crop.style.display='block';crop.style.aspectRatio=`${{cw}} / ${{ch}}`;cropImage.src=current().frame_filename;cropImage.style.width=`${{audit.video.width/cw*100}}%`;cropImage.style.left=`${{-cx/cw*100}}%`;cropImage.style.top=`${{-cy/ch*100}}%`;cropTarget.style.left=`${{(x1-cx)/cw*100}}%`;cropTarget.style.top=`${{(y1-cy)/ch*100}}%`;cropTarget.style.width=`${{bw/cw*100}}%`;cropTarget.style.height=`${{bh/ch*100}}%`;drawOverlay()}}
document.getElementById('player').onclick=()=>{{const d=selected();if(!d)return;decisions[d.detection_key]='player';drawOverlay()}};document.getElementById('false').onclick=()=>{{const d=selected();if(!d)return;decisions[d.detection_key]='false_detection';drawOverlay()}};document.getElementById('missingA').onclick=()=>{{selectedKey=null;drawTeam='A';notice=null;renderSelection()}};document.getElementById('missingB').onclick=()=>{{selectedKey=null;drawTeam='B';notice=null;renderSelection()}};document.getElementById('previous').onclick=()=>{{index=Math.max(0,index-1);render()}};document.getElementById('next').onclick=()=>{{index=Math.min(audit.items.length-1,index+1);render()}};
function point(event){{const r=overlay.getBoundingClientRect();return [(event.clientX-r.left)/r.width*audit.video.width,(event.clientY-r.top)/r.height*audit.video.height]}}overlay.addEventListener('pointerdown',event=>{{const [x,y]=point(event);if(drawTeam){{start=[x,y];draftEnd=[x,y];notice=null;if(overlay.setPointerCapture&&event.pointerId!==undefined)overlay.setPointerCapture(event.pointerId);drawOverlay();return}}const hits=current().detections.filter(d=>{{const [x1,y1,x2,y2]=d.bbox_xyxy;return x>=x1&&x<=x2&&y>=y1&&y<=y2}}).sort((left,right)=>(left.bbox_xyxy[2]-left.bbox_xyxy[0])*(left.bbox_xyxy[3]-left.bbox_xyxy[1])-(right.bbox_xyxy[2]-right.bbox_xyxy[0])*(right.bbox_xyxy[3]-right.bbox_xyxy[1]));selectedKey=hits[0]?.detection_key||null;renderSelection()}});overlay.addEventListener('pointermove',event=>{{if(!start||!drawTeam)return;draftEnd=point(event);drawOverlay()}});overlay.addEventListener('pointerup',event=>{{if(!start||!drawTeam)return;const team=drawTeam,end=point(event),left=Math.min(start[0],end[0]),top=Math.min(start[1],end[1]),right=Math.max(start[0],end[0]),bottom=Math.max(start[1],end[1]);start=null;draftEnd=null;if(right-left>8&&bottom-top>8){{missing.push({{frame_number:current().frame_number,team_label:team,bbox_xyxy:[left,top,right,bottom].map(v=>Number(v.toFixed(3))),reviewed_at:new Date().toISOString()}});drawTeam=null;notice=`Dodano bbox Team ${{team}}.`;updateFrameInfo()}}else notice='Ramka jest za mala. Przeciagnij ponownie.';renderSelection()}});overlay.addEventListener('pointercancel',()=>{{start=null;draftEnd=null;notice='Rysowanie anulowane.';renderSelection()}});
frameComment.addEventListener('input',()=>{{const frameNumber=current().frame_number,value=frameComment.value;if(value.trim())frameComments[frameNumber]=value;else delete frameComments[frameNumber]}});
document.getElementById('download').onclick=()=>{{const output=JSON.parse(JSON.stringify(audit));output.reviewed_at=new Date().toISOString();output.manual_review={{detection_decisions:decisions,missing_players:missing,frame_comments:Object.entries(frameComments).map(([frameNumber,comment])=>({{frame_number:Number(frameNumber),comment}})).sort((left,right)=>left.frame_number-right.frame_number)}};const blob=new Blob([JSON.stringify(output,null,2)+'\\n'],{{type:'application/json'}}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download={filename};link.click();URL.revokeObjectURL(url)}};render();
</script></body></html>'''


def _valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    bbox = [float(item) for item in value]
    if not all(math.isfinite(item) for item in bbox):
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return [round(item, 3) for item in bbox]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_value(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
