from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import MATCHES_DIR
from app.services.pass_quality import evaluate_pass_candidates_against_gold, load_pass_goldset
from app.services.post_yolo_reprocess import default_reprocess_output_dir, reprocess_match_from_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-run post-YOLO Orlik Vision analysis from stored tracks/ball artifacts.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--match-id", default=None, help="Existing match id from backend/storage/matches.")
    source.add_argument("--source-dir", type=Path, default=None, help="Directory containing pitch_config.json and tracks.json.")
    parser.add_argument("--video", type=Path, default=None, help="Video path override. Usually inferred from match dir or benchmark_input.json.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Where to write reprocessed outputs. Defaults to backend/storage/reprocess.")
    parser.add_argument("--label", default="", help="Human label for the default output directory and report.")
    parser.add_argument("--start-sec", type=float, default=0.0, help="Start time for a trimmed post-YOLO reprocess window.")
    parser.add_argument("--max-seconds", type=float, default=0.0, help="Limit post-YOLO reprocess to this many seconds.")
    parser.add_argument("--no-ball", action="store_true", help="Ignore stored ball artifacts even if present.")
    parser.add_argument("--no-possession", action="store_true", help="Skip possession/contact candidate rebuild.")
    parser.add_argument("--raw-overlay", action="store_true", help="Also rebuild raw P## overlay from tracks.json.")
    parser.add_argument("--debug-overlay", action="store_true", help="Also write debug_identity_overlay.mp4.")
    parser.add_argument("--no-stable-overlay", action="store_true", help="Skip stable_overlay_preview.mp4 render.")
    parser.add_argument("--no-identity-diagnostics", action="store_true", help="Disable read-only P0 identity diagnostics.")
    parser.add_argument("--pass-goldset", type=Path, default=None, help="Optional manual pass goldset JSON for quality evaluation.")
    parser.add_argument("--pass-gold-tolerance-frames", type=int, default=45, help="Frame tolerance for pass goldset matching.")
    parser.add_argument(
        "--player-label",
        action="append",
        default=[],
        metavar="SLOT=LABEL",
        help="Override a stable overlay label, e.g. --player-label A06=Krzysiek. Can be repeated.",
    )
    args = parser.parse_args()

    source_dir = (MATCHES_DIR / args.match_id) if args.match_id else args.source_dir
    if source_dir is None:
        raise ValueError("Missing source directory.")
    output_dir = args.output_dir or default_reprocess_output_dir(source_dir, label=args.label or args.match_id or "")

    report = reprocess_match_from_artifacts(
        source_dir,
        args.video,
        output_dir=output_dir,
        label=args.label or args.match_id or source_dir.name,
        include_ball=False if args.no_ball else None,
        build_possession=not args.no_possession,
        write_raw_overlay=bool(args.raw_overlay),
        write_debug_overlay=bool(args.debug_overlay),
        render_stable_overlay=not args.no_stable_overlay,
        enable_identity_diagnostics=not args.no_identity_diagnostics,
        player_label_overrides=_parse_player_labels(args.player_label),
        start_sec=max(0.0, float(args.start_sec or 0.0)),
        max_seconds=max(0.0, float(args.max_seconds or 0.0)) or None,
        progress=_print_progress,
    )
    pass_quality_report = None
    if args.pass_goldset:
        pass_quality_report = _evaluate_pass_quality(output_dir, args.pass_goldset, args.pass_gold_tolerance_frames)

    print(
        json.dumps(
            {
                "status": report.get("status"),
                "analysis_type": report.get("analysis_type"),
                "output_dir": str(output_dir.resolve()),
                "run_id": report.get("run_id"),
                "tracks_count": report.get("tracks_count"),
                "stable_players_count": report.get("stable_players_count"),
                "ball_tracking_summary": report.get("ball_tracking_summary"),
                "pass_quality_summary": (pass_quality_report or {}).get("summary"),
                "warnings": report.get("warnings") or [],
            },
            indent=2,
        )
    )


def _parse_player_labels(values: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for value in values:
        slot_id, sep, label = value.partition("=")
        if not sep or not slot_id.strip() or not label.strip():
            raise ValueError(f"Invalid --player-label value: {value!r}. Expected SLOT=LABEL.")
        labels[slot_id.strip()] = label.strip()
    return labels


def _print_progress(stage: str, progress_percent: float, message: str, extra: dict[str, Any] | None) -> None:
    payload = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "progress_percent": round(float(progress_percent), 2),
        "message": message,
    }
    if extra:
        payload["extra"] = extra
    print(json.dumps(payload, ensure_ascii=True), flush=True)


def _evaluate_pass_quality(output_dir: Path, goldset_path: Path, tolerance_frames: int) -> dict[str, Any]:
    pass_path = output_dir / "pass_candidates.json"
    if not pass_path.exists():
        raise FileNotFoundError(f"Cannot evaluate passes; missing {pass_path}")
    pass_doc = json.loads(pass_path.read_text(encoding="utf-8"))
    report = evaluate_pass_candidates_against_gold(
        pass_doc,
        load_pass_goldset(goldset_path),
        tolerance_frames=max(0, int(tolerance_frames)),
    )
    (output_dir / "pass_quality_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    main()
