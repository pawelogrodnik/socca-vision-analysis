from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.player_detection_quality_review import (
    PlayerObservationQaStaleSourceError,
    analyze_player_detection_quality_review_files,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an offline downstream player-observation coverage QA "
            "export and attribute visual losses across identity, tracklet, "
            "and raw-track layers."
        )
    )
    parser.add_argument("--reviewed-audit", type=Path, required=True)
    parser.add_argument("--audit-package-dir", type=Path, required=True)
    parser.add_argument("--match-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        report = analyze_player_detection_quality_review_files(
            reviewed_audit_path=args.reviewed_audit.resolve(),
            audit_package_dir=args.audit_package_dir.resolve(),
            match_path=args.match_dir.resolve(),
        )
    except PlayerObservationQaStaleSourceError as exc:
        print(
            json.dumps(
                {
                    "status": exc.status,
                    "changed_artifacts": exc.changed_artifacts,
                    "report_written": False,
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from exc
    print(
        json.dumps(
            {
                "report": str(
                    args.audit_package_dir.resolve() / "review_report.json"
                ),
                "summary": report["summary"],
                "missing_attribution": report["missing_attribution"]["counts"],
                "identity_layer_attribution": report[
                    "identity_layer_attribution"
                ]["counts"],
                "raw_track_attribution": report["raw_track_attribution"][
                    "counts"
                ],
                "projected_visual_recovery": {
                    key: value
                    for key, value in report[
                        "projected_visual_recovery"
                    ].items()
                    if key not in {"items", "identity_overlay_items"}
                },
                "primary_bottleneck": report["conclusion"]["primary_bottleneck"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
