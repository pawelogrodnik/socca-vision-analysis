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

from app.services.identity_occlusion_assignment_audit import (
    build_joint_assignment_audit_manifest,
    render_joint_assignment_audit,
)


DEFAULT_MANIFEST = REPO_ROOT / "examples" / "player_identity_benchmarks.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render joint occlusion identity assignment review cards.")
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--control-limit", type=int, default=3)
    parser.add_argument("--cards-per-sheet", type=int, default=4)
    args = parser.parse_args()
    benchmark_root = args.benchmark_root.resolve()
    benchmark_manifest = _load_json(args.manifest.resolve())
    cases = _select_cases(benchmark_manifest.get("benchmarks") or [], args.case)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else benchmark_root / f"visual-joint-occlusion-audit-{timestamp}"
    )
    output_root.mkdir(parents=True, exist_ok=False)
    reports = [
        _generate_case(
            case,
            benchmark_root=benchmark_root,
            output_root=output_root,
            control_limit=max(0, args.control_limit),
            cards_per_sheet=max(1, args.cards_per_sheet),
        )
        for case in cases
    ]
    suite = {
        "schema_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_root": str(benchmark_root),
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


def _generate_case(
    case: dict[str, Any],
    *,
    benchmark_root: Path,
    output_root: Path,
    control_limit: int,
    cards_per_sheet: int,
) -> dict[str, Any]:
    label = str(case.get("label") or case.get("benchmark_id"))
    candidate_dir = benchmark_root / label / "candidate-shadow-diagnostics"
    assignments_doc = _load_json(candidate_dir / "identity_occlusion_assignments.json")
    tracklets_doc = _load_json(candidate_dir / "tracklets.json")
    video_value = case.get("visual_clip_path") or case.get("video_path")
    if not video_value:
        raise ValueError(f"Benchmark {label} does not define a visual video.")
    video_path = (REPO_ROOT / str(video_value)).resolve()
    video_offset = float(case.get("start_sec") or 0.0) if case.get("visual_clip_path") else 0.0
    manifest = build_joint_assignment_audit_manifest(
        assignments_doc,
        tracklets_doc,
        benchmark_id=str(case.get("benchmark_id") or label),
        benchmark_label=label,
        video_path=str(video_path),
        video_time_offset_sec=video_offset,
        control_limit=control_limit,
    )
    case_output = output_root / label
    rendered = render_joint_assignment_audit(
        manifest,
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
