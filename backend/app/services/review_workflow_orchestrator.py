from __future__ import annotations

"""Mutation orchestration for the authoritative operator review workflow."""

import logging
from pathlib import Path
import time
from typing import Any

from app.services.identity_canonical_io import (
    invalidate_cached_json,
    review_build_context,
    scoped_memo_invalidate,
)
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_coverage import compact_mixed_players_summary
from app.services.identity_reviewed_output_jobs import generate_reviewed_output
from app.services.identity_reviewed_progress import (
    PROGRESS_SCHEMA_VERSION,
    build_reviewed_identity_progress,
)
from app.services.identity_reviewed_recompute_state import (
    clear_reviewed_identity_recompute_required,
)
from app.services.identity_reviewed_snapshot import (
    finalize_reviewed_identity,
    get_reviewed_identity_status,
    last_snapshot_build_phases,
)
from app.services.identity_reviewed_segments import (
    load_segment_review,
    render_segment_review_evidence,
)
from app.services.identity_reviewed_mixed_store import (
    build_mixed_review_queue,
    render_mixed_review_evidence,
)
from app.services.identity_reviewed_team_attribution_evidence import (
    FOCUSED_SOURCE_ALREADY_ACTIONABLE,
    classify_team_attribution_evidence_status,
    mark_team_attribution_evidence_technical_failure,
    materialize_team_attribution_evidence,
    resolve_current_team_attribution_sources,
)
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.identity_reviewed_team_attribution_policy import (
    persist_automatic_team_assignments,
)
from app.services.identity_seeded_review_reduction import load_initial_audit_completion_evidence
from app.services.identity_seeded_candidate_assignments import (    rebuild_identity_seeded_candidate_assignments,
)
from app.services.review_workflow_state import (
    RECOMPUTE_FAILURE_FILENAME,
    WorkflowActionError,
    assert_workflow_action_allowed,
    build_compact_review_workflow_state,
    build_cheap_finalize_preflight_state,
    get_review_workflow_state,
)
from app.services.review_workflow_store import (
    current_approval_fingerprint,
    load_json_object,
    save_video_qa_approval,
)


logger = logging.getLogger(__name__)
PROGRESS_FILENAME = "reviewed_identity_progress.json"
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
    operator_evidence: bool = True,
    leave_hot_state_warm: bool = False,
    reuse_current_snapshot: bool = False,
    retry_technical_team_attribution_evidence: bool = False,
) -> dict[str, Any]:
    """Perform only the cheap reviewed-identity work needed for the next click."""
    with review_build_context():
        return _refresh_review_after_identity_mutation_scoped(
            match_path,
            match_doc,
            source=source,
            rebuild_seeded_candidates=rebuild_seeded_candidates,
            operator_evidence=operator_evidence,
            leave_hot_state_warm=leave_hot_state_warm,
            reuse_current_snapshot=reuse_current_snapshot,
            retry_technical_team_attribution_evidence=retry_technical_team_attribution_evidence,
        )


def _refresh_review_after_identity_mutation_scoped(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    source: str,
    rebuild_seeded_candidates: bool = False,
    operator_evidence: bool = True,
    leave_hot_state_warm: bool = False,
    reuse_current_snapshot: bool = False,
    retry_technical_team_attribution_evidence: bool = False,
) -> dict[str, Any]:
    try:
        return _refresh_review_after_identity_mutation_scoped_inner(
            match_path,
            match_doc,
            source=source,
            rebuild_seeded_candidates=rebuild_seeded_candidates,
            operator_evidence=operator_evidence,
            leave_hot_state_warm=leave_hot_state_warm,
            reuse_current_snapshot=reuse_current_snapshot,
            retry_technical_team_attribution_evidence=retry_technical_team_attribution_evidence,
        )
    except ReviewWorkflowRecomputeError as exc:
        # The final exact-source convergence guard can raise after persisting
        # its narrow technical evidence. It must also persist the controlled
        # workflow failure envelope so subsequent reads cannot fall back to a
        # stale generic materialization-required generation.
        _persist_review_recompute_failure(match_path, source, exc)
        raise
    except Exception as exc:
        _raise_review_recompute_failure(match_path, match_doc, source, exc)


