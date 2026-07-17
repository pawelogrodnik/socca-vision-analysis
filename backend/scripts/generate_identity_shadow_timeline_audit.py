from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_shadow_timeline_audit import (
    build_shadow_timeline_audit_manifest,
    render_shadow_timeline_audit,
)


DEFAULT_MANIFEST = REPO_ROOT / "examples" / "player_identity_benchmarks.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a compact P1.4 shadow timeline visual audit.")
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--direct-controls", type=int, default=4)
    parser.add_argument("--missing-controls", type=int, default=4)
    parser.add_argument("--cards-per-sheet", type=int, default=4)
    args = parser.parse_args()

    benchmark_root = args.benchmark_root.resolve()
    benchmark_manifest = _load_json(args.manifest.resolve())
    cases = _select_cases(benchmark_manifest.get("benchmarks") or [], args.case)
    if not cases:
        raise ValueError("No benchmark cases selected.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else benchmark_root / f"visual-shadow-timeline-audit-{timestamp}"
    )
    output_root.mkdir(parents=True, exist_ok=False)

    reports = [
        generate_case_audit(
            case,
            benchmark_root=benchmark_root,
            output_root=output_root,
            direct_control_limit=max(0, int(args.direct_controls)),
            missing_control_limit=max(0, int(args.missing_controls)),
            cards_per_sheet=max(1, int(args.cards_per_sheet)),
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
    (output_root / "index.html").write_text(_suite_html(reports), encoding="utf-8")
    print(json.dumps({"output_root": str(output_root), **suite["summary"]}, indent=2))


def generate_case_audit(
    case: dict[str, Any],
    *,
    benchmark_root: Path,
    output_root: Path,
    direct_control_limit: int,
    missing_control_limit: int,
    cards_per_sheet: int,
) -> dict[str, Any]:
    label = str(case.get("label") or case.get("benchmark_id"))
    candidate_dir = benchmark_root / label / "candidate-shadow-diagnostics"
    timeline_doc = _load_json(candidate_dir / "identity_offline_shadow_timeline.json")
    tracklets_doc = _load_json(candidate_dir / "tracklets.json")
    video_value = case.get("visual_clip_path") or case.get("video_path")
    if not video_value:
        raise ValueError(f"Benchmark {label} does not define an audit video.")
    video_path = (REPO_ROOT / str(video_value)).resolve()
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    video_offset = float(case.get("start_sec") or 0.0) if case.get("visual_clip_path") else 0.0
    manifest = build_shadow_timeline_audit_manifest(
        timeline_doc,
        tracklets_doc,
        benchmark_id=str(case.get("benchmark_id") or label),
        benchmark_label=label,
        video_path=str(video_path),
        video_time_offset_sec=video_offset,
        direct_control_limit=direct_control_limit,
        missing_control_limit=missing_control_limit,
    )
    case_output = output_root / label
    rendered = render_shadow_timeline_audit(
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


def _suite_html(reports: list[dict[str, Any]]) -> str:
    links = "".join(
        (
            f'<a href="{escape(str(row["label"]))}/index.html">'
            f'<strong>{escape(str(row["label"]))}</strong>'
            f'<span>{int(row["summary"]["review_items"])} cases</span></a>'
        )
        for row in reports
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P1.4 timeline audit suite</title><style>
:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0b1220;color:#eef2f8}}body{{margin:0;padding:32px}}h1{{letter-spacing:0}}main{{display:grid;gap:14px;max-width:760px}}a{{display:flex;justify-content:space-between;padding:20px;border:1px solid #30415e;border-radius:6px;background:#101a2b;color:#eef2f8;text-decoration:none}}a:hover{{background:#172338}}span{{color:#9aacbf}}
</style></head><body><h1>P1.4 shadow timeline visual audit</h1><p>Open each benchmark and download its reviewed manifest when complete.</p><main>{links}</main></body></html>"""


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
