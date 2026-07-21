#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from app.services.identity_partial_candidate import build_partial_candidate_artifacts
from app.services.identity_roster_subject_remediation import (
    build_empty_remediation_decisions,
    build_identity_roster_subject_remediation_plan,
)


PRODUCTION_FILES = (
    "player_identity_assignments.json",
    "resolved_player_stats.json",
    "player_heatmaps.json",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build P1.20B remediation and P1.21 candidate-only artifacts.")
    parser.add_argument("--promotion-plan", type=Path, required=True)
    parser.add_argument("--match-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--remediation-decisions", type=Path)
    args = parser.parse_args()

    promotion = _load_json(args.promotion_plan)
    match_doc = _load_json(args.match_dir / "match.json")
    pitch_config = _load_json_optional(args.match_dir / "pitch_config.json")
    production_stats = _load_json_optional(args.match_dir / "resolved_player_stats.json")
    decisions = _load_json_optional(args.remediation_decisions) if args.remediation_decisions else {}
    before = _file_hashes(args.match_dir, PRODUCTION_FILES)

    remediation = build_identity_roster_subject_remediation_plan(promotion, decisions)
    artifacts = build_partial_candidate_artifacts(
        promotion,
        remediation,
        match_doc,
        pitch_config_doc=pitch_config,
        production_stats_doc=production_stats,
    )
    artifacts = {
        "identity_roster_subject_remediation_decisions_shadow.json": (
            decisions or build_empty_remediation_decisions(promotion)
        ),
        "identity_roster_subject_remediation_plan.json": remediation,
        **artifacts,
    }
    _atomic_write_directory(args.output_root, artifacts)
    after = _file_hashes(args.match_dir, PRODUCTION_FILES)
    unchanged = before == after
    if not unchanged:
        raise RuntimeError("Production identity artifacts changed during candidate-only apply")

    manifest = artifacts["identity_candidate_apply_manifest.json"]
    print(json.dumps({
        "output_root": str(args.output_root),
        "remediation_status": remediation.get("status"),
        "candidate_status": manifest.get("status"),
        "eligible_observations": remediation.get("summary", {}).get("eligible_observations"),
        "excluded_fragments": remediation.get("summary", {}).get("excluded_fragments"),
        "candidate_hard_conflicts": manifest.get("safety", {}).get("hard_conflicts"),
        "production_unchanged": unchanged,
    }, indent=2))
    return 0


def _atomic_write_directory(root: Path, artifacts: dict[str, dict[str, Any]]) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{root.name}-", dir=root.parent))
    backup = root.with_name(f".{root.name}.backup")
    try:
        for name, document in artifacts.items():
            (temp / name).write_text(
                json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        if backup.exists():
            shutil.rmtree(backup)
        if root.exists():
            os.replace(root, backup)
        os.replace(temp, root)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if root.exists() and backup.exists():
            shutil.rmtree(root)
        if backup.exists():
            os.replace(backup, root)
        if temp.exists():
            shutil.rmtree(temp)
        raise


def _file_hashes(root: Path, names: tuple[str, ...]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in names:
        path = root / name
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return result


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_optional(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return _load_json(path)


if __name__ == "__main__":
    raise SystemExit(main())
