from __future__ import annotations

"""Pure authoritative state derivation for the operator review workflow."""

import json
from pathlib import Path
from typing import Any

from app.services.identity_reviewed_output_jobs import reviewed_output_status_read_only
from app.services.identity_reviewed_coverage import COVERAGE_POLICY_VERSION
from app.services.identity_reviewed_snapshot import get_reviewed_identity_status
from app.services.identity_reviewed_progress import PROGRESS_SCHEMA_VERSION
from app.services.identity_review_scope import (
    review_scope_dependency_matches,
)
from app.services.identity_seeded_review_reduction import (
    load_initial_audit_completion_evidence,
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
        steps["mixed_players"] = _step("mixed_players", "locked", "identity_issues_remaining", {"count": normal_blocking})
        steps["finalize"] = _step("finalize", "locked", "identity_issues_remaining", {"count": blocking})
        steps["video_qa"] = _step("video_qa", "locked", "identity_issues_remaining", {"count": blocking})
        blockers.append(_blocker("identity_issues_remaining", "exceptions", {"count": normal_blocking}))
        allowed = ["review_identity_issue"]
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
                user_actionable=False,
            )
        )
        return _state(
            match_id,
            True,
            "action_required",
            "exceptions",
            steps,
            blockers,
            [],
            initial,
            issues,
            freshness,
            render,
            None,
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


def get_review_workflow_state(match_path: Path, match_doc: dict[str, Any]) -> dict[str, Any]:
    """Read compact artifacts only; this function must remain mutation-free."""
    snapshot = get_reviewed_identity_status(match_path)
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
    progress, progress_reason = _current_cached_progress(
        match_path,
        snapshot,
        match_doc,
    )
    initial = load_initial_audit_completion_evidence(match_path)
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
    progress = load_json_object(match_path / "reviewed_identity_progress.json")
    if not progress:
        return None, "review_progress_missing"
    snapshot_digest = str(snapshot.get("semantic_digest") or "")
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


def _state(match_id: str, available: bool, status: str, phase: str, steps_by_id: dict[str, dict[str, Any]], blockers: list[dict[str, Any]], allowed_actions: list[str], initial: dict[str, Any], issues: dict[str, Any], freshness: dict[str, Any], render: dict[str, Any], required_action: dict[str, Any] | None) -> dict[str, Any]:
    complete = status == "complete"
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "match_id": match_id,
        "available": available,
        "phase": phase,
        "status": status,
        "current_step_id": phase if phase in STEP_IDS else "video_qa" if phase == "complete" else "finalize",
        "review_complete": complete,
        "can_enter_report": complete,
        "can_publish": complete,
        "steps": [steps_by_id[step_id] for step_id in STEP_IDS],
        "required_action": required_action,
        "issues": {"blocking": int(issues.get("blocking") or 0), "actionable_blocking": int(issues.get("actionable_blocking") or issues.get("blocking") or 0), "normal_blocking": int(issues.get("normal_blocking") or 0), "mixed_blocking": int(issues.get("mixed_blocking") or 0), "mixed_total": int(issues.get("mixed_total") or 0), "mixed_resolved": int(issues.get("mixed_resolved") or 0), "important": int(issues.get("important") or 0), "semantic": int(issues.get("semantic") or 0), "coverage": int(issues.get("coverage") or 0), "optional": int(issues.get("optional") or 0), "optional_audit": int(issues.get("optional_audit") or 0), "coverage_readiness_blocked": bool(issues.get("coverage_readiness_blocked")), "overall_identity_blocked": bool(issues.get("overall_identity_blocked") or int(issues.get("blocking") or 0)), "coverage_readiness": issues.get("coverage_readiness"), "identity_coverage": issues.get("identity_coverage"), "workload": issues.get("workload")},
        "initial_audit": initial,
        "freshness": freshness,
        "processing": render if status in {"processing", "error"} else None,
        "blockers": blockers,
        "allowed_actions": allowed_actions,
    }
