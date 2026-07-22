from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_benchmark_selection import (
    build_targeted_jersey_number_benchmark,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a small, targeted N5 jersey-number benchmark.")
    parser.add_argument("--anchor-crops", type=Path, required=True)
    parser.add_argument("--candidate-identity", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--team", default="A")
    parser.add_argument("--max-subjects", type=int, default=7)
    parser.add_argument("--max-crops", type=int, default=30)
    parser.add_argument("--min-seed-crops", type=int, default=3)
    parser.add_argument("--min-independent-seed-reads", type=int, default=3)
    parser.add_argument("--minimum-seed-frame-separation", type=int, default=12)
    parser.add_argument("--minimum-visibility-episode-gap-frames", type=int, default=45)
    args = parser.parse_args()

    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_anchor_path = args.anchor_crops.resolve()
    document = build_targeted_jersey_number_benchmark(
        _load(source_anchor_path),
        _load(args.candidate_identity),
        team_label=str(args.team).upper(),
        max_subjects=args.max_subjects,
        max_crops=args.max_crops,
        min_seed_crops=args.min_seed_crops,
        min_independent_seed_reads=args.min_independent_seed_reads,
        minimum_seed_frame_separation=args.minimum_seed_frame_separation,
        minimum_visibility_episode_gap_frames=args.minimum_visibility_episode_gap_frames,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    _copy_artifacts(source_anchor_path.parent, output, document)
    _write(output / "identity_roster_anchor_crops_shadow_targeted.json", document)
    _write(
        output / "identity_jersey_number_reference.json",
        {
            "schema_version": "0.1.0",
            "source": "match_config",
            "players": [],
            "players_without_confirmed_number": [],
        },
    )
    print(json.dumps(document["summary"], indent=2, sort_keys=True))


def _copy_artifacts(source_root: Path, output_root: Path, document: dict[str, Any]) -> None:
    for card in document.get("cards") or []:
        for crop in card.get("anchor_crops") or []:
            relative = Path(str(crop.get("artifact") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Unsafe crop path: {relative}")
            source = source_root / relative
            if not source.is_file():
                raise FileNotFoundError(source)
            target = output_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
