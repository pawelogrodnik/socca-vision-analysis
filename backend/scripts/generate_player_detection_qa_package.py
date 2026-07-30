from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.player_detection_quality_audit import (
    build_player_detection_quality_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a static player-detector QA package with an offline JSON export."
        )
    )
    parser.add_argument("--match-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--maximum-frames", type=int, default=24)
    parser.add_argument("--minimum-gap-seconds", type=float, default=5.0)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise ValueError(f"Output directory already exists: {output_dir}")
    manifest = build_player_detection_quality_audit(
        match_path=args.match_dir.resolve(),
        output_dir=output_dir,
        maximum_frames=max(1, int(args.maximum_frames)),
        minimum_gap_seconds=max(0.0, float(args.minimum_gap_seconds)),
    )
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "frames": (manifest.get("summary") or {}).get("frames"),
                "detections": (manifest.get("summary") or {}).get(
                    "existing_detections"
                ),
                "prefilled_false_detections": (manifest.get("summary") or {}).get(
                    "prefilled_false_detections"
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
