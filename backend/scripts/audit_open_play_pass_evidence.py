"""Generate the read-only v1 open-play pass evidence audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import MATCHES_DIR
from evaluation.contact_action_baseline import (
    assert_source_tree_unchanged,
    build_current_automatic_documents,
    snapshot_source_tree,
    summarize_source_tree_snapshot,
)
from evaluation.open_play_pass_evidence_audit import (
    audit_open_play_pass_evidence,
    render_open_play_pass_evidence_audit_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only evidence audit for current open-play Pass Policy v1.")
    parser.add_argument("--goldset", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--matches-root", type=Path, default=MATCHES_DIR)
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing checked-in evaluation report explicitly.")
    args = parser.parse_args()

    goldset = _read(args.goldset)
    source_ids = sorted({str(row["source_match_id"]) for row in goldset.get("windows") or []})
    match_dirs = {source_id: args.matches_root / source_id for source_id in source_ids}
    missing = [source_id for source_id, path in match_dirs.items() if not path.is_dir()]
    if missing:
        raise ValueError(f"Missing source match directories: {', '.join(missing)}")
    before = snapshot_source_tree(match_dirs)
    try:
        documents = {source_id: build_current_automatic_documents(path, pass_policy_version="v1") for source_id, path in match_dirs.items()}
        report = audit_open_play_pass_evidence(goldset, documents)
    finally:
        assert_source_tree_unchanged(before, match_dirs)
    report["source_tree_snapshot"] = summarize_source_tree_snapshot(before)
    _write(args.output_json, json.dumps(report, indent=2, ensure_ascii=False) + "\n", overwrite=args.overwrite)
    _write(args.output_markdown, render_open_play_pass_evidence_audit_markdown(report), overwrite=args.overwrite)
    print(json.dumps({"current_error_budget": report["current_error_budget"], "final_conclusion": report["final_conclusion"]}, indent=2))


def _read(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Goldset must be a JSON object")
    return document


def _write(path: Path, content: str, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite evaluation output: {path}")
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
