from __future__ import annotations

"""Bind cross-capture ReID diagnostics to a safe future operator surface."""

from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


def build_reid_active_model_binding(
    *,
    portable_artifact: dict[str, Any],
    portable_evaluation: dict[str, Any],
    portable_display_gate: dict[str, Any],
    preferred_artifact: dict[str, Any] | None,
    preferred_evaluation: dict[str, Any] | None,
    preferred_display_gate: dict[str, Any],
    subject_tracklets: dict[str, list[str]],
) -> dict[str, Any]:
    """Separate diagnostic model evidence from future operator suggestions.

    A portable descriptor may be useful for measuring regressions, but it is
    never a candidate active operator model.  The sole allowed active model is
    a preferred run that itself passed the preferred-only display gate.
    """

    portable = _diagnostic_model(
        portable_artifact,
        portable_evaluation,
        portable_display_gate,
    )
    preferred = (
        _diagnostic_model(
            preferred_artifact,
            preferred_evaluation or {},
            preferred_display_gate,
        )
        if preferred_artifact is not None
        else {
            "status": "unavailable",
            "diagnostic_only": True,
            "artifact_digest": None,
            "rankings": [],
            "suggestions": [],
            "display_eligible": False,
        }
    )
    active_operator_run = (
        _active_operator_run(preferred, subject_tracklets)
        if preferred_artifact is not None
        and preferred_display_gate.get("display_eligible")
        else None
    )
    operator_advisory = {
        "display_eligible": active_operator_run is not None,
        "rankings": (
            list(active_operator_run.get("rankings") or [])
            if active_operator_run is not None
            else []
        ),
        "suggestions": (
            list(active_operator_run.get("suggestions") or [])
            if active_operator_run is not None
            else []
        ),
    }
    return {
        "diagnostic_models": {
            "portable": portable,
            "preferred": preferred,
        },
        "active_operator_run": active_operator_run,
        "active_operator_model": (
            active_operator_run.get("model")
            if active_operator_run is not None
            else None
        ),
        "active_operator_model_name": (
            (active_operator_run.get("model") or {}).get("model_name")
            if active_operator_run is not None
            else None
        ),
        "active_operator_runtime": (
            (active_operator_run.get("model") or {}).get("runtime")
            if active_operator_run is not None
            else None
        ),
        "active_operator_artifact_digest": (
            active_operator_run.get("artifact_digest")
            if active_operator_run is not None
            else None
        ),
        "portable_diagnostic_artifact_digest": portable["artifact_digest"],
        "preferred_diagnostic_artifact_digest": preferred["artifact_digest"],
        "portable_internal_calibration": portable.get(
            "internal_reference_calibration"
        ),
        "preferred_internal_calibration": preferred.get(
            "internal_reference_calibration"
        ),
        "active_operator_internal_calibration": (
            active_operator_run.get("internal_reference_calibration")
            if active_operator_run is not None
            else None
        ),
        "portable_cross_capture_evaluation": portable.get(
            "cross_capture_evaluation"
        ),
        "preferred_cross_capture_evaluation": preferred.get(
            "cross_capture_evaluation"
        ),
        "active_operator_cross_capture_evaluation": (
            active_operator_run.get("cross_capture_evaluation")
            if active_operator_run is not None
            else None
        ),
        "operator_advisory": operator_advisory,
        "operator_advisory_digest": canonical_digest(operator_advisory),
    }


def _diagnostic_model(
    artifact: dict[str, Any],
    evaluation: dict[str, Any],
    display_gate: dict[str, Any],
) -> dict[str, Any]:
    rankings = list(artifact.get("unresolved_rankings") or [])
    return {
        "status": "completed",
        "diagnostic_only": True,
        "model": artifact.get("model") or {},
        "artifact_digest": canonical_digest(artifact),
        "internal_reference_calibration": (
            artifact.get("internal_reference_calibration") or {}
        ),
        "cross_capture_evaluation": evaluation,
        "display_gate": display_gate,
        "display_eligible": False,
        "rankings": rankings,
        "suggestions": _suggestions_from_rankings(rankings, {}),
    }


def _active_operator_run(
    preferred: dict[str, Any],
    subject_tracklets: dict[str, list[str]],
) -> dict[str, Any]:
    rankings = list(preferred.get("rankings") or [])
    return {
        "model": preferred.get("model") or {},
        "artifact_digest": preferred.get("artifact_digest"),
        "internal_reference_calibration": preferred.get(
            "internal_reference_calibration"
        ),
        "cross_capture_evaluation": preferred.get(
            "cross_capture_evaluation"
        ),
        "rankings": rankings,
        "suggestions": _suggestions_from_rankings(rankings, subject_tracklets),
    }


def _suggestions_from_rankings(
    rankings: list[dict[str, Any]],
    subject_tracklets: dict[str, list[str]],
) -> list[dict[str, Any]]:
    return [
        {
            "candidate_subject_id": row.get("candidate_subject_id"),
            "team_label": row.get("team_label"),
            "tracklet_ids": subject_tracklets.get(
                str(row.get("candidate_subject_id") or ""),
                [],
            ),
            "suggestions": list(row.get("suggestions") or [])[:3],
            "advisory_only": True,
        }
        for row in rankings
        if row.get("status") == "ranked"
    ]
