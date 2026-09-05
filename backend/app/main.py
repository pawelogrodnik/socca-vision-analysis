from __future__ import annotations

import json
import logging
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from app.config import ADMIN_IMPORT_TOKEN, APP_MODE, CORS_ORIGINS, MATCHES_DIR, PUBLISH_TARGET
from app.logging_config import configure_application_logging
from app.models import AnalyzePayload, BallAnalyzePayload, MatchGroupExternalVideoPayload, MatchGroupPayload, MatchMetadataPayload, PitchConfigPayload
from app.services.analysis import analyze_match, analyze_match_ball_yolo
from app.services.analysis_jobs import list_analysis_jobs, load_analysis_job, mark_interrupted_analysis_jobs, start_analysis_job
from app.services.change_candidates import load_change_candidates_review, save_change_candidate_reviews
from app.services.chunked_analysis import analyze_match_chunked_yolo
from app.services.contact_review import load_contact_candidates_review, save_contact_candidate_reviews
from app.services.identity import build_identity_review, save_identity_assignments
from app.services.identity_crop_review import (
    build_identity_crop_review,
    refresh_identity_crop_assignments,
    save_identity_crop_assignments,
)
from app.services.identity_bounded_h2_reid_followup import (
    load_bounded_h2_reid_followup,
    save_bounded_h2_reid_decisions,
)
from app.services.identity_initial_audit import prepare_initial_identity_audit
from app.services.identity_initial_audit_store import (
    InitialIdentityAuditConflictError,
    InitialIdentityAuditStaleError,
    OperatorDecisionBudgetExceededError,
    load_initial_identity_audit_seeds,
    save_initial_identity_audit_seeds,
    write_identity_json_atomic,
)
from app.services.identity_product_flow_state import (
    ProductFlowStateError,
    benchmark_context_for_workspace,
)
from app.services.identity_second_half_reanchor import (
    prepare_second_half_identity_reanchor,
)
from app.services.identity_second_half_reanchor_store import (
    load_second_half_identity_reanchor_seeds,
    save_second_half_identity_reanchor_seeds,
)
from app.services.identity_seeded_candidate_assignments import (
    SeededCandidateAssignmentsStaleError,
    load_identity_seeded_candidate_assignments,
    rebuild_identity_seeded_candidate_assignments,
)
from app.services.identity_seeded_subject_review_rebuild import (
    rebuild_identity_seeded_subject_review,
)
from app.services.identity_product_flow_benchmark import (
    ProductFlowBenchmarkError,
    build_product_flow_benchmark_report,
    finish_product_flow_h1,
    finish_product_flow_h2,
    prepare_product_flow_benchmark,
)
from app.services.identity_review_gallery import build_identity_review_gallery, load_identity_review_gallery
from app.services.identity_review_segments import save_identity_review_splits
from app.services.identity_roster_subject_review_store import (
    load_identity_roster_subject_review,
    save_identity_roster_subject_review,
)
from app.services.identity_reviewed_output_jobs import (
    ReviewedOutputBusyError,
    generate_reviewed_output,
    reviewed_output_status,
)
from app.services.identity_reviewed_action_gate import (
    DeferredReviewActionError,
    validate_deferred_review_action,
)
from app.services.identity_reviewed_action_scope import (
    ReviewedIdentityActionScopeError,
    reviewed_identity_action_capabilities,
)
from app.services.identity_reviewed_corrections import (
    persist_reviewed_identity_correction,
    save_reviewed_identity_correction,
)
from app.services.identity_reviewed_coverage import (
    paginate_progress,
)
from app.services.identity_reviewed_progress import (
    build_reviewed_identity_progress,
    reviewed_snapshot_file_fingerprint,
)
from app.services.identity_reviewed_correction_context import (
    reviewed_decisions_semantic_digest,
)
from app.services.identity_review_scope import (
    identity_review_scope_digest,
    validate_identity_review_scope,
)
from app.services.identity_reviewed_recompute_state import (
    reviewed_identity_recompute_required,
)
from app.services.identity_reviewed_hot_state import (
    ReviewedIdentityHotStateError,
    hot_context,
    hot_historical_split_repair_context,
    hot_progress,
    hot_review_unit,
    invalidate_review_hot_state,
    load_existing_fresh_hot_state,
    load_or_rebuild_review_hot_state,
    load_or_rebuild_review_hot_state_with_source,
    update_hot_state_after_deferred_save,
)
from app.services.identity_reviewed_snapshot import (
    finalize_reviewed_identity,
    get_reviewed_identity_status,
    reviewed_assignment_at,
)
from app.services.identity_reviewed_slot_review import (
    load_reviewed_slot_assignments,
    reviewed_slot_assignment_read_model,
    save_reviewed_slot_assignments,
)
from app.services.identity_reviewed_segments import SegmentTargetError
from app.services.identity_reviewed_mixed_store import (
    build_focused_mixed_review_case,
    build_mixed_boundary_refinement,
    build_mixed_review_queue,
    inline_temporal_split_for_source,
    materialize_mixed_review_artifact,
    render_mixed_review_evidence,
    load_mixed_player_cases,
)
from app.services.identity_reviewed_decision_audit import (
    commit_staged_operator_decision_audit,
    discard_staged_operator_decision_audit,
    prepare_operator_decision_audit_event,
    recover_staged_operator_decision_audits,
    stage_operator_decision_audit,
)
from app.services.identity_canonical_io import review_build_context
from app.services.identity_reviewed_mixed_resolution import (
    MixedPlayerTargetError,
    save_inline_temporal_split,
    save_mixed_player_resolution,
    validate_concurrent_lane_resolution_request,
)
from app.services.identity_reviewed_concurrent_lanes import ConcurrentLaneResolutionError
from app.services.identity_reviewed_mixed_topology import (
    MixedTemporalTopologyError,
    require_simple_temporal_split,
)
from app.services.identity_reviewed_review_source import (
    ReviewedIdentityReviewSourceError,
    build_concurrent_lane_boundary_refinement,
    build_review_source_boundary_refinement,
    resolve_review_source,
    source_case_id,
)
from app.services.review_workflow_orchestrator import (
    ReviewWorkflowRecomputeError,
    after_video_qa_correction,
    approve_review_video_qa,
    finalize_review_for_qa,
    public_finalized_identity,
    public_review_progress,
    refresh_review_after_identity_mutation,
    retry_review_recompute,
    retry_review_render,
)
from app.services.review_workflow_state import (
    WorkflowActionError,
    assert_workflow_action_allowed,
    build_compact_review_workflow_state,
    build_cheap_finalize_preflight_state,
    get_review_workflow_state,
)
from app.services.json_publish_store import (
    delete_published_match,
    get_published_match,
    import_match_package,
    init_publish_store,
    list_eligible_match_group_sources,
    list_published_matches,
    publish_store_health,
)
from app.services.match_group_aggregation import (
    build_match_group_report_candidate,
    generate_match_group_report,
    get_coherent_match_group_report,
)
from app.services.match_group_refresh import preview_match_group_refresh, refresh_match_group_to_latest
from app.services.match_group_video import (
    COMBINED_VIDEO_FILENAME,
    MatchGroupVideoError,
    delete_match_group_when_video_idle,
    generation_video,
    get_match_group_video_status,
    submit_match_group_video_generation,
)
from app.services.match_group_external_video import (
    MatchGroupExternalVideoError,
    delete_match_group_external_video,
    get_match_group_external_video,
    save_match_group_external_video,
)
from app.services.match_groups import (
    MatchGroupError,
    create_match_group_and_generate_report,
    get_match_group,
    list_match_groups,
    preview_match_group,
    update_match_group_and_generate_report,
    validate_match_group,
)
from app.services.merged_public_match import (
    _ensure_merged_published_match_locked,
    check_merged_projection,
    delete_merged_projection_by_id,
    delete_merged_published_match,
    ensure_merged_published_match,
    group_id_for_merged_published_id,
    is_merged_published_id,
    merged_ids_for_group,
    merged_published_id_for_group,
    regenerate_merged_match_group,
    refresh_merged_match_to_latest,
)
from app.services.match_phase_config import load_match_phase_config, save_match_phase_config
from app.services.pass_review import load_pass_candidates_review, save_pass_candidate_reviews
from app.services.player_identity import build_player_identity_review, save_player_identity_assignments
from app.services.player_profiles import build_player_profile_stats
from app.services.publish import PublishError, publish_match_package
from app.services.resolved_player_stats import build_resolved_player_stats_from_files
from app.services.reviewed_match_report import (
    REVIEWED_PACKAGE_INPUTS,
    apply_reviewed_identity_to_report_package,
    build_reviewed_match_report,
    reviewed_identity_package_status,
)
from app.services.runtime import build_performance_report, collect_runtime_info, normalize_yolo_device
from app.services.stabilization import load_stable_review, load_team_config_review, save_stable_review, save_team_config_review
from app.services.team_profiles import build_team_profile_stats
from app.services.team_registry import create_team as registry_create_team
from app.services.team_registry import delete_team as registry_delete_team
from app.services.team_registry import get_team as registry_get_team
from app.services.team_registry import list_teams as registry_list_teams
from app.services.team_registry import update_team as registry_update_team
from app.services.video import extract_frame, read_video_metadata, resolve_match_video_path

app = FastAPI(title="Orlik Vision API", version="0.6.0")
logger = logging.getLogger(__name__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _workflow_http_error(error: WorkflowActionError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": error.code,
            "attempted_action": error.action,
            "workflow": error.state,
        },
    )


def _assert_publish_workflow(match_path: Path) -> None:
    workflow = get_review_workflow_state(match_path, read_match_meta(match_path))
    if workflow.get("review_complete"):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "review_not_completed",
            "workflow": workflow,
        },
    )


@app.on_event("startup")
def startup() -> None:
    application_logger = configure_application_logging()
    application_logger.info(
        "[app-logging] configured level=%s",
        logging.getLevelName(application_logger.getEffectiveLevel()),
    )
    mark_interrupted_analysis_jobs(MATCHES_DIR)
    init_publish_store()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "item"


def require_admin_import_token(authorization: str | None) -> None:
    if not ADMIN_IMPORT_TOKEN:
        return
    expected = f"Bearer {ADMIN_IMPORT_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing admin import token")


def with_generated_ids(metadata: dict[str, Any]) -> dict[str, Any]:
    """Ensure teams and players have stable IDs before writing match metadata."""
    teams = []
    for team_idx, team in enumerate(metadata.get("teams") or []):
        team = dict(team)
        team_id = team.get("id") or f"team-{team_idx + 1}-{slugify(str(team.get('name') or 'team'))}"
        team["id"] = team_id
        players = []
        for player_idx, player in enumerate(team.get("players") or []):
            player = dict(player)
            player["id"] = player.get("id") or f"{team_id}-player-{player_idx + 1}-{slugify(str(player.get('name') or 'player'))}"
            players.append(player)
        team["players"] = players
        teams.append(team)
    metadata["teams"] = teams
    return metadata


