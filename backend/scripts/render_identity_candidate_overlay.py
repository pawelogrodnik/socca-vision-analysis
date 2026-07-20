from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_candidate_overlay import render_identity_candidate_overlay
from app.services.identity_candidate_shadow import build_identity_candidate_shadow
from app.services.identity_active_roster_shadow import build_identity_active_roster_shadow


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the P1.5 shadow identity-only overlay.")
    parser.add_argument("--video", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidate-overlay", type=Path)
    source.add_argument("--candidate-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument(
        "--active-roster",
        action="store_true",
        help="Apply the P1.6/P1.7 shadow active-roster selector before rendering.",
    )
    args = parser.parse_args()
    if args.active_roster and args.candidate_overlay:
        parser.error("--active-roster requires --candidate-dir so the selector can read candidate metadata.")
    if args.candidate_overlay:
        document = json.loads(args.candidate_overlay.resolve().read_text(encoding="utf-8"))
    else:
        candidate_dir = args.candidate_dir.resolve()
        documents = build_identity_candidate_shadow(
            json.loads((candidate_dir / "identity_offline_shadow.json").read_text(encoding="utf-8")),
            json.loads((candidate_dir / "identity_offline_shadow_timeline.json").read_text(encoding="utf-8")),
            json.loads((candidate_dir / "global_identity.json").read_text(encoding="utf-8")),
            fps=_timeline_fps(candidate_dir),
            generated_at="on-demand-overlay",
            include_overlay=True,
        )
        document = documents["identity_candidate_shadow_overlay"]
        if args.active_roster:
            active_documents = build_identity_active_roster_shadow(
                documents["identity_candidate_shadow"],
                document,
                generated_at="on-demand-overlay",
                include_overlay=True,
            )
            document = active_documents["identity_active_roster_shadow_overlay"]
    output = render_identity_candidate_overlay(
        args.video.resolve(),
        args.output.resolve(),
        document,
        start_sec=float(args.start_sec),
        max_seconds=args.max_seconds,
    )
    print(json.dumps({"status": "completed", "output": str(output)}, indent=2))


def _timeline_fps(candidate_dir: Path) -> float:
    analysis_report_path = candidate_dir / "analysis_report.json"
    if analysis_report_path.exists():
        report = json.loads(analysis_report_path.read_text(encoding="utf-8"))
        return float((report.get("video") or {}).get("fps") or 25.0)
    return 25.0


if __name__ == "__main__":
    main()
