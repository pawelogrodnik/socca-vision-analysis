from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.services.artifact_lineage import canonical_json_sha256
from app.services.identity_jersey_number_panel_digitnet import contract_metadata
from app.services.identity_jersey_number_panel_digitnet_training import resolve_panel_training_selection


R2_GATE_THRESHOLDS = {
    "readable_recall": 0.95,
    "negative_specificity": 0.95,
    "exact_sequence_accuracy": 0.90,
}

# These are deliberately stricter than R2. A diagnostic recognizer must be
# consistently safe on episode-disjoint data before it can leave shadow mode.
R3_SAFETY_THRESHOLDS = {
    "crop_exact_sequence_accuracy": 0.90,
    "episode_exact_sequence_accuracy": 0.90,
    "episode_precision": 0.95,
    "episode_recall": 0.95,
    "plain_shirt_false_confirmed_reads": 0,
    "real10_exact": True,
}


def build_j84_closeout_report(
    dataset_doc: dict[str, Any],
    selection_doc: dict[str, Any],
    r2_report_doc: dict[str, Any],
    r3_report_doc: dict[str, Any],
    *,
    checkpoint_path: Path,
) -> dict[str, Any]:
    """Summarize the one permitted J8.4 R2/R3 cycle without mutating identity."""
    selected_keys = resolve_panel_training_selection(dataset_doc, selection_doc)
    selected_rows = [
        sample
        for sample in dataset_doc.get("samples") or []
        if isinstance(sample, dict) and str(sample.get("sample_key") or "") in selected_keys
    ]
    r2_evaluation = _mapping(r2_report_doc.get("evaluation"))
    r3_evaluation = _mapping(r3_report_doc.get("heldout_evaluation"))
    split = _mapping(r3_report_doc.get("split"))
    r2_metrics = {
        key: _number(r2_evaluation.get(key))
        for key in R2_GATE_THRESHOLDS
    }
    r2_passed = all(r2_metrics[key] >= threshold for key, threshold in R2_GATE_THRESHOLDS.items())

    heldout_episode_ids = _string_list(split.get("heldout_episode_ids"))
    heldout_episode_set = set(heldout_episode_ids)
    train_episode_ids = sorted(
        {
            str(row.get("visibility_episode_id") or "")
            for row in selected_rows
            if row.get("visibility_episode_id")
            and str(row.get("visibility_episode_id") or "") not in heldout_episode_set
        }
    )
    episode_leakage = sorted(set(train_episode_ids) & heldout_episode_set)
    incorrect_predictions = _incorrect_confirmed_predictions(r3_evaluation.get("predictions"))
    r3_metrics = _r3_metrics(r3_evaluation)
    r3_passed = _r3_passed(r3_metrics)
    final_decision = (
        "SHADOW_VALIDATION_PENDING_NEW_CAPTURE_DOMAIN"
        if r2_passed and r3_passed and not episode_leakage
        else "DIAGNOSTIC_COMPLETE_NOT_ELIGIBLE"
    )

    return {
        "schema_version": "1.0.0",
        "algorithm": {
            "name": "identity_jersey_number_j84_closeout",
            "version": "1.0.0",
            "mode": "diagnostic_shadow_only",
        },
        "dataset": {
            "digest": canonical_json_sha256(dataset_doc),
            "sample_count": len(dataset_doc.get("samples") or []),
            "approved_selection_digest": canonical_json_sha256(selection_doc),
            "approved_selection_declared_digest": selection_doc.get("selection_digest"),
            "approved_selection_sample_count": len(selected_rows),
        },
        "model": {
            "checkpoint_path": str(checkpoint_path),
            "checkpoint_sha256": _file_sha256(checkpoint_path),
            "model_version": str(contract_metadata()["algorithm"]["version"]),
            "contract": contract_metadata(),
        },
        "r2": {
            "status": "passed" if r2_passed else "failed",
            "gate_thresholds": R2_GATE_THRESHOLDS,
            "metrics": r2_metrics,
            "report_digest": canonical_json_sha256(r2_report_doc),
        },
        "r3": {
            "status": "passed" if r3_passed else "failed",
            "run_count": 1,
            "report_digest": canonical_json_sha256(r3_report_doc),
            "train_sample_count": split.get("train_sample_count"),
            "holdout_sample_count": split.get("heldout_sample_count"),
            "heldout_visibility_episode_ids": heldout_episode_ids,
            "heldout_visibility_episode_count": len(heldout_episode_ids),
            "train_visibility_episode_count": len(train_episode_ids),
            "episode_leakage_detected": bool(episode_leakage),
            "episode_leakage_ids": episode_leakage,
            "metrics": r3_metrics,
            "incorrect_confirmed_predictions": incorrect_predictions,
        },
        "identity_mutation_confirmation": {
            "automatic_assignments": 0,
            "candidate_identity_mutations": 0,
            "production_identity_mutations": 0,
            "shadow_only": True,
        },
        "final_decision": final_decision,
        "freeze": {
            "status": "FROZEN_UNTIL_NEW_INDEPENDENT_CAPTURE_DOMAIN",
            "reason": "J8.4 ends after the single episode-disjoint R3; no further tuning on the current capture domain is allowed.",
            "prohibited_current_capture_domain": [
                "collect_more_panels",
                "change_architecture",
                "tune_hyperparameters",
                "run_another_r3",
                "integrate_jersey_into_candidate_identity",
                "integrate_jersey_into_production_identity",
            ],
        },
    }