def read_match_meta(path: Path) -> dict[str, Any]:
    meta_path = path / "match.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="match.json not found")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def write_match_meta(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    meta["updated_at"] = now_iso()
    (path / "match.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def match_dir(match_id: str) -> Path:
    path = MATCHES_DIR / match_id
    if not path.exists():
        raise HTTPException(status_code=404, detail="Match not found")
    return path


PRODUCT_FLOW_BENCHMARKS_DIR = MATCHES_DIR.parent / "benchmarks" / "player_identity"


def match_video_path(path: Path) -> Path:
    try:
        return resolve_match_video_path(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def parse_metadata_form(
    *,
    title: str,
    match_date: str | None,
    season: str | None,
    venue: str | None,
    format: str,
    teams_json: str | None,
    identity_review_scope_json: str | None = None,
) -> dict[str, Any]:
    teams: list[dict[str, Any]] = []
    if teams_json:
        try:
            loaded = json.loads(teams_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid teams_json: {exc}") from exc
        if not isinstance(loaded, list):
            raise HTTPException(status_code=400, detail="teams_json must be a JSON array")
        teams = loaded
    review_scope = None
    if identity_review_scope_json:
        try:
            review_scope = validate_identity_review_scope(
                json.loads(identity_review_scope_json)
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid identity_review_scope_json: {exc}",
            ) from exc
    payload = MatchMetadataPayload(
        title=title,
        match_date=match_date or None,
        season=season or None,
        venue=venue or None,
        format=format or "7v7",
        status="uploaded",
        teams=teams,
        identity_review_scope=review_scope,
    )
    metadata = payload.model_dump()
    if metadata.get("identity_review_scope") is None:
        metadata.pop("identity_review_scope", None)
    from app.services.match_roster import require_match_roster

    require_match_roster(metadata, require_player_ids=False)
    metadata = with_generated_ids(metadata)
    require_match_roster(metadata)
    return metadata


def load_tracks(path: Path) -> list[dict[str, Any]]:
    tracks_path = path / "tracks.json"
    if not tracks_path.exists():
        raise HTTPException(status_code=404, detail="tracks.json not found. Run analysis first.")
    tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
    if not isinstance(tracks, list):
        raise HTTPException(status_code=400, detail="tracks.json must contain a list")
    return tracks


def summarize_track(track: dict[str, Any]) -> dict[str, Any]:
    positions = track.get("positions") if isinstance(track.get("positions"), list) else []
    confidences = [float(pos.get("confidence")) for pos in positions if isinstance(pos, dict) and pos.get("confidence") is not None]
    first = positions[0] if positions else {}
    last = positions[-1] if positions else {}
    return {
        "tracklet_id": int(track.get("track_id")),
        "start_time_sec": track.get("start_time_sec"),
        "end_time_sec": track.get("end_time_sec"),
        "duration_sec": track.get("duration_sec"),
        "positions_count": track.get("positions_count", len(positions)),
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "first_pitch_m": first.get("pitch_m") if isinstance(first, dict) else None,
        "last_pitch_m": last.get("pitch_m") if isinstance(last, dict) else None,
        "first_bbox_xyxy": first.get("bbox_xyxy") if isinstance(first, dict) else None,
        "last_bbox_xyxy": last.get("bbox_xyxy") if isinstance(last, dict) else None,
    }


def summarize_analysis_run(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": report.get("run_id"),
        "status": report.get("status"),
        "analysis_type": report.get("analysis_type"),
        "generated_at": report.get("generated_at"),
        "frames_processed": report.get("frames_processed"),
        "tracks_count": report.get("tracks_count"),
        "stable_players_count": report.get("stable_players_count"),
        "parameters": report.get("parameters") or {},
        "run_directory": report.get("run_directory"),
        "run_manifest": report.get("run_manifest"),
    }


def run_match_analysis_and_update_meta(
    *,
    match_id: str,
    path: Path,
    video_path: Path,
    payload: AnalyzePayload,
    job_id: str | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    if progress:
        progress("preparing", 8.0, "Preparing analysis inputs.", None)
    started_at = time.perf_counter()
    if payload.chunked:
        report = analyze_match_chunked_yolo(
            path,
            video_path,
            payload=payload.model_dump(),
            job_id=job_id,
            progress=progress,
        )
    else:
        if progress:
            progress("analyzing", 20.0, "Running video analysis.", None)
        report = analyze_match(
            path,
            video_path,
            adapter=payload.adapter,  # type: ignore[arg-type]
            max_seconds=payload.max_seconds,
            frame_stride=max(1, payload.frame_stride),
            yolo_model=payload.yolo_model,
            yolo_conf=payload.yolo_conf,
            yolo_imgsz=payload.yolo_imgsz,
            yolo_tracker=payload.yolo_tracker,
            yolo_device=payload.yolo_device,
            include_ball=payload.include_ball,
            ball_yolo_model=payload.ball_yolo_model,
            ball_yolo_conf=payload.ball_yolo_conf,
            ball_yolo_imgsz=payload.ball_yolo_imgsz,
            ball_yolo_device=payload.ball_yolo_device,
            camera_motion_compensation=payload.camera_motion_compensation,
            camera_motion_interval_sec=payload.camera_motion_interval_sec,
            camera_motion_min_inlier_ratio=payload.camera_motion_min_inlier_ratio,
            render_stable_overlay=payload.render_stable_overlay,
        )
    elapsed_wall_sec = time.perf_counter() - started_at
    report_runtime = report.get("runtime") if isinstance(report.get("runtime"), dict) else collect_runtime_info()
    report_parameters = report.get("parameters") if isinstance(report.get("parameters"), dict) else {}
    normalized_device = str(report_parameters.get("yolo_device") or "")
    if normalized_device == "auto":
        normalized_device = normalize_yolo_device(payload.yolo_device) or "auto"
    performance_report = build_performance_report(
        label=f"{match_id}-{payload.adapter}",
        requested_device=payload.yolo_device,
        normalized_device=normalized_device,
        elapsed_wall_sec=elapsed_wall_sec,
        analysis_report=report,
        runtime_info=report_runtime,
    )
    report["performance_report"] = performance_report
    (path / "performance_report.json").write_text(json.dumps(performance_report, indent=2), encoding="utf-8")
    report = attach_analysis_artifact_to_report(path, report, key="performance_report", filename="performance_report.json")
    if progress:
        progress("finalizing", 95.0, "Updating match metadata.", None)
    meta = read_match_meta(path)
    if report.get("status") == "completed":
        meta["status"] = "analyzed"
    run_summary = summarize_analysis_run(report)
    if run_summary.get("run_id"):
        existing_runs = [item for item in meta.get("analysis_runs", []) if isinstance(item, dict)]
        existing_runs = [item for item in existing_runs if item.get("run_id") != run_summary["run_id"]]
        meta["analysis_runs"] = [run_summary, *existing_runs][:30]
        meta["latest_analysis_run_id"] = run_summary["run_id"]
    meta["latest_analysis_job_id"] = job_id or meta.get("latest_analysis_job_id")
    if job_id:
        meta["analysis_job_status"] = "completed" if report.get("status") == "completed" else str(report.get("status") or "finished")
    meta["updated_at"] = now_iso()
    write_match_meta(path, meta)
    return report


def attach_analysis_artifact_to_report(path: Path, report: dict[str, Any], *, key: str, filename: str) -> dict[str, Any]:
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), dict) else {}
    artifacts[key] = filename
    report["artifacts"] = artifacts
    run_directory = report.get("run_directory")
    if run_directory:
        run_dir = path / str(run_directory)
        run_dir.mkdir(parents=True, exist_ok=True)
        source = path / filename
        if source.exists() and source.is_file():
            shutil.copy2(source, run_dir / Path(filename).name)
        run_artifacts = report.get("run_artifacts") if isinstance(report.get("run_artifacts"), dict) else {}
        run_artifacts[key] = f"{run_directory}/{Path(filename).name}"
        report["run_artifacts"] = run_artifacts
        manifest_path = path / str(report.get("run_manifest") or "")
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                manifest["artifacts"] = artifacts
                manifest["run_artifacts"] = run_artifacts
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (run_dir / "analysis_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (path / "analysis_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def default_assignments_for_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tracklet_id": int(track.get("track_id")),
            "status": "unassigned",
            "team_id": None,
            "player_id": None,
            "notes": "",
        }
        for track in tracks
        if track.get("track_id") is not None
    ]


def load_player_assignments(path: Path, tracks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    assignment_path = path / "player_assignments.json"
    if assignment_path.exists():
        data = json.loads(assignment_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    if tracks is None:
        tracks = load_tracks(path)
    return {
        "schema_version": "0.1.0",
        "updated_at": now_iso(),
        "assignments": default_assignments_for_tracks(tracks),
        "summary": {},
    }


PACKAGE_REQUIRED_KEYS = [
    "analysis_report",
    "stable_players",
    "player_identity_assignments",
    "resolved_player_stats",
    "team_config",
    "team_stats",
]

PACKAGE_OPTIONAL_KEYS = [
    "pitch_config",
    "performance_report",
    "camera_motion_report",
    "analysis_chunk_manifest",
    "global_identity",
    "global_identity_report",
    "analysis_quality_report",
    "stabilization_report",
    "team_clusters",
    "frame_detection_counts",
    "movement_stats",
    "player_stats",
    "player_heatmaps",
    "team_shape",
    "change_candidates",
    "change_review_report",
    "ball_tracks",
    "ball_analysis_report",
    "ball_tracking_report",
    "ball_quality_report",
    "possession_candidates",
    "possession_segments",
    "contact_candidates",
    "match_phase_config",
    "event_candidates",
    "event_review_report",
    "pass_candidates",
    "pass_review_report",
    "attacking_momentum",
    "analytics_readiness",
    "possession_report",
    *REVIEWED_PACKAGE_INPUTS.keys(),
]

PACKAGE_DEBUG_KEYS = [
    "player_assignments",
    "identity_candidates",
    "identity_assignments",
    "tracklets",
    "tracking_quality_report",
]

PACKAGE_EMBEDDED_JSON_FILES = [
    ("pitch_config", "pitch_config.json"),
    ("analysis_report", "analysis_report.json"),
    ("performance_report", "performance_report.json"),
    ("camera_motion_report", "camera_motion_report.json"),
    ("analysis_chunk_manifest", "analysis_chunk_manifest.json"),
    ("player_identity_assignments", "player_identity_assignments.json"),
    ("identity_review_gallery", "identity_review_gallery.json"),
    ("stable_players", "stable_players.json"),
    ("global_identity_report", "global_identity_report.json"),
    ("analysis_quality_report", "analysis_quality_report.json"),
    ("stabilization_report", "stabilization_report.json"),
    ("frame_detection_counts", "frame_detection_counts.json"),
    ("team_clusters", "team_clusters.json"),
    ("movement_stats", "movement_stats.json"),
    ("player_stats", "player_stats.json"),
    ("resolved_player_stats", "resolved_player_stats.json"),
    ("resolved_stats_quality_report", "resolved_stats_quality_report.json"),
    ("player_heatmaps", "player_heatmaps.json"),
    ("team_config", "team_config.json"),
    ("team_stats", "team_stats.json"),
    ("team_shape", "team_shape.json"),
    ("change_candidates", "change_candidates.json"),
    ("change_review_report", "change_review_report.json"),
    ("ball_analysis_report", "ball_analysis_report.json"),
    ("ball_tracking_report", "ball_tracking_report.json"),
    ("ball_quality_report", "ball_quality_report.json"),
    ("match_phase_config", "match_phase_config.json"),
    ("pass_candidates", "pass_candidates.json"),
    ("pass_review_report", "pass_review_report.json"),
    ("attacking_momentum", "attacking_momentum.json"),
    ("analytics_readiness", "analytics_readiness.json"),
    ("possession_report", "possession_report.json"),
    *REVIEWED_PACKAGE_INPUTS.items(),
]

STABLE_PLAYER_PACKAGE_FIELDS = {
    "slot_id",
    "stable_subject_id",
    "stable_player_id",
    "identity_semantics",
    "status",
    "team_label",
    "team_id",
    "team_name",
    "team_confidence",
    "confidence",
    "confidence_score",
    "duration_sec",
    "start_time_sec",
    "end_time_sec",
    "tracklet_ids",
    "raw_track_ids",
    "tracklet_count",
    "positions_count",
    "real_positions_count",
    "overlay_positions_count",
    "trusted_overlay_positions_count",
    "detected_frames",
    "predicted_frames",
    "missing_frames",
    "ambiguous_frames",
    "interpolated_positions_count",
    "interpolated_gaps_count",
    "mean_detection_confidence",
    "jersey_color_hex",
    "movement_stats",
    "stints",
    "stint_count",
    "slot_creation_reason",
    "slot_spawn_frame",
    "slot_spawn_time_sec",
    "reused_from_slot_id",
    "blocked_team_switches",
    "blocked_identity_switches",
    "source",
    "heatmap_path",
    "heatmap_samples",
    "heatmap_quality",
}

STABLE_PLAYER_STINT_FIELDS = {
    "stint_id",
    "start_frame",
    "end_frame",
    "start_time_sec",
    "end_time_sec",
    "duration_sec",
    "detected_frames",
    "missing_frames",
    "ambiguous_frames",
    "source",
}


def _compact_row(row: dict[str, Any], allowed_keys: set[str]) -> dict[str, Any]:
    return {key: row[key] for key in allowed_keys if key in row}


def _slim_stable_players_doc(doc: dict[str, Any]) -> dict[str, Any]:
    players = doc.get("players") if isinstance(doc.get("players"), list) else []
    slim_players: list[dict[str, Any]] = []
    for player in players:
        if not isinstance(player, dict):
            continue
        slim_player = _compact_row(player, STABLE_PLAYER_PACKAGE_FIELDS)
        stints = player.get("stints") if isinstance(player.get("stints"), list) else []
        slim_player["stints"] = [
            _compact_row(stint, STABLE_PLAYER_STINT_FIELDS)
            for stint in stints
            if isinstance(stint, dict)
        ]
        slim_players.append(slim_player)

    slim_doc: dict[str, Any] = {
        key: doc[key]
        for key in [
            "schema_version",
            "generated_at",
            "source",
            "identity_semantics",
            "pitch_dimensions_m",
            "summary",
            "frame_detection_summary",
            "movement_stats_summary",
            "player_stats_summary",
            "team_stats_summary",
            "player_heatmaps_summary",
        ]
        if key in doc
    }
    slim_doc["players"] = slim_players
    slim_doc["package_note"] = "Stable players are compacted for report publishing; full debug data remains available as stable_players.json asset."
    return slim_doc


def _summary_only_doc(doc: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: doc[key] for key in keys if key in doc}


def _load_package_json_doc(key: str, file_path: Path) -> dict[str, Any]:
    doc = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return doc
    if key == "stable_players":
        return _slim_stable_players_doc(doc)
    if key == "frame_detection_counts":
        return _summary_only_doc(doc, ["schema_version", "generated_at", "source", "target_players", "summary"])
    if key == "global_identity_report":
        return _summary_only_doc(
            doc,
            [
                "schema_version",
                "generated_at",
                "status",
                "resolver_version",
                "identity_semantics",
                "summary",
                "frame_detection_summary",
            ],
        )
    if key == "stabilization_report":
        return _summary_only_doc(
            doc,
            [
                "schema_version",
                "generated_at",
                "status",
                "summary",
                "frame_detection_summary",
                "movement_stats_summary",
                "team_clusters_summary",
            ],
        )
    return doc


def _package_key_available(package: dict[str, Any], key: str) -> bool:
    if package.get(key) is not None:
        return True
    assets = package.get("assets") if isinstance(package.get("assets"), dict) else {}
    return f"{key}_json" in assets or key in assets


def _package_presence_map(package: dict[str, Any], keys: list[str]) -> dict[str, bool]:
    return {key: _package_key_available(package, key) for key in keys}


def _has_assigned_real_player(identity_doc: dict[str, Any] | None) -> bool:
    if not isinstance(identity_doc, dict):
        return False
    for assignment in identity_doc.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        if assignment.get("status") == "assigned" and assignment.get("player_id"):
            return True
    return False


def build_package_validation(package: dict[str, Any]) -> dict[str, Any]:
    identity_keys = {"player_identity_assignments", "resolved_player_stats"}
    missing_required = [
        key
        for key in PACKAGE_REQUIRED_KEYS
        if key not in identity_keys and not _package_key_available(package, key)
    ]
    warnings: list[str] = []
    reviewed_status = reviewed_identity_package_status(package)
    legacy_identity_ready = all(_package_key_available(package, key) for key in identity_keys)
    identity_source: str | None = None
    if reviewed_status["present"]:
        if reviewed_status["ready"]:
            identity_source = "reviewed_identity"
        else:
            missing_required.append("reviewed_identity_current")
            if reviewed_status.get("detail"):
                warnings.append(str(reviewed_status["detail"]))
    elif legacy_identity_ready:
        identity_source = "legacy_identity"
    else:
        missing_required.extend(
            key for key in identity_keys if not _package_key_available(package, key)
        )
    analysis_report = package.get("analysis_report") if isinstance(package.get("analysis_report"), dict) else None
    if analysis_report and analysis_report.get("status") != "completed":
        missing_required.append("analysis_report.status_completed")
    identity_doc = package.get("player_identity_assignments") if isinstance(package.get("player_identity_assignments"), dict) else None
    if identity_doc and not _has_assigned_real_player(identity_doc):
        warnings.append("No real roster player is assigned. This is allowed, but player profile aggregation will be empty.")
    summary = identity_doc.get("summary") if isinstance(identity_doc, dict) and isinstance(identity_doc.get("summary"), dict) else {}
    conflicts_total = int(summary.get("conflicts_total") or 0) if isinstance(summary, dict) else 0
    if conflicts_total > 0:
        warnings.append(f"Player identity review contains {conflicts_total} conflict(s).")
    status = "blocked" if missing_required else ("warnings" if warnings else "ready")
    return {
        "status": status,
        "missing_required": missing_required,
        "warnings": warnings,
        "optional_available": [key for key in PACKAGE_OPTIONAL_KEYS if _package_key_available(package, key)],
        "debug_available": [key for key in PACKAGE_DEBUG_KEYS if _package_key_available(package, key)],
        "identity_source": identity_source,
    }


def ensure_package_publishable(package: dict[str, Any]) -> None:
    validation = package.get("package_validation") if isinstance(package.get("package_validation"), dict) else build_package_validation(package)
    if validation.get("status") == "blocked":
        missing = ", ".join(str(item) for item in validation.get("missing_required") or [])
        raise ValueError(f"Match package is not publishable. Missing required data: {missing or 'unknown'}")


def build_assignment_summary(meta: dict[str, Any], tracks: list[dict[str, Any]], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    track_ids = {int(track.get("track_id")) for track in tracks if track.get("track_id") is not None}
    valid_assignments = [a for a in assignments if int(a.get("tracklet_id", -1)) in track_ids]
    assigned_tracklets = [a for a in valid_assignments if a.get("status") == "assigned" and a.get("player_id")]
    ignored_tracklets = [a for a in valid_assignments if a.get("status") in {"false_positive", "referee", "opponent"}]
    unassigned_tracklets = [a for a in valid_assignments if a.get("status") in {None, "", "unassigned", "unknown"}]

    unique_players_by_team: dict[str, set[str]] = {}
    assigned_tracklets_by_team: dict[str, int] = {}
    for assignment in assigned_tracklets:
        team_id = assignment.get("team_id") or "unknown-team"
        player_id = assignment.get("player_id")
        if player_id:
            unique_players_by_team.setdefault(team_id, set()).add(str(player_id))
        assigned_tracklets_by_team[team_id] = assigned_tracklets_by_team.get(team_id, 0) + 1

    roster_by_team = {
        str(team.get("id")): len(team.get("players") or [])
        for team in meta.get("teams") or []
        if isinstance(team, dict) and team.get("id")
    }

    return {
        "raw_tracklets": len(tracks),
        "assignments_total": len(valid_assignments),
        "assigned_tracklets": len(assigned_tracklets),
        "ignored_tracklets": len(ignored_tracklets),
        "unassigned_tracklets": len(unassigned_tracklets),
        "unique_players_total": len({str(a.get("player_id")) for a in assigned_tracklets if a.get("player_id")}),
        "unique_players_by_team": {team_id: len(players) for team_id, players in unique_players_by_team.items()},
        "assigned_tracklets_by_team": assigned_tracklets_by_team,
        "roster_players_by_team": roster_by_team,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app_mode": APP_MODE,
        "publish_target": PUBLISH_TARGET,
        "publish_store": publish_store_health(),
    }


@app.get("/api/runtime")
def runtime_info() -> dict[str, Any]:
    return collect_runtime_info()


@app.get("/api/teams")
def api_list_teams() -> list[dict[str, Any]]:
    return registry_list_teams()


@app.post("/api/teams")
def api_create_team(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return registry_create_team(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/teams/{team_id}")
def api_get_team(team_id: str) -> dict[str, Any]:
    try:
        return registry_get_team(team_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc


@app.get("/api/teams/{team_id}/stats")
def api_get_team_stats(team_id: str, season: str | None = Query(default=None)) -> dict[str, Any]:
    try:
        return build_team_profile_stats(MATCHES_DIR, team_id, season=season or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Team not found: {team_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/teams/{team_id}")
def api_update_team(team_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return registry_update_team(team_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/teams/{team_id}")
def api_delete_team(team_id: str) -> dict[str, Any]:
    try:
        return registry_delete_team(team_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc


@app.post("/api/matches")
def create_match(
    video: UploadFile = File(...),
    title: str = Form("Untitled match"),
    match_date: str | None = Form(None),
    season: str | None = Form(None),
    venue: str | None = Form(None),
    format: str = Form("7v7"),
    teams_json: str | None = Form(None),
    identity_review_scope_json: str | None = Form(None),
) -> dict[str, Any]:
    if APP_MODE == "production-viewer":
        raise HTTPException(status_code=403, detail="Video upload is disabled in production-viewer mode")
    if not video.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    try:
        meta = parse_metadata_form(
            title=title,
            match_date=match_date,
            season=season,
            venue=venue,
            format=format,
            teams_json=teams_json,
            identity_review_scope_json=(
                identity_review_scope_json
                if isinstance(identity_review_scope_json, str)
                else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    suffix = Path(video.filename).suffix.lower() or ".mp4"
    match_id = str(uuid.uuid4())[:8]
    path = MATCHES_DIR / match_id
    path.mkdir(parents=True, exist_ok=True)
    video_path = path / f"video{suffix}"
    with video_path.open("wb") as f:
        shutil.copyfileobj(video.file, f)

    metadata = read_video_metadata(video_path)
    meta.update(
        {
            "id": match_id,
            "video_filename": video.filename,
            "video": metadata,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    write_match_meta(path, meta)
    return meta


@app.get("/api/matches")
def list_matches() -> list[dict[str, Any]]:
    matches = []
    for path in sorted(MATCHES_DIR.iterdir(), reverse=True):
        meta_path = path / "match.json"
        if meta_path.exists():
            matches.append(json.loads(meta_path.read_text(encoding="utf-8")))
    return matches


@app.post("/api/product-flow-benchmarks")
def create_product_flow_benchmark(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    source_match_id = str(payload.get("source_match_id") or "7655bf7c")
    target_match_id = str(payload.get("target_match_id") or "343980c8")
    benchmark_id = str(payload.get("benchmark_id") or "product-flow-20260730-v2")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,80}", benchmark_id):
        raise HTTPException(status_code=400, detail="Invalid benchmark_id")
    try:
        return prepare_product_flow_benchmark(
            matches_root=MATCHES_DIR,
            benchmark_root=PRODUCT_FLOW_BENCHMARKS_DIR,
            source_match_id=source_match_id,
            target_match_id=target_match_id,
            benchmark_id=benchmark_id,
        )
    except ProductFlowBenchmarkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/product-flow-benchmarks/{benchmark_id}")
def get_product_flow_benchmark(benchmark_id: str) -> dict[str, Any]:
    root = PRODUCT_FLOW_BENCHMARKS_DIR / benchmark_id
    path = root / "benchmark_session.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Product-flow benchmark not found")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not document.get("state"):
        legacy_source_status = document.get("status")
        document["state"] = "FAILED"
        document["status"] = "FAILED"
        document["legacy_session"] = {
            "reason": (
                "Session predates the sequential H1→H2 state machine and "
                "cannot be resumed safely."
            ),
            "source_status": legacy_source_status,
        }
    for domain, workspace in (document.get("workspaces") or {}).items():
        if not isinstance(workspace, dict):
            continue
        match_id = str(workspace.get("match_id") or "")
        if not match_id:
            continue
        metadata = read_match_meta(match_dir(match_id))
        workspace["match"] = {
            key: metadata.get(key)
            for key in ("id", "title", "teams", "format", "status")
        }
    return document


@app.post("/api/product-flow-benchmarks/{benchmark_id}/report")
def refresh_product_flow_benchmark_report(benchmark_id: str) -> dict[str, Any]:
    root = PRODUCT_FLOW_BENCHMARKS_DIR / benchmark_id
    if not (root / "benchmark_session.json").exists():
        raise HTTPException(status_code=404, detail="Product-flow benchmark not found")
    report = build_product_flow_benchmark_report(root)
    if report.get("status") != "REPORT_READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BENCHMARK_REPORT_NOT_READY",
                "current_state": report.get("status"),
            },
        )
    (root / "benchmark_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


@app.post("/api/product-flow-benchmarks/{benchmark_id}/h1/finish")
def finish_product_flow_benchmark_h1(
    benchmark_id: str,
) -> dict[str, Any]:
    root = PRODUCT_FLOW_BENCHMARKS_DIR / benchmark_id
    if not (root / "benchmark_session.json").exists():
        raise HTTPException(
            status_code=404,
            detail="Product-flow benchmark not found",
        )
    try:
        return finish_product_flow_h1(
            root=root,
            matches_root=MATCHES_DIR,
        )
    except ProductFlowBenchmarkError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/product-flow-benchmarks/{benchmark_id}/h2/finish")
def finish_product_flow_benchmark_h2(
    benchmark_id: str,
) -> dict[str, Any]:
    root = PRODUCT_FLOW_BENCHMARKS_DIR / benchmark_id
    if not (root / "benchmark_session.json").exists():
        raise HTTPException(
            status_code=404,
            detail="Product-flow benchmark not found",
        )
    try:
        return finish_product_flow_h2(root=root)
    except ProductFlowBenchmarkError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/bounded-h2-reid-sessions/{session_id}")
def get_bounded_h2_reid_session(session_id: str) -> dict[str, Any]:
    session_path = PRODUCT_FLOW_BENCHMARKS_DIR / session_id
    if not (session_path / "bounded_h2_selection.json").is_file():
        raise HTTPException(status_code=404, detail="Bounded H2 session not found")
    try:
        return load_bounded_h2_reid_followup(session_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.put("/api/bounded-h2-reid-sessions/{session_id}/decisions")
def update_bounded_h2_reid_session(
    session_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    session_path = PRODUCT_FLOW_BENCHMARKS_DIR / session_id
    if not (session_path / "bounded_h2_selection.json").is_file():
        raise HTTPException(status_code=404, detail="Bounded H2 session not found")
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    try:
        return save_bounded_h2_reid_decisions(
            session_path,
            updates=updates,
            finished=bool(payload.get("finished")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    "/api/bounded-h2-reid-sessions/{session_id}/artifact/{artifact_path:path}"
)
def get_bounded_h2_reid_artifact(
    session_id: str,
    artifact_path: str,
) -> FileResponse:
    session_path = (PRODUCT_FLOW_BENCHMARKS_DIR / session_id).resolve()
    target = (session_path / artifact_path).resolve()
    if session_path not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Bounded H2 artifact not found")
    return FileResponse(target)


@app.get("/api/matches/{match_id}")
def get_match(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    for optional in [
        "pitch_config.json",
        "analysis_report.json",
        "performance_report.json",
        "camera_motion_report.json",
        "analysis_chunk_manifest.json",
        "match_package.json",
        "player_assignments.json",
        "identity_candidates.json",
        "identity_assignments.json",
        "player_identity_assignments.json",
        "stable_players.json",
        "global_identity_report.json",
        "analysis_quality_report.json",
        "stabilization_report.json",
        "team_clusters.json",
        "frame_detection_counts.json",
        "movement_stats.json",
        "player_stats.json",
        "resolved_player_stats.json",
        "resolved_stats_quality_report.json",
        "player_heatmaps.json",
        "team_config.json",
        "team_stats.json",
        "team_shape.json",
        "change_candidates.json",
        "change_review_report.json",
        "tracking_quality_report.json",
        "ball_analysis_report.json",
        "ball_tracking_report.json",
        "ball_quality_report.json",
        "possession_candidates.json",
        "possession_segments.json",
        "contact_candidates.json",
        "match_phase_config.json",
        "event_candidates.json",
        "event_review_report.json",
        "pass_candidates.json",
        "pass_review_report.json",
        "attacking_momentum.json",
        "possession_report.json",
    ]:
        optional_path = path / optional
        if optional_path.exists():
            meta[optional.removesuffix(".json")] = json.loads(optional_path.read_text(encoding="utf-8"))
    return meta


@app.put("/api/matches/{match_id}/metadata")
def update_match_metadata(match_id: str, payload: MatchMetadataPayload) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    previous_scope_digest = identity_review_scope_digest(meta)
    next_metadata = payload.model_dump()
    if next_metadata.get("identity_review_scope") is None:
        next_metadata.pop("identity_review_scope", None)
    next_metadata = with_generated_ids(next_metadata)
    if next_metadata.get("identity_review_scope") is not None:
        try:
            next_metadata["identity_review_scope"] = validate_identity_review_scope(
                next_metadata["identity_review_scope"]
            )
            from app.services.match_roster import require_match_roster

            require_match_roster(next_metadata)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta.update(next_metadata)
    write_match_meta(path, meta)
    if (
        identity_review_scope_digest(meta) != previous_scope_digest
        and (path / "reviewed_identity_snapshot.json").exists()
    ):
        progress = build_reviewed_identity_progress(path, meta)
        write_identity_json_atomic(path / "reviewed_identity_progress.json", progress)
    return meta


@app.get("/api/matches/{match_id}/frame")
def get_frame(match_id: str, second: float = 0.0) -> FileResponse:
    path = match_dir(match_id)
    video_path = match_video_path(path)
    frame_path = path / f"frame_{second:.2f}.jpg"
    if not frame_path.exists():
        extract_frame(video_path, second, frame_path)
    return FileResponse(frame_path, media_type="image/jpeg")


@app.get("/api/matches/{match_id}/video")
def get_video(match_id: str) -> FileResponse:
    path = match_dir(match_id)
    return FileResponse(match_video_path(path))


@app.post("/api/matches/{match_id}/pitch")
def save_pitch(match_id: str, payload: PitchConfigPayload) -> dict[str, Any]:
    path = match_dir(match_id)
    data = payload.model_dump()
    existing_path = path / "pitch_config.json"
    existing = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.exists() else {}
    data["pitch_dimensions_m"] = {
        "width_m": float(data.get("width_m") or 30.0),
        "length_m": float(data.get("length_m") or 47.4),
    }
    if data.get("calibration_frame_time_sec") is None and existing.get("calibration_frame_time_sec") is not None:
        data["calibration_frame_time_sec"] = existing.get("calibration_frame_time_sec")
    data["created_at"] = data.get("created_at") or existing.get("created_at") or now_iso()
    data["updated_at"] = now_iso()
    (path / "pitch_config.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    meta = read_match_meta(path)
    if meta.get("status") in {"draft", "uploaded"}:
        meta["status"] = "calibrated"
    meta["updated_at"] = now_iso()
    write_match_meta(path, meta)
    return {"status": "saved", "pitch_config": data}


@app.post("/api/matches/{match_id}/analyze")
def analyze(match_id: str, payload: AnalyzePayload) -> dict[str, Any]:
    if APP_MODE == "production-viewer":
        raise HTTPException(status_code=403, detail="Video analysis is disabled in production-viewer mode")
    path = match_dir(match_id)
    video_path = match_video_path(path)
    try:
        return run_match_analysis_and_update_meta(
            match_id=match_id,
            path=path,
            video_path=video_path,
            payload=payload,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected analysis error: {exc}") from exc


@app.post("/api/matches/{match_id}/analyze/background")
def analyze_background(match_id: str, payload: AnalyzePayload) -> dict[str, Any]:
    if APP_MODE == "production-viewer":
        raise HTTPException(status_code=403, detail="Video analysis is disabled in production-viewer mode")
    path = match_dir(match_id)
    video_path = match_video_path(path)

    def runner(job_id: str, update: Any) -> dict[str, Any]:
        try:
            return run_match_analysis_and_update_meta(
                match_id=match_id,
                path=path,
                video_path=video_path,
                payload=payload,
                job_id=job_id,
                progress=update,
            )
        except Exception:
            meta = read_match_meta(path)
            meta["latest_analysis_job_id"] = job_id
            meta["analysis_job_status"] = "failed"
            write_match_meta(path, meta)
            raise

    job = start_analysis_job(
        match_id=match_id,
        match_path=path,
        payload=payload.model_dump(),
        runner=runner,
    )
    meta = read_match_meta(path)
    meta["latest_analysis_job_id"] = job["job_id"]
    meta["analysis_job_status"] = job["status"]
    write_match_meta(path, meta)
    return job


@app.get("/api/matches/{match_id}/analysis-jobs")
def get_match_analysis_jobs(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    jobs = list_analysis_jobs(path)
    return {
        "schema_version": "0.1.0",
        "match_id": match_id,
        "jobs": jobs,
        "latest_job": jobs[0] if jobs else None,
    }


@app.get("/api/analysis-jobs/{job_id}")
def get_analysis_job(job_id: str) -> dict[str, Any]:
    try:
        return load_analysis_job(MATCHES_DIR, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/matches/{match_id}/analyze-ball")
def analyze_ball(match_id: str, payload: BallAnalyzePayload) -> dict[str, Any]:
    if APP_MODE == "production-viewer":
        raise HTTPException(status_code=403, detail="Video analysis is disabled in production-viewer mode")
    path = match_dir(match_id)
    video_path = match_video_path(path)
    try:
        report = analyze_match_ball_yolo(
            path,
            video_path,
            max_seconds=payload.max_seconds,
            frame_stride=max(1, payload.frame_stride),
            yolo_model=payload.yolo_model,
            yolo_conf=payload.yolo_conf,
            yolo_imgsz=payload.yolo_imgsz,
            yolo_device=payload.yolo_device,
        )
        meta = read_match_meta(path)
        run_summary = {
            "run_id": report.get("run_id"),
            "status": report.get("status"),
            "analysis_type": report.get("analysis_type"),
            "generated_at": report.get("generated_at"),
            "frames_processed": report.get("frames_processed"),
            "parameters": report.get("parameters") or {},
            "run_directory": report.get("run_directory"),
            "run_manifest": report.get("run_manifest"),
        }
        existing_runs = [item for item in meta.get("ball_analysis_runs", []) if isinstance(item, dict)]
        existing_runs = [item for item in existing_runs if item.get("run_id") != run_summary["run_id"]]
        meta["ball_analysis_runs"] = [run_summary, *existing_runs][:30]
        meta["latest_ball_analysis_run_id"] = run_summary["run_id"]
        meta["updated_at"] = now_iso()
        write_match_meta(path, meta)
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected ball analysis error: {exc}") from exc


@app.get("/api/matches/{match_id}/tracklets")
def get_match_tracklets(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    tracks = load_tracks(path)
    assignment_doc = load_player_assignments(path, tracks)
    assignments = assignment_doc.get("assignments") if isinstance(assignment_doc.get("assignments"), list) else []
    summary = build_assignment_summary(meta, tracks, assignments)
    assignment_doc["summary"] = summary
    return {
        "tracklets": sorted([summarize_track(track) for track in tracks], key=lambda item: float(item.get("duration_sec") or 0), reverse=True),
        "assignments": assignments,
        "summary": summary,
    }


@app.get("/api/matches/{match_id}/identity-candidates")
def get_identity_candidates(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    try:
        return build_identity_review(path, meta)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/identity-assignments")
def save_candidate_assignments(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        raise HTTPException(status_code=400, detail="assignments must be a list")
    try:
        doc = save_identity_assignments(path, meta, assignments)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/player-identity")
def get_player_identity(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    try:
        doc = build_player_identity_review(path, meta)
        if (path / "player_identity_assignments.json").exists() and (path / "player_stats.json").exists():
            doc["resolved_player_stats"] = build_resolved_player_stats_from_files(path, persist=True)
        return doc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/player-identity")
def review_player_identity(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        raise HTTPException(status_code=400, detail="assignments must be a list")
    try:
        doc = save_player_identity_assignments(path, meta, assignments)
        try:
            doc["resolved_player_stats"] = build_resolved_player_stats_from_files(path, persist=True)
        except FileNotFoundError:
            doc["resolved_player_stats"] = None
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/identity-review-gallery")
def get_identity_review_gallery(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_identity_review_gallery(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/matches/{match_id}/identity-review-gallery")
def generate_identity_review_gallery(
    match_id: str,
    samples_per_stint: int = Query(8, ge=1, le=24),
    force: bool = Query(False),
) -> dict[str, Any]:
    path = match_dir(match_id)
    video_path = match_video_path(path)
    try:
        gallery = build_identity_review_gallery(
            path,
            video_path,
            samples_per_stint=samples_per_stint,
            force=force,
        )
        refresh_identity_crop_assignments(path, read_match_meta(path))
        return gallery
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/identity-review-gallery/splits")
def split_identity_review_gallery(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    splits = payload.get("splits")
    if not isinstance(splits, list):
        raise HTTPException(status_code=400, detail="splits must be a list")
    samples_per_stint = payload.get("samples_per_stint") or 8
    if not isinstance(samples_per_stint, int) or not 1 <= samples_per_stint <= 24:
        raise HTTPException(status_code=400, detail="samples_per_stint must be between 1 and 24")
    try:
        save_identity_review_splits(path, splits)
        gallery = build_identity_review_gallery(
            path,
            match_video_path(path),
            samples_per_stint=samples_per_stint,
            force=True,
        )
        refresh_identity_crop_assignments(path, read_match_meta(path))
        return gallery
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/identity-crop-review")
def get_identity_crop_review(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return build_identity_crop_review(path, read_match_meta(path))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/initial-identity-audit")
def get_initial_identity_audit(
    match_id: str,
    force: bool = Query(False),
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return prepare_initial_identity_audit(
            path,
            match_video_path(path),
            read_match_meta(path),
            force=force,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def rebuild_seeded_identity_after_operator_audit(
    path: Path,
    match_document: dict[str, Any],
) -> dict[str, Any]:
    try:
        seeded_candidates = rebuild_identity_seeded_candidate_assignments(
            path,
            match_document,
        )
    except (
        FileNotFoundError,
        SeededCandidateAssignmentsStaleError,
        ValueError,
    ) as exc:
        return {
            "status": "unavailable",
            "reason": str(exc),
        }

    rebuild_status: dict[str, Any] = {
        "status": "fresh",
        "summary": seeded_candidates.get("summary") or {},
        "safety": seeded_candidates.get("safety") or {},
    }
    try:
        reduced_review = rebuild_identity_seeded_subject_review(
            path,
            match_document,
            video_path=match_video_path(path),
        )
        rebuild_status["whole_subject_review"] = {
            "status": reduced_review.get("status"),
            "summary": reduced_review.get("summary") or {},
            "initial_audit_integration": (
                reduced_review.get("initial_audit_integration") or {}
            ),
            "rendered_anchor_crops": reduced_review.get(
                "rendered_anchor_crops"
            ),
        }
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        rebuild_status["whole_subject_review"] = {
            "status": "warning",
            "reason": str(exc),
        }
    return rebuild_status


@app.get("/api/matches/{match_id}/initial-identity-audit/seeds")
def get_initial_identity_audit_seeds(
    match_id: str,
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        match_document = read_match_meta(path)
        prepare_initial_identity_audit(
            path,
            match_video_path(path),
            match_document,
        )
        return load_initial_identity_audit_seeds(path, match_document)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/initial-identity-audit/seeds")
def update_initial_identity_audit_seeds(
    match_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    telemetry_events = payload.get("telemetry_events") or []
    finalize = payload.get("finalize", False)
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    if not isinstance(telemetry_events, list):
        raise HTTPException(
            status_code=400,
            detail="telemetry_events must be a list",
        )
    if not isinstance(finalize, bool):
        raise HTTPException(status_code=400, detail="finalize must be a boolean")
    has_identity_updates = bool(updates)
    try:
        match_document = read_match_meta(path)
        workflow_before = get_review_workflow_state(path, match_document)
        if has_identity_updates:
            try:
                assert_workflow_action_allowed(
                    workflow_before,
                    "identify_players",
                )
            except WorkflowActionError as exc:
                raise _workflow_http_error(exc) from exc
        prepare_initial_identity_audit(
            path,
            match_video_path(path),
            match_document,
        )
        result = save_initial_identity_audit_seeds(
            path,
            match_document,
            updates,
            telemetry_events=telemetry_events,
        )
        if not finalize:
            # Frame transitions only persist decisions and telemetry.  Seeded
            # identity propagation runs once, after the required final save.
            result["workflow"] = (
                get_review_workflow_state(path, match_document)
                if has_identity_updates
                else workflow_before
            )
            return result
        if workflow_before.get("phase") != "initial_audit":
            result["workflow"] = workflow_before
            return result
        benchmark_context = benchmark_context_for_workspace(path)
        rebuild_status = (
            {
                "status": "deferred_until_benchmark_stage_finish",
                "benchmark_state": benchmark_context["state"],
            }
            if benchmark_context is not None
            else rebuild_seeded_identity_after_operator_audit(
                path,
                match_document,
            )
        )
        if rebuild_status.get("status") == "fresh":
            result["safety"] = {
                **(result.get("safety") or {}),
                "downstream_rebuild_triggered": True,
            }
        result["seeded_candidate_rebuild"] = rebuild_status
        refreshed = refresh_review_after_identity_mutation(
            path,
            match_document,
            source="initial_audit_finish",
        )
        result["workflow"] = refreshed["workflow"]
        result["reviewed_identity"] = public_finalized_identity(refreshed["snapshot"])
        return result
    except InitialIdentityAuditConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InitialIdentityAuditStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OperatorDecisionBudgetExceededError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "stage": exc.stage,
                "limit": exc.limit,
                "attempted": exc.attempted,
            },
        ) from exc
    except ProductFlowStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "current_state": exc.current_state,
                "requested_state": exc.requested_state,
            },
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/second-half-identity-reanchor")
def get_second_half_identity_reanchor(
    match_id: str,
    force: bool = Query(False),
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return prepare_second_half_identity_reanchor(
            path,
            match_video_path(path),
            read_match_meta(path),
            force=force,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/second-half-identity-reanchor/seeds")
def get_second_half_identity_reanchor_seeds(
    match_id: str,
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        match_document = read_match_meta(path)
        document = prepare_second_half_identity_reanchor(
            path,
            match_video_path(path),
            match_document,
        )
        if document.get("status") != "ready":
            raise HTTPException(
                status_code=409,
                detail=str(
                    document.get("reason")
                    or "Second-half re-anchor is not available"
                ),
            )
        return load_second_half_identity_reanchor_seeds(
            path,
            match_document,
        )
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/second-half-identity-reanchor/seeds")
def update_second_half_identity_reanchor_seeds(
    match_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    telemetry_events = payload.get("telemetry_events") or []
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    if not isinstance(telemetry_events, list):
        raise HTTPException(
            status_code=400,
            detail="telemetry_events must be a list",
        )
    try:
        match_document = read_match_meta(path)
        document = prepare_second_half_identity_reanchor(
            path,
            match_video_path(path),
            match_document,
        )
        if document.get("status") != "ready":
            raise HTTPException(
                status_code=409,
                detail=str(
                    document.get("reason")
                    or "Second-half re-anchor is not available"
                ),
            )
        result = save_second_half_identity_reanchor_seeds(
            path,
            match_document,
            updates,
            telemetry_events=telemetry_events,
        )
        benchmark_context = benchmark_context_for_workspace(path)
        rebuild_status = (
            {
                "status": "deferred_until_benchmark_stage_finish",
                "benchmark_state": benchmark_context["state"],
            }
            if benchmark_context is not None
            else rebuild_seeded_identity_after_operator_audit(
                path,
                match_document,
            )
        )
        if rebuild_status.get("status") == "fresh":
            result["safety"] = {
                **(result.get("safety") or {}),
                "downstream_rebuild_triggered": True,
            }
        result["seeded_candidate_rebuild"] = rebuild_status
        return result
    except HTTPException:
        raise
    except InitialIdentityAuditConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InitialIdentityAuditStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OperatorDecisionBudgetExceededError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "stage": exc.stage,
                "limit": exc.limit,
                "attempted": exc.attempted,
            },
        ) from exc
    except ProductFlowStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "current_state": exc.current_state,
                "requested_state": exc.requested_state,
            },
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get(
    "/api/matches/{match_id}/initial-identity-audit/seeded-candidates"
)
def get_initial_identity_audit_seeded_candidates(
    match_id: str,
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_identity_seeded_candidate_assignments(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/matches/{match_id}/initial-identity-audit/seeded-candidates/rebuild"
)
def rebuild_initial_identity_audit_seeded_candidates(
    match_id: str,
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return rebuild_identity_seeded_candidate_assignments(
            path,
            read_match_meta(path),
        )
    except SeededCandidateAssignmentsStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/identity-roster-subject-review")
def get_identity_roster_subject_review(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_identity_roster_subject_review(path, match_doc=read_match_meta(path))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/identity-roster-subject-review")
def review_identity_roster_subjects(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    telemetry_events = payload.get("telemetry_events") or []
    if not isinstance(telemetry_events, list):
        raise HTTPException(status_code=400, detail="telemetry_events must be a list")
    try:
        return save_identity_roster_subject_review(
            path,
            updates,
            match_doc=read_match_meta(path),
            telemetry_events=telemetry_events,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/identity-crop-review")
def review_identity_crops(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    try:
        result = save_identity_crop_assignments(path, read_match_meta(path), updates)
        try:
            result["resolved_player_stats"] = build_resolved_player_stats_from_files(path, persist=True)
        except FileNotFoundError:
            result["resolved_player_stats"] = None
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-identity")
def get_reviewed_identity(match_id: str) -> dict[str, Any]:
    return get_reviewed_identity_status(match_dir(match_id))


@app.get("/api/matches/{match_id}/review-workflow")
def get_match_review_workflow(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    # Normal browser reads must not parse the observation-level snapshot just
    # to derive a workflow card. This compact path is conservative on stale
    # generations and keeps authoritative recomputation at mutation/finalize.
    return build_compact_review_workflow_state(path, read_match_meta(path))


@app.post("/api/matches/{match_id}/review-workflow/finalize")
def finalize_match_review_workflow(
    match_id: str,
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return finalize_review_for_qa(path, read_match_meta(path), payload)
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": str(exc)}) from exc
    except ReviewedOutputBusyError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_busy", "message": str(exc)}) from exc


@app.post("/api/matches/{match_id}/review-workflow/approve-video-qa")
def approve_match_review_video_qa(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return approve_review_video_qa(path, read_match_meta(path))
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc


@app.post("/api/matches/{match_id}/review-workflow/retry-render")
def retry_match_review_render(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return retry_review_render(path, read_match_meta(path))
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc
    except ReviewedOutputBusyError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_busy", "message": str(exc)}) from exc


@app.post("/api/matches/{match_id}/review-workflow/retry-recompute")
def retry_match_review_recompute(match_id: str, response: Response) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        refreshed = retry_review_recompute(path, read_match_meta(path))
        performance = dict(refreshed.get("performance") or {})
        response.headers["Server-Timing"] = ", ".join(
            f"{key.removesuffix('_ms')};dur={value}"
            for key, value in performance.items()
            if key.endswith("_ms") and isinstance(value, (int, float))
        )
        # Full snapshot/progress stays in the service result for authoritative
        # in-process consumers. The browser only needs the bounded workflow.
        return {"workflow": refreshed["workflow"], "performance": performance}
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": str(exc)}) from exc


@app.post("/api/matches/{match_id}/review-workflow/reproject")
def reproject_match_review_workflow(match_id: str, response: Response) -> dict[str, Any]:
    """Refresh durable Review projections after a structural Review mutation.

    This is deliberately separate from finalization and from the operator
    retry action. A Mixed temporal split invalidates topology, coverage and
    Required pagination; it therefore needs one authoritative projection even
    while other Required work remains.
    """
    path = match_dir(match_id)
    try:
        refreshed = refresh_review_after_identity_mutation(
            path,
            read_match_meta(path),
            source="mixed_players_reproject",
            # Structural Mixed saves already invalidate the old generation.
            # Reproject exactly once, leave that authoritative generation warm
            # for Required offset-0 navigation, and do not globally render
            # every possible Review crop on this click path.
            operator_evidence=False,
            leave_hot_state_warm=True,
        )
        performance = dict(refreshed.get("performance") or {})
        response.headers["Server-Timing"] = ", ".join(
            f"{key.removesuffix('_ms')};dur={value}"
            for key, value in performance.items()
            if key.endswith("_ms") and isinstance(value, (int, float))
        )
        # The in-process service retains full snapshot/progress data for
        # authoritative callers. The browser needs only this bounded result.
        return {"workflow": refreshed["workflow"], "performance": performance}
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/review-progress")
def get_match_reviewed_identity_progress(
    match_id: str,
    response: Response,
    offset: int = 0,
    limit: int = 20,
    team_label: Literal["A", "B", "U"] | None = None,
    queue: Literal["required", "optional_audit"] = "required",
) -> dict[str, Any]:
    path = match_dir(match_id)
    started = time.perf_counter()
    try:
        match_document = read_match_meta(path)
        if reviewed_snapshot_file_fingerprint(path) is None:
            raise FileNotFoundError(path / "reviewed_identity_snapshot.json")
        recompute_required = reviewed_identity_recompute_required(path)
        state_started = time.perf_counter()
        # One probe: never parses/validates a stale hot document twice.
        state, hot_state_source = load_or_rebuild_review_hot_state_with_source(path, match_document)
        hot_state_fresh = hot_state_source == "warm_hit"
        state_ms = round((time.perf_counter() - state_started) * 1000, 1)
        paginate_started = time.perf_counter()
        payload = {
            **paginate_progress(
                hot_progress(state),
                offset=offset,
                limit=limit,
                team_label=team_label,
                queue=queue,
            ),
            "recompute_required": recompute_required,
            "review_state_version": state.get("state_version"),
        }
        paginate_ms = round((time.perf_counter() - paginate_started) * 1000, 1)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["Server-Timing"] = (
            f"review_hot_state;dur={state_ms}, review_queue_page;dur={paginate_ms}, total;dur={elapsed_ms}"
        )
        payload["server_timing"] = {
            "review_hot_state_ms": state_ms,
            "review_queue_page_ms": paginate_ms,
            "total_ms": elapsed_ms,
            "review_hot_state_source": "warm_hit" if hot_state_fresh else "cold_rebuild",
        }
        return payload
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/slot-review")
def get_match_reviewed_slot_assignments(match_id: str) -> dict[str, Any]:
    return reviewed_slot_assignment_read_model(match_dir(match_id))


@app.put("/api/matches/{match_id}/reviewed-identity/slot-review")
def put_match_reviewed_slot_assignments(
    match_id: str, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    candidate_path = path / "identity_candidate_shadow.json"
    if not candidate_path.exists():
        raise HTTPException(status_code=404, detail="identity_candidate_shadow.json is missing")
    try:
        match_document: dict[str, Any] | None = None
        if (path / "match.json").exists():
            match_document = read_match_meta(path)
            assert_workflow_action_allowed(
                get_review_workflow_state(path, match_document),
                "review_identity_issue",
            )
        candidate_document = json.loads(candidate_path.read_text(encoding="utf-8"))
        result = save_reviewed_slot_assignments(path, candidate_document, updates)
        # Slot-review fixtures and legacy developer tooling may intentionally
        # omit match.json. Real match mutations always use the workflow path.
        if match_document is not None:
            result["workflow"] = refresh_review_after_identity_mutation(
                path,
                match_document,
                source="reviewed_slot_decision",
            )["workflow"]
        return result
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/matches/{match_id}/reviewed-identity/finalize")
def finalize_match_reviewed_identity(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        result = refresh_review_after_identity_mutation(
            path,
            read_match_meta(path),
            source="legacy_reviewed_identity_finalize",
        )
        return {**public_finalized_identity(result["snapshot"]), "workflow": result["workflow"]}
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": str(exc)}) from exc
    except ReviewedOutputBusyError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_busy", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/corrections/context")
def get_match_reviewed_correction_context(
    match_id: str,
    response: Response,
    candidate_subject_id: str = Query(...),
    review_target_id: str | None = Query(default=None),
) -> dict[str, Any]:
    path = match_dir(match_id)
    started = time.perf_counter()
    try:
        state_started = time.perf_counter()
        # Context is a read for the card selected by progress. It must never
        # cold-rebuild the shared queue: concurrent prefetches previously
        # raced a save and changed the review-state version underneath the
        # operator. Progress owns authoritative recovery materialization.
        state = load_existing_fresh_hot_state(path, read_match_meta(path))
        if state is None:
            raise ReviewedIdentityHotStateError("review_state_stale")
        state_ms = round((time.perf_counter() - state_started) * 1000, 1)
        context_started = time.perf_counter()
        result = hot_context(state, candidate_subject_id, review_target_id)
        context_ms = round((time.perf_counter() - context_started) * 1000, 1)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["Server-Timing"] = (
            f"review_hot_state;dur={state_ms}, review_context;dur={context_ms}, total;dur={elapsed_ms}"
        )
        result["server_timing"] = {
            "review_hot_state_ms": state_ms,
            "review_context_ms": context_ms,
            "total_ms": elapsed_ms,
        }
        return result
    except ReviewedIdentityHotStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": "Stan Review zmienił się. Synchronizuję kolejkę Review.",
            },
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/corrections/historical-split/{case_id}")
def get_match_reviewed_historical_split_repair_context(
    match_id: str,
    case_id: str,
    response: Response,
) -> dict[str, Any]:
    """Read one correction-only historical parent from fresh hot state."""
    path = match_dir(match_id)
    started = time.perf_counter()
    try:
        state_started = time.perf_counter()
        state = load_existing_fresh_hot_state(path, read_match_meta(path))
        if state is None:
            raise ReviewedIdentityHotStateError("review_state_stale")
        state_ms = round((time.perf_counter() - state_started) * 1000, 1)
        context_started = time.perf_counter()
        result = hot_historical_split_repair_context(state, case_id)
        context_ms = round((time.perf_counter() - context_started) * 1000, 1)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["Server-Timing"] = (
            f"review_hot_state;dur={state_ms}, review_context;dur={context_ms}, total;dur={elapsed_ms}"
        )
        result["server_timing"] = {
            "review_hot_state_ms": state_ms,
            "review_context_ms": context_ms,
            "total_ms": elapsed_ms,
        }
        return result
    except ReviewedIdentityHotStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": "Stan Review zmienił się. Synchronizuję kolejkę Review.",
            },
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "historical_split_repair_unavailable",
                "message": "Pierwotny podział nie jest już dostępny do bezpiecznej naprawy.",
            },
        ) from exc


@app.post("/api/matches/{match_id}/reviewed-identity/corrections")
def post_match_reviewed_identity_correction(
    match_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        match_document = read_match_meta(path)
        recover_staged_operator_decision_audits(path)
        if payload.get("defer_recompute") is True:
            started = time.perf_counter()
            deferred_gate = validate_deferred_review_action(
                path,
                match_document,
                payload,
            )
            deferred_gate_ms = round((time.perf_counter() - started) * 1000, 1)
            if deferred_gate.get("idempotent_replay") is True:
                # The mutation was already durably accepted. Do not touch
                # canonical decisions or patch the hot projection again: the
                # caller must obtain the current authoritative queue instead
                # of treating this replay as a second successful decision.
                # The prior canonical write may have succeeded immediately
                # before an audit filesystem failure. Replaying the same
                # decision repairs only the staged append-only audit event.
                recover_staged_operator_decision_audits(path)
                hot_state = deferred_gate.get("hot_state")
                return {
                    "saved_decision": deferred_gate.get("saved_decision"),
                    "effective_action": str(payload.get("action") or ""),
                    "allocated_stable_slot_id": None,
                    "semantic_decision_digest": reviewed_decisions_semantic_digest(path),
                    "recompute_deferred": True,
                    "idempotent_replay": True,
                    "review_state_version": (
                        hot_state.get("state_version")
                        if isinstance(hot_state, dict)
                        else None
                    ),
                    "coverage_debt": (
                        dict((hot_state.get("progress") or {}).get("coverage_debt") or {})
                        if isinstance(hot_state, dict)
                        else None
                    ),
                    "persistence": {
                        "status": "already_saved",
                        "downstream_recompute_triggered": False,
                    },
                    "performance": {
                        "workflow_validation_ms": 0.0,
                        "deferred_gate_ms": deferred_gate_ms,
                        "persist_decision_ms": 0.0,
                        "hot_state_update_ms": 0.0,
                        "seeded_candidate_rebuild_ms": 0.0,
                        "finalize_reviewed_identity_ms": 0.0,
                        "segment_evidence_ms": 0.0,
                        "progress_build_ms": 0.0,
                        "final_workflow_ms": 0.0,
                        "total_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                }
            persist_started = time.perf_counter()
            result = persist_reviewed_identity_correction(
                path,
                match_document,
                payload,
                trusted_materialized_detected_team_labels=deferred_gate.get(
                    "detected_team_labels_by_subject"
                ),
                authorized_review_unit=deferred_gate.get("review_unit"),
                audit_required=bool(deferred_gate.get("audit_required")),
            )
            persist_ms = round((time.perf_counter() - persist_started) * 1000, 1)
            hot_started = time.perf_counter()
            hot_state = deferred_gate.get("hot_state")
            if isinstance(hot_state, dict):
                if result.get("review_topology_changed") is True:
                    # Splits, child cleanup and manual-slot creation alter
                    # exact source topology. A new read must materialize from
                    # canonical artifacts rather than patch the old queue.
                    invalidate_review_hot_state(path)
                    result["review_state_rebuild_required"] = True
                else:
                    try:
                        hot_state = update_hot_state_after_deferred_save(
                            path,
                            match_document,
                            hot_state,
                            deferred_gate["review_unit"],
                            result.get("saved_decision"),
                            str(result.get("semantic_decision_digest") or ""),
                        )
                        result["review_state_version"] = hot_state.get("state_version")
                        result["coverage_debt"] = dict(
                            (hot_state.get("progress") or {}).get("coverage_debt") or {}
                        )
                    except (OSError, ValueError):
                        # Canonical persistence already succeeded. Never keep a
                        # potentially contradictory cache: the next read will
                        # rebuild from canonical artifacts.
                        invalidate_review_hot_state(path)
                        result["review_state_rebuild_required"] = True
            if isinstance(hot_state, dict) and result.get("review_state_rebuild_required") is not True:
                # A versioned deferred save has an authoritative hot
                # projection already. Return its workflow view so Required
                # and Mixed badges do not display a stale durable snapshot
                # while the later structural reprojection is still pending.
                result["workflow"] = get_review_workflow_state(
                    path,
                    match_document,
                    progress=hot_progress(hot_state),
                )
            hot_state_ms = round((time.perf_counter() - hot_started) * 1000, 1)
            total_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "reviewed_correction_perf mode=deferred authorization_source=%s match=%s "
                "workflow_validation_ms=0.0 deferred_gate_ms=%.1f "
                "persist_decision_ms=%.1f "
                "seeded_candidate_rebuild_ms=0.0 finalize_reviewed_identity_ms=0.0 "
                "segment_evidence_ms=0.0 progress_build_ms=0.0 final_workflow_ms=0.0 "
                "total_ms=%.1f",
                deferred_gate.get("authorization_source", "batch_baseline"),
                match_document.get("id") or path.name,
                deferred_gate_ms,
                persist_ms,
                total_ms,
            )
            return {
                **result,
                "performance": {
                    "workflow_validation_ms": 0.0,
                    "deferred_gate_ms": deferred_gate_ms,
                    "persist_decision_ms": persist_ms,
                    "hot_state_update_ms": hot_state_ms,
                    "seeded_candidate_rebuild_ms": 0.0,
                    "finalize_reviewed_identity_ms": 0.0,
                    "segment_evidence_ms": 0.0,
                    "progress_build_ms": 0.0,
                    "final_workflow_ms": 0.0,
                    "total_ms": total_ms,
                },
            }
        state_before = get_review_workflow_state(path, match_document)
        if "correct_video_identity" not in set(state_before.get("allowed_actions") or []):
            assert_workflow_action_allowed(state_before, "review_identity_issue")
        result = save_reviewed_identity_correction(path, match_document, payload)
        refreshed = (
            after_video_qa_correction(path, match_document)
            if state_before.get("phase") in {"video_qa", "complete"}
            else refresh_review_after_identity_mutation(
                path,
                match_document,
                source="review_exception_decision",
                rebuild_seeded_candidates=False,
            )
        )
        response = {
            **result,
            "workflow": refreshed["workflow"],
            "reviewed_identity": public_finalized_identity(refreshed["snapshot"]),
        }
        if refreshed.get("render_job") is not None:
            response["render_job"] = refreshed["render_job"]
        return response
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": str(exc)}) from exc
    except SegmentTargetError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": str(exc), "message": str(exc)},
        ) from exc
    except DeferredReviewActionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ReviewedIdentityHotStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": "Stan Review zmienił się. Odśwież kartę przed zapisem."},
        ) from exc
    except ReviewedIdentityActionScopeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/matches/{match_id}/reviewed-identity/temporal-split")
def post_match_reviewed_identity_temporal_split(
    match_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """One atomic temporal split for the exact currently reviewed source."""
    path = match_dir(match_id)
    try:
        match_document = read_match_meta(path)
        recover_staged_operator_decision_audits(path)
        state_before = get_review_workflow_state(path, match_document)
        if "correct_video_identity" not in set(state_before.get("allowed_actions") or []):
            assert_workflow_action_allowed(state_before, "review_identity_issue")
        resolved_source = resolve_review_source(
            path,
            match_document,
            candidate_subject_id=str(payload.get("candidate_subject_id") or ""),
            review_target_id=str(payload.get("review_target_id") or "") or None,
            continuity_group_id=str(payload.get("continuity_group_id") or "") or None,
            source_ownership_digest=str(payload.get("source_ownership_digest") or ""),
        )
        resolution = str(payload.get("resolution") or "split")
        if resolution == "split":
            # Reject a structurally impossible split before loading or
            # generating a hot Review state. The same resolved source is
            # reused by persistence to avoid a second exact-source parse.
            require_simple_temporal_split(list(resolved_source["observations"]))
        elif resolution == "concurrent_lanes":
            existing_inline = inline_temporal_split_for_source(path, resolved_source)
            validate_concurrent_lane_resolution_request(
                resolved_source,
                payload,
                case_id=(
                    str(existing_inline.get("case_id") or "")
                    if isinstance(existing_inline, dict)
                    else None
                ),
            )
        hot_state = load_or_rebuild_review_hot_state(path, match_document)
        from app.services.identity_reviewed_hot_state import assert_hot_state_version

        assert_hot_state_version(hot_state, payload.get("review_state_version"))
        review_unit = hot_review_unit(
            hot_state,
            str(payload.get("candidate_subject_id") or ""),
            str(payload.get("review_target_id") or "") or None,
        )
        # A completed inline split correctly leaves the active queue. It may
        # still be reopened from the reviewed-video inspector before final
        # output, provided that its exact source and semantic version match.
        materialized_review_unit = review_unit if isinstance(review_unit, dict) else None
        if not isinstance(review_unit, dict):
            existing_split = inline_temporal_split_for_source(path, resolved_source)
            if not isinstance(existing_split, dict) or str(existing_split.get("resolution_status") or "") != "resolved":
                raise ReviewedIdentityActionScopeError("reviewed_identity_split_not_allowed")
            review_unit = {
                "scope_kind": resolved_source["scope_kind"],
                "detected_observation_count": resolved_source["detected_observation_count"],
            }
        capabilities = reviewed_identity_action_capabilities(review_unit)
        if not isinstance(review_unit, dict) or not capabilities["split"].get("allowed"):
            raise ReviewedIdentityActionScopeError("reviewed_identity_split_not_allowed")
        audit_event = prepare_operator_decision_audit_event(
            unit={
                **(materialized_review_unit or {}),
                "candidate_subject_id": resolved_source.get("candidate_subject_id"),
                "review_target_id": source_case_id(resolved_source),
                "scope_kind": resolved_source.get("scope_kind"),
                "continuity_group_id": resolved_source.get("continuity_group_id"),
                "source_ownership_digest": resolved_source.get("source_ownership_digest"),
                "tracklet_ids": resolved_source.get("tracklet_ids"),
                "frame_start": resolved_source.get("frame_start"),
                "frame_end": resolved_source.get("frame_end"),
                "detected_observation_count": resolved_source.get("detected_observation_count"),
            },
            payload={**payload, "action": "temporal_split"},
            required=True,
            mutation_kind="temporal_split",
        )
        stage_operator_decision_audit(path, audit_event)
        try:
            with review_build_context():
                result = save_inline_temporal_split(
                    path,
                    match_document,
                    payload,
                    materialized_review_unit=materialized_review_unit,
                    resolved_source=resolved_source,
                )
        except Exception:
            discard_staged_operator_decision_audit(path, str(audit_event["event_id"]))
            raise
        commit_staged_operator_decision_audit(path, str(audit_event["event_id"]))
        # A split changes the number and exact ownership of review units, so
        # this is deliberately a cache invalidation, not a guessed incremental
        # queue mutation. The next request safely materializes canonical state.
        invalidate_review_hot_state(path)
        return {**result, "review_state_rebuild_required": True}
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc
    except MixedPlayerTargetError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)}) from exc
    except MixedTemporalTopologyError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except ConcurrentLaneResolutionError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except ReviewedIdentityActionScopeError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc
    except SegmentTargetError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)}) from exc
    except ReviewedIdentityHotStateError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": "Stan Review zmienił się. Odśwież kartę przed zapisem."},
        ) from exc
    except ValueError as exc:
        code = str(exc)
        status = 409 if code in {"review_target_stale", "material_continuity_target_stale"} else 400
        raise HTTPException(status_code=status, detail={"code": code, "message": code}) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/temporal-split/refine")
def get_match_reviewed_identity_temporal_split_refinement(
    match_id: str,
    candidate_subject_id: str = Query(..., min_length=1),
    source_ownership_digest: str = Query(..., min_length=1),
    after_frame: int = Query(..., ge=0),
    before_frame: int = Query(..., ge=1),
    review_target_id: str | None = Query(default=None),
    continuity_group_id: str | None = Query(default=None),
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return build_review_source_boundary_refinement(
            path,
            read_match_meta(path),
            candidate_subject_id=candidate_subject_id,
            review_target_id=review_target_id,
            continuity_group_id=continuity_group_id,
            source_ownership_digest=source_ownership_digest,
            after_frame=after_frame,
            before_frame=before_frame,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MixedTemporalTopologyError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        code = str(exc)
        status = 409 if code in {"review_target_stale", "material_continuity_target_stale"} else 400
        raise HTTPException(status_code=status, detail={"code": code, "message": code}) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/concurrent-lanes/refine")
def get_match_reviewed_identity_concurrent_lane_refinement(
    match_id: str,
    candidate_subject_id: str = Query(..., min_length=1),
    parent_case_id: str = Query(..., min_length=1),
    parent_source_digest: str = Query(..., min_length=1),
    lane_id: str = Query(..., min_length=1),
    lane_source_digest: str = Query(..., min_length=1),
    after_frame: int = Query(..., ge=0),
    before_frame: int = Query(..., ge=1),
    review_target_id: str | None = Query(default=None),
    continuity_group_id: str | None = Query(default=None),
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return build_concurrent_lane_boundary_refinement(
            path,
            read_match_meta(path),
            candidate_subject_id=candidate_subject_id,
            parent_case_id=parent_case_id,
            parent_source_digest=parent_source_digest,
            lane_id=lane_id,
            lane_source_digest=lane_source_digest,
            after_frame=after_frame,
            before_frame=before_frame,
            review_target_id=review_target_id,
            continuity_group_id=continuity_group_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConcurrentLaneResolutionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ReviewedIdentityReviewSourceError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/matches/{match_id}/reviewed-identity/corrections/finalize")
def finalize_match_reviewed_identity_corrections(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        refreshed = refresh_review_after_identity_mutation(
            path,
            read_match_meta(path),
            source="review_exception_finish",
            rebuild_seeded_candidates=False,
        )
        return {
            "workflow": refreshed["workflow"],
            "reviewed_identity": public_finalized_identity(refreshed["snapshot"]),
            "review_progress": paginate_progress(refreshed["review_progress"]),
            "recompute_deferred": False,
            "performance": refreshed.get("performance") or {},
        }
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/mixed-players")
def get_match_reviewed_identity_mixed_players(
    match_id: str,
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        with review_build_context():
            match_document = read_match_meta(path)
            queue = build_mixed_review_queue(path, match_document)
            # Manual entry needs authoritative membership and ordering, but
            # still materializes evidence only for its first visible card.
            evidence_case = next(iter(queue.get("cases") or []), None)
            if isinstance(evidence_case, dict):
                render_mixed_review_evidence(
                    path,
                    match_document,
                    {"cases": [evidence_case]},
                )
            return queue
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/mixed-players/refine")
def get_match_reviewed_identity_mixed_boundary_refinement(
    match_id: str,
    candidate_subject_id: str = Query(..., min_length=1),
    after_frame: int = Query(..., ge=0),
    before_frame: int = Query(..., ge=1),
    case_id: str | None = Query(None),
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        with review_build_context():
            return build_mixed_boundary_refinement(
                path,
                read_match_meta(path),
                candidate_subject_id,
                after_frame,
                before_frame,
                case_id=case_id if isinstance(case_id, str) else None,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MixedTemporalTopologyError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except ConcurrentLaneResolutionError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        status = 409 if str(exc) == "mixed_player_case_stale" else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/mixed-players/{case_id}")
def get_match_reviewed_identity_mixed_player_case(
    match_id: str,
    case_id: str,
) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        with review_build_context():
            match_document = read_match_meta(path)
            focused = build_focused_mixed_review_case(
                path,
                match_document,
                case_id,
            )
            exact_case = focused.get("case")
            if isinstance(exact_case, dict):
                render_mixed_review_evidence(
                    path,
                    match_document,
                    {"cases": [exact_case]},
                )
            return focused
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/matches/{match_id}/reviewed-identity/mixed-players/resolve")
def post_match_reviewed_identity_mixed_resolution(
    match_id: str,
    response: Response,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    path = match_dir(match_id)
    started = time.perf_counter()
    try:
        gate_started = time.perf_counter()
        match_document = read_match_meta(path)
        recover_staged_operator_decision_audits(path)
        state = build_compact_review_workflow_state(path, match_document)
        assert_workflow_action_allowed(state, "review_mixed_players")
        case_id = str(payload.get("case_id") or payload.get("candidate_subject_id") or "")
        audit_case = next(
            (
                dict(row)
                for row in load_mixed_player_cases(path).get("cases") or []
                if str(row.get("case_id") or row.get("candidate_subject_id") or "") == case_id
            ),
            None,
        )
        workflow_gate_ms = round((time.perf_counter() - gate_started) * 1000, 1)
        audit_event = None
        if audit_case is not None:
            source = audit_case.get("source") if isinstance(audit_case.get("source"), dict) else {}
            audit_event = prepare_operator_decision_audit_event(
                unit={
                    **source,
                    "candidate_subject_id": audit_case.get("candidate_subject_id"),
                    "review_target_id": audit_case.get("case_id"),
                    "scope_kind": "mixed",
                    "detected_observation_count": audit_case.get("observation_count"),
                    "current_decision": audit_case.get("current_decision"),
                },
                payload={**payload, "action": "mixed_players"},
                required=True,
                mutation_kind="mixed_resolution",
            )
            stage_operator_decision_audit(path, audit_event)
        try:
            with review_build_context():
                result = save_mixed_player_resolution(path, match_document, payload)
        except Exception:
            if audit_event is not None:
                discard_staged_operator_decision_audit(path, str(audit_event["event_id"]))
            raise
        if audit_event is not None:
            commit_staged_operator_decision_audit(path, str(audit_event["event_id"]))
        # Resolving a staged marker creates/removes exact child targets.  Do
        # not let a browser retain a queue projected before that topology
        # change; the following progress read materializes it once.
        invalidate_review_hot_state(path)
        performance = {
            **dict(result.get("performance") or {}),
            "workflow_gate_ms": workflow_gate_ms,
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        response.headers["Server-Timing"] = ", ".join(
            f"{key.removesuffix('_ms')};dur={value}"
            for key, value in performance.items()
            if key.endswith("_ms") and isinstance(value, (int, float))
        )
        return {
            **result,
            "performance": performance,
            "review_state_rebuild_required": True,
        }
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc
    except MixedPlayerTargetError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": str(exc), "message": str(exc)},
        ) from exc
    except MixedTemporalTopologyError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except ConcurrentLaneResolutionError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-identity/at")
def get_reviewed_identity_at(match_id: str, time_sec: float = Query(..., ge=0)) -> dict[str, Any]:
    path = match_dir(match_id); snapshot = get_reviewed_identity_status(path)
    if snapshot.get("status") == "missing":
        raise HTTPException(status_code=409, detail="Sfinalizuj reviewed identity przed wyszukaniem klatki.")
    metadata = read_video_metadata(match_video_path(path))
    fps = float(metadata.get("fps") or 25)
    tracklets_document = json.loads((path / "tracklets.json").read_text(encoding="utf-8"))
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_document.get("tracklets") or []
        if row.get("tracklet_id")
    }
    return {
        "time_sec": time_sec,
        "frame": round(time_sec * fps),
        "reference_snapshot_stale": snapshot.get("status") == "stale",
        "entities": reviewed_assignment_at(snapshot, tracklets, time_sec, fps),
    }


@app.post("/api/matches/{match_id}/reviewed-output/generate")
def generate_match_reviewed_output(match_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    path = match_dir(match_id)
    options = {"include_minimap": bool(payload.get("include_minimap", True)), "include_ball": bool(payload.get("include_ball", True)), "show_roster_number": bool(payload.get("show_roster_number", False))}
    try:
        result = finalize_review_for_qa(path, read_match_meta(path), options)
        return result["render_job"]
    except WorkflowActionError as exc:
        raise _workflow_http_error(exc) from exc
    except ReviewWorkflowRecomputeError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": str(exc)}) from exc
    except ReviewedOutputBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/reviewed-output/status")
def get_match_reviewed_output_status(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    return reviewed_output_status(path, get_reviewed_identity_status(path))


@app.get("/api/matches/{match_id}/reviewed-output/video")
def get_match_reviewed_video(match_id: str, digest: str | None = Query(default=None)) -> FileResponse:
    match_path = match_dir(match_id)
    snapshot = get_reviewed_identity_status(match_path)
    job = reviewed_output_status(match_path, snapshot)
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Reviewed video is not current and completed")
    if job.get("source_snapshot_digest") != snapshot.get("semantic_digest"):
        raise HTTPException(status_code=409, detail="Reviewed video was generated from an older identity snapshot")
    video_digest = str(job.get("video_digest") or "")
    if digest is not None and digest != video_digest:
        raise HTTPException(status_code=409, detail="Requested reviewed video digest is stale")
    path = match_path / "reviewed_video.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Reviewed video is not available")
    return FileResponse(path, media_type="video/mp4", filename="reviewed_video.mp4", headers={"ETag": video_digest})


@app.get("/api/matches/{match_id}/reviewed-output/stats")
def get_match_reviewed_stats(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    snapshot = get_reviewed_identity_status(path)
    job = reviewed_output_status(path, snapshot)
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Reviewed stats are not current and completed")
    stats_path = path / "reviewed_player_stats.json"
    readiness_path = path / "reviewed_stats_readiness.json"
    if not stats_path.exists() or not readiness_path.exists():
        raise HTTPException(status_code=404, detail="Reviewed stats are not available")
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    if stats.get("source_snapshot_digest") != snapshot.get("semantic_digest"):
        raise HTTPException(status_code=409, detail="Reviewed stats were generated from an older identity snapshot")
    return {"stats": stats, "readiness": readiness}


@app.get("/api/matches/{match_id}/reviewed-report")
def get_match_reviewed_report(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    snapshot = get_reviewed_identity_status(path)
    job = reviewed_output_status(path, snapshot)
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Reviewed stats are not current and completed")
    try:
        report = build_reviewed_match_report(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Reviewed report input is missing: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if report.get("reviewed_identity_digest") != snapshot.get("semantic_digest"):
        raise HTTPException(status_code=409, detail="Reviewed report was generated from an older identity snapshot")
    return report


@app.get("/api/matches/{match_id}/resolved-player-stats")
def get_resolved_player_stats(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return build_resolved_player_stats_from_files(path, persist=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/players/{player_id}/stats")
def get_player_profile_stats(player_id: str) -> dict[str, Any]:
    try:
        return build_player_profile_stats(MATCHES_DIR, player_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/stable-players")
def get_stable_players(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_stable_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/stable-players/review")
def review_stable_players(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        doc = save_stable_review(path, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/change-candidates")
def get_change_candidates(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_change_candidates_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/change-candidates/review")
def review_change_candidates(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    try:
        doc = save_change_candidate_reviews(path, updates)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/team-config")
def get_team_config(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_team_config_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/team-config")
def review_team_config(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        doc = save_team_config_review(path, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/contact-candidates")
def get_contact_candidates(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_contact_candidates_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/contact-candidates/review")
def review_contact_candidates(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    try:
        doc = save_contact_candidate_reviews(path, updates)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.get("/api/matches/{match_id}/match-phase-config")
def get_match_phase_config(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    try:
        return load_match_phase_config(path, meta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/match-phase-config")
def update_match_phase_config(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    try:
        document = save_match_phase_config(path, meta, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return document


@app.get("/api/matches/{match_id}/pass-candidates")
def get_pass_candidates(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    try:
        return load_pass_candidates_review(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/matches/{match_id}/pass-candidates/review")
def review_pass_candidates(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    try:
        doc = save_pass_candidate_reviews(path, updates)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = read_match_meta(path)
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


@app.put("/api/matches/{match_id}/player-assignments")
def save_player_assignments(match_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    path = match_dir(match_id)
    meta = read_match_meta(path)
    tracks = load_tracks(path)
    track_ids = {int(track.get("track_id")) for track in tracks if track.get("track_id") is not None}
    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        raise HTTPException(status_code=400, detail="assignments must be a list")

    normalized: list[dict[str, Any]] = []
    allowed_statuses = {"unassigned", "assigned", "unknown", "false_positive", "referee", "opponent"}
    for item in assignments:
        if not isinstance(item, dict):
            continue
        try:
            tracklet_id = int(item.get("tracklet_id"))
        except (TypeError, ValueError):
            continue
        if tracklet_id not in track_ids:
            continue
        status = str(item.get("status") or "unassigned")
        if status not in allowed_statuses:
            status = "unassigned"
        normalized.append(
            {
                "tracklet_id": tracklet_id,
                "status": status,
                "team_id": item.get("team_id") or None,
                "player_id": item.get("player_id") or None,
                "notes": item.get("notes") or "",
            }
        )

    existing_ids = {int(item["tracklet_id"]) for item in normalized}
    for track_id in sorted(track_ids - existing_ids):
        normalized.append({"tracklet_id": track_id, "status": "unassigned", "team_id": None, "player_id": None, "notes": ""})

    summary = build_assignment_summary(meta, tracks, normalized)
    doc = {
        "schema_version": "0.1.0",
        "updated_at": now_iso(),
        "assignments": sorted(normalized, key=lambda item: int(item["tracklet_id"])),
        "summary": summary,
    }
    (path / "player_assignments.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    if meta.get("status") == "analyzed":
        meta["status"] = "reviewed"
        write_match_meta(path, meta)
    return doc


def _refresh_resolved_player_stats_if_stale(path: Path) -> None:
    identity_path = path / "player_identity_assignments.json"
    player_stats_path = path / "player_stats.json"
    if not identity_path.exists() or not player_stats_path.exists():
        return
    identity = _load_package_json_doc("player_identity_assignments", identity_path)
    resolved_path = path / "resolved_player_stats.json"
    resolved = _load_package_json_doc("resolved_player_stats", resolved_path) if resolved_path.exists() else {}
    expected_method = "exact_identity_coverage" if (path / "global_identity.json").exists() else None
    identity_updated_at = str(identity.get("updated_at") or "")
    resolved_generated_at = str(resolved.get("generated_at") or "")
    stale = (
        not resolved
        or bool(resolved.get("is_stale"))
        or (identity_updated_at and resolved_generated_at < identity_updated_at)
        or (expected_method and resolved.get("calculation_method") != expected_method)
    )
    if stale:
        build_resolved_player_stats_from_files(path, persist=True)


def build_match_package(path: Path) -> dict[str, Any]:
    _refresh_resolved_player_stats_if_stale(path)
    from app.services.ball_event_rebuild import ensure_ball_event_artifacts_fresh
    from app.services.team_shape import ensure_team_shape_artifact_fresh

    analytics_readiness = ensure_ball_event_artifacts_fresh(path)
    team_shape_document = ensure_team_shape_artifact_fresh(path)
    meta = read_match_meta(path)
    package = {
        "schema_version": "0.2.0",
        "generated_at": now_iso(),
        "contains_video": False,
        "match": meta,
        "pitch_config": None,
        "analysis_report": None,
        "performance_report": None,
        "camera_motion_report": None,
        "analysis_chunk_manifest": None,
        "player_assignments": None,
        "identity_candidates": None,
        "identity_assignments": None,
        "player_identity_assignments": None,
        "identity_review_gallery": None,
        "stable_players": None,
        "global_identity": None,
        "global_identity_report": None,
        "analysis_quality_report": None,
        "stabilization_report": None,
        "team_clusters": None,
        "frame_detection_counts": None,
        "movement_stats": None,
        "player_stats": None,
        "resolved_player_stats": None,
        "legacy_resolved_player_stats": None,
        "reviewed_player_stats": None,
        "reviewed_player_heatmaps": None,
        "reviewed_stats_readiness": None,
        "reviewed_output_manifest": None,
        "published_video": None,
        "identity_report_source": None,
        "reviewed_identity_digest": None,
        "player_heatmaps": None,
        "team_config": None,
        "team_stats": None,
        "team_shape": None,
        "change_candidates": None,
        "change_review_report": None,
        "tracklets": None,
        "tracking_quality_report": None,
        "ball_tracks": None,
        "ball_analysis_report": None,
        "ball_tracking_report": None,
        "ball_quality_report": None,
        "possession_candidates": None,
        "possession_segments": None,
        "contact_candidates": None,
        "match_phase_config": None,
        "event_candidates": None,
        "event_review_report": None,
        "pass_candidates": None,
        "pass_review_report": None,
        "attacking_momentum": None,
        "analytics_readiness": analytics_readiness,
        "possession_report": None,
        "team_count": len(meta.get("teams") or []),
        "player_count": sum(len(team.get("players") or []) for team in meta.get("teams") or []),
        "assets": {},
        "publish_status": "draft-package",
    }
    for key, filename in PACKAGE_EMBEDDED_JSON_FILES:
        file_path = path / filename
        if file_path.exists():
            if key == "team_shape":
                if team_shape_document is None:
                    continue
                package[key] = team_shape_document
                continue
            document = _load_package_json_doc(key, file_path)
            if key == "attacking_momentum" and document.get("status") == "not_available":
                continue
            package[key] = document
    try:
        apply_reviewed_identity_to_report_package(package)
    except ValueError as exc:
        package["reviewed_identity_error"] = str(exc)
    from app.services.published_video import build_publication_video_descriptor
    package["published_video"] = build_publication_video_descriptor(path)
    if (path / "heatmap_all_tracks.png").exists():
        package["assets"]["heatmap_all_tracks"] = "heatmap_all_tracks.png"
    if (path / "tracks.json").exists():
        package["assets"]["tracks_json"] = "tracks.json"
    if (path / "overlay_preview.mp4").exists():
        package["assets"]["overlay_preview"] = "overlay_preview.mp4"
    if (path / "analysis_chunk_manifest.json").exists():
        package["assets"]["analysis_chunk_manifest_json"] = "analysis_chunk_manifest.json"
    if (path / "performance_report.json").exists():
        package["assets"]["performance_report_json"] = "performance_report.json"
    if (path / "camera_motion_report.json").exists():
        package["assets"]["camera_motion_report_json"] = "camera_motion_report.json"
    if (path / "camera_motion_overlay.mp4").exists():
        package["assets"]["camera_motion_overlay"] = "camera_motion_overlay.mp4"
    if (path / "player_assignments.json").exists():
        package["assets"]["player_assignments_json"] = "player_assignments.json"
    if (path / "identity_candidates.json").exists():
        package["assets"]["identity_candidates_json"] = "identity_candidates.json"
    if (path / "identity_assignments.json").exists():
        package["assets"]["identity_assignments_json"] = "identity_assignments.json"
    if (path / "player_identity_assignments.json").exists():
        package["assets"]["player_identity_assignments_json"] = "player_identity_assignments.json"
    if (path / "identity_review_gallery.json").exists():
        package["assets"]["identity_review_gallery_json"] = "identity_review_gallery.json"
    if (path / "stable_players.json").exists():
        package["assets"]["stable_players_json"] = "stable_players.json"
    if (path / "global_identity.json").exists():
        package["assets"]["global_identity_json"] = "global_identity.json"
    if (path / "global_identity_report.json").exists():
        package["assets"]["global_identity_report_json"] = "global_identity_report.json"
    if (path / "analysis_quality_report.json").exists():
        package["assets"]["analysis_quality_report_json"] = "analysis_quality_report.json"
    if (path / "stabilization_report.json").exists():
        package["assets"]["stabilization_report_json"] = "stabilization_report.json"
    if (path / "stable_overlay_preview.mp4").exists():
        package["assets"]["stable_overlay_preview"] = "stable_overlay_preview.mp4"
    if (path / "debug_identity_overlay.mp4").exists():
        package["assets"]["debug_identity_overlay"] = "debug_identity_overlay.mp4"
    if (path / "team_clusters.json").exists():
        package["assets"]["team_clusters_json"] = "team_clusters.json"
    if (path / "frame_detection_counts.json").exists():
        package["assets"]["frame_detection_counts_json"] = "frame_detection_counts.json"
    if (path / "movement_stats.json").exists():
        package["assets"]["movement_stats_json"] = "movement_stats.json"
    if (path / "player_stats.json").exists():
        package["assets"]["player_stats_json"] = "player_stats.json"
    if (path / "resolved_player_stats.json").exists():
        package["assets"]["resolved_player_stats_json"] = "resolved_player_stats.json"
    if (path / "resolved_stats_quality_report.json").exists():
        package["assets"]["resolved_stats_quality_report_json"] = "resolved_stats_quality_report.json"
    if (path / "player_heatmaps.json").exists():
        package["assets"]["player_heatmaps_json"] = "player_heatmaps.json"
    if (path / "team_config.json").exists():
        package["assets"]["team_config_json"] = "team_config.json"
    if (path / "team_stats.json").exists():
        package["assets"]["team_stats_json"] = "team_stats.json"
    if isinstance(package.get("team_shape"), dict):
        package["assets"]["team_shape_json"] = "team_shape.json"
    if (path / "change_candidates.json").exists():
        package["assets"]["change_candidates_json"] = "change_candidates.json"
    if (path / "change_review_report.json").exists():
        package["assets"]["change_review_report_json"] = "change_review_report.json"
    if (path / "tracklets.json").exists():
        package["assets"]["tracklets_json"] = "tracklets.json"
    if (path / "tracking_quality_report.json").exists():
        package["assets"]["tracking_quality_report_json"] = "tracking_quality_report.json"
    if (path / "ball_candidates.json").exists():
        package["assets"]["ball_candidates_json"] = "ball_candidates.json"
    if (path / "ball_tracks.json").exists():
        package["assets"]["ball_tracks_json"] = "ball_tracks.json"
    if (path / "ball_analysis_report.json").exists():
        package["assets"]["ball_analysis_report_json"] = "ball_analysis_report.json"
    if (path / "ball_tracking_report.json").exists():
        package["assets"]["ball_tracking_report_json"] = "ball_tracking_report.json"
    if (path / "ball_quality_report.json").exists():
        package["assets"]["ball_quality_report_json"] = "ball_quality_report.json"
    if (path / "ball_overlay_preview.mp4").exists():
        package["assets"]["ball_overlay_preview"] = "ball_overlay_preview.mp4"
    if (path / "possession_candidates.json").exists():
        package["assets"]["possession_candidates_json"] = "possession_candidates.json"
    if (path / "possession_segments.json").exists():
        package["assets"]["possession_segments_json"] = "possession_segments.json"
    if (path / "contact_candidates.json").exists():
        package["assets"]["contact_candidates_json"] = "contact_candidates.json"
    if (path / "match_phase_config.json").exists():
        package["assets"]["match_phase_config_json"] = "match_phase_config.json"
    if (path / "event_candidates.json").exists():
        package["assets"]["event_candidates_json"] = "event_candidates.json"
    if (path / "event_review_report.json").exists():
        package["assets"]["event_review_report_json"] = "event_review_report.json"
    if (path / "pass_candidates.json").exists():
        package["assets"]["pass_candidates_json"] = "pass_candidates.json"
    if (path / "pass_review_report.json").exists():
        package["assets"]["pass_review_report_json"] = "pass_review_report.json"
    if package.get("attacking_momentum") is not None:
        package["assets"]["attacking_momentum_json"] = "attacking_momentum.json"
    if (path / "analytics_readiness.json").exists():
        package["assets"]["analytics_readiness_json"] = "analytics_readiness.json"
    if (path / "possession_report.json").exists():
        package["assets"]["possession_report_json"] = "possession_report.json"
    for key, filename in REVIEWED_PACKAGE_INPUTS.items():
        if (path / filename).exists():
            package["assets"][f"{key}_json"] = filename
    if (path / "possession_overlay_preview.mp4").exists():
        package["assets"]["possession_overlay_preview"] = "possession_overlay_preview.mp4"
    package["package_validation"] = build_package_validation(package)
    package["required"] = {
        **_package_presence_map(package, PACKAGE_REQUIRED_KEYS),
        **_package_presence_map(package, list(REVIEWED_PACKAGE_INPUTS)),
        "identity_output": package["package_validation"].get("identity_source") is not None,
    }
    package["optional"] = _package_presence_map(package, PACKAGE_OPTIONAL_KEYS)
    package["debug"] = {
        "available": _package_presence_map(package, PACKAGE_DEBUG_KEYS),
        "assets": {
            key: value
            for key, value in package["assets"].items()
            if key
            in {
                "tracks_json",
                "overlay_preview",
                "debug_identity_overlay",
                "identity_candidates_json",
                "identity_assignments_json",
                "tracklets_json",
                "tracking_quality_report_json",
            }
        },
    }
    (path / "match_package.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
    return package


@app.post("/api/matches/{match_id}/package")
def create_match_package(match_id: str) -> dict[str, Any]:
    path = match_dir(match_id)
    _assert_publish_workflow(path)
    return build_match_package(path)


@app.post("/api/matches/{match_id}/publish")
def publish_match(match_id: str, replace: bool = Query(False)) -> dict[str, Any]:
    path = match_dir(match_id)
    _assert_publish_workflow(path)
    try:
        package = build_match_package(path)
        ensure_package_publishable(package)
        published = publish_match_package(package, replace=replace)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PublishError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meta = read_match_meta(path)
    meta["status"] = "published"
    meta["publish_target"] = "local-json" if PUBLISH_TARGET == "local-db" else PUBLISH_TARGET
    meta["published_match_id"] = published.get("id")
    write_match_meta(path, meta)
    return published


@app.post("/api/matches/{match_id}/publish-local")
def publish_local_match(match_id: str, replace: bool = Query(False)) -> dict[str, Any]:
    path = match_dir(match_id)
    _assert_publish_workflow(path)
    try:
        package = build_match_package(path)
        ensure_package_publishable(package)
        published = import_match_package(package, replace=replace)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    meta = read_match_meta(path)
    meta["status"] = "published"
    meta["publish_target"] = "local-json"
    meta["published_match_id"] = published["id"]
    write_match_meta(path, meta)
    return published


@app.get("/api/published/matches")
def api_list_published_matches() -> list[dict[str, Any]]:
    return list_published_matches()


def _match_group_error_response(error: MatchGroupError) -> HTTPException:
    return HTTPException(status_code=409, detail=error.reason())


def _group_with_validation(group: dict[str, Any]) -> dict[str, Any]:
    group_id = str(group["group_id"])
    return {
        "group": group,
        "validation": validate_match_group(group_id),
        # Read-only sidecar lookup: creating the projection is an explicit
        # lifecycle step (create/regenerate/refresh/merged-match endpoint).
        "merged_published_match_id": merged_published_id_for_group(group_id),
    }


def _merged_projection_response(group_id: str, *, rebuild: bool = True) -> dict[str, Any]:
    """Build (or rebuild) the canonical merged projection for one group."""
    try:
        if rebuild:
            projection = ensure_merged_published_match(group_id)
        else:
            merged_id = merged_published_id_for_group(group_id)
            if merged_id is None:
                raise MatchGroupError("merged_projection_missing", "Canonical merged projection was not created.")
            projection = {
                "merged_published_match_id": merged_id,
                "report": get_published_match(merged_id)["public_report"],
            }
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error
    return {
        **_group_with_validation(get_match_group(group_id)),
        "merged_published_match_id": projection["merged_published_match_id"],
        "merged_report": projection["report"],
    }


def _cleanup_failed_create(group_id: str) -> None:
    """Remove every trace of a failed group creation (best effort).

    Deletes the group under the maintenance lock protocol, then any live
    canonical projection resolved by sidecar or summary scan.  Physical
    source publications are never touched.  Cleanup errors never mask the
    original failure.
    """

    try:
        delete_match_group_when_video_idle(group_id)
    except (KeyError, MatchGroupError, MatchGroupVideoError):
        pass
    try:
        delete_merged_published_match(group_id)
    except (KeyError, MatchGroupError, OSError):
        pass


def _require_backing_group_id(published_match_id: str) -> str:
    group_id = group_id_for_merged_published_id(published_match_id)
    if group_id is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "merged_match_not_found", "detail": "Merged published match not found."},
        )
    try:
        get_match_group(group_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "backing_group_not_found", "detail": "Backing logical match no longer exists."},
        ) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error
    return group_id


@app.get("/api/published/match-groups/eligible-sources")
def api_list_eligible_match_group_sources() -> list[dict[str, Any]]:
    return list_eligible_match_group_sources()


@app.post("/api/published/match-groups/preview")
def api_preview_match_group(payload: MatchGroupPayload) -> dict[str, Any]:
    try:
        return preview_match_group(
            member_published_ids=payload.member_published_ids,
            metadata=payload.metadata.model_dump(),
        )
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.get("/api/published/match-groups")
def api_list_match_groups() -> list[dict[str, Any]]:
    return [_group_with_validation(group) for group in list_match_groups()]


@app.post("/api/published/match-groups")
def api_create_match_group(payload: MatchGroupPayload) -> dict[str, Any]:
    try:
        group, report = create_match_group_and_generate_report(
            member_published_ids=payload.member_published_ids,
            metadata=payload.metadata.model_dump(),
            generate_and_persist_report=generate_match_group_report,
        )
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error
    group_id = str(group["group_id"])
    try:
        projection = ensure_merged_published_match(group_id)
    except MatchGroupError as error:
        # Never leave a group without its user-facing merged match: the
        # group directory belongs solely to this operation.  Cleanup covers
        # any live projection too (resolved by summary scan when the sidecar
        # is already gone), so no orphan published-merged-* can survive a
        # failed create.  Storage errors (OSError and friends) take the same
        # path and are re-raised unconverted — never swallowed.
        _cleanup_failed_create(group_id)
        raise _match_group_error_response(error) from error
    except Exception:
        _cleanup_failed_create(group_id)
        raise
    return {
        **_group_with_validation(get_match_group(group_id)),
        "report": report,
        "merged_published_match_id": projection["merged_published_match_id"],
        "merged_report": projection["report"],
    }


@app.get("/api/published/match-groups/{group_id}")
def api_get_match_group(group_id: str) -> dict[str, Any]:
    try:
        return _group_with_validation(get_match_group(group_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.put("/api/published/match-groups/{group_id}")
def api_update_match_group(group_id: str, payload: MatchGroupPayload) -> dict[str, Any]:
    try:
        group, report = update_match_group_and_generate_report(
            group_id,
            member_published_ids=payload.member_published_ids,
            metadata=payload.metadata.model_dump(),
            build_report_candidate=build_match_group_report_candidate,
            rebuild_canonical_projection=_ensure_merged_published_match_locked,
        )
        return {**_merged_projection_response(str(group["group_id"]), rebuild=False), "report": report}
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.post("/api/published/match-groups/{group_id}/regenerate")
def api_regenerate_match_group(group_id: str) -> dict[str, Any]:
    try:
        regenerated = regenerate_merged_match_group(group_id)
        return {
            **_group_with_validation(get_match_group(group_id)),
            "report": regenerated["report"],
            **_merged_projection_response(group_id, rebuild=False),
        }
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.get("/api/published/match-groups/{group_id}/refresh-preview")
def api_preview_match_group_refresh(group_id: str) -> dict[str, Any]:
    try:
        return preview_match_group_refresh(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.post("/api/published/match-groups/{group_id}/refresh-to-latest")
def api_refresh_match_group_to_latest(group_id: str) -> dict[str, Any]:
    try:
        return refresh_merged_match_to_latest(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.delete("/api/published/match-groups/{group_id}")
def api_delete_match_group(group_id: str) -> dict[str, Any]:
    try:
        # Resolve owned canonical projections BEFORE the group directory
        # (and its sidecar) disappears; otherwise the merged publication
        # would be orphaned.  Physical sources are never touched.
        owned_merged_ids = merged_ids_for_group(group_id)
        group = delete_match_group_when_video_idle(group_id)
        for merged_id in owned_merged_ids:
            delete_merged_projection_by_id(merged_id)
        return {"status": "deleted", "group": group}
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.get("/api/published/match-groups/{group_id}/merged-match")
def api_get_merged_match_for_group(group_id: str) -> dict[str, Any]:
    """Resolve (and lazily create) the canonical merged published match.

    This is the backward-compatibility path for match groups created before
    the canonical projection existed: the group stays authoritative, a
    stable ``published-merged-*`` ID is allocated once, and the canonical
    report is rebuilt from current pins.
    """

    try:
        get_match_group(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error
    try:
        projection = ensure_merged_published_match(group_id)
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error
    return {"group_id": group_id, "merged_published_match_id": projection["merged_published_match_id"]}


@app.get("/api/published/match-groups/{group_id}/video")
def api_get_match_group_video(group_id: str) -> dict[str, Any]:
    try:
        return get_match_group_video_status(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error


@app.post("/api/published/match-groups/{group_id}/video/generate")
def api_generate_match_group_video(group_id: str) -> dict[str, Any]:
    try:
        return submit_match_group_video_generation(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupVideoError as error:
        raise _match_group_error_response(error) from error


@app.get("/api/published/match-groups/{group_id}/external-video")
def api_get_match_group_external_video(group_id: str) -> dict[str, Any]:
    try:
        return get_match_group_external_video(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error


@app.put("/api/published/match-groups/{group_id}/external-video")
def api_save_match_group_external_video(group_id: str, payload: MatchGroupExternalVideoPayload) -> dict[str, Any]:
    try:
        return save_match_group_external_video(group_id, payload.url)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupExternalVideoError as error:
        if error.code == "unsupported_youtube_url":
            raise HTTPException(status_code=422, detail={"code": error.code, "detail": error.detail}) from error
        raise _match_group_error_response(error) from error


@app.delete("/api/published/match-groups/{group_id}/external-video")
def api_delete_match_group_external_video(group_id: str) -> dict[str, Any]:
    try:
        return delete_match_group_external_video(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error


@app.get("/api/published/match-groups/{group_id}/video/file")
def api_get_match_group_video_file(group_id: str) -> RedirectResponse:
    try:
        status = get_match_group_video_status(group_id)
        if status.get("status") != "ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "combined_video_not_current",
                    "detail": "The combined video is not current for this logical match.",
                },
            )
        return RedirectResponse(str(status["artifact_url"]), status_code=307)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "combined_video_not_found", "detail": "Combined video not found."}) from error


@app.get("/api/published/match-groups/{group_id}/video/generations/{generation_id}/file")
def api_get_match_group_video_generation_file(group_id: str, generation_id: str) -> FileResponse:
    try:
        generation = generation_video(group_id, generation_id)
        digest = str(generation["manifest"].get("output", {}).get("semantic_digest") or "")
        return FileResponse(
            generation["video_path"],
            media_type="video/mp4",
            filename=COMBINED_VIDEO_FILENAME,
            headers={"ETag": digest},
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "combined_video_not_found", "detail": "Combined video generation not found."}) from error


@app.get("/api/published/match-groups/{group_id}/report")
def api_get_match_group_report(group_id: str) -> dict[str, Any]:
    try:
        # One coherent snapshot: the manifest, report and validation below
        # always belong to the same logical generation, so a concurrent
        # refresh can never produce a NEW-manifest/OLD-report response.
        snapshot = get_coherent_match_group_report(group_id)
        return {
            "report": snapshot["report"],
            "validation": snapshot["validation"],
            "external_video": get_match_group_external_video(group_id),
        }
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "aggregate_report_not_generated", "detail": "Aggregate report has not been generated."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.get("/api/published/matches/{published_match_id}")
def api_get_published_match(published_match_id: str) -> dict[str, Any]:
    try:
        match = get_published_match(published_match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Published match not found") from exc
    if str(match.get("source_kind") or "") == "merged":
        # Fail closed when the user-facing projection disagrees with its
        # backing group generation; a stale projection is rebuilt safely,
        # anything unrecoverable becomes an explicit conflict.
        coherence = check_merged_projection(published_match_id)
        if coherence["status"] == "orphan":
            raise HTTPException(
                status_code=404,
                detail={"code": "merged_match_orphaned", "detail": "Merged match backing group no longer exists."},
            )
        if coherence["status"] != "current":
            try:
                ensure_merged_published_match(str(coherence.get("group_id") or ""))
                match = get_published_match(published_match_id)
            except (KeyError, MatchGroupError) as error:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "merged_projection_stale", "detail": "Merged report is stale and cannot be rebuilt safely."},
                ) from error
    return match


@app.post("/api/published/matches/{published_match_id}/regenerate-report")
def api_regenerate_merged_published_match(published_match_id: str) -> dict[str, Any]:
    """Rebuild the canonical merged report from currently pinned sources.

    This never repins sources — it is distinct from refresh-to-latest.
    Physical publications are rejected: their lifecycle is rebuild.
    """

    if not is_merged_published_id(published_match_id):
        raise HTTPException(
            status_code=409,
            detail={"code": "not_a_merged_match", "detail": "Only merged published matches support report regeneration."},
        )
    group_id = _require_backing_group_id(published_match_id)
    try:
        regenerate_merged_match_group(group_id)
        return get_published_match(published_match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Published match not found") from exc
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.get("/api/published/matches/{published_match_id}/refresh-preview")
def api_preview_merged_published_match_refresh(published_match_id: str) -> dict[str, Any]:
    if not is_merged_published_id(published_match_id):
        raise HTTPException(
            status_code=409,
            detail={"code": "not_a_merged_match", "detail": "Only merged published matches support refresh preview."},
        )
    group_id = _require_backing_group_id(published_match_id)
    try:
        return preview_match_group_refresh(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.post("/api/published/matches/{published_match_id}/refresh-to-latest")
def api_refresh_merged_published_match_to_latest(published_match_id: str) -> dict[str, Any]:
    """Repin changed sources atomically, preserving the merged published ID.

    Rebuilds the canonical merged report, reevaluates combined-video and
    external-video freshness, and regenerates Key Moments naturally.  Does
    NOT auto-regenerate combined video or auto-rebind external video.
    """

    if not is_merged_published_id(published_match_id):
        raise HTTPException(
            status_code=409,
            detail={"code": "not_a_merged_match", "detail": "Only merged published matches support refresh to latest."},
        )
    group_id = _require_backing_group_id(published_match_id)
    try:
        refresh_merged_match_to_latest(group_id)
        return get_published_match(published_match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Published match not found") from exc
    except MatchGroupError as error:
        raise _match_group_error_response(error) from error


@app.get("/api/published/matches/{published_match_id}/video")
def api_get_merged_published_match_video(published_match_id: str) -> dict[str, Any]:
    group_id = _require_backing_group_id(published_match_id)
    try:
        return get_match_group_video_status(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error


@app.post("/api/published/matches/{published_match_id}/video/generate")
def api_generate_merged_published_match_video(published_match_id: str) -> dict[str, Any]:
    group_id = _require_backing_group_id(published_match_id)
    try:
        return submit_match_group_video_generation(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupVideoError as error:
        raise _match_group_error_response(error) from error


@app.get("/api/published/matches/{published_match_id}/video/file")
def api_get_merged_published_match_video_file(published_match_id: str) -> RedirectResponse:
    group_id = _require_backing_group_id(published_match_id)
    try:
        status = get_match_group_video_status(group_id)
        if status.get("status") != "ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "combined_video_not_current",
                    "detail": "The combined video is not current for this merged match.",
                },
            )
        return RedirectResponse(str(status["artifact_url"]), status_code=307)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "combined_video_not_found", "detail": "Combined video not found."}) from error


@app.get("/api/published/matches/{published_match_id}/external-video")
def api_get_merged_published_match_external_video(published_match_id: str) -> dict[str, Any]:
    group_id = _require_backing_group_id(published_match_id)
    try:
        return get_match_group_external_video(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error


@app.put("/api/published/matches/{published_match_id}/external-video")
def api_save_merged_published_match_external_video(published_match_id: str, payload: MatchGroupExternalVideoPayload) -> dict[str, Any]:
    group_id = _require_backing_group_id(published_match_id)
    try:
        return save_match_group_external_video(group_id, payload.url)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error
    except MatchGroupExternalVideoError as error:
        if error.code == "unsupported_youtube_url":
            raise HTTPException(status_code=422, detail={"code": error.code, "detail": error.detail}) from error
        raise _match_group_error_response(error) from error


@app.delete("/api/published/matches/{published_match_id}/external-video")
def api_delete_merged_published_match_external_video(published_match_id: str) -> dict[str, Any]:
    group_id = _require_backing_group_id(published_match_id)
    try:
        return delete_match_group_external_video(group_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": "match_group_not_found", "detail": "Match group not found."}) from error


@app.post("/api/published/matches/{published_match_id}/rebuild")
def api_rebuild_published_match(published_match_id: str) -> dict[str, Any]:
    """Rebuild one existing publication from its original local match artifacts.

    This is the published-page equivalent of republishing from the local
    report ("Zaktualizuj opublikowany raport"). It preserves the stable
    published identity and fails closed on any source identity mismatch
    without touching the stored publication. Logical matches are never
    refreshed here; they observe the new snapshot through the #94 flow.
    """
    try:
        existing = get_published_match(published_match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Published match not found") from exc
    if str(existing.get("source_kind") or "physical") == "merged" or is_merged_published_id(published_match_id):
        raise HTTPException(
            status_code=409,
            detail="Merged matches have no single local analyzed source; use Regeneruj raport or Odśwież do najnowszych danych.",
        )
    source_match_id = str(existing.get("source_match_id") or "")
    if not source_match_id or f"published-{source_match_id}" != published_match_id:
        raise HTTPException(
            status_code=409,
            detail="Published match source identity is not trustworthy; rebuild refused.",
        )
    try:
        path = match_dir(source_match_id)
    except HTTPException as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Local source match {source_match_id} not found; publication left unchanged.",
        ) from exc
    _assert_publish_workflow(path)
    try:
        package = build_match_package(path)
        ensure_package_publishable(package)
        package_source_id = str((package.get("match") or {}).get("id") or "")
        if package_source_id != source_match_id or f"published-{package_source_id}" != published_match_id:
            raise ValueError(
                "Rebuilt package source identity does not match the requested publication; rebuild refused."
            )
        published = import_match_package(package, replace=True)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PublishError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meta = read_match_meta(path)
    meta["status"] = "published"
    meta["publish_target"] = "local-json"
    meta["published_match_id"] = published["id"]
    write_match_meta(path, meta)
    return published


@app.delete("/api/published/matches/{published_match_id}")
def api_delete_published_match(published_match_id: str) -> dict[str, Any]:
    try:
        deleted = delete_published_match(published_match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Published match not found") from exc
    return {"status": "deleted", "match": deleted}


@app.post("/api/admin/import-match")
def api_import_match_package(
    package: dict[str, Any] = Body(...),
    replace: bool = Query(False),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_import_token(authorization)
    try:
        return import_match_package(package, replace=replace)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/matches/{match_id}/artifact/{artifact_name:path}")
def get_artifact(match_id: str, artifact_name: str) -> FileResponse:
    path = match_dir(match_id)
    allowed = {
        "tracks.json": "application/json",
        "analysis_report.json": "application/json",
        "performance_report.json": "application/json",
        "camera_motion_report.json": "application/json",
        "analysis_chunk_manifest.json": "application/json",
        "overlay_preview.mp4": "video/mp4",
        "heatmap_all_tracks.png": "image/png",
        "pitch_config.json": "application/json",
        "match_package.json": "application/json",
        "player_assignments.json": "application/json",
        "identity_candidates.json": "application/json",
        "identity_assignments.json": "application/json",
        "player_identity_assignments.json": "application/json",
        "identity_review_gallery.json": "application/json",
        "stable_players.json": "application/json",
        "global_identity.json": "application/json",
        "global_identity_report.json": "application/json",
        "analysis_quality_report.json": "application/json",
        "stabilization_report.json": "application/json",
        "team_clusters.json": "application/json",
        "frame_detection_counts.json": "application/json",
        "movement_stats.json": "application/json",
        "player_stats.json": "application/json",
        "resolved_player_stats.json": "application/json",
        "resolved_stats_quality_report.json": "application/json",
        "player_heatmaps.json": "application/json",
        "team_config.json": "application/json",
        "team_stats.json": "application/json",
        "change_candidates.json": "application/json",
        "change_review_report.json": "application/json",
        "tracklets.json": "application/json",
        "tracking_quality_report.json": "application/json",
        "ball_candidates.json": "application/json",
        "ball_tracks.json": "application/json",
        "ball_analysis_report.json": "application/json",
        "ball_tracking_report.json": "application/json",
        "ball_quality_report.json": "application/json",
        "possession_candidates.json": "application/json",
        "possession_segments.json": "application/json",
        "contact_candidates.json": "application/json",
        "match_phase_config.json": "application/json",
        "event_candidates.json": "application/json",
        "event_review_report.json": "application/json",
        "pass_candidates.json": "application/json",
        "pass_review_report.json": "application/json",
        "attacking_momentum.json": "application/json",
        "analytics_readiness.json": "application/json",
        "possession_report.json": "application/json",
        "run_metadata.json": "application/json",
        "stable_overlay_preview.mp4": "video/mp4",
        "debug_identity_overlay.mp4": "video/mp4",
        "camera_motion_overlay.mp4": "video/mp4",
        "ball_overlay_preview.mp4": "video/mp4",
        "possession_overlay_preview.mp4": "video/mp4",
    }
    artifact_rel = Path(artifact_name)
    if artifact_rel.is_absolute() or any(part == ".." for part in artifact_rel.parts):
        raise HTTPException(status_code=404, detail="Artifact not available")
    artifact_basename = artifact_rel.name
    if (
        artifact_rel.parts
        and artifact_rel.parts[0] == "player_heatmaps"
        and artifact_basename.lower().endswith(".png")
    ):
        allowed[artifact_basename] = "image/png"
    if (
        len(artifact_rel.parts) >= 4
        and artifact_rel.parts[0] == "identity_review"
        and artifact_rel.parts[1] == "crops"
        and artifact_basename.lower().endswith((".jpg", ".jpeg"))
    ):
        allowed[artifact_basename] = "image/jpeg"
    if (
        len(artifact_rel.parts) == 3
        and artifact_rel.parts[0] == "anchor_crops"
        and artifact_rel.parts[1].startswith("shadow-")
        and artifact_basename.lower().endswith((".jpg", ".jpeg"))
    ):
        allowed[artifact_basename] = "image/jpeg"
    if (
        len(artifact_rel.parts) == 3
        and artifact_rel.parts[0] == "reviewed_identity_segments"
        and len(artifact_rel.parts[1]) == 64
        and all(character in "0123456789abcdef" for character in artifact_rel.parts[1])
        and artifact_basename.lower().endswith((".jpg", ".jpeg"))
    ):
        allowed[artifact_basename] = "image/jpeg"
    if (
        len(artifact_rel.parts) == 3
        and artifact_rel.parts[0] == "reviewed_identity_mixed"
        and len(artifact_rel.parts[1]) == 16
        and all(character in "0123456789abcdef" for character in artifact_rel.parts[1])
        and artifact_basename.lower().endswith((".jpg", ".jpeg"))
    ):
        allowed[artifact_basename] = "image/jpeg"
    if (
        len(artifact_rel.parts) == 4
        and artifact_rel.parts[0] == "team_attribution_evidence"
        and artifact_rel.parts[1][:9] in {"shadow-a-", "shadow-b-", "shadow-u-"}
        and len(artifact_rel.parts[1]) == len("shadow-a-") + 16
        and all(character in "0123456789abcdef" for character in artifact_rel.parts[1][9:])
        and len(artifact_rel.parts[2]) == 16
        and all(character in "0123456789abcdef" for character in artifact_rel.parts[2])
        and artifact_basename.lower().endswith((".jpg", ".jpeg"))
    ):
        allowed[artifact_basename] = "image/jpeg"
    if (
        len(artifact_rel.parts) == 3
        and artifact_rel.parts[0] == "identity_initial_audit"
        and artifact_rel.parts[1] == "frames"
        and artifact_basename.lower().endswith((".jpg", ".jpeg"))
    ):
        allowed[artifact_basename] = "image/jpeg"
    if (
        len(artifact_rel.parts) == 3
        and artifact_rel.parts[0] == "identity_second_half_reanchor"
        and artifact_rel.parts[1] == "frames"
        and artifact_basename.lower().endswith((".jpg", ".jpeg"))
    ):
        allowed[artifact_basename] = "image/jpeg"
    if artifact_basename not in allowed:
        raise HTTPException(status_code=404, detail="Artifact not available")
    artifact_path = (path / artifact_rel).resolve()
    match_root = path.resolve()
    if artifact_path != match_root and match_root not in artifact_path.parents:
        raise HTTPException(status_code=404, detail="Artifact not available")
    if (
        not artifact_path.exists()
        and artifact_rel.parts
        and artifact_rel.parts[0] == "reviewed_identity_mixed"
    ):
        # The card can legitimately outlive just-in-time evidence generation
        # in a local/HMR workspace. Recover only the exact current card; do
        # not make image delivery a path to old or nonmandatory review data.
        materialize_mixed_review_artifact(path, read_match_meta(path), artifact_name)
    if not artifact_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Artifact not generated yet",
            headers=(
                {"Cache-Control": "no-store"}
                if artifact_rel.parts and artifact_rel.parts[0] == "reviewed_identity_mixed"
                else None
            ),
        )
    if artifact_path.stat().st_size == 0:
        raise HTTPException(status_code=410, detail=f"Artifact {artifact_name} exists but is empty. Rerun analysis and check backend logs.")
    # Mixed-review crops are materialized just in time for the current card.
    # A transient 404 must never become a sticky browser cache entry: the
    # client can safely retry the same immutable crop once rendering finishes.
    headers = (
        {"Cache-Control": "no-store"}
        if artifact_rel.parts and artifact_rel.parts[0] == "reviewed_identity_mixed"
        else None
    )
    return FileResponse(artifact_path, media_type=allowed[artifact_basename], headers=headers)
