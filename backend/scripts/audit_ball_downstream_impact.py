from __future__ import annotations

"""CLI wrapper for the read-only ball downstream A/B impact audit."""

import argparse
import json
from pathlib import Path

from app.services.ball_downstream_impact_audit import (
    DEFAULT_LOCALITY_THRESHOLD_SEC,
    build_ball_downstream_impact_audit,
    compact_ball_downstream_impact_audit,
    render_ball_downstream_impact_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only automatic-versus-resolved ball downstream impact audit.")
    parser.add_argument("--match-root", action="append", required=True, type=Path, help="Physical match directory; repeat for multiple sources.")
    parser.add_argument("--output", type=Path, help="Optional full JSON output (single match only).")
    parser.add_argument("--compact-output", type=Path, help="Optional compact JSON output (single match only).")
    parser.add_argument("--markdown-output", type=Path, help="Optional Markdown output (single match only).")
    parser.add_argument("--locality-threshold-sec", type=float, default=DEFAULT_LOCALITY_THRESHOLD_SEC)
    parser.add_argument("--quiet", action="store_true")
    options = parser.parse_args()
    if len(options.match_root) > 1 and any((options.output, options.compact_output, options.markdown_output)):
        parser.error("File outputs require exactly one --match-root; run one audit per output report.")
    _guard_outputs(
        options.match_root,
        options.output,
        options.compact_output,
        options.markdown_output,
        parser=parser,
    )
    reports = [build_ball_downstream_impact_audit(path, locality_threshold_sec=options.locality_threshold_sec) for path in options.match_root]
    report: dict | list[dict] = reports[0] if len(reports) == 1 else reports
    if options.output:
        _write(options.output, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    if options.compact_output:
        _write(options.compact_output, json.dumps(compact_ball_downstream_impact_audit(reports[0]), indent=2, ensure_ascii=False) + "\n")
    if options.markdown_output:
        _write(options.markdown_output, render_ball_downstream_impact_markdown(reports[0]))
    if not options.quiet:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _guard_outputs(match_roots: list[Path], *outputs: Path | None, parser: argparse.ArgumentParser) -> None:
    for output in outputs:
        if output is None:
            continue
        target = output.resolve()
        for root in match_roots:
            try:
                target.relative_to(root.resolve())
            except ValueError:
                continue
            if target.exists():
                parser.error("Diagnostic output must not overwrite an existing file inside a match root.")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
