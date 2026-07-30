from __future__ import annotations

"""Isolated, operator-driven IA0–IA6 product-flow benchmark sessions.

The workspaces intentionally contain fresh operator seed stores.  Historical
whole-subject decisions are never copied into them; they may only be referenced
by a separately generated evaluation report after an operator completes a run.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any

from app.services.identity_initial_audit import (
    AUDIT_DIRECTORY,
    FRAME_DIRECTORY,
    SELECTION_FILENAME,
    build_initial_identity_audit_document,
    export_identity_audit_frames,
)
from app.services.identity_initial_audit_frame_selection import (
    ALGORITHM_NAME as IA0_ALGORITHM_NAME,
)
from app.services.identity_initial_audit_store import save_initial_identity_audit_seeds
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_second_half_reanchor import prepare_second_half_identity_reanchor
from app.services.identity_second_half_reanchor import build_second_half_identity_reanchor_document, REANCHOR_DIRECTORY, SELECTION_FILENAME as REANCHOR_SELECTION_FILENAME, FRAME_DIRECTORY as REANCHOR_FRAME_DIRECTORY


SCHEMA_VERSION = "0.1.0"
MODE = "product_flow_benchmark_shadow_only"
REQUIRED_H1_ARTIFACTS = ("analysis_report.json", "global_identity.json", "tracklets.json")
REQUIRED_H2_ARTIFACTS = (
    "analysis_report.json",
    "global_identity.json",
    "tracklets.json",
    "identity_candidate_shadow.json",
    "identity_offline_shadow_timeline.json",
)


class ProductFlowBenchmarkError(ValueError):
    pass


def prepare_product_flow_benchmark(
    *,
    matches_root: Path,
    benchmark_root: Path,
    source_match_id: str,
    target_match_id: str,
    benchmark_id: str,
) -> dict[str, Any]:
    """Create fresh H1/H2 workspaces from frozen artifacts, never old seeds."""
    source = matches_root / source_match_id
    target = matches_root / target_match_id
    if not source.exists() or not target.exists():
        raise ProductFlowBenchmarkError("Requested H1/H2 analysis is missing")
    source_meta = _load(source / "match.json")
    target_meta = _load(target / "match.json")
    _validate_pair(source_meta, target_meta)

    root = benchmark_root / benchmark_id
    if root.exists():
        raise ProductFlowBenchmarkError(f"Benchmark already exists: {benchmark_id}")
    root.mkdir(parents=True)
    h1_workspace = root / "h1_workspace"
    h2_workspace = root / "h2_workspace"
    h1_source = _latest_run_path(source, source_meta)
    _require(h1_source, REQUIRED_H1_ARTIFACTS)
    _require(target, REQUIRED_H2_ARTIFACTS)

    _create_workspace(h1_workspace, source_meta, h1_source, benchmark_id, "H1")
    _create_workspace(h2_workspace, target_meta, target, benchmark_id, "H2")
    _build_h1_shadow_artifacts(h1_workspace)
    _write_h2_phase_config(h2_workspace)

    h1_meta = _load(h1_workspace / "match.json")
    h2_meta = _load(h2_workspace / "match.json")
    h1_audit = _prepare_h1_audit(h1_workspace, h1_meta)
    # A zero-action initial store is needed only because the shared resolver
    # combines stages. It is not an operator action and is never displayed.
    save_initial_identity_audit_seeds(h1_workspace, h1_meta, [])
    h2_reanchor = _prepare_h2_reanchor(h2_workspace, h2_meta)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "status": "READY_FOR_OPERATOR",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_id": benchmark_id,
        "physical_match": {
            "match_date": source_meta.get("match_date"),
            "source_title": source_meta.get("title"),
            "target_title": target_meta.get("title"),
            "distinct_capture_domains": True,
        },
        "workspaces": {
            "h1": _workspace_descriptor(h1_workspace, source_match_id, "H1", h1_audit),
            "h2": _workspace_descriptor(h2_workspace, target_match_id, "H2", h2_reanchor),
        },
        "operator_budget": {
            "h1_maximum_frames": 8,
            "h1_maximum_actions": 12,
            "h2_maximum_frames": 3,
            "h2_maximum_confirmations": 5,
            "early_finish_allowed": True,
            "skip_always_available": True,
        },
        "ground_truth_policy": {
            "historical_decisions_copied_into_session": False,
            "historical_decisions_may_be_used_after_completion": True,
        },
        "safety": _safety(),
    }
    _write(root / "benchmark_session.json", manifest)
    _write(root / "benchmark_before.json", build_product_flow_benchmark_report(root))
    for domain, workspace in (("h1", h1_workspace), ("h2", h2_workspace)):
        match_id = str(manifest["workspaces"][domain]["match_id"])
        alias = matches_root / match_id
        if alias.exists() or alias.is_symlink():
            raise ProductFlowBenchmarkError(f"Benchmark match alias already exists: {match_id}")
        alias.symlink_to(workspace.resolve(), target_is_directory=True)
    return manifest


def build_product_flow_benchmark_report(root: Path) -> dict[str, Any]:
    manifest = _load(root / "benchmark_session.json")
    rows = []
    for label in ("h1", "h2"):
        workspace = root / f"{label}_workspace"
        candidate = _load(workspace / "identity_candidate_shadow.json")
        timeline = _load(workspace / "identity_offline_shadow_timeline.json")
        seeded_path = workspace / "identity_seeded_candidate_assignments.json"
        seeded = _load(seeded_path) if seeded_path.exists() else None
        rows.append(_domain_metrics(label.upper(), candidate, timeline, seeded))
    before = _sum_metrics(rows, after=False)
    after = _sum_metrics(rows, after=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "status": "before_ready" if not any(row["has_operator_actions"] for row in rows) else "after_available",
        "benchmark_id": manifest["benchmark_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "after": after if any(row["has_operator_actions"] for row in rows) else None,
        "capture_domains": rows,
        "safety": _safety(),
        "limitations": [
            "No AFTER result is emitted until an operator saves at least one decision or finishes the audit.",
            "Historical manual assignments are not operator actions in this benchmark.",
        ],
    }


def _build_h1_shadow_artifacts(workspace: Path) -> None:
    report = _load(workspace / "analysis_report.json")
    global_identity = _load(workspace / "global_identity.json")
    fps = max(1.0, float((report.get("video") or {}).get("fps") or 30.0))
    subjects = []
    timeline_subjects = []
    for slot in global_identity.get("slots") or []:
        subject_id = str(slot.get("stable_subject_id") or "")
        if not subject_id:
            continue
        positions = [
            {"frame": int(row.get("frame") or 0), "time_sec": float(row.get("time_sec") or 0.0), "status": str(row.get("status") or "detected"), "tracklet_id": row.get("tracklet_id"), "bbox_xyxy": row.get("bbox_xyxy"), "visual_trusted": row.get("visual_trusted", True)}
            for row in slot.get("overlay_positions") or [] if row.get("bbox_xyxy")
        ]
        detected = [row for row in positions if row["status"] == "detected"]
        tracklet_ids = [str(value) for value in slot.get("tracklet_ids") or []]
        subjects.append({"candidate_subject_id": subject_id, "team_label": slot.get("team_label"), "role": "field_player", "tracklet_ids": tracklet_ids, "production_subject_ids": [subject_id], "start_frame": int(slot.get("slot_spawn_frame") or 0), "end_frame": max((row["frame"] for row in positions), default=0), "detected_frames": len(detected), "quality_flags": [], "requires_review": True})
        timeline_subjects.append({"shadow_subject_id": subject_id, "team_label": slot.get("team_label"), "tracklet_ids": tracklet_ids, "start_frame": int(slot.get("slot_spawn_frame") or 0), "end_frame": max((row["frame"] for row in positions), default=0), "observations": positions})
    candidate = {"schema_version": "0.1.0", "mode": "frozen_h1_lineage_candidate_shadow", "algorithm": {"name": "frozen_h1_slot_adapter", "version": "0.1.0"}, "subjects": subjects, "summary": {"candidate_subjects": len(subjects)}, "safety": {"mutates_production_identity": False, "eligible_for_player_stats": False}}
    timeline = {"schema_version": "0.1.0", "mode": "frozen_h1_lineage_candidate_shadow", "algorithm": candidate["algorithm"], "subjects": timeline_subjects, "summary": {"subjects": len(timeline_subjects), "fps": fps}}
    _write(workspace / "identity_candidate_shadow.json", candidate)
    _write(workspace / "identity_offline_shadow_timeline.json", timeline)


def _prepare_h1_audit(workspace: Path, match_document: dict[str, Any]) -> dict[str, Any]:
    """Use the frozen selector without rescanning every frame for blur.

    The original IA0 scoring remains deterministic and still incorporates
    detection quality, overlap, continuity and temporal diversity.  Blur is
    absent rather than guessed because this benchmark must not rerun tracking
    or exhaustively decode H1 before the operator can begin.
    """
    report = _load(workspace / "analysis_report.json")
    identity = _load(workspace / "global_identity.json")
    tracklets = _load(workspace / "tracklets.json")
    camera = _load(workspace / "camera_motion_report.json") if (workspace / "camera_motion_report.json").exists() else None
    selection = _bounded_h1_selection(identity, report)
    frames_path = workspace / AUDIT_DIRECTORY / "frames"
    frames_path.mkdir(parents=True, exist_ok=True)
    export_identity_audit_frames(
        workspace / "video.mp4",
        [int(row["frame"]) for row in selection.get("selected_frames") or []],
        frames_path,
    )
    _write(workspace / AUDIT_DIRECTORY / SELECTION_FILENAME, selection)
    return build_initial_identity_audit_document(selection, match_document)


def _prepare_h2_reanchor(workspace: Path, match_document: dict[str, Any]) -> dict[str, Any]:
    selection = _bounded_h1_selection(_load(workspace / "global_identity.json"), _load(workspace / "analysis_report.json"), maximum=3, capture_domain="analysis:343980c8", artifact_directory=REANCHOR_FRAME_DIRECTORY)
    selection["mode"] = "second_half_identity_reanchor_selection_shadow"
    selection["second_half"] = {"start_time_sec": 0.0, "start_frame": 0, "safely_resolved_players_before_reanchor": []}
    frame_path = workspace / REANCHOR_DIRECTORY / "frames"
    frame_path.mkdir(parents=True, exist_ok=True)
    export_identity_audit_frames(workspace / "video.mp4", [int(row["frame"]) for row in selection.get("selected_frames") or []], frame_path)
    _write(workspace / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME, selection)
    return build_second_half_identity_reanchor_document(selection, match_document, safely_resolved_players=[])


def _bounded_h1_selection(identity: dict[str, Any], report: dict[str, Any], *, maximum: int = 8, capture_domain: str = "analysis:7655bf7c", artifact_directory: str = FRAME_DIRECTORY) -> dict[str, Any]:
    """Select eight diverse, high-visibility frames in one bounded pass.

    This is the benchmark adapter for an older frozen H1 artifact whose full
    IA0 scorer is computationally impractical to replay. It uses only frozen
    slot positions; no detection, tracking or visual inference is performed.
    """
    video = report.get("video") or {}
    fps = max(1.0, float(video.get("fps") or 30.0))
    positions_by_frame: dict[int, list[dict[str, Any]]] = {}
    for slot in identity.get("slots") or []:
        for position in slot.get("overlay_positions") or []:
            if position.get("status") != "detected" or not position.get("bbox_xyxy"):
                continue
            frame = int(position.get("frame") or 0)
            positions_by_frame.setdefault(frame, []).append({
                "stable_subject_id": slot.get("stable_subject_id"), "stable_player_id": slot.get("stable_player_id"),
                "slot_id": slot.get("slot_id"), "tracklet_id": position.get("tracklet_id"),
                "raw_track_id": position.get("raw_track_id"), "stint_id": position.get("stint_id"),
                "team_label": slot.get("team_label"), "role": "field_player", "source": position.get("source"),
                "bbox_xyxy": position.get("bbox_xyxy"), "confidence": position.get("confidence"),
            })
    candidates = [
        (frame, rows) for frame, rows in positions_by_frame.items()
        if frame % 150 == 0 and len(rows) >= 7
    ]
    candidates.sort(key=lambda row: (-len(row[1]), row[0]))
    chosen: list[tuple[int, list[dict[str, Any]]]] = []
    for frame, rows in candidates:
        if all(abs(frame - existing) >= int(20 * fps) for existing, _ in chosen):
            chosen.append((frame, rows))
        if len(chosen) == maximum:
            break
    chosen.sort(key=lambda row: row[0])
    selected = [{
        "frame": frame, "time_sec": round(frame / fps, 3), "intrinsic_score": round(len(rows) / 14.0, 6),
        "selection_score": round(len(rows) / 14.0, 6), "score_components": {"visible_players": round(len(rows) / 14.0, 6), "frozen_h1_adapter": 1.0},
        "visible_detections": rows, "capture_domain": capture_domain, "selection_rank": index,
        "selection_reasons": ["high_visible_player_count", "temporal_diversity", "frozen_artifact_adapter"],
        "full_frame_artifact": f"{artifact_directory}/frame-{frame:06d}.jpg", "thumbnail_artifact": f"{artifact_directory}/frame-{frame:06d}-thumb.jpg",
    } for index, (frame, rows) in enumerate(chosen, start=1)]
    selection = {"schema_version": "0.1.0", "mode": "frozen_h1_benchmark_selection", "algorithm": {"name": IA0_ALGORITHM_NAME, "version": "benchmark-adapter-v1"}, "generated_at": datetime.now(timezone.utc).isoformat(), "video": {"fps": fps, "frame_count": int(video.get("frame_count") or 0), "duration_sec": float(video.get("duration_sec") or 0.0), "width": int(video.get("width") or 1), "height": int(video.get("height") or 1)}, "source": {"analysis_run_id": "frozen_benchmark_source", "frozen_artifacts_only": True}, "selected_frames": selected, "summary": {"selected_frames": len(selected), "maximum_frames": maximum, "full_ia0_replay": False}}
    selection["selection_digest"] = canonical_digest(selected)
    return selection


def _create_workspace(workspace: Path, original_meta: dict[str, Any], source: Path, benchmark_id: str, domain: str) -> None:
    workspace.mkdir()
    required = set(REQUIRED_H1_ARTIFACTS if domain == "H1" else REQUIRED_H2_ARTIFACTS)
    required.update({"camera_motion_report.json", "match_phase_config.json", "identity_occlusion_events.json"})
    for item in source.iterdir():
        if item.name in {"identity_operator_seeds.json", "identity_seeded_candidate_assignments.json", "identity_seeded_review_reduction_report.json", "identity_roster_subject_review_decisions_shadow.json", "identity_initial_audit", "identity_second_half_reanchor"}:
            continue
        if item.is_file() and item.name in required:
            # Session artifacts are intentionally writable; never hard-link a
            # JSON document that a downstream shadow rebuild may replace.
            shutil.copy2(item, workspace / item.name)
        elif item.is_file() and item.name == "video.mp4":
            _link_or_copy(item, workspace / item.name)
    if not (workspace / "video.mp4").exists():
        for candidate_root in (source.parent, source.parent.parent):
            for video in candidate_root.glob("video.*"):
                _link_or_copy(video, workspace / "video.mp4")
                break
            if (workspace / "video.mp4").exists():
                break
    if not (workspace / "video.mp4").exists():
        raise ProductFlowBenchmarkError("Frozen source video is missing")
    meta = {**original_meta, "id": f"benchmark-{benchmark_id}-{domain.lower()}", "title": f"Benchmark {domain}: {original_meta.get('title')}", "benchmark_session": {"id": benchmark_id, "domain": domain, "shadow_only": True, "reanchor_only": domain == "H2"}}
    meta.pop("analysis_runs", None)
    meta.pop("latest_analysis_run_id", None)
    _write(workspace / "match.json", meta)


def _write_h2_phase_config(workspace: Path) -> None:
    _write(workspace / "match_phase_config.json", {"schema_version": "0.1.0", "second_half_start_time_sec": 0.0, "periods": [{"period_id": "second_half", "start_time_sec": 0.0}], "summary": {"has_second_half": True}})


def _workspace_descriptor(workspace: Path, source_match_id: str, domain: str, audit: dict[str, Any]) -> dict[str, Any]:
    match_id = str((_load(workspace / "match.json")).get("id") or "")
    return {"match_id": match_id, "source_match_id": source_match_id, "capture_domain": domain, "workspace": workspace.name, "audit_status": audit.get("status", "ready"), "frames": int((audit.get("summary") or {}).get("selected_frames") or 0), "source_digests": {name: canonical_digest(_load(workspace / name)) for name in ("analysis_report.json", "global_identity.json", "tracklets.json", "identity_candidate_shadow.json", "identity_offline_shadow_timeline.json") if (workspace / name).exists()}}


def _domain_metrics(domain: str, candidate: dict[str, Any], timeline: dict[str, Any], seeded: dict[str, Any] | None) -> dict[str, Any]:
    subjects = candidate.get("subjects") or []
    timeline_subjects = timeline.get("subjects") or []
    tracklets = {str(tracklet) for subject in subjects for tracklet in subject.get("tracklet_ids") or []}
    detected_frames = {int(obs.get("frame") or 0) for subject in timeline_subjects for obs in subject.get("observations") or [] if str(obs.get("status") or "detected") == "detected"}
    accepted = (seeded or {}).get("accepted_assignments") or []
    summary = (seeded or {}).get("summary") or {}
    safety = (seeded or {}).get("safety") or {}
    safe_tracklets = min(
        len(tracklets),
        int(summary.get("tracklets_resolved_after_seeding") or 0),
    )
    safe_frames = min(
        len(detected_frames),
        int(summary.get("frames_resolved_after_seeding") or 0),
    )
    return {"capture_domain": domain, "has_operator_actions": bool((seeded or {}).get("exact_observation_seeds")), "review_cards_before": len(subjects), "review_cards_after": len(subjects) - len(accepted), "unresolved_subjects_before": len(subjects), "unresolved_subjects_after": len(subjects) - len(accepted), "unresolved_tracklets_before": len(tracklets), "unresolved_tracklets_after": len(tracklets) - safe_tracklets, "unresolved_frames_before": len(detected_frames), "unresolved_frames_after": len(detected_frames) - safe_frames, "safe_subjects_after": int(summary.get("subjects_resolved_after_seeding") or 0), "safe_tracklets_after": safe_tracklets, "safe_frames_after": safe_frames, "conflicts": int(summary.get("conflicts_created") or 0), "cross_team_conflicts": int(safety.get("cross_team_links") or 0), "parallel_conflicts": int(safety.get("parallel_assignment_conflicts_detected") or 0), "candidate_digest": canonical_digest(candidate), "timeline_digest": canonical_digest(timeline)}


def _sum_metrics(rows: list[dict[str, Any]], *, after: bool) -> dict[str, Any]:
    suffix = "after" if after else "before"
    return {"review_cards": sum(int(row[f"review_cards_{suffix}"]) for row in rows), "unresolved_subjects": sum(int(row[f"unresolved_subjects_{suffix}"]) for row in rows), "unresolved_tracklets": sum(int(row.get(f"unresolved_tracklets_{suffix}") or 0) for row in rows), "unresolved_frames": sum(int(row.get(f"unresolved_frames_{suffix}") or 0) for row in rows), "safely_resolved_subjects": sum(int(row["safe_subjects_after"]) for row in rows) if after else 0, "safely_resolved_tracklets": sum(int(row["safe_tracklets_after"]) for row in rows) if after else 0, "safely_resolved_frames": sum(int(row["safe_frames_after"]) for row in rows) if after else 0, "parallel_conflicts": sum(int(row["parallel_conflicts"]) for row in rows) if after else 0, "cross_team_conflicts": sum(int(row["cross_team_conflicts"]) for row in rows) if after else 0, "structural_conflicts": sum(int(row["conflicts"]) for row in rows) if after else 0}


def _safety() -> dict[str, Any]:
    return {"automatic_cross_analysis_assignments": 0, "automatic_reid_merges": 0, "candidate_identity_mutations": 0, "production_identity_mutations": 0, "production_stats_mutations": 0, "yolo_reruns": 0, "tracking_reruns": 0, "shadow_candidate_only": True, "reid_advisory_top_k": 3}


def _validate_pair(source: dict[str, Any], target: dict[str, Any]) -> None:
    if not source.get("match_date") or source.get("match_date") != target.get("match_date"):
        raise ProductFlowBenchmarkError("H1 and H2 do not prove the same physical match")
    source_teams = [str(team.get("id")) for team in source.get("teams") or []]
    target_teams = [str(team.get("id")) for team in target.get("teams") or []]
    if source_teams != target_teams:
        raise ProductFlowBenchmarkError("H1 and H2 roster/team contracts differ")


def _latest_run_path(match: Path, meta: dict[str, Any]) -> Path:
    run_id = str(meta.get("latest_analysis_run_id") or "")
    runs = [row for row in meta.get("analysis_runs") or [] if isinstance(row, dict)]
    runs.sort(key=lambda row: (str(row.get("run_id") or "") != run_id, str(row.get("generated_at") or "")))
    for row in runs:
        if row.get("run_directory"):
            candidate = match / str(row["run_directory"])
            if all((candidate / name).exists() for name in REQUIRED_H1_ARTIFACTS):
                return candidate
    return match


def _require(path: Path, filenames: tuple[str, ...]) -> None:
    missing = [name for name in filenames if not (path / name).exists()]
    if missing:
        raise ProductFlowBenchmarkError(f"Frozen artifacts are missing: {', '.join(missing)}")


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
