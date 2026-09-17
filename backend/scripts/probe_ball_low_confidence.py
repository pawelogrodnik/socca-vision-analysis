#!/usr/bin/env python3
"""Run a localized, external-only ball detector confidence sweep."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app import config
from app.services.analysis import _load_yolo_model, _resolve_yolo_model_name, load_pitch_config
from app.services.ball_tracking import _resolve_ball_model_classes, collect_ball_candidates_range
from app.services.camera_motion import build_camera_motion_model
from app.services.shot_review_editor import load_shot_review_document
from app.services.video import read_match_video_metadata, resolve_match_video_path
from evaluation.ball_low_confidence_shadow_probe import (
    DEFAULT_THRESHOLDS,
    compact_probe_report,
    probe_low_confidence,
    render_probe_markdown,
)
from evaluation.operator_ball_anchor_shadow import extract_operator_ball_anchors
from scripts.analyze_operator_ball_detector_misses import _source_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published-id", required=True)
    parser.add_argument("--thresholds", default=",".join(map(str, DEFAULT_THRESHOLDS)))
    parser.add_argument("--window-sec", type=float, default=1.0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="Optional external directory for per-anchor exact-frame PNG and JSON evidence.",
    )
    args = parser.parse_args()
    if args.window_sec <= 0:
        raise ValueError("window_sec_must_be_positive")
    for path in (args.output, args.compact_output, args.markdown_output, args.evidence_dir):
        if path is not None:
            _assert_external(path)
    anchors = extract_operator_ball_anchors(load_shot_review_document(args.published_id))
    sources = _source_artifacts(anchors)
    contexts: dict[str, tuple[Any, Any, dict[str, Any], Path, dict[str, Any], dict[str, Any]]] = {}
    cameras: dict[tuple[str, float], Any] = {}

    def runner(source: dict[str, Any], threshold: float, window: float) -> dict[str, Any]:
        source_id = str(source["_source_match_id"])
        root = config.MATCHES_DIR / source_id
        if source_id not in contexts:
            parameters = source["ball_candidates"].get("parameters") or {}
            identity = _production_model_identity(root)
            resolved = Path(identity["resolved_local_path"])
            pitch = load_pitch_config(root)
            metadata = read_match_video_metadata(
                root,
                json.loads((root / "match.json").read_text()),
            )
            video = resolve_match_video_path(root, str(metadata.get("filename") or "") or None)
            model = _load_yolo_model(str(resolved))
            identity["detector_source"] = _resolve_ball_model_classes(model)["source"]
            identity["model_class_configuration"] = _resolve_ball_model_classes(model)
            contexts[source_id] = (model, pitch, parameters, video, metadata, identity)
        model, pitch, parameters, video, metadata, identity = contexts[source_id]
        camera_key = (source_id, float(source["_anchor_time"]))
        if camera_key not in cameras:
            camera = build_camera_motion_model(
                video,
                metadata,
                calibration_frame_time_sec=pitch.calibration_frame_time_sec,
                start_time_sec=max(0.0, float(source["_anchor_time"]) - window), end_time_sec=float(source["_anchor_time"]) + window,
                reference_pitch_polygon=pitch.polygon_np,
                enabled=bool(parameters.get("camera_motion_compensation", False)),
            )
            cameras[camera_key] = camera
        camera = cameras[camera_key]
        # This is production's localized detector path; no artifact write occurs.
        result = collect_ball_candidates_range(
            video,
            pitch,
            metadata,
            model=model,
            start_time_sec=max(0.0, float(source["_anchor_time"]) - window), end_time_sec=float(source["_anchor_time"]) + window,
            frame_stride=int(parameters.get("frame_stride") or 1),
            yolo_imgsz=int(parameters.get("imgsz") or 960),
            yolo_device=args.device or parameters.get("device"),
            ball_conf=threshold,
            camera_motion=camera,
            include_raw_predictions=True,
        )
        result["fps"] = metadata["fps"]
        result["model_identity"] = identity
        return result
    thresholds = [float(value) for value in args.thresholds.split(",") if value.strip()]
    report = probe_low_confidence(
        anchors,
        sources,
        thresholds=thresholds,
        window_sec=args.window_sec,
        runner=runner,
    )
    _write_json(args.output, report)
    if args.compact_output:
        _write_json(args.compact_output, compact_probe_report(report))
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_probe_markdown(report), encoding="utf-8")
    if args.evidence_dir:
        _write_evidence(args.evidence_dir, report, contexts)
    print(json.dumps({"output": str(args.output), "evaluation_only": True, "anchors": len(report["anchors"])}, indent=2))


def _assert_external(path: Path) -> None:
    matches = config.MATCHES_DIR.resolve()
    if path.resolve() == matches or matches in path.resolve().parents:
        raise ValueError("diagnostic_output_must_not_be_under_match_storage")


def _production_model_identity(root: Path) -> dict[str, Any]:
    """Resolve the ball weights from the persisted source analysis manifest."""
    match = _read_json_object(root / "match.json")
    report = _read_json_object(root / "analysis_report.json")
    run_id = str(report.get("run_id") or match.get("latest_analysis_run_id") or "")
    manifest_name = str(report.get("run_manifest") or "")
    if not manifest_name and run_id:
        manifest_name = f"analysis_runs/{run_id}/run_metadata.json"
    if not manifest_name:
        raise ValueError("production_ball_model_identity_unavailable:missing_run_manifest")
    manifest_path = root / manifest_name
    if not manifest_path.is_file():
        raise ValueError("production_ball_model_identity_unavailable:run_manifest_missing")
    manifest = _read_json_object(manifest_path)
    parameters = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
    persisted_model = parameters.get("ball_yolo_model")
    if not isinstance(persisted_model, str) or not persisted_model.strip():
        raise ValueError("production_ball_model_identity_unavailable:ball_yolo_model_missing")
    resolved = Path(_resolve_yolo_model_name(persisted_model))
    if not resolved.is_file():
        raise FileNotFoundError(f"production_ball_model_file_unavailable:{resolved}")
    return {
        "source_analysis_run_id": run_id or None,
        "run_manifest": str(manifest_path),
        "persisted_ball_yolo_model": persisted_model,
        "resolved_local_path": str(resolved),
        "model_file_exists": True,
        "model_sha256": _file_sha256(resolved),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected_json_object:{path}")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _write_evidence(
    directory: Path,
    report: dict[str, Any],
    contexts: dict[str, tuple[Any, Any, dict[str, Any], Path, dict[str, Any], dict[str, Any]]],
) -> None:
    """Render exact-frame raw/accepted/rejected evidence outside match storage."""
    import cv2

    directory.mkdir(parents=True, exist_ok=True)
    for row in report["anchors"]:
        if row["role"] != "raw_detector_miss":
            continue
        anchor = row["anchor"]
        source_id = str(anchor["source_match_id"])
        _model, _pitch, _parameters, video, _metadata, _identity = contexts[source_id]
        for threshold_row in row["thresholds"]:
            frame = threshold_row["exact_frame"]["frame"]
            capture = cv2.VideoCapture(str(video))
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame))
            ok, image = capture.read()
            capture.release()
            if not ok or image is None:
                continue
            _draw_anchor(image, anchor)
            _draw_rows(image, threshold_row.get("raw_predictions") or [], (255, 170, 0), "raw")
            _draw_rows(image, threshold_row.get("accepted_candidates") or [], (0, 220, 0), "accepted")
            _draw_rows(image, threshold_row.get("rejected_candidates") or [], (0, 80, 255), "rejected")
            label = (
                f"conf={threshold_row['threshold']:g} frame={frame} "
                f"raw={threshold_row['raw_prediction_count']} "
                f"accepted={threshold_row['accepted_candidate_count']}"
            )
            cv2.putText(image, label, (16, 28), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 2, cv2.LINE_AA)
            base = f"{source_id}-{anchor['canonical_shot_id']}-f{int(frame):06d}-c{threshold_row['threshold']:g}"
            cv2.imwrite(str(directory / f"{base}.png"), image)
            _write_json(
                directory / f"{base}.json",
                {"anchor": anchor, "recovery": row["recovery"], "threshold": threshold_row},
            )


def _draw_anchor(image: Any, anchor: dict[str, Any]) -> None:
    import cv2

    cv2.drawMarker(
        image,
        (round(float(anchor["x_px"])), round(float(anchor["y_px"]))),
        (0, 255, 255),
        cv2.MARKER_CROSS,
        28,
        2,
    )


def _draw_rows(image: Any, rows: list[dict[str, Any]], color: tuple[int, int, int], label: str) -> None:
    import cv2

    for row in rows:
        x1, y1, x2, y2 = [round(float(value)) for value in row["bbox_xyxy"]]
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            image,
            f"{label} {float(row.get('confidence') or 0):.4f}",
            (x1, max(16, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            .45,
            color,
            1,
            cv2.LINE_AA,
        )


if __name__ == "__main__":
    main()
