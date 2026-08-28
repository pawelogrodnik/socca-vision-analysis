from __future__ import annotations

"""Pure authoritative state derivation for the operator review workflow."""

import json
from pathlib import Path
from typing import Any

from app.services.identity_reviewed_output_jobs import reviewed_output_status_read_only
from app.services.identity_reviewed_coverage import COVERAGE_POLICY_VERSION
from app.services.identity_reviewed_snapshot import get_reviewed_identity_status
from app.services.identity_reviewed_progress import PROGRESS_SCHEMA_VERSION
from app.services.identity_reviewed_team_attribution_evidence import (
    classify_team_attribution_evidence_status,
)
from app.services.identity_review_scope import (
    review_scope_dependency_matches,
)
from app.services.identity_seeded_review_reduction import (
    load_initial_audit_completion_evidence,
)
from app.services.review_source_fingerprints import (
    FINGERPRINTS_FIELD,
    canonical_generation_maybe_current,
)
from app.services.review_workflow_store import (
    approval_is_current,
    current_approval_fingerprint,
    load_json_object,
    load_video_qa_approval,
)


WORKFLOW_SCHEMA_VERSION = "1.0.0"
STEP_IDS = ("initial_audit", "exceptions", "mixed_players", "finalize", "video_qa")
PROCESSING_RENDER_STATUSES = {"queued", "running"}
RECOMPUTE_FAILURE_FILENAME = "review_workflow_recompute_failure.json"


class WorkflowActionError(ValueError):
    def __init__(self, code: str, state: dict[str, Any], action: str) -> None:
        self.code = code
        self.state = state
        self.action = action
        super().__init__(code)


