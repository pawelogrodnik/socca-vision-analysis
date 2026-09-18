#!/usr/bin/env python3
"""Run one external-only, low-confidence bridge experiment for a ball gap."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.analysis import _load_yolo_model, load_pitch_config
from app.services.ball_tracking import _resolve_ball_model_classes, collect_ball_candidates_range
from app.services.camera_motion import build_camera_motion_model
from app.services.shot_review_editor import load_shot_review_document
from app.services.video import read_match_video_metadata, resolve_match_video_path
from evaluation.operator_ball_anchor_shadow import extract_operator_ball_anchors
from evaluation.targeted_ball_gap_recovery import evaluate_operator_after_recovery, recover_targeted_gap
from scripts.probe_ball_low_confidence import _assert_external, _production_model_identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published-id", required=True, help="Used only for the after-the-fact operator comparison.")
    parser.add_argument("--source-match-id", required=True)
    parser.add_argument("--target-time-sec", required=True, type=float)
    parser.add_argument("--recovery-conf", type=float, default=.001)
    parser.add_argument("--context-sec", type=float, default=.25)
    parser.add_argument("--device")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    if not 0 < args.recovery_conf <= 1 or args.target_time_sec < 0 or args.context_sec < 0:
        raise ValueError("invalid_targeted_gap_recovery_configuration")
    for path in (args.output, args.markdown_output):
        if path is not None:
            _assert_external(path)

    result = run_experiment(
        source_match_id=args.source_match_id,
        target_time_sec=args.target_time_sec,
        recovery_conf=args.recovery_conf,
        context_sec=args.context_sec,
        device=args.device,
    )
    # Anchor loading deliberately happens only after the recovery has ended.
    anchor = _operator_anchor_after_recovery(args.published_id, args.source_match_id, args.target_time_sec)
    result["operator_evaluation"] = evaluate_operator_after_recovery(result["recovery"], anchor)
    _write_json(args.output, result)
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "evaluation_only": True, "outcome": result["recovery"]["outcome"]}, indent=2))


def run_experiment(
    *,
    source_match_id: str,
    target_time_sec: float,
    recovery_conf: float,
    context_sec: float,
    device: str | None,
) -> dict[str, Any]:
    """Run detector/bridge selection with no Shot Review or anchor input."""
    root = config.MATCHES_DIR / source_match_id
    match = _read(root / "match.json")
    tracks = _read(root / "ball_tracks.json")
    candidates = _read(root / "ball_candidates.json")
    metadata = read_match_video_metadata(root, match)
    fps = float(metadata.get("fps") or 0.0)
    if fps <= 0:
        raise ValueError("source_fps_unavailable")
    preliminary = recover_targeted_gap(tracks, target_time_sec=target_time_sec, low_confidence_frames=[], fps=fps)
    boundaries = preliminary.get("boundaries")
    if boundaries is None:
        return {
            "schema_version": "targeted-ball-gap-recovery-experiment:v1",
            "evaluation_only": True,
            "canonical_artifacts_mutated": False,
            "source_match_id": source_match_id,
            "target_time_sec": target_time_sec,
            "recovery": preliminary,
            "inference": None,
        }
    parameters = candidates.get("parameters") if isinstance(candidates.get("parameters"), dict) else {}
    production_conf = float(parameters["ball_conf"])
    start_time = max(0.0, float(boundaries["left_boundary"]["time_sec"]) - context_sec)
    end_time = float(boundaries["right_boundary"]["time_sec"]) + context_sec
    identity = _production_model_identity(root)
    pitch = load_pitch_config(root)
    video = resolve_match_video_path(root, str(metadata.get("filename") or "") or None)
    run_parameters = _production_run_parameters(root)
    camera = build_camera_motion_model(
        video,
        metadata,
        calibration_frame_time_sec=_calibration_time_sec(run_parameters, fps, pitch.calibration_frame_time_sec),
        start_time_sec=0.0,
        end_time_sec=float(run_parameters.get("max_seconds") or 0.0) or None,
        interval_sec=float(run_parameters["camera_motion_interval_sec"]),
        min_inlier_ratio=float(run_parameters["camera_motion_min_inlier_ratio"]),
        enabled=bool(run_parameters["camera_motion_compensation"]),
        reference_pitch_polygon=pitch.polygon_np,
    )
    model = _load_yolo_model(str(identity["resolved_local_path"]))
    class_configuration = _resolve_ball_model_classes(model)
    evidence = collect_ball_candidates_range(
        video,
        pitch,
        metadata,
        model=model,
        start_time_sec=start_time,
        end_time_sec=end_time,
        frame_stride=int(parameters.get("frame_stride") or 1),
        yolo_imgsz=int(parameters.get("imgsz") or 960),
        yolo_device=device or parameters.get("device"),
        ball_conf=recovery_conf,
        camera_motion=camera,
        include_raw_predictions=True,
    )
    recovery = recover_targeted_gap(
        tracks,
        target_time_sec=target_time_sec,
        low_confidence_frames=list(evidence.get("frames") or []),
        fps=fps,
    )
    frames = list(evidence.get("frames") or [])
    return {
        "schema_version": "targeted-ball-gap-recovery-experiment:v1",
        "evaluation_only": True,
        "canonical_artifacts_mutated": False,
        "source_match_id": source_match_id,
        "target_time_sec": target_time_sec,
        "model_identity": identity | {"class_configuration": class_configuration},
        "inference": {
            "production_conf": production_conf,
            "recovery_conf": recovery_conf,
            "start_time_sec": round(start_time, 6),
            "end_time_sec": round(end_time, 6),
            "processed_frames": [int(row["frame"]) for row in frames],
            "frames_rerun": len(frames),
            "raw_candidates": sum(len(row.get("raw_prediction_rows") or []) for row in frames),
            "accepted_candidates": sum(len(row.get("candidates") or []) for row in frames),
            "rejected_candidates": sum(len(row.get("rejected_candidates") or []) for row in frames),
        },
        "recovery": recovery,
    }


def _operator_anchor_after_recovery(published_id: str, source_match_id: str, target_time_sec: float) -> dict[str, Any] | None:
    anchors = extract_operator_ball_anchors(load_shot_review_document(published_id))
    matching = [anchor for anchor in anchors if str(anchor["source_match_id"]) == source_match_id]
    return min(matching, key=lambda anchor: abs(float(anchor["source_time_sec"]) - target_time_sec)) if matching else None


def _production_run_parameters(root: Path) -> dict[str, Any]:
    match = _read(root / "match.json")
    report = _read(root / "analysis_report.json")
    run_id = str(report.get("run_id") or match.get("latest_analysis_run_id") or "")
    manifest_name = str(report.get("run_manifest") or f"analysis_runs/{run_id}/run_metadata.json")
    manifest = _read(root / manifest_name)
    parameters = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
    for key in ("camera_motion_compensation", "camera_motion_interval_sec", "camera_motion_min_inlier_ratio"):
        if key not in parameters:
            raise ValueError(f"production_camera_motion_metadata_missing:{key}")
    return parameters


def _calibration_time_sec(parameters: dict[str, Any], fps: float, fallback: float) -> float:
    reference_frame = parameters.get("camera_motion_reference_frame")
    return float(reference_frame) / fps if isinstance(reference_frame, (int, float)) and not isinstance(reference_frame, bool) else float(fallback)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected_json_object:{path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _markdown(result: dict[str, Any]) -> str:
    recovery = result["recovery"]
    boundaries = recovery.get("boundaries") or {}
    inference = result.get("inference") or {}
    bridge = recovery["bridge"]
    operator = result.get("operator_evaluation") or {}
    return "\n".join([
        f"# Targeted ball gap recovery — {result['source_match_id']}",
        "",
        f"Target: {result['target_time_sec']:.3f}s",
        f"Boundaries: {boundaries.get('left_boundary', {}).get('frame')} → {boundaries.get('right_boundary', {}).get('frame')} ({boundaries.get('duration_sec')}s)",
        f"Low-confidence inference: {inference.get('frames_rerun', 0)} frames; raw={inference.get('raw_candidates', 0)}; accepted={inference.get('accepted_candidates', 0)}",
        f"Bridge: {recovery['outcome']}; candidates={bridge['candidate_ids']}; max speed={bridge['max_speed_mps']}",
        f"After-the-fact operator check: distance={operator.get('distance_px_to_operator')}; threshold={operator.get('threshold_px')}; within={operator.get('within_operator_threshold')}",
        "",
        "Read-only experiment. The operator anchor was not used to select the window, candidates, or bridge.",
    ])


if __name__ == "__main__":
    main()
