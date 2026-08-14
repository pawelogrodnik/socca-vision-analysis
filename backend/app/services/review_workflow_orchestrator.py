from __future__ import annotations

"""Mutation orchestration for the authoritative operator review workflow."""

import logging
from pathlib import Path
import time
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_output_jobs import generate_reviewed_output
from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.identity_reviewed_recompute_state import (
    clear_reviewed_identity_recompute_required,
)
from app.services.identity_reviewed_snapshot import finalize_reviewed_identity, get_reviewed_identity_status
from app.services.identity_reviewed_segments import (
    load_segment_review,
    render_segment_review_evidence,
)
from app.services.identity_reviewed_mixed_store import (
    build_mixed_review_queue,
    render_mixed_review_evidence,
)
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_seeded_candidate_assignments import (
    rebuild_identity_seeded_candidate_assignments,
)
from app.services.review_workflow_state import (
    WorkflowActionError,
    assert_workflow_action_allowed,
    get_review_workflow_state,
)
from app.services.review_workflow_store import (
    current_approval_fingerprint,
    load_json_object,
    save_video_qa_approval,
)


logger = logging.getLogger(__name__)
PROGRESS_FILENAME = "reviewed_identity_progress.json"
RECOMPUTE_FAILURE_FILENAME = "review_workflow_recompute_failure.json"
DEFAULT_RENDER_OPTIONS = {
    "include_minimap": True,
    "include_ball": True,
    "show_roster_number": False,
}


