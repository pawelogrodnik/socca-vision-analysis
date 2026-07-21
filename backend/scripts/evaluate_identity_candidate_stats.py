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

from app.services.identity_candidate_stats_validation import build_identity_candidate_stats_validation


PRODUCTION_FILES = (
    "player_identity_assignments.json",
    "resolved_player_stats.json",
    "player_heatmaps.json",
    "match_package.json",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build P1.23 candidate-only stats validation artifacts.")
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--match-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--production-root", type=Path)
    args = parser.parse_args()

    production_root = args.production_root or args.match_dir
    before = _file_hashes(args.match_dir, PRODUCTION_FILES)
    artifacts = build_identity_candidate_stats_validation(
        candidate_timeline=_load(args.candidate_root / "resolved_player_timeline_candidate_v2.json"),
        candidate_stats=_load(args.candidate_root / "resolved_player_stats_candidate_v2.json"),
        candidate_diff=_load(args.candidate_root / "identity_candidate_vs_production_diff.json"),
        candidate_manifest=_load(args.candidate_root / "identity_candidate_apply_manifest.json"),
        match_doc=_load(args.match_dir / "match.json"),
        candidate_heatmaps=_load_optional(args.candidate_root / "player_heatmaps_candidate_v2.json"),
        production_heatmaps=_load_optional(production_root / "player_heatmaps.json"),
        possession_doc=_load_optional(args.match_dir / "possession_segments.json"),
        passes_doc=_load_optional(args.match_dir / "pass_candidates.json"),
        events_doc=_load_optional(args.match_dir / "event_candidates.json"),
    )
    artifacts["P1_23_CANDIDATE_STATS_VALIDATION.md"] = _markdown(
        artifacts["identity_candidate_stats_validation.json"],
        artifacts["identity_feature_readiness_candidate.json"],
    )
    _atomic_write_directory(args.output_root, artifacts)

    after = _file_hashes(args.match_dir, PRODUCTION_FILES)
    if before != after:
        raise RuntimeError("Production identity artifacts changed during P1.23 candidate validation")
    validation = artifacts["identity_candidate_stats_validation.json"]
    print(json.dumps({
        "output_root": str(args.output_root),
        "status": validation["status"],
        **validation["summary"],
        "production_unchanged": True,
    }, indent=2))
    return 0


def _markdown(validation: dict[str, Any], readiness: dict[str, Any]) -> str:
    summary = validation["summary"]
    lines = [
        "# P1.23 Candidate Stats Validation",
        "",
        f"Status: `{validation['status']}`",
        "",
        "## Safety summary",
        "",
        f"- players: {summary['players']}",
        f"- parallel observations: {summary['parallel_observations']}",
        f"- large spatial jumps: {summary['large_spatial_jumps']}",
        f"- impossible jumps affecting stats: {summary['stats_affecting_impossible_jumps']}",
        f"- large stat deltas requiring explanation: {summary['large_stat_deltas']}",
        f"- players without production baseline: {summary['production_baseline_unavailable_players']}",
        f"- hard conflicts: {summary['hard_conflicts']}",
        "",
        "## Feature readiness",
        "",
    ]
    for name, feature in readiness["features"].items():
        reasons = ", ".join(feature.get("reason_codes") or []) or "none"
        lines.append(f"- `{name}`: `{feature['status']}` ({reasons})")
    lines.extend(["", "## Players", ""])
    for player in validation["players"]:
        known = player["known_on_pitch"]
        lines.append(
            f"- {player['player_name']}: {known['seconds']:.3f}s known, "
            f"{player['fragment_count']} fragments, {len(player['large_spatial_jumps'])} large jumps, "
            f"{len(player['explainable_deltas'])} large deltas, "
            f"production comparison `{player['production_comparison']['status']}`"
        )
    lines.extend(["", "Production artifacts were read-only and remained unchanged.", ""])
    return "\n".join(lines)


def _atomic_write_directory(root: Path, artifacts: dict[str, Any]) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{root.name}-", dir=root.parent))
    backup = root.with_name(f".{root.name}.backup")
    try:
        for name, document in artifacts.items():
            path = temp / name
            if isinstance(document, str):
                path.write_text(document, encoding="utf-8")
            else:
                path.write_text(
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
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() if (root / name).exists() else None
        for name in names
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_optional(path: Path) -> dict[str, Any]:
    return _load(path) if path.exists() else {}


if __name__ == "__main__":
    raise SystemExit(main())
