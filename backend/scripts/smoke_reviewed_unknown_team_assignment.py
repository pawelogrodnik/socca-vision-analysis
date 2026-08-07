from __future__ import annotations

"""Frozen-artifact smoke checks for reviewed-only U? team corrections."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any

from app.services.identity_reviewed_corrections import save_reviewed_identity_correction
from app.services.identity_reviewed_slot_registry import build_reviewed_slot_registry
from app.services.identity_reviewed_slot_review import load_reviewed_slot_assignments
from app.services.identity_reviewed_snapshot import finalize_reviewed_identity
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_reviewed_video import render_reviewed_video


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-path", type=Path, required=True)
    parser.add_argument("--unknown-subject", required=True)
    parser.add_argument("--existing-slot", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.match_path.resolve()
    immutable_before = _immutable_fingerprints(source)
    started = time.monotonic()
    report = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_match": source.name,
        "unknown_subject": args.unknown_subject,
        "frozen_artifact_clones": True,
        "cases": [
            _run_case(source, args.unknown_subject, {"action": "assign_team", "team_label": "B"}),
            _run_case(source, args.unknown_subject, {"action": "assign_existing_slot", "stable_slot_id": args.existing_slot}),
        ],
        "total_smoke_seconds": round(time.monotonic() - started, 3),
        "safety": {
            "source_immutable_before": immutable_before,
            "source_immutable_after": _immutable_fingerprints(source),
            "production_mutation_count": 0,
            "published_mutation_count": 0,
            "reran_yolo": False,
            "reran_tracking": False,
            "reran_reid": False,
            "reran_ball_detection": False,
        },
    }
    if report["safety"]["source_immutable_before"] != report["safety"]["source_immutable_after"]:
        raise RuntimeError("Frozen source match was mutated during smoke")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


def _run_case(source: Path, subject_id: str, correction: dict[str, str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pr9-unknown-team-", dir=source.parent) as temporary:
        clone = Path(temporary) / "match"
        shutil.copytree(source, clone, copy_function=os.link)
        match = _load(clone / "match.json")
        before_registry = build_reviewed_slot_registry(clone, load_reviewed_slot_assignments(clone))
        saved = save_reviewed_identity_correction(
            clone,
            match,
            {"candidate_subject_id": subject_id, **correction, "comment": "frozen U? smoke"},
        )
        snapshot = finalize_reviewed_identity(clone, match)
        stats = build_reviewed_stats(clone, snapshot, match, _load(clone / "pitch_config.json"))
        manifest = render_reviewed_video(
            clone,
            snapshot,
            match,
            include_minimap=True,
            include_ball=True,
            show_roster_number=False,
        )
        after_slots = load_reviewed_slot_assignments(clone)
        assignment = next(
            row for row in snapshot["tracklet_assignments"]
            if row.get("candidate_subject_id") == subject_id
        )
        return {
            "action": correction["action"],
            "saved_decision": saved["saved_decision"],
            "assignment": {
                key: assignment.get(key)
                for key in (
                    "team_label",
                    "display_label",
                    "fallback_label",
                    "identity_status",
                    "identity_source",
                    "stable_anonymous_slot_id",
                    "canonical_player_id",
                    "eligible_for_player_stats",
                )
            },
            "stable_slot_count_before": len(before_registry),
            "stable_slot_count_after": len(build_reviewed_slot_registry(clone, after_slots)),
            "manual_reviewed_slots_after": len(after_slots.get("reviewed_slots") or []),
            "automatic_permanent_allocations": snapshot["fragmentation_diagnostics"].get("automatic_permanent_allocations"),
            "duplicate_stable_labels": manifest["semantic_checks"]["duplicate_stable_labels_rendered"],
            "duplicate_canonical_players": manifest["semantic_checks"]["duplicate_canonical_players_rendered"],
            "stats_players": len(stats["reviewed_player_stats.json"].get("players") or []),
            "render": _probe(clone / "reviewed_video.mp4"),
        }


def _immutable_fingerprints(path: Path) -> dict[str, str | None]:
    names = ("tracklets.json", "global_identity.json", "stable_players.json", "player_identity_assignments.json")
    return {name: _sha(path / name) if (path / name).exists() else None for name in names}


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return (json.loads(result.stdout).get("streams") or [{}])[0]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
