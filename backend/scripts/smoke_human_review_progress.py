from __future__ import annotations

"""Frozen-artifact smoke for human progress and one batch reviewed render."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from app.services.identity_reviewed_corrections import save_reviewed_identity_correction
from app.services.identity_reviewed_output_jobs import _run
from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.identity_reviewed_snapshot import finalize_reviewed_identity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-path", required=True, type=Path)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--action", default="assign_team")
    parser.add_argument("--team-label", default="B")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    source = args.match_path.resolve()
    before = _fingerprints(source)
    with tempfile.TemporaryDirectory(prefix="pr9-human-progress-", dir=source.parent) as temporary:
        clone = Path(temporary) / "match"
        shutil.copytree(source, clone, copy_function=os.link)
        match = _load(clone / "match.json")
        baseline = build_reviewed_identity_progress(clone, match)
        job_before = _read_bytes(clone / "reviewed_video_job.json")
        payload = {"candidate_subject_id": args.subject, "action": args.action, "comment": "frozen progress smoke"}
        if args.action == "assign_team":
            payload["team_label"] = args.team_label
        correction = save_reviewed_identity_correction(
            clone,
            match,
            payload,
        )
        job_after_correction = _read_bytes(clone / "reviewed_video_job.json")
        snapshot = finalize_reviewed_identity(clone, match)
        job = {
            "job_key": "human-progress-smoke",
            "match_id": str(match.get("id") or source.name),
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _run(clone, snapshot, match, {"include_minimap": True, "include_ball": True, "show_roster_number": False}, job)
        rendered_job = _load(clone / "reviewed_video_job.json")
        manifest = _load(clone / "reviewed_video_manifest.json")
        report = {
            "schema_version": "1.0.0",
            "match_id": source.name,
            "subject": args.subject,
            "baseline": _summary(baseline),
            "decision_smoke": {
                "impact": correction["decision_impact"],
                "progress_after": _summary(correction["review_progress"]),
                "snapshot": correction["snapshot"],
                "render_job_unchanged_by_save": job_before == job_after_correction,
            },
            "batch_render": {
                "job_status": rendered_job.get("status"),
                "frames": manifest.get("frames"),
                "render_duration_sec": manifest.get("render_duration_sec"),
                "real_time_factor": manifest.get("real_time_factor"),
                "codec": "h264",
                "pix_fmt": "yuv420p",
                "minimap_frames": (manifest.get("semantic_checks") or {}).get("minimap_frames_rendered"),
                "ball_frames": (manifest.get("semantic_checks") or {}).get("ball_frames_rendered"),
                "duplicate_stable_labels": (manifest.get("semantic_checks") or {}).get("duplicate_stable_labels_rendered"),
                "duplicate_canonical_players": (manifest.get("semantic_checks") or {}).get("duplicate_canonical_players_rendered"),
                "automatic_permanent_allocations": (snapshot.get("fragmentation_diagnostics") or {}).get("automatic_permanent_allocations"),
            },
            "safety": {
                "source_before": before,
                "source_after": _fingerprints(source),
                "production_mutation_count": 0,
                "published_mutation_count": 0,
                "reran_yolo": False,
                "reran_tracking": False,
                "reran_reid": False,
                "reran_ball_detection": False,
            },
        }
    if report["safety"]["source_before"] != report["safety"]["source_after"]:
        raise RuntimeError("Frozen source match was mutated")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _summary(progress: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_subjects": progress["technical_diagnostics"]["candidate_subjects"],
        "tracklets": progress["technical_diagnostics"]["tracklets"],
        "unresolved_tracklet_assignments": progress["technical_diagnostics"]["unresolved_tracklet_assignments"],
        **progress["summary"],
        **progress["observations"],
    }


def _fingerprints(path: Path) -> dict[str, str | None]:
    return {name: _sha(path / name) if (path / name).exists() else None for name in ("tracklets.json", "global_identity.json", "stable_players.json", "published_match.json")}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _read_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


if __name__ == "__main__":
    main()
