from __future__ import annotations

"""Machine-checkable semantic and media QA for reviewed match output."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic


_BOUNDED_SLOT = re.compile(r"^[AB](?:0[1-9]|1[0-4])$")
_UNBOUNDED_UNKNOWN = re.compile(r"^U\d+")


def build_reviewed_output_qa(
    root: Path,
    snapshot: dict[str, Any],
    job: dict[str, Any],
    *,
    production_before: dict[str, Any] | None = None,
    production_after: dict[str, Any] | None = None,
    published_before: dict[str, Any] | None = None,
    published_after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    video = root / "reviewed_video.mp4"
    manifest = _load(root / "reviewed_video_manifest.json")
    semantic = manifest.get("semantic_checks") or {}
    assignments = snapshot.get("tracklet_assignments") or []
    stable_slots = sorted(
        {
            str(row["stable_anonymous_slot_id"])
            for row in assignments
            if row.get("stable_anonymous_slot_id")
        }
    )
    fallback_labels = sorted(
        {str(row.get("fallback_label") or "") for row in assignments}
    )
    probe = _ffprobe(video)
    screenshots = _capture_representative_frames(root, video)
    detected_balls = sum(
        str(row.get("source") or row.get("status") or "detected") == "detected"
        and isinstance(row.get("position_m"), list)
        and len(row["position_m"]) >= 2
        for row in _load(root / "ball_tracks.json").get("positions") or []
    )
    confirmed_ids = {
        str(row["canonical_player_id"])
        for row in assignments
        if row.get("identity_status") == "confirmed"
        and row.get("canonical_player_id")
    }
    confirmed_ids.update(
        str(row["canonical_player_id"])
        for row in snapshot.get("observation_overrides") or []
        if row.get("identity_status") == "confirmed"
        and row.get("canonical_player_id")
    )
    stats = _load(root / "reviewed_player_stats.json")
    stats_ids = {
        str(row.get("player_id")) for row in stats.get("players") or []
    }
    stats_players = list(stats.get("players") or [])
    movement_regressions = [
        str(row.get("player_id") or "")
        for row in stats_players
        if int(row.get("expected_positive_movement_segments") or 0) > 0
        and float(row.get("total_distance_m") or 0.0) == 0.0
    ]
    options = job.get("options") or {}
    checks = {
        "mp4_exists_and_nonempty": video.is_file() and video.stat().st_size > 0,
        "mp4_sha256_matches_manifest": video.is_file() and _sha(video) == manifest.get("digest"),
        "mp4_sha256_matches_job": video.is_file() and _sha(video) == job.get("video_digest"),
        "codec_is_h264": probe.get("codec_name") == "h264",
        "pixel_format_is_yuv420p": probe.get("pix_fmt") == "yuv420p",
        "six_representative_frames_captured": len(screenshots) == 6 and all(row["captured"] for row in screenshots),
        "source_snapshot_digest_matches": manifest.get("source_snapshot_digest") == snapshot.get("semantic_digest"),
        "bounded_stable_slot_ids": all(_BOUNDED_SLOT.fullmatch(value) for value in stable_slots),
        "no_numbered_unknown_labels": not any(_UNBOUNDED_UNKNOWN.match(value) for value in fallback_labels),
        "automatic_permanent_allocations_zero": int((snapshot.get("fragmentation_diagnostics") or {}).get("automatic_permanent_allocations") or 0) == 0,
        "duplicate_stable_labels_rendered_zero": int(semantic.get("duplicate_stable_labels_rendered") or 0) == 0,
        "duplicate_canonical_players_rendered_zero": int(semantic.get("duplicate_canonical_players_rendered") or 0) == 0,
        "confirmed_name_rendered_when_expected": not any(row.get("identity_status") == "confirmed" for row in assignments) and not any(row.get("identity_status") == "confirmed" for row in snapshot.get("observation_overrides") or []) or int(semantic.get("confirmed_labels_rendered") or 0) > 0,
        "fallback_label_rendered_when_expected": not assignments or int(semantic.get("fallback_labels_rendered") or 0) > 0,
        "minimap_rendered_when_requested": not bool(options.get("include_minimap")) or int(semantic.get("minimap_frames_rendered") or 0) > 0,
        "detected_ball_rendered_when_available_and_requested": not (bool(options.get("include_minimap")) and bool(options.get("include_ball")) and detected_balls) or int(semantic.get("ball_frames_rendered") or 0) > 0,
        "conflict_metrics_present": snapshot.get("summary", {}).get("conflict_count") is not None,
        "coverage_metrics_present": snapshot.get("summary", {}).get("coverage_unit") == "unique_detected_tracklet_frame_observation",
        "stats_use_confirmed_players_only": stats_ids <= confirmed_ids,
        "stats_snapshot_digest_matches": not stats or stats.get("source_snapshot_digest") == snapshot.get("semantic_digest"),
        "reviewed_movement_stats_recorded": all(
            all(
                key in row
                for key in (
                    "confirmed_detected_observations",
                    "confirmed_fragments",
                    "observed_distance_m",
                    "estimated_short_gap_distance_m",
                    "total_distance_m",
                    "heatmap_samples",
                    "accepted_movement_segments",
                )
            )
            for row in stats_players
        ),
        "reviewed_movement_regression_absent": not movement_regressions,
        "production_identity_unchanged": production_before is None or production_before == production_after,
        "published_packages_unchanged": published_before is None or published_before == published_after,
        "renderer_declares_no_cv_rerun": all((manifest.get("safety") or {}).get(key) is False for key in ("reran_yolo", "reran_tracking")),
    }
    report = {
        "schema_version": "1.0.0",
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "video_probe": probe,
        "video_digest": _sha(video) if video.exists() else None,
        "frames": screenshots,
        "identity_semantics": {
            "stable_slot_ids": stable_slots,
            "fallback_labels": fallback_labels,
            "highest_stable_number_by_team": {
                team: max((int(value[1:]) for value in stable_slots if value.startswith(team)), default=None)
                for team in ("A", "B")
            },
            "exact_named_observations": int(snapshot.get("summary", {}).get("exact_named_observations") or 0),
            "unanchored_fragments": int(snapshot.get("fragmentation_diagnostics", {}).get("unanchored_fragments") or 0),
        },
        "ball": {"detected_input_positions": detected_balls, "rendered_frames": int(semantic.get("ball_frames_rendered") or 0)},
        "semantic_checks": semantic,
        "reviewed_player_stats": [
            {
                key: row.get(key)
                for key in (
                    "player_id",
                    "player_name",
                    "confirmed_detected_observations",
                    "confirmed_fragments",
                    "observed_distance_m",
                    "estimated_short_gap_distance_m",
                    "total_distance_m",
                    "heatmap_samples",
                    "accepted_movement_segments",
                    "expected_positive_movement_segments",
                    "skipped_outlier_segments",
                    "skipped_long_gap_segments",
                )
            }
            for row in stats_players
        ],
        "movement_regression_players": movement_regressions,
        "production_identity_before": production_before,
        "production_identity_after": production_after,
        "published_packages_before": published_before,
        "published_packages_after": published_after,
    }
    write_identity_json_atomic(root / "reviewed_output_visual_qa_report.json", report)
    return report


def _ffprobe(video: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if not executable or not video.exists():
        return {"available": False}
    result = subprocess.run(
        [executable, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_frames,duration", "-of", "json", str(video)],
        check=False,
        capture_output=True,
        text=True,
    )
    streams = (json.loads(result.stdout).get("streams") or []) if result.returncode == 0 else []
    return {"available": result.returncode == 0 and bool(streams), **(streams[0] if streams else {}), "stderr": result.stderr.strip() or None}


def _capture_representative_frames(root: Path, video: Path) -> list[dict[str, Any]]:
    import cv2

    capture = cv2.VideoCapture(str(video))
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    qa_dir = root / "reviewed_output_qa"
    qa_dir.mkdir(exist_ok=True)
    frames = sorted({min(total - 1, round((total - 1) * index / 5)) for index in range(6)}) if total else []
    evidence = []
    for frame_number in frames:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = capture.read()
        path = qa_dir / f"frame-{frame_number:06d}.jpg"
        if ok:
            cv2.imwrite(str(path), frame)
        evidence.append({"frame": frame_number, "path": str(path.relative_to(root)), "captured": bool(ok)})
    capture.release()
    return evidence


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_tree_snapshot(root: Path) -> dict[str, Any]:
    files = [
        {"path": str(path.relative_to(root)), "sha256": _sha(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ] if root.exists() else []
    return {"root": str(root), "files": files}
