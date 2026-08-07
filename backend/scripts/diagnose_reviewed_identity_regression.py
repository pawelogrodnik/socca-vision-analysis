from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.identity_reviewed_regression_diagnostic import (
    build_reviewed_identity_regression_diagnostic,
    compact_reviewed_identity_regression_report,
    render_markdown_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only A/B comparison of stable and reviewed identity artifacts."
    )
    parser.add_argument("--match-root", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Optional JSON diagnostic report path.")
    parser.add_argument(
        "--compact-output",
        type=Path,
        help="Optional commit-safe JSON report path without frame-level observations.",
    )
    parser.add_argument(
        "--markdown-output", type=Path, help="Optional human-readable report path."
    )
    parser.add_argument(
        "--case-name",
        action="append",
        dest="case_names",
        help="Roster name to include in a timeline; repeat for multiple names.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print the full frame-level JSON report to stdout.",
    )
    options = parser.parse_args()
    try:
        report = build_reviewed_identity_regression_diagnostic(
            options.match_root,
            case_names=tuple(options.case_names) if options.case_names else (
                "Mati GK", "Przemek", "Andrzej", "Roman", "Piotrek", "Paweł", "Krzysiek"
            ),
        )
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    protected_paths = {
        (options.match_root / name).resolve()
        for name in (
            "match.json",
            "tracklets.json",
            "global_identity.json",
            "stable_players.json",
            "identity_candidate_shadow.json",
            "reviewed_identity_snapshot.json",
            "reviewed_identity_slot_assignments.json",
            "identity_roster_subject_review_decisions_shadow.json",
            "identity_seeded_candidate_assignments.json",
        )
    }
    for path in (options.output, options.compact_output, options.markdown_output):
        if path and path.resolve() in protected_paths:
            parser.error("Diagnostic output must not overwrite a frozen match artifact.")
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(rendered + "\n", encoding="utf-8")
    if options.compact_output:
        options.compact_output.parent.mkdir(parents=True, exist_ok=True)
        compact = json.dumps(
            compact_reviewed_identity_regression_report(report),
            indent=2,
            ensure_ascii=False,
        )
        options.compact_output.write_text(compact + "\n", encoding="utf-8")
    if options.markdown_output:
        options.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        options.markdown_output.write_text(render_markdown_report(report), encoding="utf-8")
    if not options.quiet:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