def derive_review_workflow_state(evidence: dict[str, Any]) -> dict[str, Any]:
    """Derive all product workflow state from compact, persisted evidence."""
    match_id = str(evidence.get("match_id") or "")
    analysis_completed = bool(evidence.get("analysis_completed"))
    initial = dict(evidence.get("initial_audit") or {})
    issues = dict(evidence.get("issues") or {})
    freshness = dict(evidence.get("freshness") or {})
    render = dict(evidence.get("render") or {})
    recompute_failed = bool(evidence.get("recompute_failed"))
    qa_current = bool(freshness.get("qa_approval_current"))
    initial_complete = bool(initial.get("complete"))
    blocking = max(0, int(issues.get("blocking") or 0))
    normal_blocking = max(
        0,
        int(
            issues["normal_blocking"]
            if "normal_blocking" in issues
            else blocking
        ),
    )
    mixed_blocking = max(0, int(issues.get("mixed_blocking") or 0))
    coverage_readiness_blocked = bool(
        issues.get("coverage_readiness_blocked")
    )
    team_attribution_not_materialized = bool(
        issues.get("team_attribution_evidence_not_materialized")
    )
    render_status = str(render.get("status") or "missing")
    render_current = bool(freshness.get("reviewed_output_current"))
    stats_current = bool(freshness.get("reviewed_stats_current"))
    identity_current = bool(freshness.get("reviewed_identity_current"))
    progress_current = bool(freshness.get("review_progress_current", True))
    progress_reason = str(
        freshness.get("review_progress_reason") or "review_progress_missing"
    )
    steps = {step_id: _step(step_id, "locked") for step_id in STEP_IDS}
    blockers: list[dict[str, Any]] = []
    allowed: list[str] = []

    if not analysis_completed:
        steps["initial_audit"] = _step("initial_audit", "locked", "analysis_not_completed")
        steps["exceptions"] = _step("exceptions", "locked", "analysis_not_completed")
        steps["finalize"] = _step("finalize", "locked", "analysis_not_completed")
        steps["mixed_players"] = _step("mixed_players", "locked", "analysis_not_completed")
        steps["video_qa"] = _step("video_qa", "locked", "analysis_not_completed")
        blockers.append(_blocker("analysis_not_completed", "initial_audit"))
        return _state(match_id, False, "unavailable", "initial_audit", steps, blockers, allowed, initial, issues, freshness, render, {"type": "complete_analysis", "step_id": "analysis"})

    if recompute_failed:
        steps["initial_audit"] = _step("initial_audit", "error", "review_recompute_failed")
        steps["exceptions"] = _step("exceptions", "locked", "review_recompute_failed")
        steps["finalize"] = _step("finalize", "locked", "review_recompute_failed")
        steps["mixed_players"] = _step("mixed_players", "locked", "review_recompute_failed")
        steps["video_qa"] = _step("video_qa", "locked", "review_recompute_failed")
        blockers.append(_blocker("review_recompute_failed", "initial_audit"))
        return _state(match_id, True, "error", "initial_audit", steps, blockers, ["retry_review_recompute"], initial, issues, freshness, render, {"type": "retry_review_recompute", "step_id": "initial_audit"})

    if not initial_complete:
        steps["initial_audit"] = _step("initial_audit", "current", completed=initial.get("completed"), total=initial.get("total"), remaining=initial.get("remaining"))
        steps["exceptions"] = _step("exceptions", "locked", "initial_audit_incomplete")
        steps["finalize"] = _step("finalize", "locked", "initial_audit_incomplete")
        steps["mixed_players"] = _step("mixed_players", "locked", "initial_audit_incomplete")
        steps["video_qa"] = _step("video_qa", "locked", "initial_audit_incomplete")
        blockers.append(_blocker("initial_audit_incomplete", "initial_audit", {"remaining": initial.get("remaining")}))
        allowed = ["identify_players"]
        return _state(match_id, True, "action_required", "initial_audit", steps, blockers, allowed, initial, issues, freshness, render, {"type": "identify_players", "step_id": "initial_audit", "remaining": initial.get("remaining")})

    steps["initial_audit"] = _step("initial_audit", "completed", completed=initial.get("completed"), total=initial.get("total"), remaining=0)
    if not progress_current:
        steps["exceptions"] = _step("exceptions", "error", progress_reason)
        steps["finalize"] = _step("finalize", "locked", progress_reason)
        steps["mixed_players"] = _step("mixed_players", "locked", progress_reason)
        steps["video_qa"] = _step("video_qa", "locked", progress_reason)
        blockers.append(_blocker(progress_reason, "exceptions"))
        return _state(
            match_id,
            True,
            "error",
            "exceptions",
            steps,
            blockers,
            ["retry_review_recompute"],
            initial,
            issues,
            freshness,
            render,
            {"type": "retry_review_recompute", "step_id": "exceptions"},
        )

    if normal_blocking:
        steps["exceptions"] = _step("exceptions", "current", completed=issues.get("completed"), total=issues.get("total"), remaining=normal_blocking)
        # Required and scope-blocking Mixed are peer queues inside one Review
        # stage.  They can be worked in either order; only finalization stays
        # locked until both authoritative queues are empty.
        steps["mixed_players"] = _step(
            "mixed_players",
            "current" if mixed_blocking else "completed",
            remaining=mixed_blocking,
            total=issues.get("mixed_total"),
            completed=issues.get("mixed_resolved"),
        )
        steps["finalize"] = _step("finalize", "locked", "identity_issues_remaining", {"count": blocking})
        steps["video_qa"] = _step("video_qa", "locked", "identity_issues_remaining", {"count": blocking})
        blockers.append(_blocker("identity_issues_remaining", "exceptions", {"count": normal_blocking}))
        allowed = ["review_identity_issue"]
        if mixed_blocking:
            allowed.append("review_mixed_players")
        return _state(match_id, True, "action_required", "exceptions", steps, blockers, allowed, initial, issues, freshness, render, {"type": "review_identity_issue", "step_id": "exceptions", "remaining": normal_blocking})

    steps["exceptions"] = _step("exceptions", "completed", completed=issues.get("completed"), total=issues.get("total"), remaining=0)
    if mixed_blocking:
        steps["mixed_players"] = _step("mixed_players", "current", remaining=mixed_blocking, total=issues.get("mixed_total"), completed=issues.get("mixed_resolved"))
        steps["finalize"] = _step("finalize", "locked", "mixed_player_issues_remaining", {"count": mixed_blocking})
        steps["video_qa"] = _step("video_qa", "locked", "mixed_player_issues_remaining", {"count": mixed_blocking})
        blockers.append(_blocker("mixed_player_issues_remaining", "mixed_players", {"count": mixed_blocking}))
        return _state(match_id, True, "action_required", "mixed_players", steps, blockers, ["review_mixed_players"], initial, issues, freshness, render, {"type": "review_mixed_players", "step_id": "mixed_players", "remaining": mixed_blocking})
    steps["mixed_players"] = _step("mixed_players", "completed", remaining=0, total=issues.get("mixed_total"), completed=issues.get("mixed_resolved"))
    if coverage_readiness_blocked:
        readiness = issues.get("coverage_readiness") or {}
        details = {
            "readiness_status": readiness.get("status"),
            "blockers": list(readiness.get("blockers") or []),
        }
        steps["exceptions"] = _step(
            "exceptions",
            "error",
            "identity_coverage_unresolved_without_reviewable_evidence",
            details,
            completed=issues.get("completed"),
            total=issues.get("total"),
            remaining=0,
        )
        steps["finalize"] = _step(
            "finalize",
            "locked",
            "identity_coverage_unresolved_without_reviewable_evidence",
            details,
        )
        steps["video_qa"] = _step(
            "video_qa",
            "locked",
            "identity_coverage_unresolved_without_reviewable_evidence",
            details,
        )
        blockers.append(
            _blocker(
                "identity_coverage_unresolved_without_reviewable_evidence",
                "exceptions",
                details,
                user_actionable=team_attribution_not_materialized,
            )
        )
        allowed = ["retry_review_recompute"] if team_attribution_not_materialized else []
        return _state(
            match_id,
            True,
            "error",
            "exceptions",
            steps,
            blockers,
            allowed,
            initial,
            issues,
            freshness,
            render,
            (
                {"type": "retry_review_recompute", "step_id": "exceptions"}
                if team_attribution_not_materialized
                else {"type": "coverage_evidence_unavailable", "step_id": "exceptions"}
            ),
            terminal_data_quality_error=True,
        )
    if render_status in PROCESSING_RENDER_STATUSES:
        steps["finalize"] = _step("finalize", "processing")
        steps["video_qa"] = _step("video_qa", "locked", "render_running")
        blockers.append(_blocker("workflow_busy", "finalize", {"render_status": render_status}))
        return _state(match_id, True, "processing", "rendering_review_video", steps, blockers, [], initial, issues, freshness, render, {"type": "wait_for_render", "step_id": "finalize"})
    if render_status == "failed":
        steps["finalize"] = _step("finalize", "error", "render_failed")
        steps["video_qa"] = _step("video_qa", "locked", "render_failed")
        blockers.append(_blocker("render_failed", "finalize"))
        return _state(match_id, True, "error", "rendering_review_video", steps, blockers, ["retry_render"], initial, issues, freshness, render, {"type": "retry_render", "step_id": "finalize"})
    if not (identity_current and stats_current and render_current):
        steps["finalize"] = _step("finalize", "current")
        steps["video_qa"] = _step("video_qa", "locked", "reviewed_output_stale")
        allowed = ["finalize_identity", "review_identity_issue"]
        return _state(match_id, True, "ready", "ready_to_finalize", steps, blockers, allowed, initial, issues, freshness, render, {"type": "finalize_identity", "step_id": "finalize"})

    steps["finalize"] = _step("finalize", "completed")
    if qa_current:
        steps["video_qa"] = _step("video_qa", "completed")
        return _state(match_id, True, "complete", "complete", steps, blockers, ["review_video", "correct_video_identity"], initial, issues, freshness, render, None)
    steps["video_qa"] = _step("video_qa", "current")
    return _state(match_id, True, "action_required", "video_qa", steps, blockers, ["review_video", "approve_video_qa", "correct_video_identity"], initial, issues, freshness, render, {"type": "approve_video_qa", "step_id": "video_qa"})


