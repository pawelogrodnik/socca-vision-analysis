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

from app.services.identity_fragment_visual_content_audit import (
    build_identity_fragment_visual_content_audit_manifest,
    render_identity_fragment_visual_content_audit,
)


DEFAULT_MANIFEST = REPO_ROOT / "examples" / "player_identity_benchmarks.json"
DEFAULT_GOLDSET = (
    BACKEND_DIR
    / "tests"
    / "fixtures"
    / "player_identity"
    / "identity_fragment_consolidation_goldset_v1.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a selective P1.11 endpoint visual-content audit.",
    )
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--goldset", type=Path, default=DEFAULT_GOLDSET)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()

    benchmark_root = args.benchmark_root.resolve()
    manifest_path = args.manifest.resolve()
    goldset_path = args.goldset.resolve()
    benchmark_manifest = _load_json(manifest_path)
    goldset = _load_json(goldset_path)
    cases = _select_cases(benchmark_manifest.get("benchmarks") or [], args.case)
    if not cases:
        raise ValueError("No benchmark cases selected.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else benchmark_root / f"visual-endpoint-content-audit-{timestamp}"
    )
    output_root.mkdir(parents=True, exist_ok=False)

    reports = [
        _generate_case(
            case,
            benchmark_root=benchmark_root,
            output_root=output_root,
            goldset=goldset,
        )
        for case in cases
    ]
    suite = {
        "schema_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_root": str(benchmark_root),
        "manifest": str(manifest_path),
        "goldset": str(goldset_path),
        "summary": {
            "cases": len(reports),
            "review_items": sum(int(row["review_items"]) for row in reports),
        },
        "cases": reports,
    }
    (output_root / "audit_suite_manifest.json").write_text(
        json.dumps(suite, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    links = "".join(
        f'<a href="{row["label"]}/index.html"><strong>{row["label"]}</strong>'
        f'<span>{row["review_items"]} endpoints</span></a>'
        for row in reports
    )
    (output_root / "index.html").write_text(
        _suite_html(links), encoding="utf-8"
    )
    print(json.dumps({"output_root": str(output_root), **suite["summary"]}, indent=2))


def _generate_case(
    case: dict[str, Any],
    *,
    benchmark_root: Path,
    output_root: Path,
    goldset: dict[str, Any],
) -> dict[str, Any]:
    label = str(case.get("label") or case.get("benchmark_id"))
    benchmark_id = str(case.get("benchmark_id") or label)
    candidate_dir = benchmark_root / label / "candidate-shadow-diagnostics"
    consolidation = _load_json(candidate_dir / "identity_fragment_consolidation_shadow.json")
    video_value = case.get("visual_clip_path") or case.get("video_path")
    if not video_value:
        raise ValueError(f"Benchmark {label} does not define a video path")
    video_path = (REPO_ROOT / str(video_value)).resolve()
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    offset = float(case.get("start_sec") or 0.0) if case.get("visual_clip_path") else 0.0
    manifest = build_identity_fragment_visual_content_audit_manifest(
        consolidation,
        goldset,
        benchmark_id=benchmark_id,
        benchmark_label=label,
        video_path=str(video_path),
        video_time_offset_sec=offset,
    )
    case_output = output_root / label
    rendered = render_identity_fragment_visual_content_audit(
        manifest,
        video_path=video_path,
        output_dir=case_output,
    )
    return {
        "benchmark_id": benchmark_id,
        "label": label,
        "output_dir": str(case_output),
        "index_html": str(case_output / "index.html"),
        "review_items": int((rendered.get("summary") or {}).get("review_items") or 0),
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


def _suite_html(links: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P1.11 endpoint content audit</title><style>
:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0b1220;color:#eef2f8}}body{{margin:0;padding:32px}}h1{{margin:0 0 8px}}p{{color:#9aacbf;margin:0 0 24px}}main{{display:grid;gap:12px;max-width:720px}}a{{display:flex;justify-content:space-between;padding:18px;border:1px solid #33445f;border-radius:6px;background:#111c2e;color:#eef2f8;text-decoration:none}}a:hover{{background:#192840}}span{{color:#9aacbf}}
</style></head><body><h1>P1.11 endpoint content audit</h1><p>Classify the boxed endpoint object, not identity continuity.</p><main>{links}</main></body></html>"""


if __name__ == "__main__":
    main()
