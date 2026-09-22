"""Generate the read-only automatic-pass upstream root-cause GO / NO-GO audit."""

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
from evaluation.pass_upstream_root_cause_audit import (
    audit_pass_upstream_root_causes,
    render_pass_upstream_root_cause_audit_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only root-cause audit of current Pass Policy v1.")
    parser.add_argument("--goldset", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--matches-root", type=Path, default=MATCHES_DIR)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    goldset = _read_object(args.goldset)
    source_ids = sorted({str(row["source_match_id"]) for row in goldset.get("windows") or []})
    match_dirs = {source_id: args.matches_root / source_id for source_id in source_ids}
    if missing := [source_id for source_id, directory in match_dirs.items() if not directory.is_dir()]:
        raise ValueError(f"Missing source match directories: {', '.join(missing)}")
    before = snapshot_source_tree(match_dirs)
    try:
        documents = {
            source_id: build_current_automatic_documents(directory, include_possession_context=True)
            for source_id, directory in match_dirs.items()
        }
        identities = {source_id: _read_object(directory / "global_identity.json") for source_id, directory in match_dirs.items()}
        report = audit_pass_upstream_root_causes(goldset, documents, identities)
    finally:
        assert_source_tree_unchanged(before, match_dirs)
    report["source_tree_snapshot"] = summarize_source_tree_snapshot(before)
    _write(args.output_json, json.dumps(report, indent=2, ensure_ascii=False) + "\n", overwrite=args.overwrite)
    _write(args.output_markdown, render_pass_upstream_root_cause_audit_markdown(report), overwrite=args.overwrite)
    print(json.dumps({"decision": report["go_no_go_decision"], "quality": report["current_pass_quality"]}, indent=2))


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write(path: Path, content: str, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite {path}")
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
