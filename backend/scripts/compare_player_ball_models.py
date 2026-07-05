from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.analysis import analyze_match
from app.services.runtime import collect_runtime_info, normalize_yolo_device
from app.services.video import read_video_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare baseline player+ball detection with a custom unified model.")
    parser.add_argument("--match-id", default="682c5606")
    parser.add_argument("--max-seconds", type=float, default=30.0)
    parser.add_argument("--frame-stride", type=int, default=1)
    parser.add_argument("--yolo-device", default="auto")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--baseline-player-model", default="yolov8n.pt")
    parser.add_argument("--baseline-ball-model", default="models/best.pt")
    parser.add_argument("--custom-model", default="models/best-model-with-ball-and-players.pt")
    parser.add_argument("--yolo-conf", type=float, default=0.05)
    parser.add_argument("--ball-conf", type=float, default=0.03)
    parser.add_argument("--player-imgsz", type=int, default=1280)
    parser.add_argument("--ball-imgsz", type=int, default=960)
    args = parser.parse_args()

    from app.config import MATCHES_DIR, STORAGE_DIR

    match_dir = MATCHES_DIR / args.match_id
    video_path = find_video(match_dir)
    pitch_config_path = match_dir / "pitch_config.json"
    if not pitch_config_path.exists():
        raise FileNotFoundError(f"pitch_config.json not found: {pitch_config_path}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root or STORAGE_DIR / "benchmarks" / f"{timestamp}-player-ball-model-comparison"
    output_root.mkdir(parents=True, exist_ok=False)

    scenarios = [
        {
            "label": "baseline-yolov8n-plus-ball-best",
            "yolo_model": args.baseline_player_model,
            "ball_yolo_model": args.baseline_ball_model,
        },
        {
            "label": "custom-player-ball",
            "yolo_model": args.custom_model,
            "ball_yolo_model": args.custom_model,
        },
    ]
    runtime_info = collect_runtime_info()
    comparison: dict[str, Any] = {
        "schema_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "match_id": args.match_id,
        "video_path": str(video_path),
        "video": read_video_metadata(video_path),
        "pitch_config_path": str(pitch_config_path),
        "parameters": {
            "max_seconds": args.max_seconds,
            "frame_stride": max(1, args.frame_stride),
            "yolo_device_requested": args.yolo_device,
            "yolo_device": normalize_yolo_device(args.yolo_device) or "auto",
            "yolo_conf": args.yolo_conf,
            "ball_conf": args.ball_conf,
            "player_imgsz": args.player_imgsz,
            "ball_imgsz": args.ball_imgsz,
            "yolo_tracker": "centroid_high_recall",
        },
        "runtime": runtime_info,
        "scenarios": [],
    }

    for scenario in scenarios:
        scenario_dir = output_root / scenario["label"]
        scenario_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(pitch_config_path, scenario_dir / "pitch_config.json")
        started = time.perf_counter()
        report = analyze_match(
            scenario_dir,
            video_path,
            adapter="yolo",
            max_seconds=float(args.max_seconds),
            frame_stride=max(1, int(args.frame_stride)),
            yolo_model=scenario["yolo_model"],
            yolo_conf=float(args.yolo_conf),
            yolo_imgsz=int(args.player_imgsz),
            yolo_tracker="centroid_high_recall",
            yolo_device=normalize_yolo_device(args.yolo_device),
            include_ball=True,
            ball_yolo_model=scenario["ball_yolo_model"],
            ball_yolo_conf=float(args.ball_conf),
            ball_yolo_imgsz=int(args.ball_imgsz),
            ball_yolo_device=normalize_yolo_device(args.yolo_device),
        )
        elapsed = time.perf_counter() - started
        scenario_summary = scenario_comparison_summary(scenario, scenario_dir, report, elapsed)
        comparison["scenarios"].append(scenario_summary)
        (scenario_dir / "scenario_summary.json").write_text(json.dumps(scenario_summary, indent=2), encoding="utf-8")

    comparison["diff"] = build_diff(comparison["scenarios"])
    (output_root / "comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    print(json.dumps(comparison, indent=2))


def find_video(match_dir: Path) -> Path:
    for candidate in sorted(match_dir.glob("video.*")):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Video file not found in {match_dir}")


def scenario_comparison_summary(
    scenario: dict[str, Any],
    scenario_dir: Path,
    report: dict[str, Any],
    elapsed_sec: float,
) -> dict[str, Any]:
    ball_report = read_json(scenario_dir / "ball_tracking_report.json")
    ball_candidates = read_json(scenario_dir / "ball_candidates.json")
    frame_counts = read_json(scenario_dir / "frame_detection_counts.json")
    global_report = read_json(scenario_dir / "global_identity_report.json")
    movement_stats = read_json(scenario_dir / "movement_stats.json")
    return {
        "label": scenario["label"],
        "output_dir": str(scenario_dir),
        "elapsed_sec": round(elapsed_sec, 3),
        "models": {
            "yolo_model": scenario["yolo_model"],
            "ball_yolo_model": scenario["ball_yolo_model"],
        },
        "artifacts": report.get("artifacts") or {},
        "players": {
            "player_class_ids": ((report.get("parameters") or {}).get("player_class_ids")),
            "player_class_names": ((report.get("parameters") or {}).get("player_class_names")),
            "model_classes": ((report.get("parameters") or {}).get("model_classes")),
            "frames_processed": report.get("frames_processed"),
            "detections_kept": report.get("detections_kept"),
            "detections_rejected_outside_pitch": report.get("detections_rejected_outside_pitch"),
            "tracks_count": report.get("tracks_count"),
            "stable_players_count": report.get("stable_players_count"),
            "frame_count_summary": summarize_frame_counts(frame_counts),
            "global_identity_summary": (global_report.get("summary") or {}),
            "movement_summary": summarize_movement(movement_stats),
        },
        "ball": {
            "summary": ((report.get("ball_tracking_summary") or {}) or (ball_report.get("summary") or {})),
            "quality_summary": report.get("ball_quality_summary"),
            "candidate_summary": summarize_ball_candidates(ball_candidates),
        },
        "warnings": report.get("warnings") or [],
    }


def summarize_frame_counts(doc: dict[str, Any]) -> dict[str, Any]:
    frames = doc.get("frames") or []
    if not isinstance(frames, list) or not frames:
        return {}
    visible_values = [int(frame.get("visible_stable_boxes") or 0) for frame in frames if isinstance(frame, dict)]
    trusted_values = [int(frame.get("trusted_detected") or 0) for frame in frames if isinstance(frame, dict)]
    return {
        "frames": len(frames),
        "avg_visible_stable_boxes": round(sum(visible_values) / len(visible_values), 3) if visible_values else 0,
        "min_visible_stable_boxes": min(visible_values) if visible_values else 0,
        "max_visible_stable_boxes": max(visible_values) if visible_values else 0,
        "avg_trusted_detected": round(sum(trusted_values) / len(trusted_values), 3) if trusted_values else 0,
    }


def summarize_movement(doc: dict[str, Any]) -> dict[str, Any]:
    players = doc.get("players") or []
    if not isinstance(players, list):
        return {}
    return {
        "players_with_stats": len(players),
        "total_distance_m": round(sum(float(player.get("distance_m") or 0.0) for player in players), 2),
        "max_top_speed_kmh": round(max((float(player.get("top_speed_kmh") or 0.0) for player in players), default=0.0), 2),
    }


def summarize_ball_candidates(doc: dict[str, Any]) -> dict[str, Any]:
    frames = doc.get("frames") or []
    if not isinstance(frames, list):
        return {}
    candidate_counts = [len(frame.get("candidates") or []) for frame in frames if isinstance(frame, dict)]
    raw_counts = [int(frame.get("raw_predictions") or 0) for frame in frames if isinstance(frame, dict)]
    return {
        "frames": len(frames),
        "frames_with_candidates": sum(1 for value in candidate_counts if value > 0),
        "total_candidates": sum(candidate_counts),
        "total_raw_predictions": sum(raw_counts),
        "avg_candidates_per_frame": round(sum(candidate_counts) / len(candidate_counts), 3) if candidate_counts else 0,
    }


def build_diff(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    if len(scenarios) != 2:
        return {}
    baseline, custom = scenarios
    return {
        "custom_minus_baseline": {
            "elapsed_sec": round(float(custom["elapsed_sec"]) - float(baseline["elapsed_sec"]), 3),
            "player_detections_kept": numeric_diff(custom, baseline, ["players", "detections_kept"]),
            "tracks_count": numeric_diff(custom, baseline, ["players", "tracks_count"]),
            "stable_players_count": numeric_diff(custom, baseline, ["players", "stable_players_count"]),
            "avg_visible_stable_boxes": numeric_diff(custom, baseline, ["players", "frame_count_summary", "avg_visible_stable_boxes"]),
            "ball_total_candidates": numeric_diff(custom, baseline, ["ball", "candidate_summary", "total_candidates"]),
            "ball_frames_with_candidates": numeric_diff(custom, baseline, ["ball", "candidate_summary", "frames_with_candidates"]),
        }
    }


def numeric_diff(left: dict[str, Any], right: dict[str, Any], path: list[str]) -> float | int | None:
    left_value = nested_get(left, path)
    right_value = nested_get(right, path)
    if left_value is None or right_value is None:
        return None
    diff = float(left_value) - float(right_value)
    return round(diff, 3)


def nested_get(data: dict[str, Any], path: list[str]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


if __name__ == "__main__":
    main()
