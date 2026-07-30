from __future__ import annotations

"""Isolated, operator-driven IA0–IA6 product-flow benchmark sessions.

The workspaces intentionally contain fresh operator seed stores.  Historical
whole-subject decisions are never copied into them; they may only be referenced
by a separately generated evaluation report after an operator completes a run.
"""

from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from app.services.identity_initial_audit import (
    AUDIT_DIRECTORY,
    FRAME_DIRECTORY,
    SELECTION_FILENAME,
    build_initial_identity_audit_document,
    export_identity_audit_frames,
)
from app.services.identity_initial_audit_frame_selection import (
    ALGORITHM_NAME as IA0_ALGORITHM_NAME,
    DEFAULT_PARAMETERS as IA0_DEFAULT_PARAMETERS,
    filter_identity_audit_observations,
)
from app.services.identity_initial_audit_store import save_initial_identity_audit_seeds
from app.services.identity_approved_appearance_gallery import (
    build_identity_approved_appearance_gallery,
)
from app.services.identity_approved_appearance_reid import (
    load_approved_appearance_embedder,
)
from app.services.identity_cross_analysis_appearance_reid import (
    build_cross_analysis_appearance_reid,
)
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_product_flow_state import (
    fail_product_flow_session,
    load_product_flow_session,
    retry_failed_product_flow_session,
    transition_product_flow_session,
    write_json_atomic,
)
from app.services.identity_seeded_candidate_assignments import (
    rebuild_identity_seeded_candidate_assignments,
)
from app.services.identity_seeded_subject_review_rebuild import (
    rebuild_identity_seeded_subject_review,
    seeded_assignments_as_roster_assignments,
)
from app.services.identity_same_match_reid import JsonEmbeddingCache
from app.services.identity_roster_anchor_crop_renderer import (
    render_identity_roster_anchor_crops,
)
from app.services.identity_roster_anchor_crops_shadow import (
    build_identity_roster_anchor_crops_shadow,
)
from app.services.identity_roster_anchor_shadow import (
    build_identity_roster_anchor_shadow,
)
from app.services.identity_second_half_reanchor import prepare_second_half_identity_reanchor
from app.services.identity_second_half_reanchor import build_second_half_identity_reanchor_document, REANCHOR_DIRECTORY, SELECTION_FILENAME as REANCHOR_SELECTION_FILENAME, FRAME_DIRECTORY as REANCHOR_FRAME_DIRECTORY
from app.services.identity_second_half_reanchor_store import (
    save_second_half_identity_reanchor_seeds,
)


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


def _reduction_error(stage: str, reason: str, *, missing: bool) -> ProductFlowBenchmarkError:
    code = f"{stage.upper()}_REDUCTION_REPORT_{'MISSING' if missing else 'INVALID'}"
    return ProductFlowBenchmarkError(f"{code}: {reason}")


def prepare_product_flow_benchmark(
    *,
    matches_root: Path,
    benchmark_root: Path,
    source_match_id: str,
    target_match_id: str,
    benchmark_id: str,
) -> dict[str, Any]:
    """Atomically publish H1 only; H2 cannot exist before H1 is rebuilt."""
    source = matches_root / source_match_id
    target = matches_root / target_match_id
    if not source.exists() or not target.exists():
        raise ProductFlowBenchmarkError("Requested H1/H2 analysis is missing")
    source_meta = _load(source / "match.json")
    target_meta = _load(target / "match.json")
    h1_source = _latest_run_path(source, source_meta)
    pair = _validate_pair(
        source_meta,
        target_meta,
        source_path=h1_source,
        target_path=target,
    )
    _require(h1_source, REQUIRED_H1_ARTIFACTS)
    _require(target, REQUIRED_H2_ARTIFACTS)
    root = benchmark_root / benchmark_id
    if root.exists():
        raise ProductFlowBenchmarkError(f"Benchmark already exists: {benchmark_id}")
    benchmark_root.mkdir(parents=True, exist_ok=True)
    temporary_root = benchmark_root / (
        f".{benchmark_id}.tmp-{uuid.uuid4().hex}"
    )
    published = False
    created_aliases: list[Path] = []
    try:
        temporary_root.mkdir()
        created_at = datetime.now(timezone.utc).isoformat()
        initial = {
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "state": "CREATING",
            "status": "CREATING",
            "created_at": created_at,
            "updated_at": created_at,
            "benchmark_id": benchmark_id,
            "physical_match": pair,
            "source_locations": {
                "h1_match": str(source.resolve()),
                "h1_artifacts": str(h1_source.resolve()),
                "h2_match": str(target.resolve()),
                "h2_artifacts": str(target.resolve()),
            },
            "source_inventory": {
                "h1": _source_inventory(
                    match_path=source,
                    artifact_path=h1_source,
                    required=REQUIRED_H1_ARTIFACTS,
                ),
                "h2": _source_inventory(
                    match_path=target,
                    artifact_path=target,
                    required=REQUIRED_H2_ARTIFACTS,
                ),
            },
            "workspaces": {"h1": None, "h2": None},
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
            "audit_log": [],
            "safety_contract": {
                "automatic_assignments_allowed": False,
                "production_apply_allowed": False,
                "source_mutations_allowed": False,
            },
        }
        write_json_atomic(
            temporary_root / "benchmark_session.json",
            initial,
        )
        h1_workspace = temporary_root / "h1_workspace"
        _create_workspace(
            h1_workspace,
            source_meta,
            h1_source,
            benchmark_id,
            "H1",
        )
        _build_h1_shadow_artifacts(h1_workspace)
        h1_meta = _load(h1_workspace / "match.json")
        h1_audit = _prepare_h1_audit(h1_workspace, h1_meta)
        initial["workspaces"]["h1"] = _workspace_descriptor(
            h1_workspace,
            source_match_id,
            "H1",
            h1_audit,
        )
        write_json_atomic(
            temporary_root / "benchmark_session.json",
            initial,
        )
        ready = transition_product_flow_session(
            temporary_root,
            "H1_READY",
            action="prepare_h1",
            details={
                "selected_frames": int(
                    (h1_audit.get("summary") or {}).get(
                        "selected_frames"
                    )
                    or 0
                )
            },
        )
        temporary_root.replace(root)
        published = True
        h1_alias = matches_root / str(ready["workspaces"]["h1"]["match_id"])
        _publish_alias(h1_alias, root / "h1_workspace")
        created_aliases.append(h1_alias)
        return load_product_flow_session(root)
    except Exception as exc:
        if published and (root / "benchmark_session.json").exists():
            fail_product_flow_session(
                root,
                action="create_benchmark_failed",
                error=exc,
            )
        for alias in created_aliases:
            if alias.is_symlink():
                alias.unlink()
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
        if isinstance(exc, ProductFlowBenchmarkError):
            raise
        raise ProductFlowBenchmarkError(str(exc)) from exc


