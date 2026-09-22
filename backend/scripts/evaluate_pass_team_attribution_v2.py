"""Evaluate canonical-identity team attribution without mutating match artifacts."""

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
from evaluation.pass_team_attribution_v2 import (
    evaluate_pass_team_attribution_v2,
    render_pass_team_attribution_v2_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Team Attribution v2 comparison over current Pass Policy v1 candidates.")
    parser.add_argument("--goldset", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--matches-root", type=Path, default=MATCHES_DIR)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    goldset = _read_object(args.goldset)
    source_ids = sorted({str(row["source_match_id"]) for row in goldset.get("windows") or []})
    match_dirs = {source_id: args.matches_root / source_id for source_id in source_ids}
    if missing := [source_id for source_id, path in match_dirs.items() if not path.is_dir()]:
        raise ValueError(f"Missing source match directories: {', '.join(missing)}")
    before = snapshot_source_tree(match_dirs)
    try:
        documents = {
            source_id: build_current_automatic_documents(path, pass_policy_version="v1", include_possession_context=True)
            for source_id, path in match_dirs.items()
        }
        identities = {source_id: _read_object(path / "global_identity.json") for source_id, path in match_dirs.items()}
        report = evaluate_pass_team_attribution_v2(goldset, documents, identities)
    finally:
        assert_source_tree_unchanged(before, match_dirs)
    report["source_tree_snapshot"] = summarize_source_tree_snapshot(before)
    _write(args.output_json, json.dumps(report, indent=2, ensure_ascii=False) + "\n", overwrite=args.overwrite)
    _write(args.output_markdown, render_pass_team_attribution_v2_markdown(report), overwrite=args.overwrite)
    print(json.dumps({"candidate_set": report["candidate_set"], "decision": report["decision"]}, indent=2))


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