def _refresh_review_after_identity_mutation_scoped_inner(
    match_path: Path,
    match_doc: dict[str, Any],
    *,
    source: str,
    rebuild_seeded_candidates: bool = False,
    operator_evidence: bool = True,
    leave_hot_state_warm: bool = False,
    reuse_current_snapshot: bool = False,
    retry_technical_team_attribution_evidence: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, Any] = {
        "seeded_candidate_rebuild_ms": 0.0,
        "finalize_reviewed_identity_ms": 0.0,
        "segment_evidence_ms": 0.0,
        "team_attribution_evidence_ms": 0.0,
        "hot_state_warm_write_ms": 0.0,
        "progress_build_ms": 0.0,
        "final_workflow_ms": 0.0,
    }
    progress: dict[str, Any] | None = None
    durable_progress: dict[str, Any] | None = None
    focused_sources: list[dict[str, Any]] | None = None
    # A terminal Team-U recovery is allowed to touch only exact sources, but
    # it must still converge.  Keep the pre-recovery identities so the final
    # projection can prove that it did not merely rediscover the same generic
    # ``not_materialized`` rows.
    remediation_source_keys: set[tuple[str, str, str, str, str]] = set()
    remediation_diagnostics: dict[str, int] = {
        "pre_retry_exact_source_count": 0,
        "requested_focused_sources": 0,
        "selector_unresolved_sources": 0,
        "materialized_actionable": 0,
        "terminal_unavailable": 0,
        "technical_failure": 0,
        "post_retry_remediable_not_established": 0,
    }
    recovery_changed_durable_evidence = False
    try:
        if rebuild_seeded_candidates:
            # Callers that mutate actual seed inputs may request this JSON-only
            # rebuild. Reviewed correction decisions are intentionally not a
            # semantic seeded-assignment dependency.
            phase_started = time.perf_counter()
            rebuild_identity_seeded_candidate_assignments(match_path, match_doc)
            timings["seeded_candidate_rebuild_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        snapshot = get_reviewed_identity_status(match_path) if reuse_current_snapshot else {}
        if not snapshot or snapshot.get("status") in {"missing", "stale"}:
            # The reuse request is only an optimization for a policy-only
            # migration. A durable semantic freshness proof is mandatory; if
            # it is absent, fall back to the canonical snapshot build.
            snapshot = finalize_reviewed_identity(match_path, match_doc)
            timings["snapshot_reused"] = False
            timings["finalize_phases"] = last_snapshot_build_phases()
            timings.update(_flatten_phase_timings("finalize", timings["finalize_phases"]))
        else:
            timings["snapshot_reused"] = True
            timings["finalize_phases"] = {}
        timings["finalize_reviewed_identity_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        # Operator-review evidence is only meaningful while reviewable cases
        # remain.  A successful finalize has zero blockers, so regenerating
        # crops there would be pure synchronous latency on the click path.
        if operator_evidence:
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
            try:
                materialize_team_attribution_evidence(match_path)
            except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
                logger.warning(
                    "review_workflow team_attribution_evidence_render_failed match=%s error=%s",
                    match_doc.get("id") or match_path.name,
                    type(exc).__name__,
                )
            timings["team_attribution_evidence_ms"] = _elapsed_ms(phase_started)
        if retry_technical_team_attribution_evidence:
            phase_started = time.perf_counter()
            focused_sources = _technical_retry_sources_from_current_durable_progress(
                match_path,
                snapshot,
            )
            timings["technical_source_lookup_ms"] = _elapsed_ms(phase_started)
        if focused_sources is None:
            phase_started = time.perf_counter()
            # Internal units are required to leave the hot read model warm without
            # a second canonical pass; the durable progress artifact stays compact.
            progress = build_reviewed_identity_progress(
                match_path,
                match_doc,
                include_internal_units=True,
            )
            timings["progress_build_ms"] = _elapsed_ms(phase_started)
            durable_progress = _durable_progress_for_snapshot(progress, snapshot, source)
    except Exception as exc:
        _raise_review_recompute_failure(match_path, match_doc, source, exc)
    (match_path / RECOMPUTE_FAILURE_FILENAME).unlink(missing_ok=True)
    completion_evidence = load_initial_audit_completion_evidence(match_path)
    if focused_sources is None:
        assert progress is not None and durable_progress is not None
        phase_started = time.perf_counter()
        # Same-transaction reuse (§22/§37): the snapshot and progress above were
        # produced from current canonical inputs moments ago. This first workflow
        # derivation only decides whether bounded evidence recovery is needed; the
        # response is derived after the final generation is durably committed.
        workflow = get_review_workflow_state(
            match_path,
            match_doc,
            snapshot=snapshot,
            progress=durable_progress,
            completion_evidence=completion_evidence,
        )
        timings["initial_workflow_ms"] = _elapsed_ms(phase_started)
        remediation_descriptors = _remediable_team_attribution_descriptors(
            workflow,
            progress,
            include_technical_failures=retry_technical_team_attribution_evidence,
        )
        focused_sources = _not_materialized_team_attribution_sources(
            workflow,
            progress,
            include_technical_failures=retry_technical_team_attribution_evidence,
        )
        remediation_source_keys = {
            key
            for row in remediation_descriptors
            if (key := _team_evidence_source_key(row)) is not None
        }
        remediation_diagnostics["pre_retry_exact_source_count"] = len(
            remediation_source_keys
        )
        selected_keys = {
            key
            for row in focused_sources
            if (key := _team_evidence_source_key(row)) is not None
        }
        unresolved_descriptors = [
            _team_attribution_failure_source(row)
            for row in remediation_descriptors
            if _team_evidence_source_key(row) not in selected_keys
        ]
        if unresolved_descriptors:
            # Readiness identified an exact remediable source, but the focused
            # selector could not reconstruct it from current canonical units.
            # Persist an explicit technical result instead of returning the
            # same generic materialization state.  The following authoritative
            # projection either drops an obsolete source or attaches this
            # exact status to a still-current one.
            mark_team_attribution_evidence_technical_failure(
                match_path,
                unresolved_descriptors,
                status="team_attribution_evidence_recovery_incomplete",
            )
            remediation_diagnostics["selector_unresolved_sources"] = len(
                unresolved_descriptors
            )
            remediation_diagnostics["technical_failure"] += len(
                unresolved_descriptors
            )
            recovery_changed_durable_evidence = True
    elif focused_sources is not None:
        remediation_source_keys = {
            key
            for row in focused_sources
            if (key := _team_evidence_source_key(row)) is not None
        }
        remediation_diagnostics["pre_retry_exact_source_count"] = len(
            remediation_source_keys
        )
    if focused_sources:
        remediation_diagnostics["requested_focused_sources"] = len(focused_sources)
        phase_started = time.perf_counter()
        try:
            focused_evidence = materialize_team_attribution_evidence(
                match_path,
                focused_sources=focused_sources,
            )
        except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
            logger.warning(
                "review_workflow focused_team_attribution_evidence_failed match=%s error=%s",
                match_doc.get("id") or match_path.name,
                type(exc).__name__,
            )
            # A failed exact materialization is not ordinary missing evidence.
            # Persist the technical outcome so this retry cannot return a
            # misleading 200 with the same generic Retry action.
            mark_team_attribution_evidence_technical_failure(
                match_path,
                focused_sources,
                status="team_attribution_evidence_materialization_failed",
            )
            remediation_diagnostics["technical_failure"] += len(focused_sources)
        else:
            _accumulate_remediation_outcomes(
                remediation_diagnostics,
                _focused_evidence_outcome_counts(focused_sources, focused_evidence),
            )
            unresolved_sources = _focused_sources_without_durable_outcome(
                focused_sources,
                focused_evidence,
            )
            if unresolved_sources:
                # This is evaluated from the exact materialization result,
                # before rebuilding the match-wide projection. It prevents a
                # second ~full progress pass merely to discover a silent drop.
                mark_team_attribution_evidence_technical_failure(
                    match_path,
                    unresolved_sources,
                    status="team_attribution_evidence_recovery_incomplete",
                )
                remediation_diagnostics["technical_failure"] += len(unresolved_sources)
        recovery_changed_durable_evidence = True
        timings["focused_team_attribution_evidence_ms"] = _elapsed_ms(phase_started)
    if recovery_changed_durable_evidence:
        # The first progress projection was intentionally built without this
        # evidence. Drop only request-local derived values, then rebuild the
        # affected authoritative projection once from current durable inputs.
        scoped_memo_invalidate("__review_units__")
        scoped_memo_invalidate("__authoritative_progress__")
        phase_started = time.perf_counter()
        progress = build_reviewed_identity_progress(
            match_path,
            match_doc,
            include_internal_units=True,
        )
        timings["focused_progress_rebuild_ms"] = _elapsed_ms(phase_started)
        durable_progress = _durable_progress_for_snapshot(progress, snapshot, source)
        still_remediable = _same_remediable_team_attribution_sources(
            progress,
            remediation_source_keys,
        )
        remediation_diagnostics["post_retry_remediable_not_established"] = len(
            still_remediable
        )
        if still_remediable:
            # A successful materializer plus rebuild is still not enough if
            # the *same exact source* remains generic. Convert only those
            # sources to the durable technical status and reproject once more
            # so the response cannot expose another identical Retry loop.
            failure_sources = [
                _team_attribution_failure_source(row)
                for row in still_remediable
            ]
            mark_team_attribution_evidence_technical_failure(
                match_path,
                failure_sources,
                status="team_attribution_evidence_recovery_incomplete",
            )
            remediation_diagnostics["technical_failure"] += len(failure_sources)
            scoped_memo_invalidate("__review_units__")
            scoped_memo_invalidate("__authoritative_progress__")
            phase_started = time.perf_counter()
            progress = build_reviewed_identity_progress(
                match_path,
                match_doc,
                include_internal_units=True,
            )
            timings["recovery_guard_progress_rebuild_ms"] = _elapsed_ms(
                phase_started
            )
            durable_progress = _durable_progress_for_snapshot(
                progress,
                snapshot,
                source,
            )
            unresolved_after_guard = _same_remediable_team_attribution_sources(
                progress,
                remediation_source_keys,
            )
            remediation_diagnostics["post_retry_remediable_not_established"] = len(
                unresolved_after_guard
            )
            if unresolved_after_guard:
                # Never acknowledge a retry as successful when its exact
                # source cannot leave generic materialization state. The
                # durable technical evidence above remains diagnostic, while
                # the HTTP failure makes the consistency fault explicit.
                raise ReviewWorkflowRecomputeError(
                    "team-attribution recovery did not converge for exact source"
                )
    assert progress is not None and durable_progress is not None
    phase_started = time.perf_counter()
    persist_automatic_team_assignments(
        match_path, progress.get("_internal_review_units") or []
    )
    write_identity_json_atomic(match_path / PROGRESS_FILENAME, durable_progress, compact=True)
    invalidate_cached_json(match_path / PROGRESS_FILENAME)
    timings["durable_progress_write_ms"] = _elapsed_ms(phase_started)
    if leave_hot_state_warm:
        from app.services.identity_reviewed_hot_state import (
            last_hot_state_build_phases,
            rebuild_review_hot_state,
        )

        phase_started = time.perf_counter()
        rebuild_review_hot_state(match_path, match_doc, prebuilt_progress=progress)
        timings["hot_state_warm_write_ms"] = _elapsed_ms(phase_started)
        timings["hot_state_warm_write_phases"] = last_hot_state_build_phases()
        timings.update(
            _flatten_phase_timings("hot", timings["hot_state_warm_write_phases"])
        )
    phase_started = time.perf_counter()
    # Return only a workflow derived from the exact generation already
    # persisted (and, where requested, warmed) for subsequent browser reads.
    workflow = get_review_workflow_state(
        match_path,
        match_doc,
        snapshot=snapshot,
        progress=durable_progress,
        completion_evidence=completion_evidence,
    )
    timings["final_workflow_ms"] = _elapsed_ms(phase_started)
    if remediation_diagnostics["pre_retry_exact_source_count"]:
        timings["team_attribution_remediation"] = remediation_diagnostics
    timings["total_ms"] = _elapsed_ms(started)
    clear_reviewed_identity_recompute_required(match_path)
    logger.info(
        "reviewed_correction_perf mode=finalize match=%s source=%s phase=%s "
        "seeded_candidate_rebuild_ms=%.1f finalize_reviewed_identity_ms=%.1f "
        "segment_evidence_ms=%.1f team_attribution_evidence_ms=%.1f "
        "progress_build_ms=%.1f final_workflow_ms=%.1f total_ms=%.1f",
        match_doc.get("id") or match_path.name,
        source,
        workflow.get("phase"),
        timings["seeded_candidate_rebuild_ms"],
        timings["finalize_reviewed_identity_ms"],
        timings["segment_evidence_ms"],
        timings["team_attribution_evidence_ms"],
        timings["progress_build_ms"],
        timings["final_workflow_ms"],
        timings["total_ms"],
    )
    return {
        "snapshot": snapshot,
        "review_progress": progress,
        "completion_evidence": completion_evidence,
        "workflow": workflow,
        "performance": timings,
    }


def public_finalized_identity(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compact HTTP projection of the authoritative finalized snapshot.

    The persisted ``reviewed_identity_snapshot.json`` keeps every exact
    observation-level row; operators only need transition metadata here.
    """
    return {
        "status": snapshot.get("status"),
        "semantic_digest": snapshot.get("semantic_digest"),
        "summary": snapshot.get("summary"),
        "identity_coverage": snapshot.get("identity_coverage"),
        "coverage_readiness": snapshot.get("coverage_readiness"),
        "source": snapshot.get("source"),
        "entities_total": len(snapshot.get("entities") or []),
    }


PROGRESS_INTERNAL_KEYS = {"_internal_review_units", "_projection_inputs"}


def public_review_progress(progress: dict[str, Any]) -> dict[str, Any]:
    """Durable/HTTP progress without server-only internal units."""
    return {
        key: value
        for key, value in progress.items()
        if key not in PROGRESS_INTERNAL_KEYS
    }


DURABLE_PROGRESS_QUEUE_KEYS = {"review_units"}


def durable_review_progress(progress: dict[str, Any]) -> dict[str, Any]:
    """Compact persisted progress contract.

    Kept durably: workflow summaries/readiness, Optional MAX summary,
    ``next_cases``/``optional_audit_cases`` (public-shaped queue consumed by
    the deferred-save action gate) and the compact deferred-correction
    context used for exact active-cap validation.
    Dropped: the duplicated full ``review_units`` list (dead diagnostics
    consumer only) and mixed-player exact sources/evidence (the operator
    panel reads them from the dedicated endpoint; hot state keeps server
    truth).
    """
    base = public_review_progress(progress)
    return {
        key: compact_mixed_players_summary(value) if key == "mixed_players" else value
        for key, value in base.items()
        if key not in DURABLE_PROGRESS_QUEUE_KEYS
    }


def _durable_progress_for_snapshot(
    progress: dict[str, Any],
    snapshot: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    return durable_review_progress(progress) | {
        "source_snapshot_digest": snapshot.get("semantic_digest"),
        "workflow_refresh_source": source,
    }


def _technical_retry_sources_from_current_durable_progress(
    match_path: Path,
    snapshot: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Resolve current terminal remediation descriptors, or request fallback.

    A terminal state can contain an earlier technical failure *and* a separate
    exact source still awaiting materialization. Repairing only the former
    would return another generic Retry state even though the operator asked
    for one authoritative recovery. Resolve both lifecycle classes in one
    bounded pass, while retaining the same strict snapshot/digest proof. Any
    absence, drift or unsupported scope is a safe ``None`` fallback to the
    existing full progress pass.
    """
    snapshot_digest = str(snapshot.get("semantic_digest") or "")
    progress = load_json_object(match_path / PROGRESS_FILENAME)
    if (
        not snapshot_digest
        or not progress
        or progress.get("schema_version") != PROGRESS_SCHEMA_VERSION
        or str(progress.get("source_snapshot_digest") or "") != snapshot_digest
    ):
        return None
    descriptors = [
        dict(case)
        for residual in (progress.get("coverage_residuals") or {}).values()
        if isinstance(residual, dict)
        for case in residual.get("non_actionable_required_team_uncertainty_cases") or []
        if isinstance(case, dict)
        and classify_team_attribution_evidence_status(
            case.get("team_attribution_evidence_status")
        ) in {"technical_failure", "remediable_not_established"}
    ]
    return resolve_current_team_attribution_sources(match_path, descriptors)


def _not_materialized_team_attribution_sources(
    workflow: dict[str, Any],
    progress: dict[str, Any],
    *,
    include_technical_failures: bool = False,
) -> list[dict[str, Any]]:
    """Find exact recovery sources from current authoritative review units.

    This deliberately reads the policy's non-actionable uncertainty rows, not
    every Team-U source. A normal Required/Mixed queue is already actionable
    and must keep the fast no-global-evidence path. Explicit evidence-retry
    also admits exact technical failures, but never trusts persisted source
    pairs: it derives them again from the current internal review units.
    """
    issues = workflow.get("issues") or {}
    if (
        not issues.get("coverage_readiness_blocked")
        or int(issues.get("normal_blocking") or 0) > 0
        or int(issues.get("mixed_blocking") or 0) > 0
    ):
        return []
    required_keys = {
        key
        for row in _remediable_team_attribution_descriptors(
            workflow,
            progress,
            include_technical_failures=include_technical_failures,
        )
        if (key := _team_evidence_source_key(row)) is not None
    }
    if not required_keys:
        return []
    sources = []
    for unit in progress.get("_internal_review_units") or []:
        if not isinstance(unit, dict):
            continue
        if _team_evidence_source_key(unit) not in required_keys:
            continue
        pairs = unit.get("detected_pairs") or []
        if not pairs:
            continue
        sources.append(
            {
                "candidate_subject_id": unit.get("candidate_subject_id"),
                "scope_kind": unit.get("scope_kind"),
                "review_target_id": unit.get("review_target_id"),
                "continuity_group_id": unit.get("continuity_group_id"),
                "source_team_label": unit.get("source_team_label"),
                "source_ownership_digest": unit.get(
                    "team_attribution_evidence_source_digest"
                ),
                "detected_pairs": pairs,
            }
        )
    return sorted(sources, key=lambda row: _team_evidence_source_key(row) or ("",) * 5)


def _remediable_team_attribution_descriptors(
    workflow: dict[str, Any],
    progress: dict[str, Any],
    *,
    include_technical_failures: bool = False,
) -> list[dict[str, Any]]:
    """Return exact residual descriptors which this transaction may repair.

    Coverage owns the declaration that remediation is necessary.  Keeping
    these descriptors separate from the raw-pair selector lets the caller
    distinguish "nothing needs recovery" from "recovery was required but
    could not safely reconstruct its exact source".
    """
    issues = workflow.get("issues") or {}
    if (
        not issues.get("coverage_readiness_blocked")
        or int(issues.get("normal_blocking") or 0) > 0
        or int(issues.get("mixed_blocking") or 0) > 0
    ):
        return []
    descriptors: list[dict[str, Any]] = []
    for residual in (progress.get("coverage_residuals") or {}).values():
        if not isinstance(residual, dict):
            continue
        for case in residual.get("non_actionable_required_team_uncertainty_cases") or []:
            if not isinstance(case, dict):
                continue
            classification = classify_team_attribution_evidence_status(
                case.get("team_attribution_evidence_status")
            )
            if classification == "remediable_not_established" or (
                include_technical_failures and classification == "technical_failure"
            ):
                if _team_evidence_source_key(case) is not None:
                    descriptors.append(dict(case))
    return sorted(
        descriptors,
        key=lambda row: _team_evidence_source_key(row) or ("",) * 5,
    )


def _team_attribution_failure_source(row: dict[str, Any]) -> dict[str, Any]:
    """Adapt a compact residual descriptor to the exact evidence contract."""
    return {
        **row,
        "source_ownership_digest": row.get(
            "team_attribution_evidence_source_digest"
        )
        or row.get("source_ownership_digest"),
    }


def _same_remediable_team_attribution_sources(
    progress: dict[str, Any],
    source_keys: set[tuple[str, str, str, str, str]],
) -> list[dict[str, Any]]:
    """Return only pre-retry exact sources still generically remediable."""
    if not source_keys:
        return []
    remaining: list[dict[str, Any]] = []
    for residual in (progress.get("coverage_residuals") or {}).values():
        if not isinstance(residual, dict):
            continue
        for case in residual.get("non_actionable_required_team_uncertainty_cases") or []:
            if not isinstance(case, dict):
                continue
            key = _team_evidence_source_key(case)
            if (
                key in source_keys
                and classify_team_attribution_evidence_status(
                    case.get("team_attribution_evidence_status")
                )
                == "remediable_not_established"
            ):
                remaining.append(dict(case))
    return sorted(
        remaining,
        key=lambda row: _team_evidence_source_key(row) or ("",) * 5,
    )


def _focused_evidence_outcome_counts(
    focused_sources: list[dict[str, Any]],
    document: dict[str, Any],
) -> dict[str, int]:
    """Summarize only exact focused outcomes for compact retry diagnostics."""
    requested_keys = {
        _team_evidence_source_key(row)
        for row in focused_sources
        if _team_evidence_source_key(row) is not None
    }
    counts = {
        "materialized_actionable": 0,
        "terminal_unavailable": 0,
        "technical_failure": 0,
    }
    for case in document.get("cases") or []:
        if not isinstance(case, dict) or _team_evidence_source_key(case) not in requested_keys:
            continue
        status = str(case.get("status") or "")
        if status in {
            "ready_for_team_attribution",
            FOCUSED_SOURCE_ALREADY_ACTIONABLE,
        }:
            counts["materialized_actionable"] += 1
        elif classify_team_attribution_evidence_status(status) == "terminal_unavailable":
            counts["terminal_unavailable"] += 1
        elif classify_team_attribution_evidence_status(status) == "technical_failure":
            counts["technical_failure"] += 1
    return counts


def _accumulate_remediation_outcomes(
    diagnostics: dict[str, int],
    outcomes: dict[str, int],
) -> None:
    """Add overlapping evidence outcomes without discarding earlier failures."""
    for key, value in outcomes.items():
        diagnostics[key] = int(diagnostics.get(key) or 0) + int(value or 0)


def _team_evidence_source_key(
    row: dict[str, Any],
) -> tuple[str, str, str, str, str] | None:
    subject_id = str(row.get("candidate_subject_id") or "")
    source_digest = str(row.get("team_attribution_evidence_source_digest") or "")
    if not source_digest:
        source_digest = str(row.get("source_ownership_digest") or "")
    if not subject_id or not source_digest:
        return None
    return (
        subject_id,
        str(row.get("scope_kind") or ""),
        str(row.get("review_target_id") or ""),
        str(row.get("continuity_group_id") or ""),
        source_digest,
    )


def _focused_sources_without_durable_outcome(
    focused_sources: list[dict[str, Any]],
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find exact focused sources lacking a materializer-owned outcome.

    `ready_for_team_attribution` is an actionable outcome: the same exact
    rendered crops are attached by the following progress projection. Terminal
    and technical builder statuses are also final outcomes. Everything else is
    explicitly converted before (not after) that one expensive projection.
    """
    cases_by_key = {
        _team_evidence_source_key(row): row
        for row in document.get("cases") or []
        if isinstance(row, dict)
        if _team_evidence_source_key(row) is not None
    }
    unresolved: list[dict[str, Any]] = []
    for source in focused_sources:
        case = cases_by_key.get(_team_evidence_source_key(source))
        if not isinstance(case, dict):
            unresolved.append(source)
            continue
        status = str(case.get("status") or "")
        if status in {
            "ready_for_team_attribution",
            FOCUSED_SOURCE_ALREADY_ACTIONABLE,
        }:
            continue
        if classify_team_attribution_evidence_status(status) in {
            "terminal_unavailable",
            "technical_failure",
        }:
            continue
        unresolved.append(source)
    return unresolved


def _raise_review_recompute_failure(
    match_path: Path,
    match_doc: dict[str, Any],
    source: str,
    exc: Exception,
) -> None:
    """Persist the one fail-closed envelope for every reproject phase."""
    _persist_review_recompute_failure(match_path, source, exc)
    logger.info(
        "review_workflow action=refresh_failed match=%s source=%s error=%s",
        match_doc.get("id") or match_path.name,
        source,
        type(exc).__name__,
    )
    raise ReviewWorkflowRecomputeError(str(exc)) from exc


def _persist_review_recompute_failure(
    match_path: Path,
    source: str,
    exc: Exception,
) -> None:
    """Write the durable fail-closed envelope without changing exception flow."""
    write_identity_json_atomic(
        match_path / RECOMPUTE_FAILURE_FILENAME,
        {
            "schema_version": "1.0.0",
            "code": "review_recompute_failed",
            "source": source,
            "error": f"{type(exc).__name__}: {exc}",
        },
    )


def _flatten_phase_timings(prefix: str, phases: dict[str, Any]) -> dict[str, float]:
    """Expose nested phase diagnostics in Server-Timing without extra work."""
    return {
        f"{prefix}_{name}": float(value)
        for name, value in phases.items()
        if name.endswith("_ms") and isinstance(value, (int, float))
    }


def _materialize_operator_evidence(match_path: Path, match_doc: dict[str, Any]) -> None:
    """Best-effort operator crop rendering for newly actionable review cases."""
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
    try:
        materialize_team_attribution_evidence(match_path)
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        logger.warning(
            "review_workflow team_attribution_evidence_render_failed match=%s error=%s",
            match_doc.get("id") or match_path.name,
            type(exc).__name__,
        )


def finalize_review_for_qa(
    match_path: Path,
    match_doc: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    # One coherent authoritative materialization scope.  The cheap preflight
    # reads only durable compact artifacts; the authoritative recomputation
    # below remains the final safety gate and may still reject finalize when
    # canonical sources changed since the last review refresh.
    with review_build_context():
        state = build_cheap_finalize_preflight_state(match_path, match_doc)
        assert_workflow_action_allowed(state, "finalize_identity")
        preflight_ms = _elapsed_ms(started)
        refreshed = refresh_review_after_identity_mutation(
            match_path,
            match_doc,
            source="finalize",
            operator_evidence=False,
            leave_hot_state_warm=True,
        )
        state = refreshed["workflow"]
        if state["issues"].get("overall_identity_blocked") or state["issues"].get("blocking"):
            # The recompute discovered (possibly new) blockers: operators are
            # returned to review and need evidence for those actionable cases.
            evidence_started = time.perf_counter()
            _materialize_operator_evidence(match_path, match_doc)
            evidence_ms = _elapsed_ms(evidence_started)
            code = (
                "identity_coverage_unresolved_without_reviewable_evidence"
                if state["issues"].get("coverage_readiness_blocked")
                and not state["issues"].get("blocking")
                else "identity_issues_remaining"
            )
            error = WorkflowActionError(code, state, "finalize_identity")
            error.performance = {  # type: ignore[attr-defined]
                **(refreshed.get("performance") or {}),
                "preflight_workflow_ms": preflight_ms,
                "operator_evidence_ms": evidence_ms,
                "total_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            raise error
        # The freshly built snapshot IS the authoritative status of this request;
        # re-deriving it from disk would re-parse every large source artifact.
        snapshot = refreshed["snapshot"]
        stats_started = time.perf_counter()
        build_reviewed_stats(match_path, snapshot, match_doc, load_json_object(match_path / "pitch_config.json"))
        stats_ms = _elapsed_ms(stats_started)
        job_started = time.perf_counter()
        job = generate_reviewed_output(
            match_path,
            snapshot,
            match_doc,
            {**DEFAULT_RENDER_OPTIONS, **(options or {})},
            stats_already_current=True,
        )
        render_submit_ms = _elapsed_ms(job_started)
    workflow_started = time.perf_counter()
    # Same-transaction reuse: stats and the queued job are the only durable
    # changes since refresh; snapshot/progress/evidence stay authoritative.
    workflow = get_review_workflow_state(
        match_path,
        match_doc,
        snapshot=snapshot,
        progress=refreshed.get("review_progress"),
        completion_evidence=refreshed.get("completion_evidence"),
    )
    final_workflow_ms = _elapsed_ms(workflow_started)
    logger.info(
        "review_workflow action=finalize match=%s render_queued=%s fingerprint=%s",
        match_doc.get("id") or match_path.name,
        job.get("status") in {"queued", "running"},
        str(snapshot.get("semantic_digest") or "")[:12],
    )
    performance = {
        **(refreshed.get("performance") or {}),
        "preflight_workflow_ms": preflight_ms,
        "stats_ms": stats_ms,
        "render_submit_ms": render_submit_ms,
        "final_workflow_ms": final_workflow_ms,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    return {
        "workflow": workflow,
        "reviewed_identity": public_finalized_identity(snapshot),
        "render_job": job,
        "performance": performance,
    }


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
    retry_started = time.perf_counter()
    preflight_started = time.perf_counter()
    state = build_compact_review_workflow_state(match_path, match_doc)
    assert_workflow_action_allowed(state, "retry_review_recompute")
    retry_preflight_ms = _elapsed_ms(preflight_started)
    technical_evidence_retry = _is_evidence_only_technical_retry(state)
    lifecycle_migration = _is_team_attribution_evidence_lifecycle_migration(state)
    # Retry is the boundary between an old operator projection and the next
    # one. Commit and warm the exact same generation before returning it so an
    # immediate Required offset-0 GET cannot observe a different queue.
    refresh_started = time.perf_counter()
    refreshed = refresh_review_after_identity_mutation(
        match_path,
        match_doc,
        source="retry",
        operator_evidence=_retry_requires_global_operator_evidence(state),
        leave_hot_state_warm=True,
        reuse_current_snapshot=_retry_can_reuse_current_snapshot(state),
        retry_technical_team_attribution_evidence=(
            technical_evidence_retry or lifecycle_migration
        ),
    )
    performance = dict(refreshed.get("performance") or {})
    performance.update({
        "retry_preflight_ms": retry_preflight_ms,
        "retry_refresh_ms": _elapsed_ms(refresh_started),
        "endpoint_total_ms": _elapsed_ms(retry_started),
    })
    return {**refreshed, "performance": performance}


def _retry_requires_global_operator_evidence(state: dict[str, Any]) -> bool:
    """Keep policy migration retry bounded without weakening other retries.

    A policy-version mismatch has current canonical decisions; it needs a new
    projection, not a full crop regeneration.  The existing focused recovery
    below still materializes exact Team-U sources when no normal queue exists.
    Other retry reasons retain the legacy broad-evidence behavior explicitly.
    """
    reason = str((state.get("freshness") or {}).get("review_progress_reason") or "")
    return (
        reason != "review_progress_policy_stale"
        and not _is_evidence_only_technical_retry(state)
        and not _is_team_attribution_evidence_lifecycle_migration(state)
    )


def _retry_can_reuse_current_snapshot(state: dict[str, Any]) -> bool:
    """Reuse only a durably current snapshot for evidence-only recovery."""
    freshness = state.get("freshness") or {}
    if not bool(freshness.get("reviewed_identity_current")):
        return False
    return (
        str(freshness.get("review_progress_reason") or "")
        == "review_progress_policy_stale"
        or _is_team_attribution_evidence_lifecycle_migration(state)
        or _is_evidence_only_technical_retry(state)
    )


def _is_team_attribution_evidence_lifecycle_migration(state: dict[str, Any]) -> bool:
    """Recognize the one bounded retry for the exact-evidence lifecycle fix."""
    freshness = state.get("freshness") or {}
    return (
        str(freshness.get("review_progress_reason") or "")
        == "review_progress_team_attribution_evidence_lifecycle_stale"
        and bool(freshness.get("reviewed_identity_current"))
    )


def _is_evidence_only_technical_retry(state: dict[str, Any]) -> bool:
    """Recognize the bounded retry that repairs only known evidence artifacts."""
    issues = state.get("issues") or {}
    freshness = state.get("freshness") or {}
    return (
        bool(issues.get("team_attribution_evidence_technical_failure"))
        and int(issues.get("normal_blocking") or 0) == 0
        and int(issues.get("mixed_blocking") or 0) == 0
        and bool(freshness.get("review_progress_current"))
        and bool(freshness.get("reviewed_identity_current"))
    )


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