def refresh_review_after_identity_mutation(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    source: str,
    rebuild_seeded_candidates: bool = False,
) -> dict[str, Any]:
    """Perform only the cheap reviewed-identity work needed for the next click."""
    started = time.perf_counter()
    timings = {
        "seeded_candidate_rebuild_ms": 0.0,
        "finalize_reviewed_identity_ms": 0.0,
        "segment_evidence_ms": 0.0,
        "progress_build_ms": 0.0,
        "final_workflow_ms": 0.0,
    }
    try:
        if rebuild_seeded_candidates:
            # Callers that mutate actual seed inputs may request this JSON-only
            # rebuild. Reviewed correction decisions are intentionally not a
            # semantic seeded-assignment dependency.
            phase_started = time.perf_counter()
            rebuild_identity_seeded_candidate_assignments(match_path, match_doc)
            timings["seeded_candidate_rebuild_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        snapshot = finalize_reviewed_identity(match_path, match_doc)
        timings["finalize_reviewed_identity_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        try:
            render_segment_review_evidence(
                match_path,
                match_doc,
                load_segment_review(match_path),
            )
            render_mixed_review_evidence(
                match_path,
                match_doc,
                build_mixed_review_queue(match_path, match_doc),
            )
        except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
            logger.warning(
                "review_workflow segment_evidence_render_failed match=%s error=%s",
                match_doc.get("id") or match_path.name,
                type(exc).__name__,
            )
        timings["segment_evidence_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        progress = build_reviewed_identity_progress(match_path, match_doc)
        timings["progress_build_ms"] = _elapsed_ms(phase_started)
        write_identity_json_atomic(
            match_path / PROGRESS_FILENAME,
            {
                **progress,
                "source_snapshot_digest": snapshot.get("semantic_digest"),
                "workflow_refresh_source": source,
            },
        )
    except Exception as exc:
        write_identity_json_atomic(
            match_path / RECOMPUTE_FAILURE_FILENAME,
            {
                "schema_version": "1.0.0",
                "code": "review_recompute_failed",
                "source": source,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        logger.info(
            "review_workflow action=refresh_failed match=%s source=%s error=%s",
            match_doc.get("id") or match_path.name,
            source,
            type(exc).__name__,
        )
        raise ReviewWorkflowRecomputeError(str(exc)) from exc
    (match_path / RECOMPUTE_FAILURE_FILENAME).unlink(missing_ok=True)
    phase_started = time.perf_counter()
    workflow = get_review_workflow_state(match_path, match_doc)
    timings["final_workflow_ms"] = _elapsed_ms(phase_started)
    timings["total_ms"] = _elapsed_ms(started)
    clear_reviewed_identity_recompute_required(match_path)
    logger.info(
        "reviewed_correction_perf mode=finalize match=%s source=%s phase=%s "
        "seeded_candidate_rebuild_ms=%.1f finalize_reviewed_identity_ms=%.1f "
        "segment_evidence_ms=%.1f progress_build_ms=%.1f final_workflow_ms=%.1f total_ms=%.1f",
        match_doc.get("id") or match_path.name,
        source,
        workflow.get("phase"),
        timings["seeded_candidate_rebuild_ms"],
        timings["finalize_reviewed_identity_ms"],
        timings["segment_evidence_ms"],
        timings["progress_build_ms"],
        timings["final_workflow_ms"],
        timings["total_ms"],
    )
    return {
        "snapshot": snapshot,
        "review_progress": progress,
        "workflow": workflow,
        "performance": timings,
    }


def finalize_review_for_qa(
    match_path: Path,
    match_doc: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = get_review_workflow_state(match_path, match_doc)
    assert_workflow_action_allowed(state, "finalize_identity")
    refreshed = refresh_review_after_identity_mutation(match_path, match_doc, source="finalize")
    state = refreshed["workflow"]
    if state["issues"].get("overall_identity_blocked") or state["issues"].get("blocking"):
        code = (
            "identity_coverage_unresolved_without_reviewable_evidence"
            if state["issues"].get("coverage_readiness_blocked")
            and not state["issues"].get("blocking")
            else "identity_issues_remaining"
        )
        raise WorkflowActionError(code, state, "finalize_identity")
    snapshot = get_reviewed_identity_status(match_path)
    build_reviewed_stats(match_path, snapshot, match_doc, load_json_object(match_path / "pitch_config.json"))
    job = generate_reviewed_output(
        match_path,
        snapshot,
        match_doc,
        {**DEFAULT_RENDER_OPTIONS, **(options or {})},
        stats_already_current=True,
    )
    workflow = get_review_workflow_state(match_path, match_doc)
    logger.info(
        "review_workflow action=finalize match=%s render_queued=%s fingerprint=%s",
        match_doc.get("id") or match_path.name,
        job.get("status") in {"queued", "running"},
        str(snapshot.get("semantic_digest") or "")[:12],
    )
    return {"workflow": workflow, "reviewed_identity": snapshot, "render_job": job}


def approve_review_video_qa(match_path: Path, match_doc: dict[str, Any]) -> dict[str, Any]:
    state = get_review_workflow_state(match_path, match_doc)
    assert_workflow_action_allowed(state, "approve_video_qa")
    snapshot = get_reviewed_identity_status(match_path)
    stats = load_json_object(match_path / "reviewed_player_stats.json")
    job = state.get("processing") or load_json_object(match_path / "reviewed_video_job.json") or {}
    manifest = load_json_object(match_path / "reviewed_output_manifest.json")
    approval = save_video_qa_approval(
        match_path,
        match_id=str(match_doc.get("id") or match_path.name),
        fingerprints=current_approval_fingerprint(snapshot, stats, job, manifest),
    )
    workflow = get_review_workflow_state(match_path, match_doc)
    logger.info("review_workflow action=qa_approved match=%s", match_doc.get("id") or match_path.name)
    return {"approval": approval, "workflow": workflow}


def retry_review_render(match_path: Path, match_doc: dict[str, Any]) -> dict[str, Any]:
    state = get_review_workflow_state(match_path, match_doc)
    assert_workflow_action_allowed(state, "retry_render")
    snapshot = get_reviewed_identity_status(match_path)
    if snapshot.get("status") in {"missing", "stale"}:
        raise WorkflowActionError("reviewed_identity_stale", state, "retry_render")
    build_reviewed_stats(match_path, snapshot, match_doc, load_json_object(match_path / "pitch_config.json"))
    job = generate_reviewed_output(
        match_path,
        snapshot,
        match_doc,
        DEFAULT_RENDER_OPTIONS,
        stats_already_current=True,
    )
    return {"workflow": get_review_workflow_state(match_path, match_doc), "render_job": job}


def retry_review_recompute(match_path: Path, match_doc: dict[str, Any]) -> dict[str, Any]:
    state = get_review_workflow_state(match_path, match_doc)
    if "retry_review_recompute" not in set(state.get("allowed_actions") or []):
        raise WorkflowActionError("workflow_action_not_allowed", state, "retry_review_recompute")
    return refresh_review_after_identity_mutation(match_path, match_doc, source="retry")


def after_video_qa_correction(match_path: Path, match_doc: dict[str, Any]) -> dict[str, Any]:
    refreshed = refresh_review_after_identity_mutation(
        match_path,
        match_doc,
        source="video_qa_correction",
        rebuild_seeded_candidates=False,
    )
    workflow = refreshed["workflow"]
    if workflow["issues"].get("overall_identity_blocked") or workflow["issues"].get("blocking"):
        return refreshed
    snapshot = get_reviewed_identity_status(match_path)
    build_reviewed_stats(match_path, snapshot, match_doc, load_json_object(match_path / "pitch_config.json"))
    job = generate_reviewed_output(
        match_path,
        snapshot,
        match_doc,
        DEFAULT_RENDER_OPTIONS,
        stats_already_current=True,
    )
    return {**refreshed, "render_job": job, "workflow": get_review_workflow_state(match_path, match_doc)}


class ReviewWorkflowRecomputeError(RuntimeError):
    code = "review_recompute_failed"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