def get_review_workflow_state(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    completion_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read compact artifacts only; this function must remain mutation-free.

    ``snapshot`` / ``progress`` / ``completion_evidence`` let the authoritative
    finalize transaction reuse artifacts it produced moments earlier in the
    same request instead of re-parsing and re-digesting every large source.
    Independent GET calls never pass them and keep reading durable state.
    """
    snapshot = snapshot if snapshot is not None else get_reviewed_identity_status(match_path)
    stats = load_json_object(match_path / "reviewed_player_stats.json")
    stats_readiness = load_json_object(match_path / "reviewed_stats_readiness.json")
    output_manifest = load_json_object(match_path / "reviewed_output_manifest.json")
    job = reviewed_output_status_read_only(match_path, snapshot)
    approval = load_video_qa_approval(match_path)
    fingerprints = current_approval_fingerprint(snapshot, stats, job, output_manifest)
    stats_current = bool(
        stats
        and snapshot.get("semantic_digest")
        and stats.get("source_snapshot_digest") == snapshot.get("semantic_digest")
        and review_scope_dependency_matches(match_doc, stats)
        and (
            not stats_readiness
            or stats_readiness.get("status") == "completed"
        )
    )
    output_current = bool(
        job.get("status") == "completed"
        and job.get("source_snapshot_digest") == snapshot.get("semantic_digest")
        and review_scope_dependency_matches(match_doc, job)
        and output_manifest
        and output_manifest.get("stale") is not True
    )
    if progress is None:
        progress, progress_reason = _current_cached_progress(
            match_path,
            snapshot,
            match_doc,
        )
    else:
        progress_reason = None
    initial = (
        completion_evidence
        if completion_evidence is not None
        else load_initial_audit_completion_evidence(match_path)
    )
    issues = _issue_evidence(snapshot, progress)
    evidence = {
        "match_id": str(match_doc.get("id") or match_path.name),
        "analysis_completed": _analysis_completed(match_path, match_doc),
        "initial_audit": initial,
        "issues": issues,
        "freshness": {
            "reviewed_identity_current": snapshot.get("status") not in {"missing", "stale"},
            "reviewed_stats_current": stats_current,
            "reviewed_output_current": output_current,
            "qa_approval_current": approval_is_current(approval, fingerprints) and output_current and stats_current,
            "review_progress_current": progress is not None,
            "review_progress_reason": progress_reason,
        },
        "render": _public_render(job),
        "recompute_failed": bool(load_json_object(match_path / "review_workflow_recompute_failure.json")),
    }
    state = derive_review_workflow_state(evidence)
    state["technical_diagnostics"] = {
        "reviewed_snapshot_status": snapshot.get("status"),
        "cached_progress_available": progress is not None,
        "review_progress_reason": progress_reason,
        "raw_structural_blockers": int(((progress or {}).get("summary") or {}).get("structural_blockers") or 0),
    }
    return state


def build_compact_review_workflow_state(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    """Derive public workflow state without parsing the large snapshot.

    This is deliberately distinct from the finalize-only preflight below:
    browser reads must describe analysis and initial-audit stages before a
    Reviewed Identity report has ever been materialized.
    """
    analysis_completed = _analysis_completed(match_path, match_doc)
    initial = load_initial_audit_completion_evidence(match_path)
    recompute_failed = bool(
        load_json_object(match_path / RECOMPUTE_FAILURE_FILENAME)
    )
    if not analysis_completed or not bool(initial.get("complete")):
        return derive_review_workflow_state({
            "match_id": str(match_doc.get("id") or match_path.name),
            "analysis_completed": analysis_completed,
            "initial_audit": initial,
            "issues": _issue_evidence({}, None),
            "freshness": {
                "reviewed_identity_current": False,
                "reviewed_stats_current": False,
                "reviewed_output_current": False,
                "qa_approval_current": False,
                "review_progress_current": False,
                "review_progress_reason": "review_progress_missing",
            },
            "render": {"status": "missing"},
            "recompute_failed": recompute_failed,
        })

    report = load_json_object(match_path / "reviewed_identity_report.json")
    snapshot_digest = str((report or {}).get("snapshot_digest") or "")
    return _compact_workflow_state_for_generation(
        match_path,
        match_doc,
        initial=initial,
        snapshot_digest=snapshot_digest,
        canonical_generation_current=bool(
            snapshot_digest
            and canonical_generation_maybe_current(
                (report or {}).get(FINGERPRINTS_FIELD),
                match_path,
            )
        ),
        recompute_failed=recompute_failed,
    )


def _compact_workflow_state_for_generation(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    initial: dict[str, Any],
    snapshot_digest: str,
    canonical_generation_current: bool,
    recompute_failed: bool,
) -> dict[str, Any]:
    """Use report/progress generation evidence to derive public workflow."""
    progress, progress_reason = _current_cached_progress_for_snapshot_digest(
        match_path,
        snapshot_digest,
        match_doc,
    )
    stats = load_json_object(match_path / "reviewed_player_stats.json")
    stats_readiness = load_json_object(match_path / "reviewed_stats_readiness.json")
    output_manifest = load_json_object(match_path / "reviewed_output_manifest.json")
    job = reviewed_output_status_read_only(
        match_path,
        snapshot_digest=snapshot_digest,
    )
    approval = load_video_qa_approval(match_path)
    fingerprints = current_approval_fingerprint(snapshot_digest, stats, job, output_manifest)
    stats_current = bool(
        stats
        and stats.get("source_snapshot_digest") == snapshot_digest
        and review_scope_dependency_matches(match_doc, stats)
        and (
            not stats_readiness
            or stats_readiness.get("status") == "completed"
        )
    )
    output_current = bool(
        job.get("status") == "completed"
        and job.get("source_snapshot_digest") == snapshot_digest
        and review_scope_dependency_matches(match_doc, job)
        and output_manifest
        and output_manifest.get("stale") is not True
    )
    state = derive_review_workflow_state({
        "match_id": str(match_doc.get("id") or match_path.name),
        "analysis_completed": _analysis_completed(match_path, match_doc),
        "initial_audit": initial,
        "issues": _issue_evidence({}, progress),
        "freshness": {
            "reviewed_identity_current": canonical_generation_current,
            "reviewed_stats_current": stats_current and canonical_generation_current,
            "reviewed_output_current": output_current and canonical_generation_current,
            "qa_approval_current": (
                approval_is_current(approval, fingerprints)
                and output_current
                and stats_current
                and canonical_generation_current
            ),
            "review_progress_current": progress is not None,
            "review_progress_reason": progress_reason,
        },
        "render": _public_render(job),
        "recompute_failed": recompute_failed,
    })
    state["compact_workflow"] = True
    return state


def build_cheap_finalize_preflight_state(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    """Durable-only finalize eligibility probe built from REAL evidence.

    Reads compact persisted artifacts only: the small reviewed identity
    report (which carries the authoritative snapshot digest), the durable
    progress, analysis/audit/recompute/render/QA evidence files.  It never
    parses large canonical sources and never loads the multi-hundred-MB
    snapshot document.

    Every workflow gate that is derivable from these compact artifacts is
    checked here with its real value: analysis completion, initial audit
    completion, previous recompute failure, required/mixed blockers,
    coverage readiness, render queued/running/failed, video-QA stage and
    already-complete states.

    Canonical source freshness is approximated with the compact
    ``source_file_fingerprints`` generation map persisted in the report by
    the authoritative build (``stat()`` size+mtime_ns only; no JSON parsing,
    no semantic hashing).  When every fingerprint still matches, the check
    that would require re-digesting all large sources stays deferred to the
    authoritative finalize recomputation.  Any difference — or an old report
    without fingerprint metadata — marks identity/stats/output/QA as not
    current so the workflow derivation admits ``finalize_identity`` and the
    authoritative pass can establish the actual semantic truth.
    """
    # Compact authoritative descriptor; deliberately NOT the full snapshot.
    report = load_json_object(match_path / "reviewed_identity_report.json")
    snapshot_digest = str((report or {}).get("snapshot_digest") or "")
    if not snapshot_digest:
        return _preflight_blocked("reviewed_identity_missing", "finalize")
    # Cheap conservative gate: True = maybe current, False = changed,
    # None = unknown (pre-fingerprint report) and handled as stale.
    canonical_generation_current = bool(
        canonical_generation_maybe_current(
            (report or {}).get(FINGERPRINTS_FIELD),
            match_path,
        )
    )

    state = _compact_workflow_state_for_generation(
        match_path,
        match_doc,
        initial=load_initial_audit_completion_evidence(match_path),
        snapshot_digest=snapshot_digest,
        canonical_generation_current=canonical_generation_current,
        recompute_failed=bool(
            load_json_object(match_path / RECOMPUTE_FAILURE_FILENAME)
        ),
    )
    state["cheap_preflight"] = True
    return state


def _preflight_blocked(
    code: str,
    step_id: str,
    issues: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "match_id": "",
        "available": True,
        "phase": step_id,
        "status": "action_required" if step_id != "finalize" else "ready",
        "current_step_id": step_id,
        "review_complete": False,
        "can_enter_report": False,
        "can_publish": False,
        "steps": [],
        "required_action": {"type": "review_identity_issue", "step_id": step_id},
        "issues": issues
        or {
            "blocking": 1,
            "actionable_blocking": 1,
            "overall_identity_blocked": True,
        },
        "freshness": {},
        "blockers": [{"code": code, "step_id": step_id}],
        "allowed_actions": [],
        "cheap_preflight": True,
    }


def assert_workflow_action_allowed(state: dict[str, Any], action: str) -> None:
    if action in set(state.get("allowed_actions") or []):
        return
    blocker = next(iter(state.get("blockers") or []), {})
    code = str(blocker.get("code") or "workflow_action_not_allowed")
    raise WorkflowActionError(code, state, action)


def _analysis_completed(match_path: Path, match_doc: dict[str, Any]) -> bool:
    report = load_json_object(match_path / "analysis_report.json") or {}
    return report.get("status") == "completed" or str(match_doc.get("status") or "") in {"analyzed", "reviewed", "published"}


def _current_cached_progress(
    match_path: Path,
    snapshot: dict[str, Any],
    match_doc: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    return _current_cached_progress_for_snapshot_digest(
        match_path,
        str(snapshot.get("semantic_digest") or ""),
        match_doc,
    )


def _current_cached_progress_for_snapshot_digest(
    match_path: Path,
    snapshot_digest: str,
    match_doc: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate the durable progress against an explicit snapshot digest.

    Compact callers (cheap finalize preflight) hold only the authoritative
    digest from ``reviewed_identity_report.json``; loading the multi-MB
    snapshot document just to read one string would defeat the purpose.
    """
    progress = load_json_object(match_path / "reviewed_identity_progress.json")
    if not progress:
        return None, "review_progress_missing"
    if not snapshot_digest or progress.get("source_snapshot_digest") != snapshot_digest:
        return None, "review_progress_stale"
    if progress.get("schema_version") != PROGRESS_SCHEMA_VERSION:
        return None, "review_progress_policy_stale"
    if (progress.get("policy") or {}).get("version") != COVERAGE_POLICY_VERSION:
        return None, "review_progress_policy_stale"
    if not review_scope_dependency_matches(match_doc, progress):
        return None, "review_progress_scope_stale"
    return progress, None


def _issue_evidence(snapshot: dict[str, Any], progress: dict[str, Any] | None) -> dict[str, Any]:
    progress_summary = (progress or {}).get("summary") or {}
    pending = int(progress_summary.get("important_decisions_remaining") or 0)
    mixed = (progress or {}).get("mixed_players", {}).get("summary", {})
    mixed_pending = int(mixed.get("unresolved") or 0)
    coverage_readiness = (progress or {}).get("coverage_readiness")
    coverage_readiness_blocked = bool(
        isinstance(coverage_readiness, dict)
        and coverage_readiness.get("allows_finalize") is False
    )
    coverage_residuals = (progress or {}).get("coverage_residuals") or {}
    team_attribution_evidence_not_materialized = any(
        classify_team_attribution_evidence_status(
            case.get("team_attribution_evidence_status")
        )
        == "remediable_not_established"
        for residual in coverage_residuals.values()
        if isinstance(residual, dict)
        for case in residual.get("non_actionable_required_team_uncertainty_cases") or []
        if isinstance(case, dict)
    )
    # The progress artifact is the authoritative operator queue.  The reviewed
    # snapshot can still report technical conflicts after an operator has made
    # every available decision (for example a multi-slot tracker fragment).
    # Counting those raw conflicts here creates an empty, impossible-to-finish
    # exceptions screen.  Keep them in snapshot diagnostics, but only block on
    # an actually actionable high-priority case.
    return {
        "blocking": pending + mixed_pending,
        "actionable_blocking": pending + mixed_pending,
        "coverage_readiness_blocked": coverage_readiness_blocked,
        "team_attribution_evidence_not_materialized": team_attribution_evidence_not_materialized,
        "overall_identity_blocked": bool(
            pending or mixed_pending or coverage_readiness_blocked
        ),
        "normal_blocking": pending,
        "mixed_blocking": mixed_pending,
        "important": pending + mixed_pending,
        "semantic": int(progress_summary.get("semantic_decisions_remaining") or 0),
        "coverage": int(progress_summary.get("coverage_decisions_remaining") or 0),
        "optional": int(progress_summary.get("optional_cases_remaining") or 0),
        "optional_audit": int(
            progress_summary.get("optional_audit_cases_remaining") or 0
        ),
        "completed": int(progress_summary.get("review_units_completed") or 0),
        "total": int(progress_summary.get("review_units_actionable_total") or 0),
        "mixed_total": int(mixed.get("total") or 0),
        "mixed_resolved": int(mixed.get("resolved") or 0),
        "coverage_readiness": coverage_readiness,
        "identity_coverage": (progress or {}).get("identity_coverage"),
        "workload": (progress or {}).get("workload"),
        "optional_audit_summary": (progress or {}).get("optional_audit"),
    }


def _public_render(job: dict[str, Any]) -> dict[str, Any]:
    return {key: job.get(key) for key in ("status", "job_key", "error", "source_snapshot_digest", "video_digest")}


def _step(step_id: str, status: str, locked_reason_code: str | None = None, locked_reason_details: dict[str, Any] | None = None, *, completed: Any = None, total: Any = None, remaining: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"id": step_id, "status": status, "completed": completed, "total": total, "remaining": remaining, "locked_reason_code": locked_reason_code}
    if locked_reason_details is not None:
        result["locked_reason_details"] = locked_reason_details
    return result


def _blocker(
    code: str,
    step_id: str,
    details: dict[str, Any] | None = None,
    *,
    user_actionable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "step_id": step_id,
        "user_actionable": user_actionable,
        "details": details or {},
    }


def _state(match_id: str, available: bool, status: str, phase: str, steps_by_id: dict[str, dict[str, Any]], blockers: list[dict[str, Any]], allowed_actions: list[str], initial: dict[str, Any], issues: dict[str, Any], freshness: dict[str, Any], render: dict[str, Any], required_action: dict[str, Any] | None, *, terminal_data_quality_error: bool = False) -> dict[str, Any]:
    complete = status == "complete"
    coverage_readiness_blocked = bool(issues.get("coverage_readiness_blocked"))
    mandatory_operator_review_complete = bool(
        initial.get("complete")
        and not int(issues.get("normal_blocking") or issues.get("blocking") or 0)
        and not int(issues.get("mixed_blocking") or 0)
        and phase not in {"initial_audit", "unavailable"}
        # An error means operator completion only when this exact,
        # authoritative branch reached exhausted queues and final coverage
        # readiness. Cached coverage flags on stale/technical errors cannot
        # turn a required refresh into a terminal data-quality state.
        and (status != "error" or terminal_data_quality_error)
    )
    data_quality_ready_for_output = bool(
        mandatory_operator_review_complete and not coverage_readiness_blocked
    )
    optional_summary = issues.get("optional_audit_summary") or {}
    optional_max_available = bool(
        data_quality_ready_for_output
        and str(optional_summary.get("status") or "") == "available"
        and int(optional_summary.get("remaining_cases") or 0) > 0
    )
    public_issues = {
        "blocking": int(issues.get("blocking") or 0),
        "actionable_blocking": int(
            issues.get("actionable_blocking") or issues.get("blocking") or 0
        ),
        "normal_blocking": int(issues.get("normal_blocking") or 0),
        "mixed_blocking": int(issues.get("mixed_blocking") or 0),
        "mixed_total": int(issues.get("mixed_total") or 0),
        "mixed_resolved": int(issues.get("mixed_resolved") or 0),
        "important": int(issues.get("important") or 0),
        "semantic": int(issues.get("semantic") or 0),
        "coverage": int(issues.get("coverage") or 0),
        "optional": int(issues.get("optional") or 0),
        "optional_audit": int(issues.get("optional_audit") or 0),
        "coverage_readiness_blocked": coverage_readiness_blocked,
        "team_attribution_evidence_not_materialized": bool(
            issues.get("team_attribution_evidence_not_materialized")
        ),
        "overall_identity_blocked": bool(
            issues.get("overall_identity_blocked")
            or int(issues.get("blocking") or 0)
        ),
        "coverage_readiness": issues.get("coverage_readiness"),
        "identity_coverage": issues.get("identity_coverage"),
        "workload": issues.get("workload"),
        "optional_audit_summary": optional_summary or None,
    }
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "match_id": match_id,
        "available": available,
        "phase": phase,
        "status": status,
        "current_step_id": phase if phase in STEP_IDS else "video_qa" if phase == "complete" else "finalize",
        "review_complete": complete,
        "mandatory_operator_review_complete": mandatory_operator_review_complete,
        "data_quality_ready_for_output": data_quality_ready_for_output,
        "optional_max_available": optional_max_available,
        "can_enter_report": complete,
        "can_publish": complete,
        "steps": [steps_by_id[step_id] for step_id in STEP_IDS],
        "required_action": required_action,
        "issues": public_issues,
        "initial_audit": initial,
        "freshness": freshness,
        "processing": render if status in {"processing", "error"} else None,
        "blockers": blockers,
        "allowed_actions": allowed_actions,
    }
