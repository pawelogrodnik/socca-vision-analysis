from __future__ import annotations

"""Frozen-artifact smoke for reviewed video identity corrections."""

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
from app.services.identity_reviewed_snapshot import finalize_reviewed_identity
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_reviewed_video import render_reviewed_video


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-path", type=Path, required=True)
    parser.add_argument("--slot-subject", required=True)
    parser.add_argument("--stable-slot", required=True)
    parser.add_argument("--roster-subject", required=True)
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.match_path.resolve()
    immutable_before = _immutable_fingerprints(source)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pr9-smoke-", dir=source.parent) as temporary:
        clone = Path(temporary) / "match"
        shutil.copytree(source, clone, copy_function=os.link)
        match_doc = _load(clone / "match.json")
        baseline_snapshot = _load(clone / "reviewed_identity_snapshot.json")
        baseline_stats = _load(clone / "reviewed_player_stats.json")

        correction_started = time.monotonic()
        slot_result = save_reviewed_identity_correction(
            clone,
            match_doc,
            {
                "candidate_subject_id": args.slot_subject,
                "action": "assign_existing_slot",
                "stable_slot_id": args.stable_slot,
                "comment": "PR #9 frozen smoke: slot derived from existing exact operator evidence",
            },
        )
        roster_result = save_reviewed_identity_correction(
            clone,
            match_doc,
            {
                "candidate_subject_id": args.roster_subject,
                "action": "assign_roster_player",
                "player_id": args.player_id,
                "comment": "PR #9 frozen smoke: name derived from exact operator observations",
            },
        )
        correction_seconds = time.monotonic() - correction_started
        snapshot = finalize_reviewed_identity(clone, match_doc)
        documents = build_reviewed_stats(
            clone,
            snapshot,
            match_doc,
            _load(clone / "pitch_config.json"),
        )
        manifest = render_reviewed_video(
            clone,
            snapshot,
            match_doc,
            include_minimap=True,
            include_ball=True,
            show_roster_number=False,
        )
        after_stats = documents["reviewed_player_stats.json"]
        probe = _probe(clone / "reviewed_video.mp4")

        report = {
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_match": source.name,
            "frozen_artifact_clone": True,
            "corrections": [
                {
                    "type": "assign_existing_slot",
                    "candidate_subject_id": args.slot_subject,
                    "stable_slot_id": args.stable_slot,
                    "trusted_basis": "existing exact roster seed for Paweł plus canonical A03 evidence for another Paweł fragment",
                    "result": slot_result,
                },
                {
                    "type": "assign_roster_player",
                    "candidate_subject_id": args.roster_subject,
                    "player_id": args.player_id,
                    "trusted_basis": "existing exact operator observations name the subject Krzysiek",
                    "result": roster_result,
                },
            ],
            "correction_workflow_seconds": round(correction_seconds, 3),
            "total_smoke_seconds": round(time.monotonic() - started, 3),
            "baseline": _metrics(baseline_snapshot, baseline_stats, args.player_id),
            "after": _metrics(snapshot, after_stats, args.player_id),
            "render": {
                "ball_frames": manifest["semantic_checks"]["ball_frames_rendered"],
                "minimap_frames": manifest["semantic_checks"]["minimap_frames_rendered"],
                "duplicate_stable_labels": manifest["semantic_checks"]["duplicate_stable_labels_rendered"],
                "duplicate_canonical_players": manifest["semantic_checks"]["duplicate_canonical_players_rendered"],
                "codec": probe.get("codec_name"),
                "pixel_format": probe.get("pix_fmt"),
            },
            "safety": {
                "source_immutable_before": immutable_before,
                "source_immutable_after": _immutable_fingerprints(source),
                "production_mutation_count": 0,
                "published_mutation_count": 0,
                "reran_yolo": False,
                "reran_tracking": False,
                "reran_reid": False,
                "automatic_permanent_allocations": snapshot["summary"].get("automatic_permanent_allocations"),
            },
            "skipped_manual_actions": [
                {
                    "actions": ["false_detection", "referee"],
                    "reason": "No trustworthy whole-subject false/referee operator evidence was available; no synthetic decision was created.",
                }
            ],
        }
        if report["safety"]["source_immutable_before"] != report["safety"]["source_immutable_after"]:
            raise RuntimeError("Frozen source match was mutated during smoke")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))


def _metrics(
    snapshot: dict[str, Any],
    stats: dict[str, Any],
    player_id: str,
) -> dict[str, Any]:
    summary = snapshot.get("summary") or {}
    assignments = snapshot.get("tracklet_assignments") or []
    labels = sorted(
        {
            str(row["stable_anonymous_slot_id"])
            for row in assignments
            if row.get("stable_anonymous_slot_id")
        }
    )
    player = next(
        (row for row in stats.get("players") or [] if row.get("player_id") == player_id),
        {},
    )
    return {
        "stable_slots": len(labels),
        "highest_a": max((value for value in labels if value.startswith("A")), default=None),
        "highest_b": max((value for value in labels if value.startswith("B")), default=None),
        "unanchored_fragments": summary.get("unanchored_fragments"),
        "conflicted_observations": summary.get("conflicted_detected_observations"),
        "confirmed_detected_observation_ratio": summary.get("confirmed_detected_observation_ratio"),
        "confirmed_detected_observations": summary.get("confirmed_detected_observations"),
        "corrected_player": {
            "player_id": player_id,
            "confirmed_detected_observations": player.get("confirmed_detected_observations", 0),
            "confirmed_fragments": player.get("confirmed_fragments", 0),
            "detected_time_sec": player.get("detected_time_sec", 0),
            "observed_distance_m": player.get("observed_distance_m", 0),
            "heatmap_samples": player.get("heatmap_samples", 0),
        },
    }


def _immutable_fingerprints(path: Path) -> dict[str, str | None]:
    names = (
        "tracklets.json",
        "global_identity.json",
        "stable_players.json",
        "player_identity_assignments.json",
    )
    values = {
        name: _sha(path / name) if (path / name).exists() else None
        for name in names
    }
    published = sorted(
        file
        for directory in (path / "published", path / "publish")
        if directory.exists()
        for file in directory.rglob("*")
        if file.is_file()
    )
    values["published_tree"] = hashlib.sha256(
        "".join(f"{file.relative_to(path)}:{_sha(file)}" for file in published).encode()
    ).hexdigest()
    return values


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "json",
            str(path),
        ],
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
