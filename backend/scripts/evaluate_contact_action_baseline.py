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
    evaluate_contact_action_baseline,
    render_contact_action_baseline_markdown,
    snapshot_source_tree,
    summarize_source_tree_snapshot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only current contact/pass baseline against Contact / Action Goldset v1.")
    parser.add_argument("--goldset", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--matches-root", type=Path, default=MATCHES_DIR)
    args = parser.parse_args()

    goldset = _read(args.goldset)
    source_ids = sorted({str(window["source_match_id"]) for window in goldset.get("windows") or []})
    match_dirs = {source_id: args.matches_root / source_id for source_id in source_ids}
    missing = [source_id for source_id, path in match_dirs.items() if not path.is_dir()]
    if missing:
        raise ValueError(f"Missing source match directories: {', '.join(missing)}")
    before = snapshot_source_tree(match_dirs)
    try:
        documents = {source_id: build_current_automatic_documents(path) for source_id, path in match_dirs.items()}
        report = evaluate_contact_action_baseline(goldset, documents)
    finally:
        assert_source_tree_unchanged(before, match_dirs)
    report["source_tree_snapshot"] = summarize_source_tree_snapshot(before)
    _write_new(args.output_json, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    _write_new(args.output_markdown, render_contact_action_baseline_markdown(report))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))


def _read(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Goldset must be a JSON object")
    return document


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite evaluation output: {path}")
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
