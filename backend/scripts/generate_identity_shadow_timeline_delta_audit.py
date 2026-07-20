from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_shadow_timeline_audit import (  # noqa: E402
    build_shadow_timeline_audit_manifest,
    build_shadow_timeline_delta_audit_manifest,
    render_shadow_timeline_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the compact P1.4 candidate delta audit.")
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument(
        "--benchmark-manifest",
        type=Path,
        default=REPO_ROOT / "examples" / "player_identity_benchmarks.json",
    )
    parser.add_argument("--goldset", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", default="hard3m")
    args = parser.parse_args()

    benchmark_root = args.benchmark_root.resolve()
    benchmark_manifest = _load_json(args.benchmark_manifest.resolve())
    case = next(
        (
            row
            for row in benchmark_manifest.get("benchmarks") or []
            if args.case in {str(row.get("label") or ""), str(row.get("benchmark_id") or "")}
        ),
        None,
    )
    if case is None:
        raise ValueError(f"Unknown benchmark case: {args.case}")
    label = str(case.get("label") or case.get("benchmark_id"))
    candidate_dir = benchmark_root / label / "candidate-shadow-diagnostics"
    timeline = _load_json(candidate_dir / "identity_offline_shadow_timeline.json")
    tracklets = _load_json(candidate_dir / "tracklets.json")
    video_value = case.get("visual_clip_path") or case.get("video_path")
    if not video_value:
        raise ValueError(f"Benchmark {label} does not define an audit video")
    video_path = (REPO_ROOT / str(video_value)).resolve()
    video_offset = float(case.get("start_sec") or 0.0) if case.get("visual_clip_path") else 0.0
    complete_manifest = build_shadow_timeline_audit_manifest(
        timeline,
        tracklets,
        benchmark_id=str(case.get("benchmark_id") or label),
        benchmark_label=f"{label}-candidate-delta",
        video_path=str(video_path),
        video_time_offset_sec=video_offset,
        direct_control_limit=0,
        missing_control_limit=10_000,
    )
    delta_manifest = build_shadow_timeline_delta_audit_manifest(
        complete_manifest,
        _load_json(args.goldset.resolve()),
        _load_json(args.evaluation.resolve()),
    )
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    rendered = render_shadow_timeline_audit(
        delta_manifest,
        video_path=video_path,
        output_dir=output_dir,
        cards_per_sheet=3,
    )
    print(json.dumps({"output_dir": str(output_dir), "summary": rendered["summary"]}, indent=2))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
