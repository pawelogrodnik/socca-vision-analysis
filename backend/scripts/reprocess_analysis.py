from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import MATCHES_DIR
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
    parser.add_argument("--no-ball", action="store_true", help="Ignore stored ball artifacts even if present.")
    parser.add_argument("--no-possession", action="store_true", help="Skip possession/contact candidate rebuild.")
    parser.add_argument("--raw-overlay", action="store_true", help="Also rebuild raw P## overlay from tracks.json.")
    parser.add_argument("--debug-overlay", action="store_true", help="Also write debug_identity_overlay.mp4.")
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
    )
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
                "warnings": report.get("warnings") or [],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