def _r3_metrics(evaluation: dict[str, Any]) -> dict[str, Any]:
    real10 = _mapping(evaluation.get("real10_episode_result"))
    target_numbers = _string_list(real10.get("target_numbers"))
    predicted_numbers = _string_list(real10.get("predicted_numbers"))
    return {
        "crop_exact_sequence_accuracy": _number(evaluation.get("crop_exact_sequence_accuracy")),
        "episode_exact_sequence_accuracy": _number(evaluation.get("episode_exact_sequence_accuracy")),
        "episode_precision": _number(evaluation.get("episode_precision")),
        "episode_recall": _number(evaluation.get("episode_recall")),
        "plain_shirt_false_confirmed_reads": evaluation.get("plain_shirt_false_confirmed_reads"),
        "real_number_10_result": {
            "available": target_numbers == ["10"],
            "target_numbers": target_numbers,
            "predicted_numbers": predicted_numbers,
            "exact_sequence": bool(real10.get("exact_sequence")),
        },
    }


def _r3_passed(metrics: dict[str, Any]) -> bool:
    real10 = _mapping(metrics.get("real_number_10_result"))
    return (
        all(_number(metrics.get(key)) >= threshold for key, threshold in R3_SAFETY_THRESHOLDS.items() if key not in {"plain_shirt_false_confirmed_reads", "real10_exact"})
        and metrics.get("plain_shirt_false_confirmed_reads") == R3_SAFETY_THRESHOLDS["plain_shirt_false_confirmed_reads"]
        and bool(real10.get("available"))
        and bool(real10.get("exact_sequence")) is R3_SAFETY_THRESHOLDS["real10_exact"]
    )


def _incorrect_confirmed_predictions(value: Any) -> list[dict[str, Any]]:
    predictions = value if isinstance(value, list) else []
    incorrect: list[dict[str, Any]] = []
    for row in predictions:
        if not isinstance(row, dict):
            continue
        target_state = str(row.get("target_state") or "")
        predicted_state = str(row.get("predicted_state") or "")
        target_number = row.get("target_number")
        predicted_number = row.get("predicted_number")
        wrong_confirmed_target = (
            target_state == "number_confirmed"
            and (predicted_state != "number_confirmed" or predicted_number != target_number)
        )
        false_confirmed_read = (
            target_state != "number_confirmed" and predicted_state == "number_confirmed"
        )
        if wrong_confirmed_target or false_confirmed_read:
            incorrect.append(
                {
                    "sample_key": row.get("sample_key"),
                    "target_state": target_state,
                    "target_number": target_number,
                    "predicted_state": predicted_state,
                    "predicted_number": predicted_number,
                    "raw_predicted_number": row.get("raw_predicted_number"),
                    "error_kind": "wrong_confirmed_target" if wrong_confirmed_target else "false_confirmed_read",
                }
            )
    return incorrect


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (float, int)) else 0.0