def build_product_flow_benchmark_report(root: Path) -> dict[str, Any]:
    manifest = load_product_flow_session(root)
    domain_rows = {
        label: _real_domain_metrics(root, label)
        for label in ("h1", "h2")
        if (root / f"{label}_workspace").exists()
    }
    mutations = _source_inventory_mutations(manifest)
    events = manifest.get("audit_log") or []
    reid = _load_optional(
        root
        / "h2_workspace"
        / "identity_cross_analysis_reid_advisory.json"
    )
    reid_metrics = _reid_metrics(root, advisory=reid or {})
    h1_lineage_metrics = _h1_lineage_metrics(root)
    operator_findings = _load_optional(
        root / "benchmark_operator_findings.json"
    )
    automatic_assignments = sum(
        int(
            ((reid or {}).get("safety") or {}).get("automatic_merges")
            or 0
        )
        for _ in [0]
    )
    production_applies = sum(
        str(event.get("action") or "") == "production_apply"
        for event in events
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "status": manifest["state"],
        "benchmark_id": manifest["benchmark_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "h1": domain_rows.get("h1"),
        "h2": domain_rows.get("h2"),
        "reid": {
            **reid_metrics,
            "false_assignments": None,
        },
        "h1_safe_lineage": h1_lineage_metrics,
        "operator_findings": operator_findings,
        "conflicts": sum(
            int((row or {}).get("conflicts") or 0)
            for row in domain_rows.values()
        ),
        "safety": {
            "automatic_assignments": automatic_assignments,
            "production_apply_count": production_applies,
            "source_artifact_mutations": len(mutations),
            "source_mutation_details": mutations,
            "yolo_reruns": sum(
                str(event.get("action") or "") == "yolo_rerun"
                for event in events
            ),
            "tracking_reruns": sum(
                str(event.get("action") or "") == "tracking_rerun"
                for event in events
            ),
        },
        "limitations": [
            "False operator assignments require independent ground truth and are not inferred.",
            "Historical manual assignments are not operator actions in this benchmark.",
        ],
    }


def finish_product_flow_h1(
    *,
    root: Path,
    matches_root: Path,
) -> dict[str, Any]:
    """Finish H1, rebuild real shadow artifacts, then and only then publish H2."""

    session = load_product_flow_session(root)
    if session["state"] in {
        "H2_READY",
        "H2_FINISHED",
        "REPORT_READY",
    }:
        return session
    if session["state"] not in {"H1_READY", "H1_FINISHED", "H1_REBUILT"}:
        raise ProductFlowBenchmarkError(
            f"H1 cannot finish from state {session['state']}"
        )
    h2_temporary = root / f".h2_workspace.tmp-{uuid.uuid4().hex}"
    h2_alias: Path | None = None
    try:
        h1_workspace = root / "h1_workspace"
        h1_meta = _load(h1_workspace / "match.json")
        if session["state"] == "H1_READY":
            save_initial_identity_audit_seeds(
                h1_workspace,
                h1_meta,
                [],
                telemetry_events=[
                    _finish_telemetry_event("H1", session["benchmark_id"])
                ],
            )
            session = transition_product_flow_session(
                root,
                "H1_FINISHED",
                action="operator_finish_h1",
                details=_seed_metrics(
                    h1_workspace / "identity_operator_seeds.json"
                ),
            )
        if session["state"] == "H1_FINISHED":
            seeded = rebuild_identity_seeded_candidate_assignments(
                h1_workspace,
                h1_meta,
            )
            _remove_previous_reduction_report(h1_workspace)
            rebuilt = rebuild_identity_seeded_subject_review(
                h1_workspace,
                h1_meta,
                video_path=h1_workspace / "video.mp4",
            )
            gallery = _rebuild_h1_approved_appearance_gallery(h1_workspace)
            reduction, reduction_summary, reduction_digest = (
                _validate_current_reduction_report(
                    h1_workspace,
                    seeded,
                    stage="H1",
                )
            )
            safely_resolved = _safely_resolved_players(seeded)
            write_json_atomic(
                h1_workspace / "benchmark_h1_rebuild_result.json",
                {
                    "seeded_summary": seeded.get("summary") or {},
                    "rebuild": rebuilt,
                    "reduction_report_digest": reduction_digest,
                    "reduction_report_source": (
                        reduction.get("source") or {}
                    ),
                    "safely_resolved_players": safely_resolved,
                },
            )
            session = transition_product_flow_session(
                root,
                "H1_REBUILT",
                action="rebuild_h1_downstream",
                details={
                    "safely_resolved_players": len(safely_resolved),
                    "review_reduction": (
                        reduction_summary
                    ),
                    "appearance_gallery": (
                        gallery.get("summary") or {}
                    ),
                    "appearance_reid": (
                        rebuilt.get("approved_appearance_reid_summary")
                        or {}
                    ),
                },
            )
        if session["state"] == "H1_REBUILT":
            locations = session["source_locations"]
            target = Path(str(locations["h2_match"]))
            target_artifacts = Path(str(locations["h2_artifacts"]))
            target_meta = _load(target / "match.json")
            _create_workspace(
                h2_temporary,
                target_meta,
                target_artifacts,
                str(session["benchmark_id"]),
                "H2",
            )
            _write_h2_phase_config(h2_temporary)
            advisory = _build_h2_cross_analysis_advisory(
                h1_workspace=h1_workspace,
                h2_workspace=h2_temporary,
                h2_match_document=_load(h2_temporary / "match.json"),
            )
            h2_reanchor = _prepare_h2_reanchor(
                h2_temporary,
                _load(h2_temporary / "match.json"),
                safely_resolved_players=_load(
                    h1_workspace / "benchmark_h1_rebuild_result.json"
                ).get("safely_resolved_players")
                or [],
                advisory_suggestions=advisory.get("suggestions") or [],
            )
            h2_workspace = root / "h2_workspace"
            h2_temporary.replace(h2_workspace)
            session["workspaces"]["h2"] = _workspace_descriptor(
                h2_workspace,
                str(
                    session["physical_match"]["target_match_id"]
                ),
                "H2",
                h2_reanchor,
            )
            write_json_atomic(root / "benchmark_session.json", session)
            session = transition_product_flow_session(
                root,
                "H2_READY",
                action="prepare_h2_from_h1_output",
                details={
                    "h1_safely_resolved_players": len(
                        _load(
                            h1_workspace
                            / "benchmark_h1_rebuild_result.json"
                        ).get("safely_resolved_players")
                        or []
                    ),
                    "reid_ranked_subjects": int(
                        (advisory.get("summary") or {}).get(
                            "ranked_subjects"
                        )
                        or 0
                    ),
                    "automatic_assignments": int(
                        (advisory.get("safety") or {}).get(
                            "automatic_merges"
                        )
                        or 0
                    ),
                },
            )
            h2_alias = matches_root / str(
                session["workspaces"]["h2"]["match_id"]
            )
            _publish_alias(h2_alias, h2_workspace)
        mutations = _source_inventory_mutations(session)
        if mutations:
            raise ProductFlowBenchmarkError(
                "Frozen source artifacts changed: "
                + ", ".join(row["artifact"] for row in mutations)
            )
        return load_product_flow_session(root)
    except Exception as exc:
        if h2_temporary.exists():
            shutil.rmtree(h2_temporary)
        if h2_alias is not None and h2_alias.is_symlink():
            h2_alias.unlink()
        if (root / "benchmark_session.json").exists():
            fail_product_flow_session(
                root,
                action="finish_h1_failed",
                error=exc,
            )
        if isinstance(exc, ProductFlowBenchmarkError):
            raise
        raise ProductFlowBenchmarkError(str(exc)) from exc


def finish_product_flow_h2(
    *,
    root: Path,
) -> dict[str, Any]:
    """Finish H2, rebuild final shadow artifacts and publish the real report."""

    session = load_product_flow_session(root)
    if session["state"] == "REPORT_READY":
        return _load(root / "benchmark_report.json")
    if session["state"] == "FAILED":
        session = retry_failed_product_flow_session(
            root,
            expected_previous_state="H2_FINISHED",
            action="retry_finish_h2",
        )
    if session["state"] not in {"H2_READY", "H2_FINISHED"}:
        raise ProductFlowBenchmarkError(
            f"H2 cannot finish from state {session['state']}"
        )
    try:
        h2_workspace = root / "h2_workspace"
        h2_meta = _load(h2_workspace / "match.json")
        if session["state"] == "H2_READY":
            save_second_half_identity_reanchor_seeds(
                h2_workspace,
                h2_meta,
                [],
                telemetry_events=[
                    _finish_telemetry_event("H2", session["benchmark_id"])
                ],
            )
            session = transition_product_flow_session(
                root,
                "H2_FINISHED",
                action="operator_finish_h2",
                details=_seed_metrics(
                    h2_workspace
                    / REANCHOR_DIRECTORY
                    / "identity_second_half_reanchor_seeds.json"
                ),
            )
        seeded = rebuild_identity_seeded_candidate_assignments(
            h2_workspace,
            h2_meta,
        )
        _remove_previous_reduction_report(h2_workspace)
        rebuilt = rebuild_identity_seeded_subject_review(
            h2_workspace,
            h2_meta,
            video_path=h2_workspace / "video.mp4",
        )
        reduction, _reduction_summary, reduction_digest = (
            _validate_current_reduction_report(
                h2_workspace,
                seeded,
                stage="H2",
            )
        )
        write_json_atomic(
            h2_workspace / "benchmark_h2_rebuild_result.json",
            {
                "seeded_summary": seeded.get("summary") or {},
                "rebuild": rebuilt,
                "reduction_report_digest": reduction_digest,
                "reduction_report_source": (
                    reduction.get("source") or {}
                ),
                "safely_resolved_players": _safely_resolved_players(seeded),
            },
        )
        report = build_product_flow_benchmark_report(root)
        safety = report["safety"]
        if (
            int(safety["automatic_assignments"]) != 0
            or int(safety["production_apply_count"]) != 0
            or int(safety["source_artifact_mutations"]) != 0
        ):
            raise ProductFlowBenchmarkError(
                "Benchmark safety invariants failed"
            )
        session = transition_product_flow_session(
            root,
            "REPORT_READY",
            action="publish_final_report",
            details={
                "automatic_assignments": safety["automatic_assignments"],
                "production_apply_count": safety["production_apply_count"],
                "source_artifact_mutations": safety[
                    "source_artifact_mutations"
                ],
            },
        )
        report = build_product_flow_benchmark_report(root)
        write_json_atomic(root / "benchmark_report.json", report)
        return report
    except Exception as exc:
        report_path = root / "benchmark_report.json"
        if report_path.exists():
            report_path.unlink()
        if (root / "benchmark_session.json").exists():
            fail_product_flow_session(
                root,
                action="finish_h2_failed",
                error=exc,
            )
        if isinstance(exc, ProductFlowBenchmarkError):
            raise
        raise ProductFlowBenchmarkError(str(exc)) from exc


def _build_h2_cross_analysis_advisory(
    *,
    h1_workspace: Path,
    h2_workspace: Path,
    h2_match_document: dict[str, Any],
) -> dict[str, Any]:
    candidate = _load(h2_workspace / "identity_candidate_shadow.json")
    timeline = _load(
        h2_workspace / "identity_offline_shadow_timeline.json"
    )
    empty_assignments = {
        "schema_version": SCHEMA_VERSION,
        "assignments": [],
    }
    roster_documents = build_identity_roster_anchor_shadow(
        candidate,
        empty_assignments,
        h2_match_document,
    )
    roster = roster_documents["identity_roster_anchor_shadow"]
    crop_documents = build_identity_roster_anchor_crops_shadow(
        roster,
        timeline,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    crops = crop_documents["identity_roster_anchor_crops_shadow"]
    for name, document in {**roster_documents, **crop_documents}.items():
        write_json_atomic(h2_workspace / f"{name}.json", document)
    render_identity_roster_anchor_crops(
        h2_workspace / "video.mp4",
        h2_workspace,
        crops,
    )
    gallery = _load_optional(
        h1_workspace / "identity_approved_appearance_gallery.json"
    ) or {
        "players": [],
        "algorithm": {"name": "unavailable_h1_gallery"},
    }
    try:
        embedder, model_status = load_approved_appearance_embedder(
            Path(__file__).resolve().parents[2] / "models"
        )
    except Exception as exc:
        embedder = None
        model_status = {
            "available": False,
            "reason": "appearance_embedder_load_failed",
            "error": str(exc),
        }
    selected_subject_ids = _h2_reanchor_candidate_subject_ids(
        h2_workspace,
        candidate,
    )
    selected_crops = {
        **crops,
        "cards": [
            card
            for card in crops.get("cards") or []
            if str(card.get("candidate_subject_id") or "")
            in selected_subject_ids
        ],
    }
    reference_cache = target_cache = None
    if embedder is not None:
        cache_kwargs = {
            "model_name": str(embedder.model_name),
            "model_version": str(embedder.model_version),
            "embedding_dimension": int(embedder.embedding_dimension),
        }
        reference_cache = JsonEmbeddingCache.load(
            h1_workspace / "cross_analysis_appearance_embeddings_cache.json",
            **cache_kwargs,
        )
        target_cache = JsonEmbeddingCache.load(
            h2_workspace / "cross_analysis_appearance_embeddings_cache.json",
            **cache_kwargs,
        )
    documents = build_cross_analysis_appearance_reid(
        gallery,
        selected_crops,
        empty_assignments,
        reference_match_path=h1_workspace,
        target_match_path=h2_workspace,
        embedder=embedder,
        model_status=model_status,
        reference_embedding_cache=reference_cache,
        target_embedding_cache=target_cache,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    artifact = documents["identity_cross_analysis_appearance_reid"]
    rankings = artifact.get("unresolved_rankings") or []
    ranking_display = artifact.get("ranking_display") or {}
    display_eligible = bool(ranking_display.get("display_eligible"))
    subject_tracklets = {
        str(row.get("candidate_subject_id") or ""): sorted(
            str(value) for value in row.get("tracklet_ids") or []
        )
        for row in candidate.get("subjects") or []
    }
    suggestions = [
        {
            "candidate_subject_id": row.get("candidate_subject_id"),
            "team_label": row.get("team_label"),
            "tracklet_ids": subject_tracklets.get(
                str(row.get("candidate_subject_id") or ""),
                [],
            ),
            "suggestions": [
                {
                    **suggestion,
                    "suggestion_source": (
                        "cross_analysis_reid_top3_advisory"
                    ),
                    "advisory_only": True,
                    "display_eligible": display_eligible,
                    "suppression_reason_codes": (
                        ranking_display.get("suppression_reason_codes") or []
                    ),
                    "candidate_subject_id": row.get(
                        "candidate_subject_id"
                    ),
                    "observation_key": None,
                }
                for suggestion in list(row.get("suggestions") or [])[:3]
            ],
            "advisory_only": True,
        }
        for row in rankings
        if row.get("status") == "ranked"
    ]
    artifact = {
        **artifact,
        "mode": "cross_analysis_h1_to_h2_advisory_only",
        "summary": {
            **(artifact.get("summary") or {}),
            "selected_h2_candidate_subjects": len(selected_subject_ids),
            "ranked_subjects": len(suggestions),
            "operator_visible_ranked_subjects": (
                len(suggestions) if display_eligible else 0
            ),
            "suggestions_shown": sum(
                len(row["suggestions"]) for row in suggestions
            ),
        },
        "suggestions": suggestions,
    }
    for name, document in documents.items():
        write_json_atomic(h2_workspace / f"{name}.json", document)
    write_json_atomic(
        h2_workspace / "identity_cross_analysis_reid_advisory.json",
        artifact,
    )
    return artifact


def _rebuild_h1_approved_appearance_gallery(
    workspace: Path,
) -> dict[str, Any]:
    """Recreate H1 reference crops from frozen observation quality fields.

    The legacy H1 adapter originally discarded confidence, pitch and visual
    trust data.  The crop selector correctly rejected the resulting empty
    placeholders, which in turn left the H1 appearance gallery empty.  This
    adapter preserves the frozen evidence and builds references only from
    operator-confirmed anchors.
    """

    seeded_path = workspace / "identity_seeded_candidate_assignments.json"
    if not seeded_path.exists():
        return {
            "summary": {
                "available": False,
                "reason": "seeded_assignments_not_persisted",
            }
        }
    candidate, timeline = _build_h1_shadow_artifacts(workspace)
    seeded = _load(seeded_path)
    roster_assignments = seeded_assignments_as_roster_assignments(
        candidate,
        seeded,
    )
    roster_documents = build_identity_roster_anchor_shadow(
        candidate,
        roster_assignments,
        _load(workspace / "match.json"),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    roster = roster_documents["identity_roster_anchor_shadow"]
    confirmed_cards = [
        card
        for card in roster.get("cards") or []
        if card.get("status") == "confirmed_manual_anchor"
    ]
    crop_documents = build_identity_roster_anchor_crops_shadow(
        {**roster, "cards": confirmed_cards},
        timeline,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    crops = crop_documents["identity_roster_anchor_crops_shadow"]
    for name, document in {
        **roster_documents,
        **crop_documents,
    }.items():
        write_json_atomic(workspace / f"{name}.json", document)
    render_identity_roster_anchor_crops(
        workspace / "video.mp4",
        workspace,
        crops,
    )
    gallery_documents = build_identity_approved_appearance_gallery(
        seeded,
        crops,
        match_phase_config_doc=_load_optional(
            workspace / "match_phase_config.json"
        ),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    for name, document in gallery_documents.items():
        write_json_atomic(workspace / f"{name}.json", document)
    return gallery_documents["identity_approved_appearance_gallery"]


def run_product_flow_cross_capture_reid_diagnostic(
    *,
    root: Path,
) -> dict[str, Any]:
    """Create post-benchmark ReID evidence without reopening an audit.

    The diagnostic uses frozen H1/H2 detections and already saved operator
    seeds.  It deliberately writes into a new diagnostic directory so the
    completed benchmark receipt remains historical evidence.
    """

    session = load_product_flow_session(root)
    if session["state"] != "REPORT_READY":
        raise ProductFlowBenchmarkError(
            "Cross-capture ReID diagnostic requires REPORT_READY"
        )
    h1_workspace = root / "h1_workspace"
    h2_workspace = root / "h2_workspace"
    diagnostic_root = root / "cross_capture_reid_diagnostic"
    h1_output = diagnostic_root / "h1"
    h2_output = diagnostic_root / "h2"
    h1_output.mkdir(parents=True, exist_ok=True)
    h2_output.mkdir(parents=True, exist_ok=True)

    h1_seeded = _load(
        h1_workspace / "identity_seeded_candidate_assignments.json"
    )
    confirmed_h1_subject_ids = {
        str(assignment.get("candidate_subject_id") or "")
        for assignment in h1_seeded.get("accepted_assignments") or []
        if assignment.get("candidate_subject_id")
        and (assignment.get("assigned_player") or {}).get("player_id")
    }
    h1_candidate, h1_timeline = _build_h1_shadow_artifacts(
        h1_workspace,
        output_root=h1_output,
        subject_ids=confirmed_h1_subject_ids,
    )
    h1_match = _load(h1_workspace / "match.json")
    h1_assignments = seeded_assignments_as_roster_assignments(
        h1_candidate,
        h1_seeded,
    )
    h1_roster_documents = build_identity_roster_anchor_shadow(
        h1_candidate,
        h1_assignments,
        h1_match,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    h1_roster = h1_roster_documents["identity_roster_anchor_shadow"]
    confirmed_cards = [
        card
        for card in h1_roster.get("cards") or []
        if card.get("status") == "confirmed_manual_anchor"
    ]
    h1_crop_documents = build_identity_roster_anchor_crops_shadow(
        {**h1_roster, "cards": confirmed_cards},
        h1_timeline,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    h1_crops = h1_crop_documents["identity_roster_anchor_crops_shadow"]
    for name, document in {
        **h1_roster_documents,
        **h1_crop_documents,
    }.items():
        write_json_atomic(h1_output / f"{name}.json", document)
    rendered_h1 = render_identity_roster_anchor_crops(
        h1_workspace / "video.mp4",
        h1_output,
        h1_crops,
    )
    h1_gallery_documents = build_identity_approved_appearance_gallery(
        h1_seeded,
        h1_crops,
        match_phase_config_doc=_load_optional(
            h1_workspace / "match_phase_config.json"
        ),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    for name, document in h1_gallery_documents.items():
        write_json_atomic(h1_output / f"{name}.json", document)

    # The frozen H1 timeline is intentionally comprehensive (and large).  It
    # is no longer needed after crop selection, so release it before parsing
    # the separate H2 candidate/crop artifacts.
    del h1_timeline
    del h1_candidate
    gc.collect()

    h2_candidate = _load(h2_workspace / "identity_candidate_shadow.json")
    h2_crops = _load(
        h2_workspace / "identity_roster_anchor_crops_shadow.json"
    )
    selected_subject_ids = _h2_reanchor_candidate_subject_ids(
        h2_workspace,
        h2_candidate,
    )
    selected_h2_crops = {
        **h2_crops,
        "cards": [
            card
            for card in h2_crops.get("cards") or []
            if str(card.get("candidate_subject_id") or "")
            in selected_subject_ids
        ],
    }
    h2_seeded = _load(
        h2_workspace / "identity_seeded_candidate_assignments.json"
    )
    try:
        embedder, model_status = load_approved_appearance_embedder(
            Path(__file__).resolve().parents[2] / "models"
        )
    except Exception as exc:
        embedder = None
        model_status = {
            "available": False,
            "reason": "appearance_embedder_load_failed",
            "error": str(exc),
        }
    reference_cache = target_cache = None
    if embedder is not None:
        cache_kwargs = {
            "model_name": str(embedder.model_name),
            "model_version": str(embedder.model_version),
            "embedding_dimension": int(embedder.embedding_dimension),
        }
        reference_cache = JsonEmbeddingCache.load(
            h1_output / "appearance_embeddings_cache.json",
            **cache_kwargs,
        )
        target_cache = JsonEmbeddingCache.load(
            h2_output / "appearance_embeddings_cache.json",
            **cache_kwargs,
        )
    reid_documents = build_cross_analysis_appearance_reid(
        h1_gallery_documents["identity_approved_appearance_gallery"],
        selected_h2_crops,
        h2_seeded,
        reference_match_path=h1_output,
        target_match_path=h2_workspace,
        embedder=embedder,
        model_status=model_status,
        reference_embedding_cache=reference_cache,
        target_embedding_cache=target_cache,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    reid_artifact = reid_documents[
        "identity_cross_analysis_appearance_reid"
    ]
    ranking_display = reid_artifact.get("ranking_display") or {}
    display_eligible = bool(ranking_display.get("display_eligible"))
    subject_tracklets = {
        str(row.get("candidate_subject_id") or ""): sorted(
            str(value) for value in row.get("tracklet_ids") or []
        )
        for row in h2_candidate.get("subjects") or []
    }
    suggestions = [
        {
            "candidate_subject_id": row.get("candidate_subject_id"),
            "team_label": row.get("team_label"),
            "tracklet_ids": subject_tracklets.get(
                str(row.get("candidate_subject_id") or ""),
                [],
            ),
            "suggestions": [
                {
                    **suggestion,
                    "suggestion_source": (
                        "cross_analysis_reid_top3_advisory"
                    ),
                    "advisory_only": True,
                    "display_eligible": display_eligible,
                    "suppression_reason_codes": (
                        ranking_display.get("suppression_reason_codes") or []
                    ),
                    "candidate_subject_id": row.get(
                        "candidate_subject_id"
                    ),
                    "observation_key": None,
                }
                for suggestion in list(row.get("suggestions") or [])[:3]
            ],
            "advisory_only": True,
        }
        for row in reid_artifact.get("unresolved_rankings") or []
        if row.get("status") == "ranked"
    ]
    advisory = {
        **reid_artifact,
        "suggestions": suggestions,
        "summary": {
            **(reid_artifact.get("summary") or {}),
            "selected_h2_candidate_subjects": len(selected_subject_ids),
            "ranked_subjects": len(suggestions),
            "operator_visible_ranked_subjects": (
                len(suggestions) if display_eligible else 0
            ),
            "suggestions_shown": sum(
                len(row["suggestions"]) for row in suggestions
            ),
        },
    }
    for name, document in reid_documents.items():
        write_json_atomic(h2_output / f"{name}.json", document)
    write_json_atomic(
        diagnostic_root / "identity_cross_analysis_reid_advisory.json",
        advisory,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "mode": "product_flow_cross_capture_reid_diagnostic",
        "status": "ready",
        "source_benchmark_id": session["benchmark_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "h1": {
            "confirmed_cards": len(confirmed_cards),
            "rendered_crops": len(rendered_h1),
            "gallery": (
                h1_gallery_documents[
                    "identity_approved_appearance_gallery"
                ].get("summary")
                or {}
            ),
        },
        "h2": {
            "selected_candidate_subjects": len(selected_subject_ids),
            "advisory": advisory.get("summary") or {},
        },
        "safety": {
            "reran_yolo": False,
            "reran_tracking": False,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_merges": 0,
        },
    }
    write_json_atomic(diagnostic_root / "diagnostic_report.json", result)
    return result


def _h2_reanchor_candidate_subject_ids(
    h2_workspace: Path,
    candidate_document: dict[str, Any],
) -> set[str]:
    selection = _load_optional(
        h2_workspace / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME
    )
    if selection is None:
        selection = _bounded_h1_selection(
            _load(h2_workspace / "global_identity.json"),
            _load(h2_workspace / "analysis_report.json"),
            maximum=3,
            capture_domain="second_half_fragment",
            artifact_directory=REANCHOR_FRAME_DIRECTORY,
        )
    selected_tracklets = {
        str(row.get("tracklet_id") or "")
        for frame in selection.get("selected_frames") or []
        for row in frame.get("visible_detections") or []
        if row.get("tracklet_id")
    }
    return {
        str(row.get("candidate_subject_id") or "")
        for row in candidate_document.get("subjects") or []
        if selected_tracklets.intersection(
            {str(value) for value in row.get("tracklet_ids") or []}
        )
    }


def _safely_resolved_players(
    seeded: dict[str, Any],
) -> list[dict[str, Any]]:
    result = []
    for assignment in seeded.get("accepted_assignments") or []:
        player = assignment.get("assigned_player") or {}
        if not player.get("player_id"):
            continue
        result.append(
            {
                "player_id": player.get("player_id"),
                "player_name": player.get("player_name"),
                "team_label": assignment.get("team_label"),
                "candidate_subject_id": assignment.get(
                    "candidate_subject_id"
                ),
                "tracklet_ids": sorted(
                    str(value)
                    for value in assignment.get("tracklet_ids") or []
                ),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            str(row.get("team_label") or ""),
            str(row.get("player_id") or ""),
        ),
    )


def _finish_telemetry_event(
    stage: str,
    benchmark_id: str,
) -> dict[str, Any]:
    return {
        "event_id": f"benchmark:{benchmark_id}:{stage}:finish",
        "session_id": f"benchmark:{benchmark_id}:{stage}",
        "event_type": "session_finished",
        "active_delta_seconds": 0.0,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def _seed_metrics(path: Path) -> dict[str, Any]:
    document = _load(path)
    telemetry = document.get("operator_telemetry") or {}
    metrics = telemetry.get("metrics") or {}
    decisions = document.get("decisions") or []
    return {
        "operator_decisions": len(decisions),
        "active_decisions": sum(
            str(row.get("action") or "") != "skip" for row in decisions
        ),
        "skipped_decisions": sum(
            str(row.get("action") or "") == "skip" for row in decisions
        ),
        "active_operator_seconds": float(
            metrics.get("active_operator_seconds") or 0.0
        ),
    }


def _remove_previous_reduction_report(workspace: Path) -> None:
    path = workspace / "identity_seeded_review_reduction_report.json"
    if path.exists():
        path.unlink()


def _validate_current_reduction_report(
    workspace: Path,
    seeded: dict[str, Any],
    *,
    stage: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    path = workspace / "identity_seeded_review_reduction_report.json"
    if not path.exists():
        raise _reduction_error(
            stage,
            "current downstream rebuild did not create the artifact",
            missing=True,
        )
    try:
        document = _load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _reduction_error(
            stage,
            f"artifact cannot be read: {exc}",
            missing=False,
        ) from exc
    metrics = document.get("metrics")
    source = document.get("source")
    safety = document.get("safety")
    if (
        document.get("mode") != "seed_aware_review_reduction_shadow"
        or document.get("status") != "fresh"
        or not isinstance(metrics, dict)
        or not isinstance(source, dict)
        or not isinstance(safety, dict)
    ):
        raise _reduction_error(
            stage,
            "artifact contract, fresh status, metrics or safety is invalid",
            missing=False,
        )
    expected_seeded_digest = canonical_digest(seeded)
    if source.get("seeded_assignments_digest") != expected_seeded_digest:
        raise _reduction_error(
            stage,
            "artifact does not originate from current seeded assignments",
            missing=False,
        )
    if (
        safety.get("mutates_production_identity") is not False
        or safety.get("writes_shadow_review_state_only") is not True
    ):
        raise _reduction_error(
            stage,
            "artifact safety contract is invalid",
            missing=False,
        )
    return document, metrics, canonical_digest(document)


def _load_receipted_reduction_report(
    workspace: Path,
    *,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report_path = workspace / "identity_seeded_review_reduction_report.json"
    receipt_path = workspace / f"benchmark_{label}_rebuild_result.json"
    if not report_path.exists() or not receipt_path.exists():
        raise _reduction_error(
            label,
            "report or current rebuild receipt is missing",
            missing=True,
        )
    report = _load(report_path)
    receipt = _load(receipt_path)
    if (
        receipt.get("reduction_report_digest")
        != canonical_digest(report)
    ):
        raise _reduction_error(
            label,
            "report digest does not match the current rebuild receipt",
            missing=False,
        )
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise _reduction_error(
            label,
            "report metrics are missing",
            missing=False,
        )
    return report, metrics


def _real_domain_metrics(root: Path, label: str) -> dict[str, Any]:
    workspace = root / f"{label}_workspace"
    selection_path = (
        workspace / AUDIT_DIRECTORY / SELECTION_FILENAME
        if label == "h1"
        else workspace / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME
    )
    seed_path = (
        workspace / "identity_operator_seeds.json"
        if label == "h1"
        else workspace
        / REANCHOR_DIRECTORY
        / "identity_second_half_reanchor_seeds.json"
    )
    seeded_path = workspace / "identity_seeded_candidate_assignments.json"
    selection = _load_optional(selection_path) or {}
    seed_metrics = _seed_metrics(seed_path) if seed_path.exists() else {}
    seeded = _load_optional(seeded_path) or {}
    seeded_summary = seeded.get("summary") or {}
    _reduction, reduction_summary = _load_receipted_reduction_report(
        workspace,
        label=label,
    )
    return {
        "selected_frames": len(selection.get("selected_frames") or []),
        **seed_metrics,
        "finished": any(
            str(event.get("to_state") or "")
            in (
                "H1_FINISHED" if label == "h1" else "H2_FINISHED",
            )
            for event in load_product_flow_session(root).get("audit_log")
            or []
        ),
        "safely_resolved_players": int(
            seeded_summary.get("subjects_resolved_after_seeding") or 0
        ),
        "review_cards_before": int(
            reduction_summary.get("review_cards_before_seeding") or 0
        ),
        "review_cards_after": int(
            reduction_summary.get("review_cards_after_seeding") or 0
        ),
        "unresolved_subjects_before": int(
            seeded_summary.get("candidate_subjects") or 0
        ),
        "unresolved_subjects_after": int(
            seeded_summary.get("unresolved_subjects") or 0
        ),
        "conflicts": int(seeded_summary.get("conflicts_created") or 0),
    }


def _reid_metrics(
    root: Path,
    *,
    advisory: dict[str, Any],
) -> dict[str, Any]:
    h2 = root / "h2_workspace"
    seeds = _load_optional(
        h2
        / REANCHOR_DIRECTORY
        / "identity_second_half_reanchor_seeds.json"
    ) or {}
    selection = _load_optional(
        h2 / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME
    ) or {}
    generated_rows = [
        row
        for row in advisory.get("suggestions") or []
        if isinstance(row, dict)
    ]
    generated_suggestions = [
        suggestion
        for row in generated_rows
        for suggestion in row.get("suggestions") or []
        if (
            isinstance(suggestion, dict)
            and suggestion.get("suggestion_source")
            == "cross_analysis_reid_top3_advisory"
        )
    ]
    displayed_by_observation = {
        str(observation.get("observation_key") or ""): [
            suggestion
            for suggestion in observation.get("reid_suggestions") or []
            if (
                isinstance(suggestion, dict)
                and suggestion.get("suggestion_source")
                == "cross_analysis_reid_top3_advisory"
                and suggestion.get("advisory_only") is True
            )
        ]
        for frame in (
            build_second_half_identity_reanchor_document(
                selection,
                _load(h2 / "match.json"),
            ).get("frames")
            or []
        )
        for observation in frame.get("observations") or []
        if observation.get("reid_suggestions")
    }
    displayed_by_observation = {
        key: rows
        for key, rows in displayed_by_observation.items()
        if key and rows
    }
    accepted = 0
    rejected = 0
    skipped = 0
    accepted_ranks: list[int] = []
    processed_observations: set[str] = set()
    for decision in seeds.get("decisions") or []:
        observation_key = str(decision.get("observation_key") or "")
        if (
            not observation_key
            or observation_key in processed_observations
            or observation_key not in displayed_by_observation
        ):
            continue
        processed_observations.add(observation_key)
        suggestions = displayed_by_observation[observation_key]
        action = str(decision.get("action") or "")
        context = decision.get("suggestion_context") or {}
        if context.get("suggestion_source") == "h1_safe_lineage":
            continue
        if action == "skip":
            skipped += 1
            continue
        if action != "assign_roster_player":
            continue
        assigned = str(
            (decision.get("assigned_player") or {}).get("player_id") or ""
        )
        if (
            context.get("suggestion_source")
            == "cross_analysis_reid_top3_advisory"
        ):
            selected = next(
                (
                    suggestion
                    for suggestion in suggestions
                    if str(suggestion.get("player_id") or "") == assigned
                    and int(suggestion.get("rank") or 0)
                    == int(context.get("rank") or 0)
                ),
                None,
            )
            if selected is not None:
                accepted += 1
                accepted_ranks.append(int(selected.get("rank") or 0))
                continue
        if assigned:
            rejected += 1
    accepted_rank_counts = {
        str(rank): accepted_ranks.count(rank)
        for rank in sorted(set(accepted_ranks))
    }
    return {
        "reid_subjects_available": len(
            {
                str(row.get("candidate_subject_id") or "")
                for row in generated_rows
                if row.get("candidate_subject_id")
            }
        ),
        "reid_suggestions_generated": len(generated_suggestions),
        "reid_suggestions_displayed": sum(
            len(rows) for rows in displayed_by_observation.values()
        ),
        "reid_observations_displayed": len(displayed_by_observation),
        "reid_suggestions_accepted": accepted,
        "reid_suggestions_rejected": rejected,
        "reid_suggestions_skipped": skipped,
        "accepted_ranks": sorted(accepted_ranks),
        "accepted_rank_counts": accepted_rank_counts,
    }


def _h1_lineage_metrics(root: Path) -> dict[str, Any]:
    h2 = root / "h2_workspace"
    selection = _load_optional(
        h2 / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME
    )
    match_document = _load_optional(h2 / "match.json")
    seeds = _load_optional(
        h2
        / REANCHOR_DIRECTORY
        / "identity_second_half_reanchor_seeds.json"
    )
    if not selection or not match_document:
        return {
            "suggestions_displayed": 0,
            "named_decisions_reviewed": 0,
            "suggestions_accepted": 0,
            "suggestions_rejected": 0,
            "suggestions_unreviewed": 0,
            "top1_accuracy_on_named_decisions": None,
        }
    displayed = {
        str(observation.get("observation_key") or ""): str(
            (observation.get("suggested_player") or {}).get(
                "player_id"
            )
            or ""
        )
        for frame in (
            build_second_half_identity_reanchor_document(
                selection,
                match_document,
            ).get("frames")
            or []
        )
        for observation in frame.get("observations") or []
        if (
            (observation.get("suggested_player") or {}).get(
                "suggestion_source"
            )
            == "h1_safe_lineage"
        )
    }
    accepted = 0
    rejected = 0
    reviewed_keys: set[str] = set()
    for decision in (seeds or {}).get("decisions") or []:
        observation_key = str(decision.get("observation_key") or "")
        if (
            observation_key not in displayed
            or observation_key in reviewed_keys
            or decision.get("action") != "assign_roster_player"
        ):
            continue
        assigned_player_id = str(
            (decision.get("assigned_player") or {}).get("player_id")
            or ""
        )
        if not assigned_player_id:
            continue
        reviewed_keys.add(observation_key)
        if assigned_player_id == displayed[observation_key]:
            accepted += 1
        else:
            rejected += 1
    reviewed = accepted + rejected
    return {
        "suggestions_displayed": len(displayed),
        "named_decisions_reviewed": reviewed,
        "suggestions_accepted": accepted,
        "suggestions_rejected": rejected,
        "suggestions_unreviewed": max(0, len(displayed) - reviewed),
        "top1_accuracy_on_named_decisions": (
            accepted / reviewed if reviewed else None
        ),
    }


def _build_h1_shadow_artifacts(
    workspace: Path,
    *,
    output_root: Path | None = None,
    subject_ids: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = _load(workspace / "analysis_report.json")
    global_identity = _load(workspace / "global_identity.json")
    fps = max(1.0, float((report.get("video") or {}).get("fps") or 30.0))
    subjects = []
    timeline_subjects = []
    for slot in global_identity.get("slots") or []:
        subject_id = str(slot.get("stable_subject_id") or "")
        if not subject_id or (
            subject_ids is not None and subject_id not in subject_ids
        ):
            continue
        positions = [
            _frozen_h1_timeline_observation(row, slot=slot)
            for row in slot.get("overlay_positions") or []
            if row.get("bbox_xyxy")
        ]
        detected = [row for row in positions if row["status"] == "detected"]
        tracklet_ids = [str(value) for value in slot.get("tracklet_ids") or []]
        subjects.append({"candidate_subject_id": subject_id, "team_label": slot.get("team_label"), "role": "field_player", "tracklet_ids": tracklet_ids, "production_subject_ids": [subject_id], "start_frame": int(slot.get("slot_spawn_frame") or 0), "end_frame": max((row["frame"] for row in positions), default=0), "detected_frames": len(detected), "quality_flags": [], "requires_review": True})
        timeline_subjects.append({"shadow_subject_id": subject_id, "team_label": slot.get("team_label"), "tracklet_ids": tracklet_ids, "start_frame": int(slot.get("slot_spawn_frame") or 0), "end_frame": max((row["frame"] for row in positions), default=0), "observations": positions})
    candidate = {"schema_version": "0.1.0", "mode": "frozen_h1_lineage_candidate_shadow", "algorithm": {"name": "frozen_h1_slot_adapter", "version": "0.2.0"}, "subjects": subjects, "summary": {"candidate_subjects": len(subjects)}, "safety": {"mutates_production_identity": False, "eligible_for_player_stats": False}}
    timeline = {"schema_version": "0.1.0", "mode": "frozen_h1_lineage_candidate_shadow", "algorithm": candidate["algorithm"], "subjects": timeline_subjects, "summary": {"subjects": len(timeline_subjects), "fps": fps}}
    target = output_root or workspace
    target.mkdir(parents=True, exist_ok=True)
    _write(target / "identity_candidate_shadow.json", candidate)
    _write(target / "identity_offline_shadow_timeline.json", timeline)
    return candidate, timeline


def _frozen_h1_timeline_observation(
    row: dict[str, Any],
    *,
    slot: dict[str, Any],
) -> dict[str, Any]:
    """Preserve available frozen visual-quality evidence for crop selection."""

    status = str(row.get("status") or "detected")
    visual_trusted = row.get("visual_trusted") is not False
    pitch = row.get("pitch_m") or []
    has_pitch_position = (
        isinstance(pitch, list)
        and len(pitch) == 2
        and all(isinstance(value, (int, float)) for value in pitch)
    )
    detected = status == "detected"
    return {
        "frame": int(row.get("frame") or 0),
        "time_sec": float(row.get("time_sec") or 0.0),
        "status": status,
        "tracklet_id": row.get("tracklet_id"),
        "bbox_xyxy": row.get("bbox_xyxy"),
        "confidence": float(
            row.get("confidence")
            or slot.get("mean_detection_confidence")
            or 0.0
        ),
        "team_confidence": float(slot.get("team_confidence") or 0.0),
        "visual_trusted": visual_trusted,
        "appearance_reliable": detected and visual_trusted,
        "appearance_reliable_ratio": 1.0 if visual_trusted else 0.0,
        "footpoint_reliable": detected and visual_trusted and has_pitch_position,
        "play_area_status": (
            "inside_play"
            if detected and visual_trusted and has_pitch_position
            else "unknown"
        ),
        "quality_class": "trusted" if visual_trusted else "untrusted",
        "quality_provenance": "frozen_global_identity_adapter",
    }


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


def _prepare_h2_reanchor(
    workspace: Path,
    match_document: dict[str, Any],
    *,
    safely_resolved_players: list[dict[str, Any]],
    advisory_suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    capture_domain = str(
        (match_document.get("benchmark_session") or {}).get(
            "capture_domain"
        )
        or f"analysis:{match_document.get('id')}"
    )
    selection = _bounded_h1_selection(
        _load(workspace / "global_identity.json"),
        _load(workspace / "analysis_report.json"),
        maximum=3,
        capture_domain=capture_domain,
        artifact_directory=REANCHOR_FRAME_DIRECTORY,
    )
    selection["mode"] = "second_half_identity_reanchor_selection_shadow"
    selection["second_half"] = {
        "start_time_sec": 0.0,
        "start_frame": 0,
        "safely_resolved_players_before_reanchor": safely_resolved_players,
        "h1_safe_lineage_allowed": False,
        "h1_safe_lineage_block_reason": (
            "independent_capture_domains_have_unrelated_tracklet_ids"
        ),
    }
    selection["reid_advisory_suggestions"] = advisory_suggestions
    frame_path = workspace / REANCHOR_DIRECTORY / "frames"
    frame_path.mkdir(parents=True, exist_ok=True)
    export_identity_audit_frames(workspace / "video.mp4", [int(row["frame"]) for row in selection.get("selected_frames") or []], frame_path)
    _write(workspace / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME, selection)
    return build_second_half_identity_reanchor_document(
        selection,
        match_document,
        safely_resolved_players=safely_resolved_players,
    )


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
    filtered_positions_by_frame = {
        frame: filter_identity_audit_observations(
            rows,
            minimum_confidence=float(
                IA0_DEFAULT_PARAMETERS["minimum_observation_confidence"]
            ),
            duplicate_containment_threshold=float(
                IA0_DEFAULT_PARAMETERS["duplicate_containment_threshold"]
            ),
        )[0]
        for frame, rows in positions_by_frame.items()
    }
    candidates = [
        (frame, rows) for frame, rows in filtered_positions_by_frame.items()
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
    meta = {**original_meta, "id": f"benchmark-{benchmark_id}-{domain.lower()}", "title": f"Benchmark {domain}: {original_meta.get('title')}", "benchmark_session": {"id": benchmark_id, "domain": domain, "capture_domain": _capture_domain_from_metadata(original_meta), "shadow_only": True, "reanchor_only": domain == "H2"}}
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


def _validate_pair(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    source_path: Path,
    target_path: Path,
) -> dict[str, Any]:
    if not source.get("match_date") or source.get("match_date") != target.get("match_date"):
        raise ProductFlowBenchmarkError("H1 and H2 do not prove the same physical match")
    source_teams = [str(team.get("id")) for team in source.get("teams") or []]
    target_teams = [str(team.get("id")) for team in target.get("teams") or []]
    if source_teams != target_teams:
        raise ProductFlowBenchmarkError("H1 and H2 roster/team contracts differ")
    if source_path.resolve() == target_path.resolve():
        raise ProductFlowBenchmarkError(
            "H1 and H2 point to the same source workspace"
        )
    source_domain = _capture_domain_from_metadata(source)
    target_domain = _capture_domain_from_metadata(target)
    if source_domain == "unknown" or target_domain == "unknown":
        raise ProductFlowBenchmarkError(
            "Capture domains are not proven by match/video metadata"
        )
    if source_domain == target_domain:
        raise ProductFlowBenchmarkError(
            "H1 and H2 capture domains are not distinct"
        )
    source_video = _source_video_path(source_path, source)
    target_video = _source_video_path(target_path, target)
    if source_video.resolve() == target_video.resolve():
        raise ProductFlowBenchmarkError(
            "H1 and H2 resolve to the same video artifact"
        )
    return {
        "match_date": source.get("match_date"),
        "team_ids": source_teams,
        "source_match_id": source.get("id"),
        "target_match_id": target.get("id"),
        "source_title": source.get("title"),
        "target_title": target.get("title"),
        "capture_domains": {
            "h1": source_domain,
            "h2": target_domain,
        },
        "capture_domain_evidence": {
            "h1": {
                "title": source.get("title"),
                "video_filename": source.get("video_filename"),
            },
            "h2": {
                "title": target.get("title"),
                "video_filename": target.get("video_filename"),
            },
        },
        "distinct_capture_domains": source_domain != target_domain,
        "independent_source_workspaces": True,
        "video_ranges": {
            "h1": _video_range(source, source_domain),
            "h2": _video_range(target, target_domain),
        },
        "video_digests": {
            "h1": _file_digest(source_video),
            "h2": _file_digest(target_video),
        },
    }


def _capture_domain_from_metadata(document: dict[str, Any]) -> str:
    explicit = str(document.get("capture_domain") or "").strip()
    if explicit:
        return explicit
    text = " ".join(
        str(document.get(key) or "")
        for key in ("title", "video_filename")
    ).lower()
    first_tokens = (
        "1 polowa",
        "1 połowa",
        "pierwsza polowa",
        "pierwsza połowa",
        "1st_half",
        "first_half",
    )
    second_tokens = (
        "2 polowa",
        "2 połowa",
        "drugiej polowy",
        "drugiej połowy",
        "druga polowa",
        "druga połowa",
        "2nd_half",
        "second_half",
    )
    if any(token in text for token in first_tokens):
        return "first_half"
    if any(token in text for token in second_tokens):
        return "second_half_fragment"
    return "unknown"


def _video_range(
    document: dict[str, Any],
    capture_domain: str,
) -> dict[str, Any]:
    video = document.get("video") or {}
    return {
        "capture_domain": capture_domain,
        "start_time_sec": 0.0,
        "end_time_sec": float(video.get("duration_sec") or 0.0),
        "frame_count": int(video.get("frame_count") or 0),
        "fps": float(video.get("fps") or 0.0),
    }


def _source_inventory(
    *,
    match_path: Path,
    artifact_path: Path,
    required: tuple[str, ...],
) -> list[dict[str, Any]]:
    paths: list[tuple[str, Path]] = [
        ("match/match.json", match_path / "match.json"),
        ("match/video", _source_video_path(match_path, _load(match_path / "match.json"))),
    ]
    paths.extend(
        (f"artifacts/{name}", artifact_path / name) for name in required
    )
    rows = []
    for relative_path, path in paths:
        if not path.exists():
            raise ProductFlowBenchmarkError(
                f"Inventory source is missing: {relative_path}"
            )
        rows.append(
            {
                "relative_path": relative_path,
                "source_path": str(path.resolve()),
                "size": path.stat().st_size,
                "canonical_digest": _file_digest(path),
            }
        )
    return sorted(rows, key=lambda row: row["relative_path"])


def _source_inventory_mutations(
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    changed = []
    for domain, rows in (session.get("source_inventory") or {}).items():
        for stored in rows or []:
            path = Path(str(stored.get("source_path") or ""))
            current_size = path.stat().st_size if path.exists() else None
            current_digest = _file_digest(path) if path.exists() else None
            if (
                current_size != stored.get("size")
                or current_digest != stored.get("canonical_digest")
            ):
                changed.append(
                    {
                        "domain": domain,
                        "artifact": stored.get("relative_path"),
                        "expected_size": stored.get("size"),
                        "current_size": current_size,
                        "expected_digest": stored.get("canonical_digest"),
                        "current_digest": current_digest,
                    }
                )
    return sorted(
        changed,
        key=lambda row: (
            str(row.get("domain") or ""),
            str(row.get("artifact") or ""),
        ),
    )


def _file_digest(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return canonical_digest(json.loads(path.read_text(encoding="utf-8")))
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_video_path(
    root: Path,
    match_document: dict[str, Any],
) -> Path:
    candidates = [root / "video.mp4"]
    metadata_path = str(
        (match_document.get("video") or {}).get("path") or ""
    )
    if metadata_path:
        candidates.append(Path(metadata_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for parent in (root, root.parent, root.parent.parent):
        for candidate in sorted(parent.glob("video.*")):
            if candidate.is_file():
                return candidate
    raise ProductFlowBenchmarkError("Frozen source video is missing")


def _publish_alias(alias: Path, workspace: Path) -> None:
    if alias.exists() or alias.is_symlink():
        raise ProductFlowBenchmarkError(
            f"Benchmark match alias already exists: {alias.name}"
        )
    alias.symlink_to(workspace.resolve(), target_is_directory=True)


def _load_optional(path: Path) -> dict[str, Any] | None:
    return _load(path) if path.exists() else None


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
