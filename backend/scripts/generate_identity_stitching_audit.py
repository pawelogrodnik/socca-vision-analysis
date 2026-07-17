from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_stitching_audit import (
    build_stitching_audit_manifest,
    render_stitching_audit,
)


DEFAULT_MANIFEST = REPO_ROOT / "examples" / "player_identity_benchmarks.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render visual review cards for recommended shadow identity stitching edges.",
    )
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--cards-per-sheet", type=int, default=4)
    args = parser.parse_args()

    benchmark_root = args.benchmark_root.resolve()
    manifest_path = args.manifest.resolve()
    benchmark_manifest = _load_json(manifest_path)
    cases = _select_cases(benchmark_manifest.get("benchmarks") or [], args.case)
    if not cases:
        raise ValueError("No benchmark cases selected.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else benchmark_root / f"visual-stitching-audit-{timestamp}"
    )
    output_root.mkdir(parents=True, exist_ok=False)

    reports: list[dict[str, Any]] = []
    for case in cases:
        reports.append(
            generate_case_audit(
                case,
                benchmark_root=benchmark_root,
                output_root=output_root,
                cards_per_sheet=max(1, int(args.cards_per_sheet)),
            )
        )
    suite = {
        "schema_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_root": str(benchmark_root),
        "manifest": str(manifest_path),
        "summary": {
            "cases": len(reports),
            "review_items": sum(int(row["summary"]["review_items"]) for row in reports),
            "skipped": sum(int(row["summary"]["skipped"]) for row in reports),
        },
        "cases": reports,
    }
    (output_root / "audit_suite_manifest.json").write_text(
        json.dumps(suite, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({"output_root": str(output_root), **suite["summary"]}, indent=2))


def generate_case_audit(
    case: dict[str, Any],
    *,
    benchmark_root: Path,
    output_root: Path,
    cards_per_sheet: int,
) -> dict[str, Any]:
    label = str(case.get("label") or case.get("benchmark_id"))
    candidate_dir = benchmark_root / label / "candidate-shadow-diagnostics"
    stitching_doc = _load_json(candidate_dir / "identity_stitching_candidates.json")
    tracklets_doc = _load_json(candidate_dir / "tracklets.json")
    video_value = case.get("visual_clip_path") or case.get("video_path")
    if not video_value:
        raise ValueError(f"Benchmark {label} does not define video_path or visual_clip_path.")
    video_path = (REPO_ROOT / str(video_value)).resolve()
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    use_visual_clip = bool(case.get("visual_clip_path"))
    video_time_offset = float(case.get("start_sec") or 0.0) if use_visual_clip else 0.0
    audit_manifest = build_stitching_audit_manifest(
        stitching_doc,
        tracklets_doc,
        benchmark_id=str(case.get("benchmark_id") or label),
        benchmark_label=label,
        video_path=str(video_path),
        video_time_offset_sec=video_time_offset,
    )
    case_output = output_root / label
    rendered = render_stitching_audit(
        audit_manifest,
        video_path=video_path,
        output_dir=case_output,
        cards_per_sheet=cards_per_sheet,
    )
    return {
        "benchmark_id": case.get("benchmark_id"),
        "label": label,
        "output_dir": str(case_output),
        "index_html": str(case_output / "index.html"),
        "summary": rendered["summary"],
    }


def _select_cases(cases: list[dict[str, Any]], requested: list[str]) -> list[dict[str, Any]]:
    if not requested:
        return cases
    wanted = set(requested)
    return [
        row
        for row in cases
        if str(row.get("label")) in wanted or str(row.get("benchmark_id")) in wanted
    ]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
